import type { AnalysisRequest, DashboardDefinition, DashboardPageSummary, LoadCase, Overview, PortfolioLayout, QualityThreshold, WorkflowDashboardLayout } from '../../types'

type Options = {
  projectId: string
  thresholds: QualityThreshold[]
  portfolioLayout: PortfolioLayout
  portfolioLayoutVersion: number
  workflowLayout: WorkflowDashboardLayout
  workflowLayoutVersion: number
  setDashboardLoading: (value: boolean) => void
  setRequests: (value: AnalysisRequest[]) => void
  setLoadCases: (value: LoadCase[]) => void
  setThresholds: (value: QualityThreshold[]) => void
  setSelectedProjectId: (value: string) => void
  setSelectedRequestId: (value: string) => void
  setSelectedLoadCaseId: (value: string) => void
  setOverview: (value: Overview | null) => void
  setDashboard: (value: DashboardDefinition | null) => void
  setAnalysisPages: (value: DashboardPageSummary[]) => void
  setActiveDashboardId: (value: string) => void
  setActiveView: (value: 'workflow') => void
  setPortfolioLayout: (value: PortfolioLayout) => void
  setPortfolioLayoutVersion: (value: number) => void
  setWorkflowDashboardLayout: (value: WorkflowDashboardLayout) => void
  setWorkflowLayoutVersion: (value: number) => void
}

export function resetWorkspaceContext({ projectId, thresholds, portfolioLayout, portfolioLayoutVersion, workflowLayout, workflowLayoutVersion, setDashboardLoading, setRequests, setLoadCases, setThresholds, setSelectedProjectId, setSelectedRequestId, setSelectedLoadCaseId, setOverview, setDashboard, setAnalysisPages, setActiveDashboardId, setActiveView, setPortfolioLayout, setPortfolioLayoutVersion, setWorkflowDashboardLayout, setWorkflowLayoutVersion }: Options) {
  setDashboardLoading(false)
  setRequests([])
  setLoadCases([])
  setThresholds(thresholds)
  setSelectedProjectId(projectId)
  setSelectedRequestId('')
  setSelectedLoadCaseId('')
  setOverview(null)
  setDashboard(null)
  setAnalysisPages([])
  setActiveDashboardId('pending-open-cell')
  setActiveView('workflow')
  setPortfolioLayout(portfolioLayout)
  setPortfolioLayoutVersion(portfolioLayoutVersion)
  setWorkflowDashboardLayout(workflowLayout)
  setWorkflowLayoutVersion(workflowLayoutVersion)
}
