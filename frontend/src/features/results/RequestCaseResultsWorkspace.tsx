import { SimulationDashboard } from './SimulationDashboard'
import { MaterialsDashboard } from '../materials/MaterialsDashboard'

/** Request-scoped SPDM review. It intentionally has no legacy Load Case, Run, or layout dependency. */
export function RequestCaseResultsWorkspace({ projectId, requestId, canManageFolders = false, canRefreshSchema = false, activeTab = 'case_results', refreshToken = 0 }: { projectId: string; requestId: string; canManageFolders?: boolean; canRefreshSchema?: boolean; activeTab?: 'case_results' | 'materials'; refreshToken?: number }) {
  return activeTab === 'materials'
    ? <MaterialsDashboard projectId={projectId} requestId={requestId} canRefreshSchema={canRefreshSchema} refreshToken={refreshToken} />
    : <SimulationDashboard projectId={projectId} requestId={requestId} canManageFolders={canManageFolders} canRefreshSchema={canRefreshSchema} />
}
