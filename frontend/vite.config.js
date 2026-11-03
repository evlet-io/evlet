import { defineConfig } from 'vitest/config'
const backendPort = Number(process.env.EVLET_BACKEND_PORT || 8000)
if (!Number.isInteger(backendPort) || backendPort < 1024 || backendPort > 65535) throw new Error('Invalid backend port')
const proxy = Object.fromEntries(['/api', '/health', '/_/mcp', '/_/oauth/register', '/_/oauth/token', '/_/oauth/revoke', '/.well-known', '/_dev/signin'].map(path => [path, { target: `http://127.0.0.1:${backendPort}`, changeOrigin: false }]))
// index.html is the production app. evlet.html (the component Library) is served by the dev server only.
export default defineConfig({
  server: { port: 5173, proxy }, preview: { port: 4173, proxy },
  build: { target: 'es2022', sourcemap: false },
  test: { environment: 'jsdom', include: ['src/**/*.test.ts'], setupFiles: ['./src/evlet/tests/setup.ts'], maxWorkers: 2 },
})
