import { api } from '../../api'
import type { MenuPolicy } from '../auth/access'
import type {
  AnalysisRequest,
  DashboardDefinition,
  DashboardPageSummary,
  LoadCase,
  Overview,
  PortfolioLayout,
  Project,
  QualityThreshold,
  Workflow,
  WorkflowDashboardLayout,
  WorkspaceLayout,
} from '../../types'
import { preferredPage } from '../analysis/pageSelection'

type BootstrapClient = {
  health: typeof api.health
  projects: typeof api.projects
  workflows: typeof api.workflows
  menuPolicy: typeof api.menuPolicy
  requests: typeof api.requests
  loadCases: typeof api.loadCases
  qualityThresholds: typeof api.qualityThresholds
  overview: typeof api.overview
  dashboardPages: typeof api.dashboardPages
  dashboard: typeof api.dashboard
  workspaceLayout: typeof api.workspaceLayout
}

type CommonWorkspace = {
  databaseBackend: 'duckdb' | 'postgresql'
  projects: Project[]
  workflows: Workflow[]
  menuPolicy: MenuPolicy | null
}

export type InitialWorkspace = CommonWorkspace & (
  | { kind: 'empty' }
  | { kind: 'setup'; selectedProjectId: string; requests: AnalysisRequest[]; selectedRequestId: string }
  | {
      kind: 'ready'
      selectedProjectId: string
      selectedRequestId: string
      selectedLoadCaseId: string
      requests: AnalysisRequest[]
      loadCases: LoadCase[]
      thresholds: QualityThreshold[]
      overview: Overview
      analysisPages: DashboardPageSummary[]
      dashboardId: string
      dashboard: DashboardDefinition | null
      portfolioLayout: WorkspaceLayout<PortfolioLayout>
      workflowLayout: WorkspaceLayout<WorkflowDashboardLayout>
    }
)

export async function loadInitialWorkspace(client: BootstrapClient = api): Promise<InitialWorkspace> {
  const [health, projects, workflows, menuPolicy] = await Promise.all([
    client.health(),
    client.projects(),
    client.workflows(),
    client.menuPolicy().catch(() => null),
  ])
  const common: CommonWorkspace = { databaseBackend: health.database_backend, projects, workflows, menuPolicy }
  if (!projects.length) return { ...common, kind: 'empty' }

  const preferredProjects = [...projects].sort((left, right) => Number(right.id === 'project-tv-001') - Number(left.id === 'project-tv-001'))
  const projectContexts = await Promise.all(preferredProjects.map(async (project) => ({
    project,
    requests: await client.requests(project.id),
  })))
  const requestContexts = await Promise.all(projectContexts.flatMap(({ project, requests }) => (
    [...requests]
      .sort((left, right) => Number(right.id === 'request-drop-001') - Number(left.id === 'request-drop-001'))
      .map(async (request) => ({ project, projectRequests: requests, request, loadCases: await client.loadCases(request.id) }))
  )))
  const analyzable = requestContexts.find((context) => context.loadCases.length > 0)
  if (!analyzable) {
    const fallback = projectContexts[0]
    return {
      ...common,
      kind: 'setup',
      selectedProjectId: fallback.project.id,
      requests: fallback.requests,
      selectedRequestId: fallback.requests[0]?.id ?? '',
    }
  }

  const { project, projectRequests, request, loadCases } = analyzable
  const loadCase = loadCases[0]
  const [thresholds, overview, analysisPages, portfolioLayout, workflowLayout] = await Promise.all([
    client.qualityThresholds(project.id),
    client.overview(loadCase.id),
    client.dashboardPages(loadCase.id),
    client.workspaceLayout<PortfolioLayout>(project.id, 'portfolio'),
    client.workspaceLayout<WorkflowDashboardLayout>(project.id, 'workflow'),
  ])
  const initialPage = preferredPage(analysisPages, overview)
  const dashboardId = initialPage?.id ?? (overview.analysis_verdicts.open_cell !== 'NO_DATA' ? 'dashboard-drop-default' : 'dashboard-chassis-default')
  const dashboard = await client.dashboard(dashboardId).catch(() => null)

  return {
    ...common,
    kind: 'ready',
    selectedProjectId: project.id,
    selectedRequestId: request.id,
    selectedLoadCaseId: loadCase.id,
    requests: projectRequests,
    loadCases,
    thresholds,
    overview,
    analysisPages,
    dashboardId,
    dashboard,
    portfolioLayout,
    workflowLayout,
  }
}
