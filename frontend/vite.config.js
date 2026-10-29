import { defineConfig } from 'vitest/config'
import solid from 'vite-plugin-solid'
import vue from '@vitejs/plugin-vue'
const backendPort = Number(process.env.EVLET_BACKEND_PORT || 8000)
if (!Number.isInteger(backendPort) || backendPort < 1024 || backendPort > 65535) throw new Error('Invalid backend port')
const proxy = Object.fromEntries(['/api', '/health'].map(path => [path, { target: `http://127.0.0.1:${backendPort}`, changeOrigin: false }]))
export default defineConfig({
  plugins: [solid(), vue()],
  server: { port: 5173, proxy }, preview: { port: 4173, proxy },
  build: { target: 'es2022', sourcemap: false },
  test: { environment: 'jsdom', include: ['src/**/*.test.{js,ts,tsx}', 'legacy/**/*.test.js'], setupFiles: ['./src/tests/setup.ts'], maxWorkers: 2 },
})
