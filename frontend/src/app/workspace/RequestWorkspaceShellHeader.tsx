import type { ResultLayoutDetailTab } from '../../features/results/ResultLayoutDetailTab'
import { ResultVersionSelector } from '../../features/results/ResultVersionSelector'
import { RequestWorkspaceHeader, type RequestWorkspaceTab } from '../../features/request-workspace/RequestWorkspaceHeader'
import type { AnalysisRequest, LoadCase, Overview, Project, Workflow } from '../../types'
import { RequestNewResultsCheck } from '../../features/results/RequestNewResultsCheck'

type Props = {
  actions: {
    onLoadCaseChange: (id: string) => void
    onOpenData: () => void
    onOpenWorkbench: () => void
    onOpenCaseResults: () => void
    onOpenMaterials: () => void
    onProjectChange: (id: string) => void
    onRequestChange: (id: string) => void
    onReviewDomain: () => void
    onReviewContextOpen?: (intent: number) => Promise<void>
    onReviewSnapshot: () => void
    onReviewUnconfigured: () => void
    onViewOverview: () => void
    /** After [새 결과 확인] found new results: reload projects, requests, load cases and result screens. */
    onRefreshResults?: () => void
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
    canRefreshResults?: boolean
    caseResultsMode: boolean
    caseResultsMaterialsTab?: boolean
    resultsRefreshing?: boolean
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
export function RequestWorkspaceShellHeader({ actions, model, onRunChange }: Props) {
  const { activeTab, activeView, analysisRunChanging, analysisRunError, analysisRuns, analysisRunsLoading, canOpenData, canOpenWorkbench, caseResultsMode, contextChanging, loadCases, owner, projectId, projects, requestId, requests, selectedAnalysisRunId, selectedLoadCaseId, selectedWorkflow, status, title } = model
  const completedCount = selectedWorkflow ? `${selectedWorkflow.steps.filter((step) => step.status === 'COMPLETED').length} / ${selectedWorkflow.total_count ?? selectedWorkflow.steps.length} 작업 완료` : undefined
  // Snapshot and unconfigured review layouts also render one selected immutable Run.
  const showRunSelector = activeTab === 'review' && !caseResultsMode
  return <RequestWorkspaceHeader
    activeTab={activeTab}
    activeView={activeView}
    activeRunSelector={showRunSelector ? <ResultVersionSelector runs={analysisRuns} selectedRunId={selectedAnalysisRunId} loading={analysisRunsLoading || contextChanging} changing={analysisRunChanging} error={analysisRunError} onChange={onRunChange} /> : undefined}
    canOpenData={canOpenData}
    canOpenWorkbench={canOpenWorkbench}
    caseResultsMode={caseResultsMode}
    contextChanging={contextChanging}
    completedCount={completedCount}
    loadCases={loadCases}
    onLoadCaseChange={actions.onLoadCaseChange}
    newResultsCheck={<RequestNewResultsCheck projectId={projectId} requestId={requestId} disabled={contextChanging} onChanged={actions.onRefreshResults} />}
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
    // The legacy Run review (기존 Run 대시보드: load case / Run selectors, snapshot · domain · unconfigured
    // analysis pages) has no entry point any more (user decision 2026-10-29). Its routes and the
    // review* actions stay wired so direct URLs keep working; only Case 결과 is offered here.
    resultReviewTab={<div className="request-review-actions"><button type="button" className={caseResultsMode ? 'active' : ''} aria-current={caseResultsMode ? 'step' : undefined} disabled={!requestId || contextChanging} onClick={actions.onOpenCaseResults}>Case 결과</button></div>}
    selectedLoadCaseId={selectedLoadCaseId}
    status={status}
    title={title}
  />
}
