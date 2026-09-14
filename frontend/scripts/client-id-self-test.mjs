import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const sourceRoot = new URL('../src/', import.meta.url)
const clientIdModule = new URL('../src/shared/identity/clientId.ts', import.meta.url)

async function sourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const files = await Promise.all(entries.map(async (entry) => entry.isDirectory()
    ? sourceFiles(path.join(directory, entry.name))
    : [path.join(directory, entry.name)]))
  return files.flat()
}

async function withCrypto(overrides, run) {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto')
  Object.defineProperty(globalThis, 'crypto', { configurable: true, value: overrides })
  try {
    return await run()
  } finally {
    if (descriptor) Object.defineProperty(globalThis, 'crypto', descriptor)
    else delete globalThis.crypto
  }
}

async function loadClientId() {
  return (await import(`${clientIdModule.href}?test=${Math.random()}`)).createClientId
}

await withCrypto({
  randomUUID() {
    assert.equal(this, globalThis.crypto, 'native randomUUID must retain its crypto receiver')
    return 'native-id'
  },
  getRandomValues() { throw new Error('fallback must not run when randomUUID exists') },
}, async () => {
  assert.equal((await loadClientId())(), 'native-id')
})

await withCrypto({
  getRandomValues(bytes) {
    assert.equal(this, globalThis.crypto, 'fallback getRandomValues must retain its crypto receiver')
    bytes.set(Array.from({ length: bytes.length }, (_, index) => index))
    return bytes
  },
}, async () => {
  assert.equal((await loadClientId())(), '00010203-0405-4607-8809-0a0b0c0d0e0f')
})

await withCrypto({ getRandomValues: webcrypto.getRandomValues.bind(webcrypto) }, async () => {
  const createClientId = await loadClientId()
  const ids = Array.from({ length: 256 }, () => createClientId())
  for (const id of ids) assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  assert.equal(new Set(ids).size, ids.length, 'fallback IDs should not repeat in a representative random sample')
})

await withCrypto({}, async () => {
  const createClientId = await loadClientId()
  assert.throws(() => createClientId(), /Secure random client ID generation is unavailable/)
})

const sourceFilesWithoutHelper = (await sourceFiles(fileURLToPath(sourceRoot)))
  .filter((file) => /\.(?:ts|tsx)$/.test(file))
  .filter((file) => path.normalize(file) !== path.normalize(fileURLToPath(clientIdModule)))
for (const file of sourceFilesWithoutHelper) {
  const contents = await readFile(file, 'utf8')
  assert.doesNotMatch(contents, /crypto\.randomUUID/, `${path.relative(fileURLToPath(sourceRoot), file)} bypasses createClientId`)
}

console.log('Client ID self-test passed.')
