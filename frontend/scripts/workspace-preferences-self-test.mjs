import { DEFAULT_WORKSPACE_PREFERENCES, loadWorkspacePreferences, saveWorkspacePreference } from '../src/app/preferences/workspacePreferences.ts'
import { workspaceFontSizeStyle } from '../src/app/preferences/workspaceFontSizeStyle.ts'

const store = new Map([['vd-workbench-theme', 'dark'], ['simdashboard.workspace.theme.v1', 'dark'], ['vd-workbench-sidebar-collapsed', 'true'], ['vd-workbench-font-size-pt', '16']])
const storage = { getItem: (key) => store.get(key) ?? null, setItem: (key, value) => store.set(key, value) }
const migrated = loadWorkspacePreferences(storage)
if (migrated.theme !== 'light' || migrated.uiFontSize !== 16) throw new Error('auto-written legacy theme must give way to the light default; font size is preserved')
if ('sidebarCollapsed' in migrated) throw new Error('removed sidebar collapse preference was restored')
saveWorkspacePreference('theme', 'dark', storage)
if (store.get('simdashboard.workspace.theme.v2') !== 'dark' || loadWorkspacePreferences(storage).theme !== 'dark') throw new Error('an explicit theme choice was not kept')
saveWorkspacePreference('uiFontSize', 15, storage)
if (store.get('simdashboard.workspace.font-size-pt.v1') !== '15') throw new Error('v1 preference was not saved')

for (const [value, expected] of [[11, '11pt'], [18, '18pt'], [14.5, '14.5pt'], ['12.25', '12.25pt'], [Number.NaN, '14pt'], [Infinity, '14pt'], [10, '14pt'], [19, '14pt'], [undefined, '14pt'], [{}, '14pt'], [Symbol('font'), '14pt']]) {
  if (workspaceFontSizeStyle(value)['--ui-font-size'] !== expected) throw new Error(`font style did not normalize ${String(value)}`)
}

const invalidStore = new Map([['simdashboard.workspace.theme.v2', 'purple'], ['simdashboard.workspace.sidebar-collapsed.v1', 'yes'], ['simdashboard.workspace.font-size-pt.v1', '40']])
const invalid = loadWorkspacePreferences({ getItem: (key) => invalidStore.get(key) ?? null, setItem: (key, value) => invalidStore.set(key, value) })
if (JSON.stringify(invalid) !== JSON.stringify(DEFAULT_WORKSPACE_PREFERENCES)) throw new Error('invalid values did not use defaults')

const blocked = { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('blocked') } }
const fallback = loadWorkspacePreferences(blocked)
if (JSON.stringify(fallback) !== JSON.stringify(DEFAULT_WORKSPACE_PREFERENCES)) throw new Error('storage failure did not fall back safely')
console.log('Workspace preferences self-test passed.')
