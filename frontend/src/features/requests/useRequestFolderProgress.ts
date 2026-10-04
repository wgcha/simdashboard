import { useEffect, useState } from 'react'

import { requestFolderProgress, type RequestFolderProgress } from '../../shared/api/simulationDashboard'

export const FOLDER_PROGRESS_POLL_MS = 30_000

/**
 * Folder-derived progress of a folder-registered request (folder-request-progress.md §3–4).
 * Returns the progress only when `applicable`; `null` means the legacy work-item overview.
 * Re-checked every 30 s while the document is visible. A failed re-check keeps the last
 * answer for the same request; a failed first check keeps the legacy overview.
 * `pending` is true until the first answer for the current request arrives, so the
 * caller can hold back the legacy stepper instead of flashing it.
 */
export function useRequestFolderProgress(projectId: string, requestId: string): { progress: RequestFolderProgress | null; pending: boolean } {
  const scope = projectId && requestId ? `${projectId}\u0000${requestId}` : ''
  const [state, setState] = useState<{ scope: string; progress: RequestFolderProgress | null } | null>(null)
  const [poll, setPoll] = useState(0)
  useEffect(() => {
    if (!scope) return
    const controller = new AbortController()
    requestFolderProgress(projectId, requestId, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setState({ scope, progress: value.applicable ? value : null }) })
      .catch(() => { if (!controller.signal.aborted) setState((current) => current?.scope === scope ? current : { scope, progress: null }) })
    return () => controller.abort()
  }, [projectId, requestId, scope, poll])
  useEffect(() => {
    if (!scope) return
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') setPoll((value) => value + 1) }, FOLDER_PROGRESS_POLL_MS)
    return () => window.clearInterval(timer)
  }, [scope])
  const answered = state?.scope === scope
  return { progress: answered ? state.progress : null, pending: Boolean(scope) && !answered }
}
