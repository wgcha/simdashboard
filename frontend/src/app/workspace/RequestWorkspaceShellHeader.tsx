import { ResultLayoutDetailTab } from '../../features/results/ResultLayoutDetailTab'
import { ResultVersionSelector } from '../../features/results/ResultVersionSelector'
import { RequestWorkspaceHeader, type RequestWorkspaceTab } from '../../features/request-workspace/RequestWorkspaceHeader'
import type { AnalysisRequest, LoadCase, Overview, Project, Workflow } from '../../types'

type Props = {
  actions: {
    onLoadCaseChange: (id: string) => void
    onOpenData: () => void
    onOpenWorkbench: () => void
    onProjectChange: (id: string) => void
    onRequestChange: (id: string) => void
    onReviewDomain: () => void
    onReviewContextOpen?: (intent: number) => Promise<void>
    onReviewSnapshot: () => void
    onReviewUnconfigured: () => void
    onViewOverview: () => void
  }
  model: {
    activeDashboardId: string
    activeTab: RequestWorkspaceTab
    activeView: string
    analysisRuns: Parameters<typeof ResultVersionSelector>[0]['runs']
    analysisRunsLoading: boolean
    analysisRunChanging: boolean
    analysisRunError: string
    canOpenData: boolean
    canOpenWorkbench: boolean
    contextChanging: boolean
    loadCases: LoadCase[]
    overview: Overview | null
    owner: string
    projectId: string
    projects: Project[]
    requestContextLoading: boolean
    requestId: string
    requests: AnalysisRequest[]
    selectedAnalysisRunId: string
    selectedLoadCaseId: string
    selectedWorkflow?: Workflow
    status: string
    title: string
  }
  onBeginReview: () => number
  isCurrentReview: (intent: number) => boolean
  loadLayout: Parameters<typeof ResultLayoutDetailTab>[0]['loadLayout']
  onReviewError: (message: string) => void
  onRunChange: (runId: string) => void
}

/** Application-level assembly keeps request navigation controls identical on all four tabs. */
export function RequestWorkspaceShellHeader({ actions, model, onBeginReview, isCurrentReview, loadLayout, onReviewError, onRunChange }: Props) {
  const { activeDashboardId, activeTab, activeView, analysisRunChanging, analysisRunError, analysisRuns, analysisRunsLoading, canOpenData, canOpenWorkbench, contextChanging, loadCases, overview, owner, projectId, projects, requestContextLoading, requestId, requests, selectedAnalysisRunId, selectedLoadCaseId, selectedWorkflow, status, title } = model
  const completedCount = selectedWorkflow ? `${selectedWorkflow.steps.filter((step) => step.status === 'COMPLETED').length} / ${selectedWorkflow.total_count ?? selectedWorkflow.steps.length} 작업 완료` : undefined
  const showRunSelector = activeTab === 'review' && activeDashboardId !== 'request-result-layout' && !activeDashboardId.startsWith('pending-')
  return <RequestWorkspaceHeader
    activeTab={activeTab}
    activeView={activeView}
    activeRunSelector={showRunSelector ? <ResultVersionSelector runs={analysisRuns} selectedRunId={selectedAnalysisRunId} loading={analysisRunsLoading || contextChanging} changing={analysisRunChanging} error={analysisRunError} onChange={onRunChange} /> : undefined}
    canOpenData={canOpenData}
    canOpenWorkbench={canOpenWorkbench}
    contextChanging={contextChanging}
    completedCount={completedCount}
    loadCases={loadCases}
    onLoadCaseChange={actions.onLoadCaseChange}
    onOpenData={actions.onOpenData}
    onOpenWorkbench={actions.onOpenWorkbench}
    onProjectChange={actions.onProjectChange}
    onRequestChange={actions.onRequestChange}
    onViewOverview={actions.onViewOverview}
    owner={owner}
    projectId={projectId}
    projects={projects}
    requestId={requestId}
    requests={requests}
    resultReviewTab={<ResultLayoutDetailTab
      active={activeTab === 'review'}
      label="결과 검토"
      requestId={requestId}
      requestContextLoading={requestContextLoading || contextChanging}
      analysisLabel={selectedLoadCaseId ? overview?.load_case.analysis_type.replace('_', ' ') ?? '결과 대기' : '하중 경우 미지정'}
      loadLayout={loadLayout}
      onBeginOpen={onBeginReview}
      onBeforeOpen={actions.onReviewContextOpen}
      isCurrentOpen={isCurrentReview}
      onSnapshot={actions.onReviewSnapshot}
      onDomain={actions.onReviewDomain}
      onUnconfigured={actions.onReviewUnconfigured}
      onError={onReviewError}
    />}
    selectedLoadCaseId={selectedLoadCaseId}
    status={status}
    title={title}
  />
}
