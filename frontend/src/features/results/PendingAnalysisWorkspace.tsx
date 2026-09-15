import type { DashboardDefinition } from '../../types'
import type { RequestResultLayout } from '../../shared/api/resultLayouts'
import { GenericResultLayoutWorkspace } from './GenericResultLayoutWorkspace'

type Props = {
  projectId: string
  projectName: string
  requestId: string
  requestTitle: string
  selectedLoadCaseId?: string
  selectedRunId?: string
  canOpenData: boolean
  onOpenData: () => void
  onLoadLayout: (requestId: string, loadCaseId?: string, runId?: string) => Promise<RequestResultLayout>
  onSnapshotPageChange?: (page: DashboardDefinition) => void
}

export function PendingAnalysisWorkspace({ projectId, projectName, requestId, requestTitle, selectedLoadCaseId, selectedRunId, canOpenData, onOpenData, onLoadLayout, onSnapshotPageChange }: Props) {
  return <GenericResultLayoutWorkspace projectId={projectId} projectName={projectName} requestId={requestId} requestTitle={requestTitle} selectedLoadCaseId={selectedLoadCaseId} selectedRunId={selectedRunId} canOpenData={canOpenData} onOpenData={onOpenData} onLoadLayout={onLoadLayout} onSnapshotPageChange={onSnapshotPageChange} />
}
