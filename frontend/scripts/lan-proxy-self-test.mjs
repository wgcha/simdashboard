import http from 'node:http'
import net from 'node:net'
import os from 'node:os'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const frontendDir = resolve(scriptDir, '..')
const viteBin = resolve(frontendDir, 'node_modules', 'vite', 'bin', 'vite.js')

function activeLanIpv4() {
  const interfaces = os.networkInterfaces()
  for (const entries of Object.values(interfaces)) {
    for (const entry of entries ?? []) {
      if (entry.family === 'IPv4' && !entry.internal && net.isIPv4(entry.address)) return entry.address
    }
  }
  return null
}

function listen(server, host) {
  return new Promise((resolvePromise, reject) => {
    const onError = (error) => { server.off('listening', onListening); reject(error) }
    const onListening = () => { server.off('error', onError); resolvePromise(server.address().port) }
    server.once('error', onError)
    server.once('listening', onListening)
    server.listen(0, host)
  })
}

async function waitFor(url, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs
  let lastError
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url)
      if (response.status > 0) return response
    } catch (error) {
      lastError = error
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 150))
  }
  throw new Error(`Timed out waiting for ${url}: ${lastError?.message ?? 'no response'}`)
}

async function getFreePort() {
  const probe = net.createServer()
  const port = await listen(probe, '127.0.0.1')
  await new Promise((resolvePromise, reject) => probe.close((error) => error ? reject(error) : resolvePromise()))
  return port
}

function request(req, res) {
  if (req.url === '/api/health') {
    res.writeHead(200, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ status: 'ok', via: 'loopback-stub' }))
    return
  }
  if (req.url === '/api/set-cookie') {
    res.writeHead(200, { 'set-cookie': ['lan_proxy=present; Path=/'], 'content-type': 'application/json' })
    res.end(JSON.stringify({ ok: true }))
    return
  }
  if (req.url === '/api/cookie-check') {
    const valid = /(?:^|;\s*)lan_proxy=present(?:;|$)/.test(req.headers.cookie ?? '')
    res.writeHead(valid ? 200 : 400, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ cookie: valid }))
    return
  }
  if (req.url === '/assets/lan-proxy.txt') {
    res.writeHead(200, { 'content-type': 'text/plain' })
    res.end('asset-through-proxy')
    return
  }
  res.writeHead(404)
  res.end()
}

async function main() {
  const lanAddress = activeLanIpv4()
  if (!lanAddress) {
    console.error('LAN proxy self-test skipped: no active non-loopback IPv4 interface was found.')
    process.exitCode = 2
    return
  }
  if (!existsSync(viteBin)) throw new Error(`Vite was not found: ${viteBin}`)

  const backend = http.createServer(request)
  let viteProcess
  try {
    const backendPort = await listen(backend, '127.0.0.1')
    const frontendPort = await getFreePort()
    viteProcess = spawn(process.execPath, [viteBin, '--host', '0.0.0.0', '--port', String(frontendPort), '--strictPort', '--configLoader', 'runner'], {
      cwd: frontendDir,
      env: { ...process.env, VITE_API_TARGET: `http://127.0.0.1:${backendPort}` },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    let viteOutput = ''
    viteProcess.stdout.on('data', (chunk) => { viteOutput += chunk.toString() })
    viteProcess.stderr.on('data', (chunk) => { viteOutput += chunk.toString() })

    const origin = `http://${lanAddress}:${frontendPort}`
    const page = await waitFor(`${origin}/`)
    if (!page.ok || !(await page.text()).includes('<title>')) throw new Error('LAN HTML request did not return the Vite page')

    const health = await fetch(`${origin}/api/health`)
    if (!health.ok || (await health.json()).via !== 'loopback-stub') throw new Error('LAN /api proxy did not reach the loopback backend')

    const cookieResponse = await fetch(`${origin}/api/set-cookie`)
    const cookie = cookieResponse.headers.get('set-cookie')
    if (!cookie?.includes('lan_proxy=present')) throw new Error('LAN /api proxy did not forward Set-Cookie')
    const cookieCheck = await fetch(`${origin}/api/cookie-check`, { headers: { cookie: 'lan_proxy=present' } })
    if (!cookieCheck.ok || !(await cookieCheck.json()).cookie) throw new Error('LAN /api proxy cookie request failed')

    const asset = await fetch(`${origin}/assets/lan-proxy.txt`)
    if (!asset.ok || (await asset.text()) !== 'asset-through-proxy') throw new Error('LAN /assets proxy did not reach the loopback backend')

    console.log(`LAN proxy self-test passed via ${lanAddress}:${frontendPort} (backend loopback 127.0.0.1:${backendPort})`)
    if (viteOutput.includes('error')) console.warn(viteOutput)
  } finally {
    if (viteProcess && !viteProcess.killed) viteProcess.kill()
    await new Promise((resolvePromise) => backend.close(() => resolvePromise()))
  }
}

main().catch((error) => {
  console.error(`LAN proxy self-test failed: ${error.message}`)
  process.exitCode = 1
})
