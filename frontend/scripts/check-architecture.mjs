import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const defaultSourceRoot = path.join(frontendDir, 'src')
const defaultBaselinePath = path.join(frontendDir, 'architecture-baseline.json')

// These values deliberately live in code as well as the reviewable JSON file. The
// JSON file cannot silently be used to raise a ceiling on the default source tree.
const lockedRawApiPathCeilings = {
  'App.tsx': 0,
  'api.ts': 0,
  'features/auth/LoginScreen.tsx': 1,
  'features/workbench/api.ts': 0,
  'shared/api/auth.ts': 1,
}

const lockedSourceLineCeilings = {
  'App.tsx': 1076,
  'app/routing/workspaceRouteModules.tsx': 26,
  'api.ts': 393,
  'features/analysis/AnalysisPageManager.tsx': 95,
  'features/data/DataWorkspace.tsx': 189,
  'features/data/FolderSchemaWorkspace.tsx': 84,
  'features/data/VariableCatalogPage.tsx': 30,
  'features/reports/ReportLayoutEditor.tsx': 206,
  'features/reports/ReportExportDialog.tsx': 32,
  'features/reports/api.ts': 132,
  'features/reports/useReportExportController.ts': 240,
  'features/requests/WorkflowView.tsx': 87,
  'features/results/AnalysisWidgets.tsx': 290,
  'features/results/DropVideoGrid.tsx': 257,
  'features/results/ResultsWorkspace.tsx': 80,
  'features/workbench/AutomationTemplatesPage.tsx': 10,
  'features/workbench/api.ts': 70,
  'shared/api/client.ts': 21,
}

const forbiddenGeneratedResponseGenericTokens = [
  'adaptRecordShape<',
  'adaptRecordArrayShape<',
  'adaptWorkbenchRecord<',
  'adaptWorkbenchArray<',
  'unwrapGenerated<',
]

const lockedCrossFeatureRelativeImports = [
  { from: 'features/access/AccessAdministration.tsx', to: 'features/auth/access', specifier: '../auth/access' },
  { from: 'features/bootstrap/BootstrapWorkspaceShell.tsx', to: 'features/auth/access', specifier: '../auth/access' },
  { from: 'features/bootstrap/loadInitialWorkspace.ts', to: 'features/analysis/pageSelection', specifier: '../analysis/pageSelection' },
  { from: 'features/bootstrap/loadInitialWorkspace.ts', to: 'features/auth/access', specifier: '../auth/access' },
  { from: 'features/navigation/workspaceRouteRegistry.ts', to: 'features/auth/access', specifier: '../auth/access' },
]

function parseArgs(argv) {
  const options = { root: defaultSourceRoot, baseline: defaultBaselinePath }
  for (let index = 0; index < argv.length; index += 1) {
    const option = argv[index]
    if (option === '--root' || option === '--baseline') {
      const value = argv[index + 1]
      if (!value) throw new Error(`${option} requires a value`)
      options[option.slice(2)] = path.resolve(value)
      index += 1
      continue
    }
    if (option === '--help') {
      console.log('Usage: node scripts/check-architecture.mjs [--root <src>] [--baseline <json>]')
      process.exit(0)
    }
    throw new Error(`Unknown option: ${option}`)
  }
  return options
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(',')}}`
  }
  return JSON.stringify(value)
}

function listFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(directory, entry.name)
    if (entry.isDirectory()) return entry.name === 'generated' ? [] : listFiles(entryPath)
    return entry.isFile() ? [entryPath] : []
  })
}

function sourcePath(root, file) {
  return path.relative(root, file).split(path.sep).join('/')
}

function featureImportRecords(relativeFile, text) {
  const fromFeature = relativeFile.match(/^features\/([^/]+)\//)?.[1]
  if (!fromFeature) return []

  const specifiers = new Set()
  for (const match of text.matchAll(/\bfrom\s*["'](\.{1,2}\/[^"']+)["']/g)) specifiers.add(match[1])
  for (const match of text.matchAll(/\bimport\s*["'](\.{1,2}\/[^"']+)["']/g)) specifiers.add(match[1])
  for (const match of text.matchAll(/\bimport\s*\(\s*["'](\.{1,2}\/[^"']+)["']\s*\)/g)) specifiers.add(match[1])

  return [...specifiers].flatMap((specifier) => {
    const target = path.posix.normalize(path.posix.join(path.posix.dirname(relativeFile), specifier))
    const targetParts = target.split('/')
    if (targetParts[0] !== 'features' || !targetParts[1] || targetParts[1] === fromFeature) return []
    return [{ from: relativeFile, to: target, specifier }]
  })
}

function rawApiPathCount(text) {
  const literalCount = [...text.matchAll(/(?:['"`])\/api\//g)].length
  const generatedPathCount = [...text.matchAll(/(?:apiClient\.(?:GET|POST|PUT|PATCH|DELETE)|apiUrl)\(\s*(?:['"`])\/api\//g)].length
  return literalCount - generatedPathCount
}

export function validateBaseline(baseline, { isDefaultBaseline }) {
  if (!baseline || typeof baseline !== 'object' || Array.isArray(baseline)) throw new Error('Baseline must be a JSON object')
  if (!baseline.rawApiPathCeilings || typeof baseline.rawApiPathCeilings !== 'object' || Array.isArray(baseline.rawApiPathCeilings)) {
    throw new Error('Baseline rawApiPathCeilings must be an object')
  }
  if (!baseline.sourceLineCeilings || typeof baseline.sourceLineCeilings !== 'object' || Array.isArray(baseline.sourceLineCeilings)) {
    throw new Error('Baseline sourceLineCeilings must be an object')
  }
  if (!Array.isArray(baseline.crossFeatureRelativeImports)) throw new Error('Baseline crossFeatureRelativeImports must be an array')
  for (const [file, ceiling] of Object.entries(baseline.rawApiPathCeilings)) {
    if (!Number.isInteger(ceiling) || ceiling < 0) throw new Error(`Invalid /api/ ceiling for ${file}`)
  }
  for (const [file, ceiling] of Object.entries(baseline.sourceLineCeilings)) {
    if (!Number.isInteger(ceiling) || ceiling < 0) throw new Error(`Invalid source line ceiling for ${file}`)
  }
  for (const record of baseline.crossFeatureRelativeImports) {
    if (!record || typeof record.from !== 'string' || typeof record.to !== 'string' || typeof record.specifier !== 'string') {
      throw new Error('Every cross-feature whitelist entry requires from, to, and specifier strings')
    }
  }
  if (isDefaultBaseline) {
    if (stableJson(baseline.rawApiPathCeilings) !== stableJson(lockedRawApiPathCeilings)) {
      throw new Error('Default /api/ ceilings are locked; edit this checker and the baseline together after architecture review')
    }
    if (stableJson(baseline.sourceLineCeilings) !== stableJson(lockedSourceLineCeilings)) {
      throw new Error('Default source line ceilings are locked; edit this checker and the baseline together after architecture review')
    }
    if (stableJson(baseline.crossFeatureRelativeImports) !== stableJson(lockedCrossFeatureRelativeImports)) {
      throw new Error('Default cross-feature whitelist is locked; edit this checker and the baseline together after architecture review')
    }
  }
}

export function run({ root, baselinePath }) {
  if (!existsSync(root) || !statSync(root).isDirectory()) throw new Error(`Source root does not exist: ${root}`)
  if (!existsSync(baselinePath)) throw new Error(`Baseline does not exist: ${baselinePath}`)

  const baseline = JSON.parse(readFileSync(baselinePath, 'utf8'))
  validateBaseline(baseline, { isDefaultBaseline: path.resolve(baselinePath) === path.resolve(defaultBaselinePath) })
  const allowedImports = new Set(baseline.crossFeatureRelativeImports.map(stableJson))
  const observedImports = new Set()
  const errors = []

  for (const file of listFiles(root)) {
    const relativeFile = sourcePath(root, file)
    const text = readFileSync(file, 'utf8')
    const apiPathCount = rawApiPathCount(text)
    const ceiling = baseline.rawApiPathCeilings[relativeFile] ?? 0
    if (apiPathCount > ceiling) errors.push(`${relativeFile}: found ${apiPathCount} raw /api/ strings (ceiling ${ceiling})`)
    const sourceLineCount = text.split(/\r?\n/).length - (text.endsWith('\n') ? 1 : 0)
    const sourceLineCeiling = baseline.sourceLineCeilings[relativeFile]
    if (sourceLineCeiling !== undefined && sourceLineCount > sourceLineCeiling) {
      errors.push(`${relativeFile}: found ${sourceLineCount} source lines (ceiling ${sourceLineCeiling})`)
    }
    for (const token of forbiddenGeneratedResponseGenericTokens) {
      if (text.includes(token)) errors.push(`${relativeFile}: forbidden generated-response generic escape ${token}`)
    }
    for (const record of featureImportRecords(relativeFile, text)) observedImports.add(stableJson(record))
  }

  for (const record of observedImports) {
    if (!allowedImports.has(record)) {
      const value = JSON.parse(record)
      errors.push(`${value.from}: cross-feature relative import ${value.specifier} -> ${value.to} is not whitelisted`)
    }
  }
  for (const record of allowedImports) {
    if (!observedImports.has(record)) {
      const value = JSON.parse(record)
      errors.push(`Stale cross-feature whitelist entry: ${value.from} ${value.specifier} -> ${value.to}`)
    }
  }

  if (errors.length) throw new Error(`Frontend architecture check failed:\n${errors.map((error) => `- ${error}`).join('\n')}`)
  console.log(`Frontend architecture check passed (${listFiles(root).length} source files, ${observedImports.size} reviewed cross-feature imports).`)
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const options = parseArgs(process.argv.slice(2))
    run({ root: options.root, baselinePath: options.baseline })
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error))
    process.exitCode = 1
  }
}
