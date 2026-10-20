import { EventEmitter } from 'node:events'
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { resolveConfig } from 'vite'
import { devServerProxy, dotenvValue, ESL_PROXY_PATH, eslTarget, eslTargetSetting, PHYSICSAI_PROXY_PATH, physicsAiTarget, physicsAiTargetSetting, STRIPPED_PROXY_HEADERS } from '../vite.proxy.ts'

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

// Root .env parsing: only VITE_PHYSICSAI_TARGET, process.env wins.
assert(dotenvValue('\uFEFFA=1\r\nVITE_PHYSICSAI_TARGET=http://h:1 # note\r\n', 'VITE_PHYSICSAI_TARGET') === 'http://h:1', 'CRLF/BOM/comment')
assert(dotenvValue('export VITE_PHYSICSAI_TARGET="http://h:2"', 'VITE_PHYSICSAI_TARGET') === 'http://h:2', 'export + double quotes')
assert(dotenvValue("VITE_PHYSICSAI_TARGET='http://h:3#x'", 'VITE_PHYSICSAI_TARGET') === 'http://h:3#x', 'single quotes keep #')
assert(dotenvValue('# VITE_PHYSICSAI_TARGET=http://h:4\nVITE_PHYSICSAI_TARGETX=http://h:5', 'VITE_PHYSICSAI_TARGET') === undefined, 'comments/lookalike keys ignored')
assert(dotenvValue('VITE_PHYSICSAI_TARGET=', 'VITE_PHYSICSAI_TARGET') === '', 'empty value')

// ESL (등가정적하중, /esl/) uses the same rules as PhysicsAI, independently of it.
for (const env of [{}, { VITE_ESL_TARGET: '' }, { VITE_ESL_TARGET: '  ' }, { VITE_ESL_TARGET: 'not a url' }, { VITE_ESL_TARGET: 'file:///etc/passwd' }]) {
  assert(!Object.keys(devServerProxy(env)).includes(ESL_PROXY_PATH), `no /esl proxy expected for ${JSON.stringify(env)}`)
}
const eslOnly = devServerProxy({ VITE_ESL_TARGET: ' http://127.0.0.1:8110 ' })
assert(JSON.stringify(Object.keys(eslOnly)) === JSON.stringify(['/api', '/assets', ESL_PROXY_PATH]), `ESL alone must not add /physicsai: ${Object.keys(eslOnly)}`)
const esl = eslOnly[ESL_PROXY_PATH]
assert(esl.target === 'http://127.0.0.1:8110', 'ESL proxy must target VITE_ESL_TARGET')
assert(esl.rewrite === undefined && esl.changeOrigin === undefined, 'ESL path and Host must be forwarded unchanged')
assert(eslTarget({ VITE_ESL_TARGET: 'https://esl.internal:8443/x' }) === 'https://esl.internal:8443', 'ESL target is reduced to its origin')
const both = devServerProxy({ VITE_PHYSICSAI_TARGET: 'http://127.0.0.1:8100', VITE_ESL_TARGET: 'http://127.0.0.1:8110' })
assert(both[PHYSICSAI_PROXY_PATH].target === 'http://127.0.0.1:8100' && both[ESL_PROXY_PATH].target === 'http://127.0.0.1:8110', 'PhysicsAI and ESL proxies coexist')
const eslMatcher = new RegExp(ESL_PROXY_PATH)
for (const url of ['/esl', '/esl/', '/esl/api/health', '/esl?next=1']) assert(eslMatcher.test(url), `${url} must be proxied to ESL`)
for (const url of ['/eslx', '/esl-old/', '/home/esl/', '/api/esl', '/physicsai/', '/']) assert(!eslMatcher.test(url), `${url} must not be proxied to ESL`)
const eslHeaders = runProxyReq(esl, { ...forwarded, cookie: 'analysis_canvas_session=abc' })
assert(STRIPPED_PROXY_HEADERS.every((name) => !eslHeaders.has(name)), 'ESL proxy must strip X-Forwarded-*/Forwarded/X-Real-IP')
assert(eslHeaders.get('cookie') === 'analysis_canvas_session=abc', 'ESL proxy must keep the session cookie')

const frontendDir = fileURLToPath(new URL('..', import.meta.url))
const sandbox = mkdtempSync(path.join(os.tmpdir(), 'vite-root-env-'))
const savedEnv = { ...process.env }
try {
  const sandboxFrontend = path.join(sandbox, 'frontend')
  mkdirSync(sandboxFrontend)
  for (const file of ['vite.config.ts', 'vite.proxy.ts']) copyFileSync(path.join(frontendDir, file), path.join(sandboxFrontend, file))
  symlinkSync(path.join(frontendDir, 'node_modules'), path.join(sandboxFrontend, 'node_modules'), 'dir')
  const rootEnvFile = path.join(sandbox, '.env')
  for (const key of ['VITE_PHYSICSAI_TARGET', 'VITE_ESL_TARGET', 'VITE_USER_NODE_ENV', 'BROWSER', 'BROWSER_ARGS', 'DB_PASSWORD', 'VITE_OTHER']) delete process.env[key]
  const resolve = () => resolveConfig({ configFile: path.join(sandboxFrontend, 'vite.config.ts'), root: sandboxFrontend, logLevel: 'silent' }, 'serve', 'development')

  const baseline = await resolve()
  assert(!Object.keys(baseline.server.proxy).includes(PHYSICSAI_PROXY_PATH), 'no root .env: no /physicsai proxy')
  assert(!Object.keys(baseline.server.proxy).includes(ESL_PROXY_PATH), 'no root .env: no /esl proxy')
  const envBefore = { ...process.env } // after Vite's own NODE_ENV=development for serve

  writeFileSync(rootEnvFile, 'NODE_ENV=production\nBROWSER=none\nBROWSER_ARGS=--x\nDB_PASSWORD=x\nVITE_OTHER=y\nVITE_PHYSICSAI_TARGET=http://127.0.0.1:8100\nVITE_ESL_TARGET=http://127.0.0.1:8110\n')
  const resolved = await resolve()
  assert(JSON.stringify(Object.keys(resolved.env).sort()) === JSON.stringify(Object.keys(baseline.env).sort()), `root .env must not add client env keys: ${Object.keys(resolved.env)}`)
  assert(resolved.env.MODE === 'development' && resolved.mode === 'development' && !resolved.isProduction, 'root .env NODE_ENV must not change the mode')
  assert(resolved.env.VITE_USER_NODE_ENV === undefined && resolved.env.VITE_OTHER === undefined && resolved.env.VITE_PHYSICSAI_TARGET === undefined && resolved.env.VITE_ESL_TARGET === undefined, 'no root .env keys in the client env')
  assert(JSON.stringify(process.env) === JSON.stringify(envBefore), 'root .env must not touch process.env')
  assert(resolved.server.proxy[PHYSICSAI_PROXY_PATH]?.target === 'http://127.0.0.1:8100', '/physicsai proxy must follow VITE_PHYSICSAI_TARGET from the root .env')

  assert(resolved.server.proxy[ESL_PROXY_PATH]?.target === 'http://127.0.0.1:8110', '/esl proxy must follow VITE_ESL_TARGET from the root .env')

  process.env.VITE_ESL_TARGET = 'http://127.0.0.1:8210'
  assert((await resolve()).server.proxy[ESL_PROXY_PATH]?.target === 'http://127.0.0.1:8210', 'process.env VITE_ESL_TARGET wins over the root .env')
  process.env.VITE_ESL_TARGET = ''
  const eslOff = await resolve()
  assert(!Object.keys(eslOff.server.proxy).includes(ESL_PROXY_PATH) && eslOff.server.proxy[PHYSICSAI_PROXY_PATH]?.target === 'http://127.0.0.1:8100', 'empty VITE_ESL_TARGET turns only the /esl proxy off')
  assert(eslTargetSetting(path.join(sandbox, 'missing.env'), {}) === undefined, 'missing root .env is not an error (ESL)')
  delete process.env.VITE_ESL_TARGET

  process.env.VITE_PHYSICSAI_TARGET = 'http://127.0.0.1:8200'
  const overridden = await resolve()
  assert(overridden.server.proxy[PHYSICSAI_PROXY_PATH]?.target === 'http://127.0.0.1:8200', 'process.env VITE_PHYSICSAI_TARGET wins over the root .env')
  process.env.VITE_PHYSICSAI_TARGET = ''
  assert(!Object.keys((await resolve()).server.proxy).includes(PHYSICSAI_PROXY_PATH), 'empty process.env value turns the proxy off')
  assert(physicsAiTargetSetting(path.join(sandbox, 'missing.env'), {}) === undefined, 'missing root .env is not an error')
} finally {
  for (const key of Object.keys(process.env)) if (!(key in savedEnv)) delete process.env[key]
  Object.assign(process.env, savedEnv)
  rmSync(sandbox, { recursive: true, force: true })
}

console.log('Vite proxy self-test passed.')
