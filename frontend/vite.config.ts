import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import os from 'node:os'

function normalizeBasePath(value: string | undefined): string {
  const trimmed = value?.trim()
  if (!trimmed || trimmed === '/') return '/'
  const withoutLeading = trimmed.replace(/^\/+/, '')
  if (!withoutLeading) return '/'
  return `/${withoutLeading.replace(/\/+$/, '')}/`
}

function allowedHosts(): string[] {
  const configured = (process.env.VITE_ALLOWED_HOSTS ?? '')
    .split(',')
    .map((host) => host.trim().toLowerCase())
    .filter(Boolean)
  const machineName = os.hostname().trim().toLowerCase()
  return Array.from(new Set([machineName, ...configured]))
}

function basePathRedirect(basePath: string): Plugin {
  return {
    name: 'redirect-bare-base-path',
    configureServer(server) {
      if (basePath === '/') return
      const barePath = basePath.slice(0, -1)
      server.middlewares.use((request, response, next) => {
        if (request.method !== 'GET' && request.method !== 'HEAD') {
          next()
          return
        }
        const requestUrl = new URL(request.url ?? '/', 'http://vite.local')
        if (requestUrl.pathname !== barePath) {
          next()
          return
        }
        response.statusCode = 307
        response.setHeader('Location', `${basePath}${requestUrl.search}`)
        response.end()
      })
    },
  }
}

export default defineConfig(({ mode }) => ({
  plugins: [react(), basePathRedirect(normalizeBasePath(process.env.VITE_APP_BASE_PATH))],
  base: normalizeBasePath(process.env.VITE_APP_BASE_PATH),
  define: {
    'process.env.NODE_ENV': JSON.stringify(mode === 'production' ? 'production' : 'development'),
  },
  optimizeDeps: {
    esbuildOptions: {
      define: { 'process.env.NODE_ENV': JSON.stringify('development') },
    },
  },
  server: {
    port: 5173,
    allowedHosts: allowedHosts(),
    proxy: {
      '/api': process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000',
      '/assets': process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000',
    },
  },
}))
