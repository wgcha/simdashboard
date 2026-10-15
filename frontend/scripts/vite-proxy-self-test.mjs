import { EventEmitter } from 'node:events'
import { devServerProxy, PHYSICSAI_PROXY_PATH, physicsAiTarget, STRIPPED_PROXY_HEADERS } from '../vite.proxy.ts'

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

function fakeRequest(headers) {
  const current = new Map(Object.entries(headers))
  return {
    removeHeader: (name) => current.delete(name),
    setHeader: (name, value) => current.set(name, value),
    headers: current,
  }
}

function runProxyReq(options, headers) {
  const proxy = new EventEmitter()
  options.configure(proxy, options)
  const proxyReq = fakeRequest(headers)
  proxy.emit('proxyReq', proxyReq, { socket: { remoteAddress: '10.0.0.7' } }, {}, {})
  return proxyReq.headers
}

// Without VITE_PHYSICSAI_TARGET the proxy table is exactly /api and /assets (unchanged behavior).
for (const env of [{}, { VITE_PHYSICSAI_TARGET: '' }, { VITE_PHYSICSAI_TARGET: '   ' }, { VITE_PHYSICSAI_TARGET: 'not a url' }, { VITE_PHYSICSAI_TARGET: 'file:///etc/passwd' }]) {
  const keys = Object.keys(devServerProxy(env))
  assert(JSON.stringify(keys) === '["/api","/assets"]', `no /physicsai proxy expected for ${JSON.stringify(env)}: ${keys}`)
}
assert(devServerProxy({})['/api'].target ==='http://127.0.0.1:8000', 'default API target')
assert(devServerProxy({ VITE_API_TARGET: 'http://127.0.0.1:9000' })['/assets'].target === 'http://127.0.0.1:9000', 'API target from env')

// With VITE_PHYSICSAI_TARGET the /physicsai proxy is added, without a path rewrite.
const proxies = devServerProxy({ VITE_PHYSICSAI_TARGET: ' http://127.0.0.1:8100 ' })
const physicsAi = proxies[PHYSICSAI_PROXY_PATH]
assert(physicsAi && physicsAi.target === 'http://127.0.0.1:8100', 'PhysicsAI proxy must target VITE_PHYSICSAI_TARGET')
assert(physicsAi.rewrite === undefined && physicsAi.changeOrigin === undefined, 'PhysicsAI path and Host must be forwarded unchanged')
assert(physicsAiTarget({ VITE_PHYSICSAI_TARGET: 'https://physicsai.internal:8443/x' }) === 'https://physicsai.internal:8443', 'target is reduced to its origin')

// The regex key matches /physicsai, /physicsai/..., /physicsai?x but not lookalike paths.
const matcher = new RegExp(PHYSICSAI_PROXY_PATH)
for (const url of ['/physicsai', '/physicsai/', '/physicsai/api/health', '/physicsai?next=1']) assert(matcher.test(url), `${url} must be proxied`)
for (const url of ['/physicsaix', '/home/physicsai/', '/api/physicsai', '/']) assert(!matcher.test(url), `${url} must not be proxied`)

// Client-sent forwarding headers are removed; cookies stay.
const forwarded = Object.fromEntries(STRIPPED_PROXY_HEADERS.map((name) => [name, 'spoofed']))
const physicsAiHeaders = runProxyReq(physicsAi, { ...forwarded, cookie: 'analysis_canvas_session=abc' })
assert(STRIPPED_PROXY_HEADERS.every((name) => !physicsAiHeaders.has(name)), 'PhysicsAI proxy must strip X-Forwarded-*/Forwarded/X-Real-IP')
assert(physicsAiHeaders.get('cookie') === 'analysis_canvas_session=abc', 'PhysicsAI proxy must keep the session cookie')
const apiHeaders = runProxyReq(proxies['/api'], { ...forwarded, cookie: 'analysis_canvas_session=abc' })
assert(apiHeaders.get('x-simdash-proxy-peer') === '10.0.0.7' && apiHeaders.get('x-forwarded-for') === undefined && apiHeaders.get('cookie'), 'API proxy behavior is unchanged')

console.log('Vite proxy self-test passed.')
