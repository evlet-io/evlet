import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

const proxy = {
  '/api': 'http://127.0.0.1:8000',
  '/health': 'http://127.0.0.1:8000',
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
