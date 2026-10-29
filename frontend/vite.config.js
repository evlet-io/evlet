import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

const backendPort = Number(process.env.EVLET_BACKEND_PORT || 8000)
if (!Number.isInteger(backendPort) || backendPort < 1024 || backendPort > 65535) throw new Error('Invalid backend port')
const proxy = {
  '/api': { target: `http://127.0.0.1:${backendPort}`, changeOrigin: false },
  '/health': { target: `http://127.0.0.1:${backendPort}`, changeOrigin: false },
}

export default defineConfig({
  plugins: [vue()],
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.js'],
    setupFiles: ['./src/test-setup.js'],
    maxWorkers: 2,
  },
})
