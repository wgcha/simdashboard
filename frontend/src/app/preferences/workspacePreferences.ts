export type WorkspaceTheme = 'dark' | 'light'

export type WorkspacePreferences = {
  theme: WorkspaceTheme
  uiFontSize: number
}

type StorageLike = Pick<Storage, 'getItem' | 'setItem'>

const STORAGE_KEYS = {
  theme: 'simdashboard.workspace.theme.v1',
  uiFontSize: 'simdashboard.workspace.font-size-pt.v1',
} as const

const LEGACY_KEYS = {
  theme: 'vd-workbench-theme',
  uiFontSize: 'vd-workbench-font-size-pt',
} as const

export const DEFAULT_WORKSPACE_PREFERENCES: WorkspacePreferences = {
  theme: 'dark',
  uiFontSize: 14,
}

function getStorage(storage: StorageLike | undefined): StorageLike | undefined {
  if (storage) return storage
  try { return window.localStorage } catch { return undefined }
}

function read(storage: StorageLike | undefined, key: string): string | null {
  try { return storage?.getItem(key) ?? null } catch { return null }
}

function write(storage: StorageLike | undefined, key: string, value: string): void {
  try { storage?.setItem(key, value) } catch { /* private mode or a full quota must not break the workspace */ }
}

function theme(value: string | null): WorkspaceTheme | undefined {
  return value === 'dark' || value === 'light' ? value : undefined
}

function fontSize(value: string | null): number | undefined {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 11 && parsed <= 18 ? parsed : undefined
}

export function loadWorkspacePreferences(storage?: StorageLike): WorkspacePreferences {
  const target = getStorage(storage)
  const storedTheme = theme(read(target, STORAGE_KEYS.theme))
  const storedFontSize = fontSize(read(target, STORAGE_KEYS.uiFontSize))
  const preferences = {
    theme: storedTheme ?? theme(read(target, LEGACY_KEYS.theme)) ?? DEFAULT_WORKSPACE_PREFERENCES.theme,
    uiFontSize: storedFontSize ?? fontSize(read(target, LEGACY_KEYS.uiFontSize)) ?? DEFAULT_WORKSPACE_PREFERENCES.uiFontSize,
  }
  if (storedTheme === undefined) write(target, STORAGE_KEYS.theme, preferences.theme)
  if (storedFontSize === undefined) write(target, STORAGE_KEYS.uiFontSize, String(preferences.uiFontSize))
  return preferences
}

export function saveWorkspacePreference<K extends keyof WorkspacePreferences>(key: K, value: WorkspacePreferences[K], storage?: StorageLike): void {
  write(getStorage(storage), STORAGE_KEYS[key], String(value))
}
