import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { run, validateBaseline } from './check-architecture.mjs'

// WSL can inherit a Windows TEMP value that is mounted read-only inside the
// Linux sandbox. Keep the fixture outside the workspace and use /tmp there.
const temporaryRoot = process.platform === 'win32' ? os.tmpdir() : '/tmp'
const fixtureDir = mkdtempSync(path.join(temporaryRoot, 'analysis-canvas-architecture-'))
const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

function writeFixture(name, files, baseline) {
  const root = path.join(fixtureDir, name, 'src')
  for (const [relativePath, contents] of Object.entries(files)) {
    const target = path.join(root, relativePath)
    mkdirSync(path.dirname(target), { recursive: true })
    writeFileSync(target, contents)
  }
  const baselinePath = path.join(fixtureDir, name, 'baseline.json')
  writeFileSync(baselinePath, `${JSON.stringify(baseline, null, 2)}\n`)
  return { root, baselinePath }
}

function expectFailure(name, action, expectedError) {
  try {
    action()
    throw new Error(`${name} passed when it should have failed with ${expectedError}`)
  } catch (error) {
    if (!(error instanceof Error) || !error.message.includes(expectedError)) {
      throw new Error(`${name} failed with an unexpected error: ${error instanceof Error ? error.message : String(error)}`)
    }
  }
}

function expectResult(name, fixture, expectedError = '') {
  const action = () => run({ root: fixture.root, baselinePath: fixture.baselinePath })
  if (expectedError) return expectFailure(name, action, expectedError)
  action()
}

try {
  const reviewedImport = { from: 'features/alpha/client.ts', to: 'features/beta/internal', specifier: '../beta/internal' }
  expectResult('reviewed imports and raw path ceiling', writeFixture('valid', {
    'features/alpha/client.ts': "import { value } from '../beta/internal'\nconst endpoint = '/api/'\nexport { value, endpoint }\n",
    'features/beta/internal.ts': 'export const value = 1\n',
  }, { rawApiPathCeilings: { 'features/alpha/client.ts': 1 }, sourceLineCeilings: {}, crossFeatureRelativeImports: [reviewedImport] }))

  expectResult('new raw path is rejected', writeFixture('raw-api-violation', {
    'features/alpha/client.ts': "const endpoint = '/api/'\n",
  }, { rawApiPathCeilings: {}, sourceLineCeilings: {}, crossFeatureRelativeImports: [] }), 'raw /api/ strings')

  expectResult('generated path does not hide a raw fetch on the same line', writeFixture('generated-and-raw-same-line', {
    'client.ts': "apiClient.GET('/api/typed'); fetch('/api/raw')\n",
  }, { rawApiPathCeilings: {}, sourceLineCeilings: {}, crossFeatureRelativeImports: [] }), 'raw /api/ strings')

  expectResult('source line ceiling is enforced', writeFixture('source-line-violation', {
    'App.tsx': 'export const first = 1\nexport const second = 2\n',
  }, { rawApiPathCeilings: {}, sourceLineCeilings: { 'App.tsx': 1 }, crossFeatureRelativeImports: [] }), 'source lines')

  expectResult('generated response generic escape is rejected', writeFixture('generic-response-escape', {
    'api.ts': 'const response = adaptRecordShape<Model>(value)\n',
  }, { rawApiPathCeilings: {}, sourceLineCeilings: {}, crossFeatureRelativeImports: [] }), 'forbidden generated-response generic escape')

  expectResult('new cross-feature import is rejected', writeFixture('cross-feature-violation', {
    'features/alpha/client.ts': "import { value } from '../beta/internal'\nexport { value }\n",
    'features/beta/internal.ts': 'export const value = 1\n',
  }, { rawApiPathCeilings: {}, sourceLineCeilings: {}, crossFeatureRelativeImports: [] }), 'not whitelisted')

  expectResult('side-effect cross-feature import is rejected', writeFixture('side-effect-cross-feature-violation', {
    'features/alpha/client.ts': "import '../beta/setup'\n",
    'features/beta/setup.ts': 'globalThis.__architectureFixture = true\n',
  }, { rawApiPathCeilings: {}, sourceLineCeilings: {}, crossFeatureRelativeImports: [] }), 'not whitelisted')

  expectResult('stale whitelist entry is rejected', writeFixture('stale-whitelist', {
    'features/alpha/client.ts': 'export const value = 1\n',
  }, { rawApiPathCeilings: {}, sourceLineCeilings: {}, crossFeatureRelativeImports: [reviewedImport] }), 'Stale cross-feature whitelist entry')

  const defaultBaseline = JSON.parse(readFileSync(path.join(frontendDir, 'architecture-baseline.json'), 'utf8'))
  expectFailure('raising a locked default ceiling is rejected', () => validateBaseline({
    ...defaultBaseline,
    rawApiPathCeilings: { ...defaultBaseline.rawApiPathCeilings, 'api.ts': defaultBaseline.rawApiPathCeilings['api.ts'] + 1 },
  }, { isDefaultBaseline: true }), 'Default /api/ ceilings are locked')
  expectFailure('raising the locked App source line ceiling is rejected', () => validateBaseline({
    ...defaultBaseline,
    sourceLineCeilings: { ...defaultBaseline.sourceLineCeilings, 'App.tsx': defaultBaseline.sourceLineCeilings['App.tsx'] + 1 },
  }, { isDefaultBaseline: true }), 'Default source line ceilings are locked')
  console.log('Frontend architecture checker self-test passed.')
} finally {
  rmSync(fixtureDir, { recursive: true, force: true })
}
