import { useEffect, useState } from 'react'

import { loadWorkspacePreferences, saveWorkspacePreference, type WorkspaceTheme } from './workspacePreferences'

/** Persistent shell-only preferences, deliberately separate from feature state. */
export function useWorkspacePreferences() {
  const [preferences] = useState(loadWorkspacePreferences)
  const [theme, setTheme] = useState<WorkspaceTheme>(preferences.theme)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(preferences.sidebarCollapsed)
  const [uiFontSize, setUiFontSize] = useState(preferences.uiFontSize)

  useEffect(() => {
    saveWorkspacePreference('sidebarCollapsed', sidebarCollapsed)
  }, [sidebarCollapsed])

  useEffect(() => {
    saveWorkspacePreference('uiFontSize', uiFontSize)
  }, [uiFontSize])

  useEffect(() => {
    saveWorkspacePreference('theme', theme)
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
  }, [theme])

  return { setSidebarCollapsed, setTheme, setUiFontSize, sidebarCollapsed, theme, uiFontSize }
}
