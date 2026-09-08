/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const API_PROXY_TARGET = process.env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    // Dev-only proxy to the FastAPI backend. Keeps the browser on a single
    // origin during development, so the dev workflow does not depend on the
    // backend's CORS_ALLOWED_ORIGINS being set. In production the frontend is
    // built to static files and VITE_API_BASE_URL points at the real API.
    // Override the target with VITE_API_PROXY_TARGET when the backend runs on a
    // different port (e.g. a second instance against a scratch database).
    proxy: {
      '/api': { target: API_PROXY_TARGET, changeOrigin: true },
      '/health': { target: API_PROXY_TARGET, changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: true,
    // Run test files one at a time in a single worker. Each parallel worker
    // loads its own jsdom instance (~200 MB), and Vitest defaults to one worker
    // per CPU -- on a many-core machine with modest free RAM that reliably
    // exhausts memory mid-run. The suite takes ~10s sequentially, so this costs
    // little and makes the run deterministic on any machine.
    fileParallelism: false,
    poolOptions: {
      forks: { maxForks: 1, minForks: 1 },
    },
  },
})
