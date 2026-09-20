import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { checkCssArchitecture, deriveCssBaseline } from './css-architecture.mjs'

const temporaryRoot = process.platform === 'win32' ? os.tmpdir() : '/tmp'
const fixtureDir = mkdtempSync(path.join(temporaryRoot, 'analysis-canvas-css-architecture-'))

function writeFixture(name, files) {
  const root = path.join(fixtureDir, name, 'src')
  for (const [relativePath, contents] of Object.entries(files)) {
    const target = path.join(root, relativePath)
    mkdirSync(path.dirname(target), { recursive: true })
    writeFileSync(target, contents)
  }
  return root
}

function expectFailure(name, root, baseline, expectedError) {
  const { errors } = checkCssArchitecture(root, baseline)
  if (!errors.some((error) => error.includes(expectedError))) {
    throw new Error(`${name} did not fail with ${expectedError}; received ${errors.join(' | ') || 'no errors'}`)
  }
}

try {
  const validRoot = writeFixture('valid', {
    'styles.css': '.app-shell { color: navy; }\n',
    'features/alpha/alpha.css': '.alpha-card { max-width: 720px; }\n@media (max-width: 620px) { .alpha-card { color: red; } }\n',
  })
  const baseline = deriveCssBaseline(validRoot)
  const valid = checkCssArchitecture(validRoot, baseline)
  if (valid.errors.length) throw new Error(`valid fixture failed: ${valid.errors.join(' | ')}`)

  const importantRoot = writeFixture('important', {
    'styles.css': '.app-shell { font-size: 14pt !important; }\n.new-copy { font-size: 12px !important; }\n',
  })
  expectFailure('new important declaration', importantRoot, deriveCssBaseline(writeFixture('important-baseline', {
    'styles.css': '.app-shell { font-size: 14pt !important; }\n',
  })), 'legacy allowlist')

  const wrapperRoot = writeFixture('wrapper', {
    'styles.css': '.app-shell { color: navy; }\n',
    'features/alpha/alpha.css': '.alpha-workspace { max-width: 960px; }\n',
  })
  expectFailure('new wrapper cap', wrapperRoot, baseline, 'wrapper max-width')

  const variableCapRoot = writeFixture('variable-cap', {
    'styles.css': '.app-shell { color: navy; }\n',
    'features/alpha/alpha.css': '.alpha-workspace { max-width: var(--alpha-cap); }\n',
  })
  expectFailure('variable wrapper cap', variableCapRoot, baseline, 'wrapper max-width')

  const mixedCapRoot = writeFixture('mixed-cap', {
    'styles.css': '.app-shell { color: navy; }\n',
    'features/alpha/alpha.css': '.alpha-workspace, .alpha-dialog { max-width: 960px; }\n',
  })
  expectFailure('mixed wrapper and dialog cap', mixedCapRoot, baseline, 'wrapper max-width')

  const breakpointRoot = writeFixture('breakpoint', {
    'styles.css': '.app-shell { color: navy; }\n',
    'features/alpha/alpha.css': '@media (max-width: 777px) { .alpha-card { color: red; } }\n',
  })
  expectFailure('new breakpoint', breakpointRoot, baseline, 'breakpoint: max-width:777px is new')

  const globalRoot = writeFixture('global', {
    'styles.css': '.app-shell { color: navy; }\n',
    'features/alpha/alpha.css': 'body { color: red; }\n',
  })
  expectFailure('feature global selector', globalRoot, baseline, 'feature CSS global selector')

  for (const [name, selector] of [
    ['functional body selector', ':where(body)'],
    ['root descendant selector', '#root button'],
    ['bare element selector', 'a'],
  ]) {
    const bareElementRoot = writeFixture(`bare-element-${name.replaceAll(' ', '-')}`, {
      'styles.css': '.app-shell { color: navy; }\n',
      'features/alpha/alpha.css': `${selector} { color: red; }\n`,
    })
    expectFailure(name, bareElementRoot, baseline, 'feature CSS global selector')
  }

  const migratedRoot = writeFixture('migrated', {
    'styles.css': '.app-shell { color: navy; }\n',
    'features/alpha/alpha.css': "[ data-ui-density = 'v1' ] .alpha-button { height: 36px; border-radius: 6px; }\n",
  })
  const migratedBaseline = deriveCssBaseline(migratedRoot)
  expectFailure('migrated control token', migratedRoot, migratedBaseline, 'migrated control')
  expectFailure('migrated radius token', migratedRoot, migratedBaseline, 'migrated radius')

  const migratedSpecialRadiusRoot = writeFixture('migrated-special-radius', {
    'styles.css': '.app-shell { color: navy; }\n',
    'features/alpha/alpha.css': '[data-ui-density=v1] .ordinary-button { border-radius: 999px; }\n',
  })
  expectFailure('unapproved migrated special radius', migratedSpecialRadiusRoot, deriveCssBaseline(migratedSpecialRadiusRoot), 'migrated radius')

  const growthRoot = writeFixture('growth', {
    'styles.css': '.app-shell { color: navy; }\n.extra { color: red; }\n',
  })
  expectFailure('global styles line ceiling', growthRoot, baseline, 'styles.css: found 2 lines')
  expectFailure('global styles declaration ceiling', writeFixture('declaration-growth', {
    'styles.css': '.app-shell { color: navy; background: white; }\n',
  }), baseline, 'styles.css: found 2 declarations')

  console.log('CSS architecture checker self-test passed.')
} finally {
  rmSync(fixtureDir, { recursive: true, force: true })
}
