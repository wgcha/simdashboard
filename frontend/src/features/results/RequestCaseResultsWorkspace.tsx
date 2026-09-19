import { SimulationDashboard } from './SimulationDashboard'

/** Request-scoped SPDM review. It intentionally has no legacy Load Case, Run, or layout dependency. */
export function RequestCaseResultsWorkspace({ projectId, requestId }: { projectId: string; requestId: string }) {
  return <SimulationDashboard projectId={projectId} requestId={requestId} />
}
