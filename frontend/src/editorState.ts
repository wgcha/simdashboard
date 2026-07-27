import { useCallback, useMemo, useState } from 'react'

export type WorkspaceEditorMode =
  | 'portfolio-layout'
  | 'analysis-dashboard'
  | 'workflow-stages'
  | 'workflow-layout'
  | null

export function useWorkspaceEditorCoordinator() {
  const [mode, setMode] = useState<WorkspaceEditorMode>(null)
  const open = useCallback((next: Exclude<WorkspaceEditorMode, null>) => setMode(next), [])
  const close = useCallback(() => setMode(null), [])

  return useMemo(() => ({
    mode,
    open,
    close,
    isEditing: mode !== null,
    isPortfolioLayout: mode === 'portfolio-layout',
    isAnalysisDashboard: mode === 'analysis-dashboard',
    isWorkflowStages: mode === 'workflow-stages',
    isWorkflowLayout: mode === 'workflow-layout',
  }), [close, mode, open])
}

export function useReportLayoutEditorState() {
  const [isEditing, setIsEditing] = useState(false)
  const open = useCallback(() => setIsEditing(true), [])
  const close = useCallback(() => setIsEditing(false), [])
  const toggle = useCallback(() => setIsEditing((current) => !current), [])
  return useMemo(() => ({ isEditing, open, close, toggle }), [close, isEditing, open, toggle])
}
