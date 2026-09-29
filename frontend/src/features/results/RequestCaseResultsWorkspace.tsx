import { SimulationDashboard } from './SimulationDashboard'
import { MaterialsDashboard } from '../materials/MaterialsDashboard'

/** Request-scoped SPDM review. It intentionally has no legacy Load Case, Run, or layout dependency. */
export function RequestCaseResultsWorkspace({ projectId, requestId, canManageFolders = false, activeTab = 'case_results', refreshToken = 0 }: { projectId: string; requestId: string; canManageFolders?: boolean; activeTab?: 'case_results' | 'materials'; refreshToken?: number }) {
  return activeTab === 'materials'
    ? <MaterialsDashboard requestId={requestId} refreshToken={refreshToken} />
    : <SimulationDashboard projectId={projectId} requestId={requestId} canManageFolders={canManageFolders} />
}
