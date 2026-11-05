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
// index.html is the production app. evlet.html (the component Library) is served by the dev server only.
export default defineConfig({
  server: { port: 5173, proxy }, preview: { port: 4173, proxy },
  build: { target: 'es2022', sourcemap: false },
  test: { environment: 'jsdom', include: ['src/**/*.test.ts', 'scripts/*.test.mjs'], setupFiles: ['./src/evlet/tests/setup.ts'], maxWorkers: 2 },
})
