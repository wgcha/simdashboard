import { createRequire } from 'node:module'
import { createHash } from 'node:crypto'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'

const require = createRequire(import.meta.url)
const postcss = require('postcss')

const controlSelectorPattern = /(?:^|[\s>+~,(])(?:button|input|select|textarea)(?:\b|[#.[:])|\[(?:role\s*=\s*["']?(?:button|textbox|combobox|spinbutton)|type\s*=\s*["']?(?:button|submit|reset))|\.(?:[^\s,{]*?(?:button|control|input|select|textarea|picker|dropdown)[^\s,{]*)/i
const wrapperSelectorPattern = /(?:workspace|dashboard|layout|page|board|view|shell|gallery|hero|card|list)/i
const wrapperExclusionPattern = /(?:dialog|form|input|select|textarea|cell|column|chart|canvas|video|image|preview|tooltip|drawer|help)/i
const migratedScopePattern = /\[\s*data-ui-density\s*=\s*(?:"v1"|'v1'|v1)\s*\]/i

function normalize(value) {
  return value.trim().replace(/\s+/g, ' ')
}

function listCssFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(directory, entry.name)
    if (entry.isDirectory()) return listCssFiles(entryPath)
    return entry.isFile() && entry.name.endsWith('.css') ? [entryPath] : []
  })
}

function relativePath(root, file) {
  return path.relative(root, file).split(path.sep).join('/')
}

function declarationKey(record) {
  return `${record.file}|${record.selector}|${record.property}|${record.value}`
}

function fingerprint(value) {
  return createHash('sha256').update(value).digest('hex')
}

function selectorKey(record) {
  return `${record.file}|${record.selector}`
}

function lineCount(text) {
  return text.split(/\r?\n/).length - (text.endsWith('\n') ? 1 : 0)
}

function isControl(selector) {
  return controlSelectorPattern.test(selector)
}

function isPotentialWrapperCap(record) {
  return !/^(?:100%|none)$/i.test(record.value)
    && wrapperSelectorPattern.test(record.selector)
    && !wrapperExclusionPattern.test(record.selector)
}

function isFeatureGlobalSelector(selector) {
  return /(?:^|[\s>+~(:])(?::root|html|body|#root)(?:\b|[\s>+~):])/i.test(selector)
    || !/[.#\[]/.test(selector)
}

function isMigratedSelector(selector) {
  return migratedScopePattern.test(selector)
}

function isKeyframesRule(rule) {
  return rule.parent?.type === 'atrule' && /(?:^|-)keyframes$/i.test(rule.parent.name)
}

function breakpointValues(params) {
  return [...params.matchAll(/(?:min|max)-(?:width|height)\s*:\s*([\d.]+(?:px|em|rem|vw|vh|%))/gi)]
    .map((match) => normalize(match[0]).replace(/:\s+/g, ':').toLowerCase())
}

export function collectCssMetrics(sourceRoot) {
  if (!statSync(sourceRoot).isDirectory()) throw new Error(`CSS source root does not exist: ${sourceRoot}`)

  const files = listCssFiles(sourceRoot).sort()
  const metrics = {
    files: {},
    fontSizeImportant: [],
    wrapperCaps: [],
    breakpoints: {},
    featureGlobalSelectors: [],
    migratedControlDeclarations: [],
    migratedRadiusDeclarations: [],
  }

  for (const file of files) {
    const text = readFileSync(file, 'utf8')
    const relativeFile = relativePath(sourceRoot, file)
    const root = postcss.parse(text, { from: file })
    let declarations = 0
    root.walkDecls((decl) => {
      declarations += 1
      const rule = decl.parent?.type === 'rule' ? decl.parent : null
      const selector = rule ? normalize(rule.selector) : '(non-rule declaration)'
      const record = {
        file: relativeFile,
        line: decl.source?.start?.line ?? null,
        selector,
        property: decl.prop.toLowerCase(),
        value: normalize(decl.value),
        important: Boolean(decl.important),
      }
      if (record.property === 'font-size' && record.important) metrics.fontSizeImportant.push(record)
      const selectors = rule ? postcss.list.comma(rule.selector).map(normalize) : [selector]
      for (const scopedSelector of selectors) {
        const selectorRecord = { ...record, selector: scopedSelector }
        if (record.property === 'max-width' && isPotentialWrapperCap(selectorRecord)) metrics.wrapperCaps.push(selectorRecord)
        if (isMigratedSelector(scopedSelector) && isControl(scopedSelector) && (record.property === 'height' || record.property === 'min-height')) {
          metrics.migratedControlDeclarations.push(selectorRecord)
        }
        if (isMigratedSelector(scopedSelector) && record.property === 'border-radius') metrics.migratedRadiusDeclarations.push(selectorRecord)
      }
    })
    root.walkAtRules('media', (atRule) => {
      for (const breakpoint of breakpointValues(atRule.params)) metrics.breakpoints[breakpoint] = (metrics.breakpoints[breakpoint] ?? 0) + 1
    })
    root.walkRules((rule) => {
      if (isKeyframesRule(rule)) return
      for (const selector of postcss.list.comma(rule.selector).map(normalize)) {
        if (relativeFile.startsWith('features/') && isFeatureGlobalSelector(selector)) {
          metrics.featureGlobalSelectors.push({ file: relativeFile, line: rule.source?.start?.line ?? null, selector })
        }
      }
    })
    metrics.files[relativeFile] = { lines: lineCount(text), declarations }
  }

  metrics.fontSizeImportant.sort((left, right) => declarationKey(left).localeCompare(declarationKey(right)))
  metrics.wrapperCaps.sort((left, right) => declarationKey(left).localeCompare(declarationKey(right)))
  metrics.featureGlobalSelectors.sort((left, right) => selectorKey(left).localeCompare(selectorKey(right)))
  return metrics
}

export function deriveCssBaseline(sourceRoot) {
  const metrics = collectCssMetrics(sourceRoot)
  const styles = metrics.files['styles.css']
  if (!styles) throw new Error('CSS baseline requires styles.css')
  return {
    globalStyles: { lineCeiling: styles.lines, declarationCeiling: styles.declarations },
    fontSizeImportant: {
      legacyCeiling: metrics.fontSizeImportant.length,
      allowedDeclarationFingerprints: metrics.fontSizeImportant.map(declarationKey).map(fingerprint),
    },
    wrapperCapExceptionFingerprints: metrics.wrapperCaps.map(declarationKey).map(fingerprint),
    breakpoints: metrics.breakpoints,
    featureGlobalSelectorFingerprints: metrics.featureGlobalSelectors.map(selectorKey).map(fingerprint),
    migratedSpecialRadiusFingerprints: [],
  }
}

function arrayOfStrings(value, name) {
  if (!Array.isArray(value) || value.some((entry) => typeof entry !== 'string')) throw new Error(`${name} must be a string array`)
}

export function validateCssBaseline(cssBaseline) {
  if (!cssBaseline || typeof cssBaseline !== 'object' || Array.isArray(cssBaseline)) throw new Error('Baseline css must be an object')
  const { globalStyles, fontSizeImportant, wrapperCapExceptionFingerprints, breakpoints, featureGlobalSelectorFingerprints, migratedSpecialRadiusFingerprints } = cssBaseline
  if (!globalStyles || !Number.isInteger(globalStyles.lineCeiling) || globalStyles.lineCeiling < 0 || !Number.isInteger(globalStyles.declarationCeiling) || globalStyles.declarationCeiling < 0) {
    throw new Error('Baseline css.globalStyles requires non-negative integer ceilings')
  }
  if (!fontSizeImportant || !Number.isInteger(fontSizeImportant.legacyCeiling) || fontSizeImportant.legacyCeiling < 0) {
    throw new Error('Baseline css.fontSizeImportant.legacyCeiling must be a non-negative integer')
  }
  arrayOfStrings(fontSizeImportant.allowedDeclarationFingerprints, 'Baseline css.fontSizeImportant.allowedDeclarationFingerprints')
  if (fontSizeImportant.allowedDeclarationFingerprints.length !== fontSizeImportant.legacyCeiling
    || new Set(fontSizeImportant.allowedDeclarationFingerprints).size !== fontSizeImportant.legacyCeiling) {
    throw new Error('Baseline css.fontSizeImportant allowlist must exactly match the legacy ceiling')
  }
  arrayOfStrings(wrapperCapExceptionFingerprints, 'Baseline css.wrapperCapExceptionFingerprints')
  arrayOfStrings(featureGlobalSelectorFingerprints, 'Baseline css.featureGlobalSelectorFingerprints')
  arrayOfStrings(migratedSpecialRadiusFingerprints, 'Baseline css.migratedSpecialRadiusFingerprints')
  if (!breakpoints || typeof breakpoints !== 'object' || Array.isArray(breakpoints)) throw new Error('Baseline css.breakpoints must be an object')
  for (const [breakpoint, count] of Object.entries(breakpoints)) {
    if (!/^(?:min|max)-(?:width|height):/.test(breakpoint) || !Number.isInteger(count) || count < 0) throw new Error(`Invalid CSS breakpoint baseline ${breakpoint}`)
  }
}

export function checkCssArchitecture(sourceRoot, cssBaseline) {
  validateCssBaseline(cssBaseline)
  const metrics = collectCssMetrics(sourceRoot)
  const errors = []
  const styles = metrics.files['styles.css']
  if (!styles) errors.push('styles.css is missing')
  else {
    if (styles.lines > cssBaseline.globalStyles.lineCeiling) errors.push(`styles.css: found ${styles.lines} lines (ceiling ${cssBaseline.globalStyles.lineCeiling})`)
    if (styles.declarations > cssBaseline.globalStyles.declarationCeiling) errors.push(`styles.css: found ${styles.declarations} declarations (ceiling ${cssBaseline.globalStyles.declarationCeiling})`)
  }

  const allowedImportant = new Set(cssBaseline.fontSizeImportant.allowedDeclarationFingerprints)
  if (metrics.fontSizeImportant.length > cssBaseline.fontSizeImportant.legacyCeiling) {
    errors.push(`font-size !important: found ${metrics.fontSizeImportant.length} declarations (legacy ceiling ${cssBaseline.fontSizeImportant.legacyCeiling})`)
  }
  for (const record of metrics.fontSizeImportant) {
    if (!allowedImportant.has(fingerprint(declarationKey(record)))) errors.push(`font-size !important: new declaration ${declarationKey(record)} is not in the legacy allowlist`)
  }

  const allowedCaps = new Set(cssBaseline.wrapperCapExceptionFingerprints)
  for (const record of metrics.wrapperCaps) {
    if (!allowedCaps.has(fingerprint(declarationKey(record)))) errors.push(`wrapper max-width: ${declarationKey(record)} is not an approved exception`)
  }

  for (const [breakpoint, count] of Object.entries(metrics.breakpoints)) {
    const ceiling = cssBaseline.breakpoints[breakpoint]
    if (ceiling === undefined) errors.push(`breakpoint: ${breakpoint} is new`)
    else if (count > ceiling) errors.push(`breakpoint: ${breakpoint} occurs ${count} times (ceiling ${ceiling})`)
  }

  const allowedGlobalSelectors = new Set(cssBaseline.featureGlobalSelectorFingerprints)
  for (const record of metrics.featureGlobalSelectors) {
    if (!allowedGlobalSelectors.has(fingerprint(selectorKey(record)))) errors.push(`feature CSS global selector: ${selectorKey(record)} is not approved`)
  }

  const allowedSpecialRadius = new Set(cssBaseline.migratedSpecialRadiusFingerprints)
  for (const record of metrics.migratedControlDeclarations) {
    if (!/^var\(--control-h-(?:sm|md|lg)\)$/.test(record.value)) {
      errors.push(`migrated control: ${declarationKey(record)} must use a --control-h-* token`)
    }
  }
  for (const record of metrics.migratedRadiusDeclarations) {
    if (!/^var\(--radius-(?:sm|md|lg)\)$/.test(record.value) && !allowedSpecialRadius.has(fingerprint(declarationKey(record)))) {
      errors.push(`migrated radius: ${declarationKey(record)} must use a --radius-* token or an approved special-shape exception`)
    }
  }

  return { errors, metrics }
}
