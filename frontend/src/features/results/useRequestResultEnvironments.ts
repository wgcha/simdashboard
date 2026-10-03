import { useEffect, useState } from 'react'

import { FOLDER_AUTO_SYNC_INTERVAL_MS } from '../../shared/hooks/useFolderAutoSync'
import { simulationDashboardApi, type DashboardEnvironment } from '../../shared/api/simulationDashboard'

/**
 * Environments of the request's registered Cases (case-results-environment.md E1–E4).
 * `null` while the first answer for this request is pending. If the lookup fails, both
 * environments are returned so the page keeps the manual toggle instead of guessing.
 * A request without Cases is re-checked on the folder auto-sync period, so results
 * registered by auto-discovery appear without a reload.
 */
export function useRequestResultEnvironments(projectId: string, requestId: string, refreshToken = 0): DashboardEnvironment[] | null {
  const scope = projectId && requestId ? `${projectId}\u0000${requestId}` : ''
  const [state, setState] = useState<{ scope: string; environments: DashboardEnvironment[] } | null>(null)
  const [poll, setPoll] = useState(0)
  useEffect(() => {
    if (!scope) return
    const controller = new AbortController()
    simulationDashboardApi.resultEnvironments(projectId, requestId, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setState({ scope, environments: value.environments }) })
      .catch(() => { if (!controller.signal.aborted) setState({ scope, environments: ['USAGE', 'DISTRIBUTION'] }) })
    return () => controller.abort()
  }, [projectId, requestId, scope, refreshToken, poll])
  // A new Case registered by the folder sync re-checks the environments at once.
  const environments = state?.scope === scope ? state.environments : null
  const empty = environments?.length === 0
  useEffect(() => {
    if (!empty) return
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') setPoll((value) => value + 1) }, FOLDER_AUTO_SYNC_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [empty, scope])
  return scope ? environments : []
}
