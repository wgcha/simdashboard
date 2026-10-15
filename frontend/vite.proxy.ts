import { readFileSync } from 'node:fs'
import type { ProxyOptions } from 'vite'

// Headers a browser must not be able to forward to the API through this proxy.  uvicorn trusts
// X-Forwarded-* from 127.0.0.1 (= this proxy), so client-supplied values are removed, and the
// real peer address is passed as X-Simdash-Proxy-Peer (overwritten, never taken from the client).
// The API uses it for the "same PC only" rule of the drive account login (issue #50).
export const STRIPPED_PROXY_HEADERS = ['x-forwarded-for', 'x-forwarded-proto', 'x-forwarded-host', 'x-forwarded-port', 'x-forwarded-prefix', 'forwarded', 'x-real-ip', 'x-simdash-proxy-peer']

export function apiProxy(target: string): ProxyOptions {
  return {
    target,
    configure(proxy) {
      proxy.on('proxyReq', (proxyReq, request) => {
        for (const name of STRIPPED_PROXY_HEADERS) proxyReq.removeHeader(name)
        proxyReq.setHeader('x-simdash-proxy-peer', request.socket.remoteAddress ?? '')
      })
    },
  }
}

// PhysicsAI (separate app: UI + API under /physicsai/) on the same origin as the dashboard.
// The path is forwarded unchanged (no rewrite) and the browser cookies, including the dashboard
// session cookie, go along so PhysicsAI can check the session against /api/auth/me.  Client-sent
// X-Forwarded-*/Forwarded/X-Real-IP and the dashboard-only peer header are removed.
// `/physicsai` is a root path: it does not move under VITE_APP_BASE_PATH (e.g. /home/), because
// Vite runs proxies before its base-path handling, like /api and /assets.  Do not use
// /physicsai/ as VITE_APP_BASE_PATH (WINDOWS_WEB_BASE_PATH).
export const PHYSICSAI_PROXY_PATH = '^/physicsai(?:[/?#]|$)'

export function physicsAiProxy(target: string): ProxyOptions {
  return {
    target,
    configure(proxy) {
      proxy.on('proxyReq', (proxyReq) => {
        for (const name of STRIPPED_PROXY_HEADERS) proxyReq.removeHeader(name)
      })
    },
  }
}

/** `VITE_PHYSICSAI_TARGET` as an http(s) origin, or null when unset/invalid (= no /physicsai proxy). */
export function physicsAiTarget(env: Record<string, string | undefined>): string | null {
  const value = env.VITE_PHYSICSAI_TARGET?.trim()
  if (!value) return null
  try {
    const url = new URL(value)
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null
    return url.origin
  } catch {
    return null
  }
}

/** Vite dev-server proxy table. `/physicsai` is added only when VITE_PHYSICSAI_TARGET is set. */
export function devServerProxy(env: Record<string, string | undefined>): Record<string, ProxyOptions> {
  const apiTarget = env.VITE_API_TARGET ?? 'http://127.0.0.1:8000'
  const proxy: Record<string, ProxyOptions> = {
    '/api': apiProxy(apiTarget),
    '/assets': apiProxy(apiTarget),
  }
  const physicsAi = physicsAiTarget(env)
  if (physicsAi) proxy[PHYSICSAI_PROXY_PATH] = physicsAiProxy(physicsAi)
  return proxy
}

/** Value of one `KEY=value` line in dotenv text (last match wins), or undefined when absent.
 * Minimal on purpose: `export KEY=`, single/double quotes, ` #` comments, CRLF and a BOM are
 * handled; no variable expansion or multi-line values (VITE_PHYSICSAI_TARGET is a plain URL). */
export function dotenvValue(text: string, key: string): string | undefined {
  let found: string | undefined
  for (const rawLine of text.replace(/^\uFEFF/, '').split(/\r?\n/)) {
    const match = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(.*)$/.exec(rawLine)
    if (!match || match[1] !== key) continue
    let value = match[2].trim()
    const quote = value[0]
    if ((quote === '"' || quote === "'") && value.indexOf(quote, 1) > 0) {
      value = value.slice(1, value.indexOf(quote, 1))
    } else {
      value = value.replace(/\s+#.*$/, '').trim()
    }
    found = value
  }
  return found
}

/**
 * VITE_PHYSICSAI_TARGET for the dev-server proxy: the process environment wins, otherwise the
 * repository-root `.env` (the file start.ps1 and the backend use) is read directly.  Only this
 * one key is parsed out of that file; nothing is written to process.env or the client env, so
 * NODE_ENV, BROWSER, secrets or other VITE_* keys in the root .env never reach Vite (unlike
 * Vite's loadEnv, which copies NODE_ENV/BROWSER/BROWSER_ARGS into process.env).
 */
export function physicsAiTargetSetting(envFilePath: string, processEnv: Record<string, string | undefined>): string | undefined {
  if (processEnv.VITE_PHYSICSAI_TARGET !== undefined) return processEnv.VITE_PHYSICSAI_TARGET
  let text: string
  try {
    text = readFileSync(envFilePath, 'utf8')
  } catch {
    return undefined
  }
  return dotenvValue(text, 'VITE_PHYSICSAI_TARGET')
}
