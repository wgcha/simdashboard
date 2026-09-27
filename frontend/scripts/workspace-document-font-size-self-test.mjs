import { applyWorkspaceDocumentFontSize } from '../src/app/preferences/workspaceFontSizeStyle.ts'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const postcss = require('postcss')

class FakeStyle {
  properties = new Map()

  constructor(initial = {}) {
    for (const [name, { value, priority = '' }] of Object.entries(initial)) {
      this.properties.set(name, { value, priority })
    }
  }

  getPropertyValue(name) { return this.properties.get(name)?.value ?? '' }
  getPropertyPriority(name) { return this.properties.get(name)?.priority ?? '' }
  setProperty(name, value, priority = '') { this.properties.set(name, { value: String(value), priority }) }
  removeProperty(name) { this.properties.delete(name) }
}

function equal(actual, expected, label) {
  if (actual !== expected) throw new Error(`${label}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`)
}

const root = { style: new FakeStyle({
  'font-size': { value: '17px', priority: 'important' },
  '--ui-font-size': { value: '12.5pt', priority: '' },
}) }

const restoreFirstMount = applyWorkspaceDocumentFontSize(root, '16.25')
equal(root.style.getPropertyValue('font-size'), '16.25pt', 'decimal preference root font size')
equal(root.style.getPropertyValue('--ui-font-size'), '16.25pt', 'decimal preference custom property')

// React StrictMode runs an effect setup, cleanup, then setup again on mount.
restoreFirstMount()
equal(root.style.getPropertyValue('font-size'), '17px', 'StrictMode cleanup restores prior root size')
equal(root.style.getPropertyPriority('font-size'), 'important', 'StrictMode cleanup restores prior priority')
equal(root.style.getPropertyValue('--ui-font-size'), '12.5pt', 'StrictMode cleanup restores prior custom property')
const restoreStrictModeSetup = applyWorkspaceDocumentFontSize(root, 18)
equal(root.style.getPropertyValue('font-size'), '18pt', 'StrictMode second setup reapplies preference')

// A preference update cleans up the old effect before applying its new value.
restoreStrictModeSetup()
const restoreUpdatedPreference = applyWorkspaceDocumentFontSize(root, 11.5)
equal(root.style.getPropertyValue('font-size'), '11.5pt', 'updated preference applies')
restoreUpdatedPreference()
equal(root.style.getPropertyValue('font-size'), '17px', 'unmount/logout cleanup restores previous value')
equal(root.style.getPropertyPriority('--ui-font-size'), '', 'cleanup restores empty custom-property priority')

const emptyRoot = { style: new FakeStyle() }
const restoreDefaultedPreference = applyWorkspaceDocumentFontSize(emptyRoot, 10)
equal(emptyRoot.style.getPropertyValue('font-size'), '14pt', 'invalid preference falls back to default')
restoreDefaultedPreference()
equal(emptyRoot.style.getPropertyValue('font-size'), '', 'cleanup removes previously absent root size')
equal(emptyRoot.style.getPropertyValue('--ui-font-size'), '', 'cleanup removes previously absent custom property')

const tokenRoot = postcss.parse(readFileSync(new URL('../src/tokens.css', import.meta.url), 'utf8'))
const rootTextTokens = new Map()
const shellTextTokens = []
tokenRoot.walkRules((rule) => rule.walkDecls(/^--text-/, (decl) => {
  if (rule.selector === ':root') rootTextTokens.set(decl.prop, decl.value)
  if (rule.selector.includes('.app-shell')) shellTextTokens.push(decl.prop)
}))
for (const [name, value] of Object.entries({
  '--text-body': '1rem',
  '--text-caption': '.875rem',
  '--text-subheading': '1.125rem',
  '--text-section': '1.25rem',
  '--text-title': '1.5rem',
})) equal(rootTextTokens.get(name), value, `${name} is supplied by the document root`)
equal(shellTextTokens.length, 0, 'text roles are not shadowed on .app-shell')

for (const cssPath of ['../src/styles.css', '../src/focused-shell.css']) {
  const rootCss = postcss.parse(readFileSync(new URL(cssPath, import.meta.url), 'utf8'))
  rootCss.walkDecls('font-size', (decl) => {
    if (!decl.important) return
    const selector = decl.parent.type === 'rule' ? decl.parent.selector : ''
    if (/\.light-theme\s+\.main-shell\s+:where\(\.catalog-page|\.light-theme\s+\.data-workspace\s+:where\(|\.app-shell\s+:where\(\.comparison-workspace|\.app-shell\s+\.recharts-text:where\(/.test(selector)) {
      throw new Error(`Broad P5 font-size override remains: ${selector}`)
    }
  })
  if (cssPath.endsWith('focused-shell.css')) rootCss.walkRules('.app-shell.light-theme .breadcrumb', (rule) => {
    if (rule.nodes?.some((node) => node.type === 'decl' && node.prop === 'font-size')) {
      throw new Error('Light-theme breadcrumb must inherit the root-relative shell role.')
    }
  })
}

console.log('Workspace document font size self-test passed.')
