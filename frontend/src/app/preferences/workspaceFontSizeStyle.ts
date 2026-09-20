import type { CSSProperties } from 'react'

import { DEFAULT_WORKSPACE_PREFERENCES, normalizeWorkspaceFontSize } from './workspacePreferences.ts'

/** The shell-only font preference remains a pt value until the P5 root migration. */
export function workspaceFontSizeStyle(fontSize: unknown): CSSProperties {
  const normalized = normalizeWorkspaceFontSize(fontSize) ?? DEFAULT_WORKSPACE_PREFERENCES.uiFontSize
  return { '--ui-font-size': `${normalized}pt` } as CSSProperties
}
