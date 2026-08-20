import type { RequestResultLayout } from '../../shared/api/resultLayouts'
import { GenericResultLayoutWorkspace } from './GenericResultLayoutWorkspace'

type Props = {
  projectId: string
  projectName: string
  requestId: string
  requestTitle: string
  selectedLoadCaseId?: string
  canOpenData: boolean
  onOpenData: () => void
  onLoadLayout: (requestId: string, loadCaseId?: string) => Promise<RequestResultLayout>
}

export function PendingAnalysisWorkspace({ projectId, projectName, requestId, requestTitle, selectedLoadCaseId, canOpenData, onOpenData, onLoadLayout }: Props) {
  return <GenericResultLayoutWorkspace projectId={projectId} projectName={projectName} requestId={requestId} requestTitle={requestTitle} selectedLoadCaseId={selectedLoadCaseId} canOpenData={canOpenData} onOpenData={onOpenData} onLoadLayout={onLoadLayout} />
}
