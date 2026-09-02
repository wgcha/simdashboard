import { DEFAULT_WORKSPACE_PREFERENCES, loadWorkspacePreferences, saveWorkspacePreference } from '../src/app/preferences/workspacePreferences.ts'

const store = new Map([['vd-workbench-theme', 'light'], ['vd-workbench-sidebar-collapsed', 'true'], ['vd-workbench-font-size-pt', '16']])
const storage = { getItem: (key) => store.get(key) ?? null, setItem: (key, value) => store.set(key, value) }
const migrated = loadWorkspacePreferences(storage)
if (migrated.theme !== 'light' || !migrated.sidebarCollapsed || migrated.uiFontSize !== 16) throw new Error('legacy values were not preserved')
if (store.get('simdashboard.workspace.theme.v1') !== 'light') throw new Error('legacy theme was not migrated')
saveWorkspacePreference('uiFontSize', 15, storage)
if (store.get('simdashboard.workspace.font-size-pt.v1') !== '15') throw new Error('v1 preference was not saved')

const invalidStore = new Map([['simdashboard.workspace.theme.v1', 'purple'], ['simdashboard.workspace.sidebar-collapsed.v1', 'yes'], ['simdashboard.workspace.font-size-pt.v1', '40']])
const invalid = loadWorkspacePreferences({ getItem: (key) => invalidStore.get(key) ?? null, setItem: (key, value) => invalidStore.set(key, value) })
if (JSON.stringify(invalid) !== JSON.stringify(DEFAULT_WORKSPACE_PREFERENCES)) throw new Error('invalid values did not use defaults')

const blocked = { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('blocked') } }
const fallback = loadWorkspacePreferences(blocked)
if (JSON.stringify(fallback) !== JSON.stringify(DEFAULT_WORKSPACE_PREFERENCES)) throw new Error('storage failure did not fall back safely')
console.log('Workspace preferences self-test passed.')
