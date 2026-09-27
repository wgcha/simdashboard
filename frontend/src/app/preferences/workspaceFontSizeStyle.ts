import type { CSSProperties } from 'react'
import { useLayoutEffect } from 'react'

import { DEFAULT_WORKSPACE_PREFERENCES, normalizeWorkspaceFontSize } from './workspacePreferences.ts'

const ROOT_FONT_PROPERTIES = ['font-size', '--ui-font-size'] as const

/** Apply the preference while retaining the exact inline root declarations for cleanup. */
export function applyWorkspaceDocumentFontSize(root: Pick<HTMLElement, 'style'>, fontSize: unknown): () => void {
  const normalized = normalizeWorkspaceFontSize(fontSize) ?? DEFAULT_WORKSPACE_PREFERENCES.uiFontSize
  const previous = ROOT_FONT_PROPERTIES.map((property) => ({
    property,
    value: root.style.getPropertyValue(property),
    priority: root.style.getPropertyPriority(property),
  }))

  root.style.setProperty('font-size', `${normalized}pt`)
  root.style.setProperty('--ui-font-size', `${normalized}pt`)

  let restored = false
  return () => {
    if (restored) return
    restored = true
    for (const { property, value, priority } of previous) {
      if (value) root.style.setProperty(property, value, priority)
      else root.style.removeProperty(property)
    }
  }
}

/** Apply the preference only while the authenticated/demo workspace is active. */
export function useWorkspaceDocumentFontSize(active: boolean, fontSize: unknown): void {
  useLayoutEffect(() => {
    if (!active) return
    return applyWorkspaceDocumentFontSize(document.documentElement, fontSize)
  }, [active, fontSize])
}

/** Shell inline value remains as a compatibility scope for existing token consumers. */
export function workspaceFontSizeStyle(fontSize: unknown): CSSProperties {
  const normalized = normalizeWorkspaceFontSize(fontSize) ?? DEFAULT_WORKSPACE_PREFERENCES.uiFontSize
  return { '--ui-font-size': `${normalized}pt` } as CSSProperties
}
