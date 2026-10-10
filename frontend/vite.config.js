import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// The API the dev server proxies to. End-to-end runs point this at a disposable backend.
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    // The hosted site's content security policy allows fonts and images from its own
    // origin only, so nothing is inlined as a data: URI.
    assetsInlineLimit: 0,
  },
  server: {
    proxy: {
      '/auth': apiTarget,
      '/api': apiTarget,
      '/health': apiTarget,
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    css: false,
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
