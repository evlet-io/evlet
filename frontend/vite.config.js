import { createHash } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { defineConfig } from 'vitest/config'
import { backendAuthority } from './scripts/dev-proxy.mjs'
const backendPort = Number(process.env.EVLET_BACKEND_PORT || 8000)
if (!Number.isInteger(backendPort) || backendPort < 1024 || backendPort > 65535) throw new Error('Invalid backend port')
const devPorts = ['5173', '4173']
// An instance accepts only its own origin, so loopback requests to this dev server are presented as the
// backend's own host and origin. Any other host or origin passes through unchanged and the backend rejects it.
const configure = proxy => proxy.on('proxyReq', request => {
  const host = backendAuthority(request.getHeader('host'), devPorts, backendPort)
  if (host) request.setHeader('host', host)
  const origin = request.getHeader('origin')
  const rewritten = typeof origin === 'string' && origin.startsWith('http://') ? backendAuthority(origin.slice(7), devPorts, backendPort) : null
  if (rewritten) request.setHeader('origin', `http://${rewritten}`)
})
const proxy = Object.fromEntries(['/api', '/health', '/_/mcp', '/_/oauth/register', '/_/oauth/token', '/_/oauth/revoke', '/.well-known', '/_dev/signin'].map(path => [path, { target: `http://127.0.0.1:${backendPort}`, changeOrigin: false, configure }]))
// The service worker keeps cache-first only the fingerprinted files this build emits ([name]-[hash] in assets/),
// listed into its copy in dist; an app's own /assets/ file with a similar name is never kept. The worker's cache
// names carry a fingerprint of that list, so every release is a new worker that drops the caches of the one before.
const MARKER = '[/* evlet:build-assets */]', BUILD = "/* evlet:build */'dev'"
const workerAssets = () => {
  let outDir, built = []
  return {
    name: 'evlet-worker-assets', apply: 'build',
    configResolved: config => { outDir = resolve(config.root, config.build.outDir) },
    generateBundle: (_, bundle) => {
      built = Object.keys(bundle).filter(name => /^assets\/[A-Za-z0-9_.-]+-[A-Za-z0-9_-]{8}\.(?:js|css|woff2)$/.test(name)).map(name => '/' + name).sort()
    },
    closeBundle: async () => {
      const file = resolve(outDir, 'assets/evlet-sw.js'), source = await readFile(file, 'utf8')
      if (source.split(MARKER).length !== 2 || source.split(BUILD).length !== 2 || !built.length) throw new Error('evlet-sw.js needs exactly one build asset marker and one build marker')
      const fingerprint = createHash('sha256').update(built.join('\n')).digest('hex').slice(0, 12)
      await writeFile(file, source.replace(MARKER, JSON.stringify(built)).replace(BUILD, `'${fingerprint}'`))
    },
  }
}
// index.html is the production app and project.html the project endpoint's portal. evlet.html (the component
// Library) is served by the dev server only.
export default defineConfig({
  plugins: [workerAssets()],
  server: { port: 5173, proxy }, preview: { port: 4173, proxy },
  build: { target: 'es2022', sourcemap: false, rollupOptions: { input: { index: resolve(import.meta.dirname, 'index.html'), project: resolve(import.meta.dirname, 'project.html') } } },
  test: { environment: 'jsdom', include: ['src/**/*.test.ts', 'scripts/*.test.mjs'], setupFiles: ['./src/evlet/tests/setup.ts'], maxWorkers: 2 },
})
