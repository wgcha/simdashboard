import type { CSSProperties } from 'react'
import { useEffect, useLayoutEffect } from 'react'

import { DEFAULT_WORKSPACE_PREFERENCES, WORKSPACE_FONT_SIZE_STORAGE_KEY, normalizeWorkspaceFontSize } from './workspacePreferences.ts'

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

/**
 * Follow the stored font size when it changes outside this page (another tab, or a test that
 * writes localStorage and dispatches a ``storage`` event). The on-screen buttons are hidden
 * (2026-10-08), so this is the only runtime path besides the value loaded at start.
 */
export function useWorkspaceFontSizeStorageSync(onChange: (fontSize: number) => void): void {
  useEffect(() => {
    const listener = (event: StorageEvent) => {
      if (event.key !== WORKSPACE_FONT_SIZE_STORAGE_KEY) return
      const next = normalizeWorkspaceFontSize(event.newValue)
      if (next !== undefined) onChange(next)
    }
    window.addEventListener('storage', listener)
    return () => window.removeEventListener('storage', listener)
  }, [onChange])
}

/** Shell inline value remains as a compatibility scope for existing token consumers. */
export function workspaceFontSizeStyle(fontSize: unknown): CSSProperties {
  const normalized = normalizeWorkspaceFontSize(fontSize) ?? DEFAULT_WORKSPACE_PREFERENCES.uiFontSize
  return { '--ui-font-size': `${normalized}pt` } as CSSProperties
}
