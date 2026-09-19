import { SimulationDashboard } from './SimulationDashboard'

/** Request-scoped SPDM review. It intentionally has no legacy Load Case, Run, or layout dependency. */
export function RequestCaseResultsWorkspace({ projectId, requestId, canManageFolders = false }: { projectId: string; requestId: string; canManageFolders?: boolean }) {
  return <SimulationDashboard projectId={projectId} requestId={requestId} canManageFolders={canManageFolders} />
}
