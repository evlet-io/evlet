import { defineConfig } from 'vitest/config'
import solid from 'vite-plugin-solid'
const backendPort = Number(process.env.EVLET_BACKEND_PORT || 8000)
if (!Number.isInteger(backendPort) || backendPort < 1024 || backendPort > 65535) throw new Error('Invalid backend port')
// Development: /ui and its deep links serve the model-driven app (ui.html).
const uiEntry = { name: 'evlet-ui-entry', configureServer(server) { server.middlewares.use((req, _res, next) => { if (req.url && /^\/ui(\/|\?|$)/.test(req.url)) req.url = '/ui.html'; next(); }); } }
const proxy = Object.fromEntries(['/api', '/health'].map(path => [path, { target: `http://127.0.0.1:${backendPort}`, changeOrigin: false }]))
export default defineConfig(async ({ mode }) => ({
  plugins: [solid(), uiEntry, ...(mode === 'test' ? [(await import('@vitejs/plugin-vue')).default()] : [])],
  server: { port: 5173, proxy }, preview: { port: 4173, proxy },
  build: { target: 'es2022', sourcemap: false },
  test: { environment: 'jsdom', include: ['src/**/*.test.{js,ts,tsx}', 'legacy/**/*.test.js'], setupFiles: ['./src/tests/setup.ts'], maxWorkers: 2 },
}))
