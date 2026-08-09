import { useEffect, useMemo, useRef, useState, type ComponentType, type CSSProperties, type FormEvent } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  BookOpen,
  Check,
  ChevronDown,
  CircleDot,
  ClipboardPlus,
  Database,
  Download,
  FlaskConical,
  GripVertical,
  LayoutDashboard,
  LoaderCircle,
  Lock,
  LogOut,
  MessageSquareText,
  Minus,
  PanelLeftClose,
  PanelLeftOpen,
  Play,
  Plus,
  RotateCcw,
  Save,
  Search,
  Settings2,
  Sparkles,
  Sun,
  Moon,
  Upload,
  Trash2,
  X,
} from 'lucide-react'
import { WidthProvider, Responsive, type Layout, type Layouts } from 'react-grid-layout'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from './api'
import { clearSession, saveSession, storedUser, type AuthUser } from './auth'
import { useReportLayoutEditorState, useWorkspaceEditorCoordinator } from './editorState'
import { DEFAULT_PORTFOLIO_LAYOUT, DEFAULT_WORKFLOW_DASHBOARD_LAYOUT, loadPortfolioLayout, loadWorkflowDashboardLayout } from './features/layouts/layoutDefaults'
import { reportVariables, withReportVariables } from './features/reports/reportLayoutUtils'
import { LoginScreen } from './features/auth/LoginScreen'
import { RequestDemoRunSummary, SimulationWorkbench, WorkbenchTypeAdmin } from './features/workbench/SimulationWorkbench'
import { RequestIntakePage } from './features/workbench/RequestIntakePage'
import { DropVideoGrid } from './features/videos/DropVideoGrid'
import { PortfolioDashboard } from './PortfolioDashboard'
import type { ReportExportOptions } from './reportExport'
import type { AnalysisRequest, AnalysisRunSummary, AutomationTemplate, DashboardDefinition, DashboardPageSummary, DashboardSummary, DashboardVersion, DashboardWidget, FeatureExample, ImportSchema, LoadCase, Overview, PortfolioLayout, Project, QualityThreshold, ReportContentItem, ReportElementDefinition, ReportElementType, ReportLayout, ReportLayoutDefinition, ReportLayoutVersion, ReportSection, ReportSlideDefinition, ReportSlideKind, ReportSource, ReportTemplateAsset, ReviewItem, RunComparison, RunTrust, VariableDefinition, VariableDefinitionInput, WidgetCatalogItem, Workflow, WorkflowDashboardLayout, WorkflowStep } from './types'

const ResponsiveGridLayout = WidthProvider(Responsive) as unknown as ComponentType<any>
const SERIES_COLORS = ['#61d4ff', '#ff647d', '#70e0a8', '#ffbf57']
type ActiveView = 'open_cell' | 'chassis' | 'custom' | 'workflow' | 'compare'
type WorkspacePage = 'portfolio' | 'dashboard' | 'intake' | 'workbench' | 'workbench_admin' | 'data' | 'schemas' | 'variables' | 'templates' | 'examples' | 'help'

const SPECIAL_WIDGET_CATALOG: WidgetCatalogItem[] = [
  { type: 'summary', label: '하중 조건 요약', category: '요약', allowed_data_types: [], default_size: [6, 2] },
  { type: 'open_cell_map', label: 'Open Cell 맵', category: '전용 평가', allowed_data_types: [], default_size: [5, 4] },
  { type: 'open_cell_summary', label: 'Open Cell 판정 요약', category: '전용 평가', allowed_data_types: [], default_size: [12, 2] },
  { type: 'chassis_summary', label: 'Chassis 판정 요약', category: '전용 평가', allowed_data_types: [], default_size: [12, 2] },
  { type: 'chassis_diagram', label: 'Chassis 위치도', category: '전용 평가', allowed_data_types: [], default_size: [7, 5] },
  { type: 'chassis_bar', label: 'Chassis 비교 그래프', category: '전용 평가', allowed_data_types: [], default_size: [5, 5] },
  { type: 'chassis_table', label: 'Chassis 상세 표', category: '전용 평가', allowed_data_types: [], default_size: [8, 4] },
]
const ANALYSIS_WIDGET_TYPES = new Set<DashboardWidget['type']>(['summary','open_cell_map','open_cell_summary','kpi','verdict','gauge','edge_bar','time_series','scatter','note','result_table','contour','video','video_grid','chassis_summary','chassis_diagram','chassis_bar','chassis_table'])

function pageView(page: DashboardPageSummary): ActiveView {
  return page.page.analysis_key === 'open_cell' ? 'open_cell' : page.page.analysis_key === 'chassis_rear' ? 'chassis' : page.page.analysis_key === 'run_comparison' ? 'compare' : 'custom'
}

function visiblePages(pages: DashboardPageSummary[], overview: Overview) {
  return pages.filter((page) => page.page.analysis_key === 'custom'
    || (page.page.analysis_key === 'open_cell' && overview.analysis_verdicts.open_cell !== 'NO_DATA')
    || (page.page.analysis_key === 'chassis_rear' && overview.analysis_verdicts.chassis_rear !== 'NO_DATA')
    || (page.page.analysis_key === 'run_comparison' && Boolean(overview.run)))
}

function preferredPage(pages: DashboardPageSummary[], overview: Overview, preferred?: ActiveView) {
  const available = visiblePages(pages, overview)
  const key = preferred === 'chassis' ? 'chassis_rear' : preferred === 'open_cell' ? 'open_cell' : preferred === 'compare' ? 'run_comparison' : preferred === 'custom' ? 'custom' : null
  return (key ? available.find((page) => page.page.analysis_key === key) : undefined) ?? available[0] ?? pages[0]
}

function hasNumericValue(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

type RunComparisonReportContext = {
  loadCaseId: string
  baselineRunId: string
  targetRunId: string
  comparison: RunComparison
  trust: RunTrust
  reviews: ReviewItem[]
}

function App() {
  const [theme, setTheme] = useState<'dark' | 'light'>(() => window.localStorage.getItem('vd-workbench-theme') === 'light' ? 'light' : 'dark')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => window.localStorage.getItem('vd-workbench-sidebar-collapsed') === 'true')
  const [uiFontSize, setUiFontSize] = useState(() => {
    const stored = Number(window.localStorage.getItem('vd-workbench-font-size-pt'))
    return Number.isFinite(stored) && stored >= 11 && stored <= 18 ? stored : 14
  })
  const [authReady, setAuthReady] = useState(false)
  const [authRequired, setAuthRequired] = useState(false)
  const [authUser, setAuthUser] = useState<AuthUser | null>(storedUser)
  const [authError, setAuthError] = useState('')
  const [overview, setOverview] = useState<Overview | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  const [requests, setRequests] = useState<AnalysisRequest[]>([])
  const [loadCases, setLoadCases] = useState<LoadCase[]>([])
  const [thresholds, setThresholds] = useState<QualityThreshold[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState('')
  const [selectedRequestId, setSelectedRequestId] = useState('')
  const [selectedLoadCaseId, setSelectedLoadCaseId] = useState('')
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [dashboard, setDashboard] = useState<DashboardDefinition | null>(null)
  const [dashboardLoading, setDashboardLoading] = useState(false)
  const [analysisPages, setAnalysisPages] = useState<DashboardPageSummary[]>([])
  const [activeDashboardId, setActiveDashboardId] = useState('dashboard-drop-default')
  const [activeView, setActiveView] = useState<ActiveView>('workflow')
  const [workspacePage, setWorkspacePage] = useState<WorkspacePage>('portfolio')
  const workspaceEditor = useWorkspaceEditorCoordinator()
  const editMode = workspaceEditor.isEditing
  const [assistantOpen, setAssistantOpen] = useState(false)
  const [command, setCommand] = useState('')
  const [proposal, setProposal] = useState<Awaited<ReturnType<typeof api.previewCommand>> | null>(null)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [databaseBackend, setDatabaseBackend] = useState<'duckdb' | 'postgresql'>('duckdb')
  const [selectedEdges, setSelectedEdges] = useState<string[]>(['top', 'bottom', 'left', 'right'])
  const [widgetCatalog, setWidgetCatalog] = useState<WidgetCatalogItem[]>([])
  const [variables, setVariables] = useState<VariableDefinition[]>([])
  const [versions, setVersions] = useState<DashboardVersion[]>([])
  const [catalogVariable, setCatalogVariable] = useState('')
  const [savedDashboards, setSavedDashboards] = useState<DashboardSummary[]>([])
  const [selectedWidgetId, setSelectedWidgetId] = useState<string | null>(null)
  const [reportDraft, setReportDraft] = useState<ReportExportOptions | null>(null)
  const [reportOverview, setReportOverview] = useState<Overview | null>(null)
  const [reportPageId, setReportPageId] = useState('')
  const [reportRuns, setReportRuns] = useState<AnalysisRunSummary[]>([])
  const [reportRunId, setReportRunId] = useState('')
  const [reportContents, setReportContents] = useState<ReportContentItem[]>([])
  const [reportSource, setReportSource] = useState<ReportSource | null>(null)
  const [reportExporting, setReportExporting] = useState(false)
  const [reportError, setReportError] = useState('')
  const [reportLayouts, setReportLayouts] = useState<ReportLayout[]>([])
  const [reportLayoutDraft, setReportLayoutDraft] = useState<ReportLayoutDefinition | null>(null)
  const [reportLayoutVersions, setReportLayoutVersions] = useState<ReportLayoutVersion[]>([])
  const [reportTemplates, setReportTemplates] = useState<ReportTemplateAsset[]>([])
  const reportLayoutEditor = useReportLayoutEditorState()
  const [portfolioLayout, setPortfolioLayout] = useState<PortfolioLayout>(loadPortfolioLayout)
  const [workflowDashboardLayout, setWorkflowDashboardLayout] = useState<WorkflowDashboardLayout>(loadWorkflowDashboardLayout)
  const [portfolioLayoutVersion, setPortfolioLayoutVersion] = useState(1)
  const [workflowLayoutVersion, setWorkflowLayoutVersion] = useState(1)
  const [operationalRefreshToken, setOperationalRefreshToken] = useState(0)
  const [pageManagerOpen, setPageManagerOpen] = useState(false)
  const [comparisonReportContext, setComparisonReportContext] = useState<RunComparisonReportContext | null>(null)
  const workflowEditorMode = workspaceEditor.isWorkflowLayout ? 'layout' : workspaceEditor.isWorkflowStages ? 'stages' : null
  const portfolioLayoutBeforeEdit = useRef<PortfolioLayout | null>(null)
  const dashboardBeforeEdit = useRef<DashboardDefinition | null>(null)
  const workflowsBeforeEdit = useRef<Workflow[] | null>(null)
  const workflowLayoutBeforeEdit = useRef<WorkflowDashboardLayout | null>(null)
  const dashboardRequestSequence = useRef(0)
  const dashboardReady = Boolean(dashboard && !dashboardLoading && dashboard.id === activeDashboardId)

  useEffect(() => {
    if (!notice) return
    const timeout = window.setTimeout(() => setNotice((current) => current === notice ? '' : current), 4000)
    return () => window.clearTimeout(timeout)
  }, [notice])

  useEffect(() => {
    window.localStorage.setItem('vd-workbench-sidebar-collapsed', String(sidebarCollapsed))
  }, [sidebarCollapsed])

  useEffect(() => {
    window.localStorage.setItem('vd-workbench-font-size-pt', String(uiFontSize))
  }, [uiFontSize])

  useEffect(() => {
    window.localStorage.setItem('vd-workbench-theme', theme)
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
  }, [theme])

  useEffect(() => {
    const prepareAuthentication = async () => {
      try {
        const status = await api.authStatus()
        setAuthRequired(status.authentication_required)
        if (status.authentication_required && authUser) {
          const verified = await api.me()
          setAuthUser(verified)
        } else if (!status.authentication_required) {
          setAuthUser(null)
        }
      } catch (reason) {
        clearSession()
        setAuthUser(null)
        setAuthError(reason instanceof Error ? reason.message : '인증 상태를 확인하지 못했습니다.')
      } finally {
        setAuthReady(true)
      }
    }
    void prepareAuthentication()
  }, [])

  useEffect(() => {
    const expired = () => {
      setAuthUser(null)
      setAuthError('로그인 세션이 만료되었습니다. 다시 로그인하세요.')
    }
    window.addEventListener('analysis-auth-expired', expired)
    return () => window.removeEventListener('analysis-auth-expired', expired)
  }, [])

  useEffect(() => {
    if (workspacePage !== 'portfolio' && portfolioLayoutBeforeEdit.current) {
      setPortfolioLayout(portfolioLayoutBeforeEdit.current)
      portfolioLayoutBeforeEdit.current = null
    }
    if (workspacePage !== 'dashboard' && dashboardBeforeEdit.current) {
      setDashboard(dashboardBeforeEdit.current)
      dashboardBeforeEdit.current = null
    }
    if (workspacePage !== 'dashboard' && workflowsBeforeEdit.current) {
      setWorkflows(workflowsBeforeEdit.current)
      workflowsBeforeEdit.current = null
    }
    if (workspacePage !== 'dashboard' && workflowLayoutBeforeEdit.current) {
      setWorkflowDashboardLayout(workflowLayoutBeforeEdit.current)
      workflowLayoutBeforeEdit.current = null
    }
    workspaceEditor.close()
    setSelectedWidgetId(null)
    setAssistantOpen(false)
  }, [workspacePage])

  useEffect(() => {
    if (!authReady || (authRequired && !authUser)) return
    const bootstrap = async () => {
      setLoading(true)
      setError('')
      try {
        const [health, projectData, workflowData, storedPortfolioLayout, storedWorkflowLayout] = await Promise.all([
          api.health(),
          api.projects(),
          api.workflows(),
          api.workspaceLayout<PortfolioLayout>('portfolio'),
          api.workspaceLayout<WorkflowDashboardLayout>('workflow'),
        ])
        if (!projectData.length) throw new Error('등록된 프로젝트가 없습니다.')
        const preferredProjects = [...projectData].sort((left, right) => Number(right.id === 'project-tv-001') - Number(left.id === 'project-tv-001'))
        let project: Project | undefined
        let requestData: AnalysisRequest[] = []
        let request: AnalysisRequest | undefined
        let caseData: LoadCase[] = []
        for (const candidateProject of preferredProjects) {
          const candidateRequests = await api.requests(candidateProject.id)
          const preferredRequests = [...candidateRequests].sort((left, right) => Number(right.id === 'request-drop-001') - Number(left.id === 'request-drop-001'))
          for (const candidateRequest of preferredRequests) {
            const candidateCases = await api.loadCases(candidateRequest.id)
            if (!candidateCases.length) continue
            project = candidateProject
            requestData = candidateRequests
            request = candidateRequest
            caseData = candidateCases
            break
          }
          if (project) break
        }
        if (!project || !request) throw new Error('대시보드에서 열 수 있는 해석 의뢰가 없습니다.')
        const loadCase = caseData[0]
        const [thresholdData, overviewData, pageData] = await Promise.all([api.qualityThresholds(project.id), api.overview(loadCase.id), api.dashboardPages(loadCase.id)])
        const initialPage = preferredPage(pageData, overviewData)
        const dashboardId = initialPage?.id ?? (overviewData.analysis_verdicts.open_cell !== 'NO_DATA' ? 'dashboard-drop-default' : 'dashboard-chassis-default')
        const dashboardData = await api.dashboard(dashboardId)
        setProjects(projectData)
        setDatabaseBackend(health.database_backend)
        setRequests(requestData)
        setLoadCases(caseData)
        setThresholds(thresholdData)
        setSelectedProjectId(project.id)
        setSelectedRequestId(request.id)
        setSelectedLoadCaseId(loadCase.id)
        setOverview(overviewData)
        setWorkflows(workflowData)
        setDashboard(dashboardData)
        setAnalysisPages(pageData)
        setActiveDashboardId(dashboardId)
        setPortfolioLayout(storedPortfolioLayout.definition)
        setPortfolioLayoutVersion(storedPortfolioLayout.version)
        setWorkflowDashboardLayout(storedWorkflowLayout.definition)
        setWorkflowLayoutVersion(storedWorkflowLayout.version)
        setActiveView('workflow')
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '초기 데이터를 불러오지 못했습니다.')
      } finally {
        setLoading(false)
      }
    }
    bootstrap()
  }, [authReady, authRequired, authUser?.id])

  const handleLogin = async (username: string, password: string) => {
    setAuthError('')
    try {
      const result = await api.login(username, password)
      saveSession(result.access_token, result.user)
      setAuthUser(result.user)
    } catch (reason) {
      setAuthError(reason instanceof Error ? reason.message : '로그인하지 못했습니다.')
      throw reason
    }
  }

  const logout = async () => {
    try { await api.logout() } catch { /* clear the local session even if the server is unavailable */ }
    clearSession()
    setAuthUser(null)
    setOverview(null)
    setLoading(true)
  }

  useEffect(() => {
    if (!activeDashboardId || !authReady || (authRequired && !authUser)) return
    const requestSequence = ++dashboardRequestSequence.current
    setDashboardLoading(true)
    api.dashboard(activeDashboardId)
      .then((definition) => {
        if (requestSequence === dashboardRequestSequence.current && definition.id === activeDashboardId) setDashboard(definition)
      })
      .catch((reason) => {
        if (requestSequence === dashboardRequestSequence.current) setError(reason instanceof Error ? reason.message : '분석 레이아웃을 불러오지 못했습니다.')
      })
      .finally(() => {
        if (requestSequence === dashboardRequestSequence.current) setDashboardLoading(false)
      })
    return () => {
      if (requestSequence === dashboardRequestSequence.current) dashboardRequestSequence.current += 1
    }
  }, [activeDashboardId, authReady, authRequired, authUser?.id])

  useEffect(() => {
    setComparisonReportContext(null)
  }, [selectedLoadCaseId, activeDashboardId])

  useEffect(() => {
    if ((!editMode && workspacePage !== 'variables') || !selectedLoadCaseId) return
    api.variables(selectedLoadCaseId).then((items) => { setVariables(items); setCatalogVariable((current) => current || items[0]?.id || '') }).catch(() => setVariables([]))
  }, [editMode, workspacePage, selectedLoadCaseId])

  useEffect(() => {
    if (!assistantOpen || !dashboard || !selectedLoadCaseId) return
    Promise.all([api.widgetCatalog(), api.variables(selectedLoadCaseId), api.dashboardVersions(dashboard.id), api.dashboards(selectedProjectId)])
      .then(([catalog, variableData, versionData, dashboardData]) => {
        const merged = [...catalog, ...SPECIAL_WIDGET_CATALOG.filter((special) => !catalog.some((item) => item.type === special.type))]
        setWidgetCatalog(merged); setVariables(variableData); setVersions(versionData); setSavedDashboards(dashboardData.filter((item) => !analysisPages.some((page) => page.id === item.id))); setCatalogVariable(variableData[0]?.id ?? '')
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : '편집 카탈로그를 불러오지 못했습니다.'))
  }, [assistantOpen, dashboard?.id, selectedLoadCaseId])

  const loadContext = async (projectId: string, requestId?: string, preferredView?: ActiveView) => {
    setError('')
    const requestData = await api.requests(projectId)
    const request = requestData.find((item) => item.id === requestId) ?? requestData[0]
    if (!request) throw new Error('선택한 프로젝트에 의뢰가 없습니다.')
    const [caseData, thresholdData] = await Promise.all([api.loadCases(request.id), api.qualityThresholds(projectId)])
    const loadCase = caseData[0]
    if (!loadCase) throw new Error('선택한 의뢰에 하중 경우가 없습니다.')
    const [overviewData, pageData] = await Promise.all([api.overview(loadCase.id), api.dashboardPages(loadCase.id)])
    const selectedPage = preferredPage(pageData, overviewData, preferredView)
    const dashboardData = selectedPage ? await api.dashboard(selectedPage.id) : null
    setRequests(requestData)
    setLoadCases(caseData)
    setThresholds(thresholdData)
    setSelectedProjectId(projectId)
    setSelectedRequestId(request.id)
    setSelectedLoadCaseId(loadCase.id)
    setOverview(overviewData)
    setAnalysisPages(pageData)
    if (selectedPage) { setActiveDashboardId(selectedPage.id); setActiveView(pageView(selectedPage)); if (dashboardData) setDashboard(dashboardData) }
  }

  const loadMonitoringContext = async (projectId: string, requestId?: string) => {
    setError('')
    const requestData = await api.requests(projectId)
    const request = requestData.find((item) => item.id === requestId) ?? requestData[0]
    if (!request) throw new Error('선택한 프로젝트에 의뢰가 없습니다.')
    const [caseData, thresholdData] = await Promise.all([api.loadCases(request.id), api.qualityThresholds(projectId)])
    setRequests(requestData)
    setLoadCases(caseData)
    setThresholds(thresholdData)
    setSelectedProjectId(projectId)
    setSelectedRequestId(request.id)
    setSelectedLoadCaseId(caseData[0]?.id ?? '')
    if (caseData[0]) {
      const [overviewData, pageData] = await Promise.all([api.overview(caseData[0].id), api.dashboardPages(caseData[0].id)])
      const selectedPage = preferredPage(pageData, overviewData)
      setOverview(overviewData)
      setAnalysisPages(pageData)
      if (selectedPage) { setActiveDashboardId(selectedPage.id); setDashboard(await api.dashboard(selectedPage.id)) }
    }
    setActiveView('workflow')
  }

  const handleProjectChange = async (projectId: string) => {
    if (editMode) cancelEditing()
    try { await (activeView === 'workflow' ? loadMonitoringContext(projectId) : loadContext(projectId)) } catch (reason) { setError(reason instanceof Error ? reason.message : '프로젝트를 변경하지 못했습니다.') }
  }

  const handleRequestChange = async (requestId: string) => {
    if (editMode) cancelEditing()
    try { await (activeView === 'workflow' ? loadMonitoringContext(selectedProjectId, requestId) : loadContext(selectedProjectId, requestId)) } catch (reason) { setError(reason instanceof Error ? reason.message : '의뢰를 변경하지 못했습니다.') }
  }

  const handleLoadCaseChange = async (loadCaseId: string) => {
    if (editMode) cancelEditing()
    try {
      const [overviewData, pageData] = await Promise.all([api.overview(loadCaseId), api.dashboardPages(loadCaseId)])
      const selectedPage = preferredPage(pageData, overviewData)
      setSelectedLoadCaseId(loadCaseId)
      setOverview(overviewData)
      setAnalysisPages(pageData)
      if (selectedPage) { setActiveDashboardId(selectedPage.id); setActiveView(pageView(selectedPage)); setDashboard(await api.dashboard(selectedPage.id)) }
    } catch (reason) { setError(reason instanceof Error ? reason.message : '하중 경우를 변경하지 못했습니다.') }
  }

  const handleLayoutChange = (_current: Layout[], layouts: Layouts) => {
    if (!editMode) return
    const positions = new Map((layouts.lg ?? []).map((item) => [item.i, item]))
    setDashboard((current) => {
      if (!current) return current
      let changed = false
      const widgets = current.widgets.map((widget) => {
        const position = positions.get(widget.id)
        if (!position || (widget.x === position.x && widget.y === position.y && widget.w === position.w && widget.h === position.h)) return widget
        changed = true
        return { ...widget, x: position.x, y: position.y, w: position.w, h: position.h }
      })
      return changed ? { ...current, widgets } : current
    })
  }

  const save = async () => {
    if (workspacePage === 'portfolio') {
      try {
        const stored = await api.saveWorkspaceLayout('portfolio', portfolioLayout)
        setPortfolioLayout(stored.definition)
        setPortfolioLayoutVersion(stored.version)
        portfolioLayoutBeforeEdit.current = null
        workspaceEditor.close()
        setNotice(`운영 대시보드 설정 v${stored.version}을 저장했습니다.`)
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '운영 대시보드 설정을 저장하지 못했습니다.')
      }
      return
    }
    if (activeView === 'workflow') {
      if (workflowEditorMode === 'layout') {
        try {
          const stored = await api.saveWorkspaceLayout('workflow', workflowDashboardLayout)
          setWorkflowDashboardLayout(stored.definition)
          setWorkflowLayoutVersion(stored.version)
          workflowLayoutBeforeEdit.current = null
          workspaceEditor.close()
          setNotice(`진행 현황 대시보드 레이아웃 v${stored.version}을 저장했습니다.`)
        } catch (reason) {
          setError(reason instanceof Error ? reason.message : '진행 현황 레이아웃을 저장하지 못했습니다.')
        }
        return
      }
      const original = workflowsBeforeEdit.current
      if (!original) { workspaceEditor.close(); return }
      const signature = (steps: WorkflowStep[]) => JSON.stringify(steps.map((step) => ({ id: step.id, name: step.name, status: step.status, owner: step.owner, progress: step.progress, is_optional: step.is_optional, note: step.note ?? '' })))
      const originalByRequest = new Map(original.map((workflow) => [workflow.request.id, workflow]))
      const changed = workflows.filter((workflow) => signature(workflow.steps) !== signature(originalByRequest.get(workflow.request.id)?.steps ?? []))
      const invalid = changed.flatMap((workflow) => workflow.steps).find((step) => step.name.trim().length < 2 || !step.owner.trim() || step.progress < 0 || step.progress > 100)
      if (invalid) { setError('단계 이름은 두 글자 이상, 담당자는 필수이며 진행률은 0~100이어야 합니다.'); return }
      try {
        await Promise.all(changed.map((workflow) => api.replaceWorkflowSteps(workflow.request.id, workflow.steps.map((step) => ({ id: step.id.startsWith('draft-step-') ? null : step.id, name: step.name.trim(), status: step.status, owner: step.owner.trim(), progress: step.progress, is_optional: step.is_optional, note: step.note ?? '' })))))
        setWorkflows(await api.workflows())
        workflowsBeforeEdit.current = null
        workspaceEditor.close()
        setNotice(changed.length ? `의뢰 ${changed.length}건의 진행 단계를 저장했습니다.` : '변경된 작업 단계가 없습니다.')
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '작업 단계를 저장하지 못했습니다.')
      }
      return
    }
    if (!dashboard || !dashboardReady) { setError('현재 상세 분석 페이지를 불러온 뒤 다시 저장해 주세요.'); return }
    try {
      const result = await api.saveDashboard(dashboard)
      setDashboard({ ...dashboard, version: result.version, updated_at: result.updated_at })
      setVersions(await api.dashboardVersions(dashboard.id))
      setAnalysisPages((items) => items.map((item) => item.id === dashboard.id ? { ...item, name: dashboard.name, description: dashboard.description, version: result.version, updated_at: result.updated_at } : item))
      dashboardBeforeEdit.current = null
      setNotice(`레이아웃 v${result.version} 저장 완료`)
      workspaceEditor.close()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '저장하지 못했습니다.')
    }
  }

  const beginEditing = () => {
    if (workspacePage === 'dashboard' && activeView !== 'workflow' && !dashboardReady) { setError('현재 상세 분석 페이지를 불러온 뒤 편집해 주세요.'); return }
    if (workspacePage === 'portfolio') portfolioLayoutBeforeEdit.current = { ...portfolioLayout, chartOrder: [...portfolioLayout.chartOrder] }
    if (workspacePage === 'dashboard' && activeView !== 'workflow' && dashboard) dashboardBeforeEdit.current = structuredClone(dashboard)
    setSelectedWidgetId(null)
    workspaceEditor.open(workspacePage === 'portfolio' ? 'portfolio-layout' : 'analysis-dashboard')
  }

  const beginWorkflowStageEditing = () => {
    workflowsBeforeEdit.current = structuredClone(workflows)
    workspaceEditor.open('workflow-stages')
  }

  const beginWorkflowLayoutEditing = () => {
    workflowLayoutBeforeEdit.current = structuredClone(workflowDashboardLayout)
    workspaceEditor.open('workflow-layout')
  }

  const cancelEditing = () => {
    if (workspacePage === 'portfolio' && portfolioLayoutBeforeEdit.current) {
      setPortfolioLayout(portfolioLayoutBeforeEdit.current)
      portfolioLayoutBeforeEdit.current = null
    }
    if (workspacePage === 'dashboard' && dashboardBeforeEdit.current) {
      setDashboard(dashboardBeforeEdit.current)
      dashboardBeforeEdit.current = null
    }
    if (workspacePage === 'dashboard' && workflowsBeforeEdit.current) {
      setWorkflows(workflowsBeforeEdit.current)
      workflowsBeforeEdit.current = null
    }
    if (workspacePage === 'dashboard' && workflowLayoutBeforeEdit.current) {
      setWorkflowDashboardLayout(workflowLayoutBeforeEdit.current)
      workflowLayoutBeforeEdit.current = null
    }
    setSelectedWidgetId(null)
    setAssistantOpen(false)
    workspaceEditor.close()
  }

  const resetPortfolioLayout = () => {
    setPortfolioLayout({ ...DEFAULT_PORTFOLIO_LAYOUT, chartOrder: [...DEFAULT_PORTFOLIO_LAYOUT.chartOrder] })
    setNotice('기본 배치를 미리 적용했습니다. 저장하거나 취소할 수 있습니다.')
  }

  const resetWorkflowDashboardLayout = () => {
    setWorkflowDashboardLayout({ ...DEFAULT_WORKFLOW_DASHBOARD_LAYOUT, items: [] })
    setNotice('진행 현황 기본 레이아웃을 미리 적용했습니다. 저장하거나 취소할 수 있습니다.')
  }

  const openDashboardWorkspace = () => {
    if (editMode) cancelEditing()
    setWorkspacePage('dashboard')
    setActiveView('workflow')
  }

  const switchDashboardView = (view: ActiveView) => {
    if (editMode) cancelEditing()
    if (view === 'open_cell' || view === 'chassis' || view === 'custom' || view === 'compare') {
      const key = view === 'open_cell' ? 'open_cell' : view === 'chassis' ? 'chassis_rear' : view === 'compare' ? 'run_comparison' : 'custom'
      const page = visiblePages(analysisPages, overview!).find((item) => item.page.analysis_key === key)
      if (page && page.id !== activeDashboardId) {
        dashboardRequestSequence.current += 1
        setDashboardLoading(true)
        setComparisonReportContext(null)
        setActiveDashboardId(page.id)
      }
    }
    setActiveView(view)
  }

  const switchAnalysisPage = (page: DashboardPageSummary) => {
    if (editMode) cancelEditing()
    setComparisonReportContext(null)
    if (page.id !== activeDashboardId) {
      dashboardRequestSequence.current += 1
      setDashboardLoading(true)
      setActiveDashboardId(page.id)
    }
    setActiveView(pageView(page))
  }

  const previewCommand = async () => {
    if (!command.trim()) return
    if (!dashboardReady) { setError('현재 상세 분석 페이지를 불러온 뒤 자연어 개선을 실행해 주세요.'); return }
    try {
      setProposal(await api.previewCommand(command))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '요청을 해석하지 못했습니다.')
    }
  }

  const applyProposal = () => {
    if (!dashboard || !dashboardReady || !proposal?.proposal) return
    if (!dashboardBeforeEdit.current) dashboardBeforeEdit.current = structuredClone(dashboard)
    if (proposal.proposal.action === 'add_widget') setDashboard({ ...dashboard, widgets: [...dashboard.widgets, proposal.proposal.widget] })
    else {
      const updates = proposal.proposal.updates
      setDashboard({ ...dashboard, widgets: dashboard.widgets.map((widget) => { const update = updates.find((item) => item.widget_type === widget.type); return update ? { ...widget, ...update, settings: { ...widget.settings, ...update.settings } } : widget }) })
    }
    setProposal(null)
    setCommand('')
    setAssistantOpen(false)
    workspaceEditor.open('analysis-dashboard')
    setNotice('변경안을 적용했습니다. 저장하면 새 버전이 생성됩니다.')
  }

  const removeWidget = (id: string) => {
    if (!dashboard) return
    setDashboard({ ...dashboard, widgets: dashboard.widgets.filter((item) => item.id !== id) })
  }

  const updateWidget = (id: string, patch: Partial<DashboardWidget>) => {
    if (!dashboard) return
    setDashboard({ ...dashboard, widgets: dashboard.widgets.map((item) => item.id === id ? { ...item, ...patch, settings: { ...item.settings, ...patch.settings } } : item) })
  }

  const addCatalogWidget = (item: WidgetCatalogItem) => {
    if (!dashboard) return
    if (!dashboardBeforeEdit.current) dashboardBeforeEdit.current = structuredClone(dashboard)
    const variable = item.allowed_data_types.length === 0 || item.type === 'video_grid' ? undefined : variables.find((entry) => entry.id === catalogVariable)
    if (variable && !item.allowed_data_types.includes(variable.data_type)) { setNotice(`${variable.display_name}에는 ${item.label}을 사용할 수 없습니다.`); return }
    const [w, h] = item.default_size
    setDashboard({ ...dashboard, widgets: [...dashboard.widgets, { id: `${item.type}-${Date.now()}`, type: item.type, title: variable ? `${variable.display_name} · ${item.label}` : item.label, x: 0, y: 30, w, h, settings: { variableId: variable?.id } }] })
    workspaceEditor.open('analysis-dashboard'); setNotice('위젯을 추가했습니다. 위치를 조정한 뒤 저장하세요.')
  }

  const cloneLayout = async () => {
    if (!dashboard) return
    const result = await api.cloneDashboard(dashboard.id, `${dashboard.name} 복제본`, dashboard.description)
    setNotice(`복제 레이아웃 ${result.id}을 저장했습니다.`)
  }

  const restorePrevious = async () => {
    if (!dashboard) return
    const previous = versions.find((item) => item.version < (dashboard.version ?? 1) && item.is_valid)
    if (!previous) { setNotice('복구할 이전 정상 버전이 없습니다.'); return }
    await api.restoreDashboard(dashboard.id, previous.version)
    setDashboard(await api.dashboard(dashboard.id)); setVersions(await api.dashboardVersions(dashboard.id)); setNotice(`v${previous.version} 내용을 새 버전으로 복구했습니다.`)
  }

  const loadDashboardVersionDraft = async (version: number) => {
    if (!dashboard) return
    try {
      const stored = await api.dashboardVersion(dashboard.id, version)
      if (!dashboardBeforeEdit.current) dashboardBeforeEdit.current = structuredClone(dashboard)
      const draft = dashboard.page ? {
        ...stored.definition,
        id: dashboard.id,
        name: dashboard.name,
        description: dashboard.description,
        page: dashboard.page,
      } : { ...stored.definition, id: dashboard.id }
      setDashboard({ ...draft, version: dashboard.version, updated_at: dashboard.updated_at })
      setSelectedWidgetId(null)
      workspaceEditor.open('analysis-dashboard')
      setNotice(`v${version} 내용을 현재 편집 초안으로 불러왔습니다. 저장하면 새 버전이 생성됩니다.`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '과거 버전을 불러오지 못했습니다.') }
  }

  const removeDashboardVersion = async (version: number) => {
    if (!dashboard) return
    try {
      await api.deleteDashboardVersion(dashboard.id, version)
      setVersions(await api.dashboardVersions(dashboard.id))
      setNotice(`v${version} 과거 이력을 삭제했습니다.`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '과거 버전을 삭제하지 못했습니다.') }
  }

  const loadSavedDashboard = async (id: string) => { setDashboard(await api.dashboard(id)); setNotice('저장된 레이아웃을 불러왔습니다.') }

  const updateWorkflowStepDraft = (stepId: string, patch: Partial<WorkflowStep>) => {
    setWorkflows((items) => items.map((workflow) => {
      if (!workflow.steps.some((step) => step.id === stepId)) return workflow
      const steps = workflow.steps.map((step) => step.id === stepId ? { ...step, ...patch } : step)
      return { ...workflow, steps, progress: Math.round(steps.reduce((sum, step) => sum + step.progress, 0) / Math.max(steps.length, 1)) }
    }))
  }

  const addWorkflowStepDraft = (requestId: string) => {
    setWorkflows((items) => items.map((workflow) => {
      if (workflow.request.id !== requestId) return workflow
      const sequence = workflow.steps.length + 1
      const steps = [...workflow.steps, { id: `draft-step-${crypto.randomUUID()}`, sequence_no: sequence, name: '새 진행 단계', status: 'WAITING' as const, owner: workflow.request.owner || '미지정', progress: 0, planned_end: workflow.request.due_at, is_optional: false, note: '' }]
      return { ...workflow, steps, progress: Math.round(steps.reduce((sum, step) => sum + step.progress, 0) / steps.length) }
    }))
  }

  const deleteWorkflowStepDraft = (requestId: string, stepId: string) => {
    setWorkflows((items) => items.map((workflow) => {
      if (workflow.request.id !== requestId) return workflow
      if (workflow.steps.length <= 1) { setNotice('의뢰에는 최소 한 개의 진행 단계가 필요합니다.'); return workflow }
      const steps = workflow.steps.filter((step) => step.id !== stepId).map((step, index) => ({ ...step, sequence_no: index + 1 }))
      return { ...workflow, steps, progress: Math.round(steps.reduce((sum, step) => sum + step.progress, 0) / steps.length) }
    }))
  }

  const moveWorkflowStepDraft = (requestId: string, stepId: string, offset: -1 | 1) => {
    setWorkflows((items) => items.map((workflow) => {
      if (workflow.request.id !== requestId) return workflow
      const index = workflow.steps.findIndex((step) => step.id === stepId)
      const target = index + offset
      if (index < 0 || target < 0 || target >= workflow.steps.length) return workflow
      const steps = [...workflow.steps]
      ;[steps[index], steps[target]] = [steps[target], steps[index]]
      return { ...workflow, steps: steps.map((step, stepIndex) => ({ ...step, sequence_no: stepIndex + 1 })) }
    }))
  }

  const saveChassisThreshold = async (value: number) => {
    const criterion = thresholds.find((item) => item.criterion_key === 'chassis_rear_permanent_deformation_mm')
    if (!criterion || !overview) return
    try {
      const updated = await api.updateQualityThreshold(overview.load_case.project_id, criterion.criterion_key, value)
      setThresholds((items) => items.map((item) => item.criterion_key === updated.criterion_key ? updated : item))
      setOverview(await api.overview(overview.load_case.id))
      setNotice(`관리자 판정 기준을 ${value.toFixed(1)} mm로 저장했습니다.`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '판정 기준을 저장하지 못했습니다.') }
  }

  const saveOpenCellThreshold = async (value: number) => {
    const criterion = thresholds.find((item) => item.criterion_key === 'open_cell_stress_mpa')
    if (!criterion || !overview) return
    try {
      const updated = await api.updateQualityThreshold(overview.load_case.project_id, criterion.criterion_key, value)
      setThresholds((items) => items.map((item) => item.criterion_key === updated.criterion_key ? updated : item))
      setOverview(await api.overview(overview.load_case.id))
      setNotice(`Open Cell 응력 관리 기준을 ${value.toFixed(1)} MPa로 저장했습니다.`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Open Cell 응력 기준을 저장하지 못했습니다.') }
  }

  const reportDataForPage = async (pageId: string, sourceOverview?: Overview | null) => {
    const dataOverview = sourceOverview ?? overview
    if (!overview) throw new Error('선택한 하중 경우의 결과가 없습니다.')
    const page = analysisPages.find((item) => item.id === pageId)
    if (!page) throw new Error('내보낼 분석 페이지를 찾을 수 없습니다.')
    const definition = dashboard?.id === pageId ? dashboard : await api.dashboard(pageId)
    const { filterOverviewForReport, filterOverviewForVariables } = await import('./reportExport')
    if (page.page.analysis_key === 'open_cell') {
      const scopedOverview = filterOverviewForReport(dataOverview!, 'open_cell')
      return { scopedOverview, contentOverview: scopedOverview, definition, page }
    }
    if (page.page.analysis_key === 'chassis_rear') {
      const scopedOverview = filterOverviewForReport(dataOverview!, 'chassis')
      return { scopedOverview, contentOverview: scopedOverview, definition, page }
    }
    const variableKeys = [...new Set(definition.widgets
      .filter((widget) => widget.settings?.includeInReport !== false && typeof widget.settings?.variableId === 'string')
      .map((widget) => String(widget.settings?.variableId)))]
    return { scopedOverview: filterOverviewForVariables(dataOverview!, variableKeys, page.name), contentOverview: dataOverview!, definition, page }
  }

  const openReportExport = async () => {
    if (!overview) return
    if (!dashboardReady) { setError('현재 상세 분석 페이지를 불러온 뒤 보고서를 내보내 주세요.'); return }
    setReportError('')
    try {
      const { createDashboardReportContent, createDefaultReportOptions, createRunComparisonReportContent, DEFAULT_REPORT_LAYOUT, normalizeReportLayout, prepareContentReportLayout } = await import('./reportExport')
      const [layouts, templates, availableRuns] = await Promise.all([api.reportLayouts(), api.reportTemplates(), api.analysisRuns(selectedLoadCaseId)])
      const selected = layouts.find((item) => item.id === 'report-layout-standard')?.definition ?? layouts[0]?.definition ?? DEFAULT_REPORT_LAYOUT
      const layoutVersions = layouts.length ? await api.reportLayoutVersions(selected.id) : []
      let scopedOverview = overview
      let contents: ReportContentItem[]
      let source: ReportSource
      let pageId: string
      if (activeView === 'compare') {
        if (!comparisonReportContext || comparisonReportContext.loadCaseId !== selectedLoadCaseId) throw new Error('현재 하중 경우의 기준 Run과 대상 Run 비교가 준비된 뒤 보고서를 내보낼 수 있습니다.')
        const [comparison, trust, reviews] = await Promise.all([
          api.runComparison(comparisonReportContext.loadCaseId, comparisonReportContext.baselineRunId, comparisonReportContext.targetRunId),
          api.runTrust(comparisonReportContext.targetRunId),
          api.reviewItems(comparisonReportContext.targetRunId),
        ])
        contents = createRunComparisonReportContent(comparison, trust, reviews)
        source = { kind: 'run_compare_review', loadCaseId: comparisonReportContext.loadCaseId, baselineRunId: comparisonReportContext.baselineRunId, targetRunId: comparisonReportContext.targetRunId }
        pageId = activeDashboardId
      } else {
        const available = visiblePages(analysisPages, overview).filter((page) => page.page.analysis_key !== 'run_comparison' && (page.page.is_system || page.page.status === 'published' || (page.id === activeDashboardId && canManagePages)))
        const selectedPage = available.find((page) => page.id === activeDashboardId) ?? available[0]
        if (!selectedPage) throw new Error('내보낼 분석 페이지가 없습니다.')
        const reportData = await reportDataForPage(selectedPage.id)
        scopedOverview = reportData.scopedOverview
        if (!scopedOverview.run) throw new Error('완료된 Run이 없어 보고서를 내보낼 수 없습니다.')
        contents = createDashboardReportContent(reportData.definition, reportData.contentOverview)
        source = { kind: 'analysis_page', dashboardId: selectedPage.id, loadCaseId: selectedLoadCaseId, runId: scopedOverview.run }
        pageId = selectedPage.id
      }
      setReportPageId(pageId)
      setReportRuns(availableRuns)
      setReportRunId(scopedOverview.run ?? '')
      setReportOverview(scopedOverview)
      setReportDraft(createDefaultReportOptions(scopedOverview))
      setReportContents(contents)
      setReportSource(source)
      setReportLayouts(layouts)
      setReportLayoutDraft(prepareContentReportLayout(withReportVariables(normalizeReportLayout(selected), scopedOverview), source, contents, true))
      setReportLayoutVersions(layoutVersions)
      setReportTemplates(templates)
      reportLayoutEditor.close()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '보고서 레이아웃을 불러오지 못했습니다.')
    }
  }

  const selectReportPage = async (pageId: string) => {
    setReportError('')
    try {
      if (!reportRunId) throw new Error('보고서에 사용할 Run을 먼저 선택해 주세요.')
      const { createDashboardReportContent, createDefaultReportOptions, prepareContentReportLayout } = await import('./reportExport')
      const selectedOverview = await api.overview(selectedLoadCaseId, reportRunId)
      const { scopedOverview, contentOverview, definition } = await reportDataForPage(pageId, selectedOverview)
      const contents = createDashboardReportContent(definition, contentOverview)
      const source: ReportSource = { kind: 'analysis_page', dashboardId: pageId, loadCaseId: selectedLoadCaseId, runId: reportRunId }
      setReportPageId(pageId)
      setReportOverview(scopedOverview)
      setReportDraft(createDefaultReportOptions(scopedOverview))
      setReportContents(contents)
      setReportSource(source)
      setReportLayoutDraft((current) => current ? prepareContentReportLayout(withReportVariables(current, scopedOverview), source, contents, true) : current)
    } catch (reason) {
      setReportError(reason instanceof Error ? reason.message : '분석 페이지를 보고서에 연결하지 못했습니다.')
    }
  }

  const selectReportRun = async (runId: string) => {
    if (!reportPageId || !runId) return
    setReportError('')
    try {
      const { createDashboardReportContent, createDefaultReportOptions, prepareContentReportLayout } = await import('./reportExport')
      const selectedOverview = await api.overview(selectedLoadCaseId, runId)
      const { scopedOverview, contentOverview, definition } = await reportDataForPage(reportPageId, selectedOverview)
      const contents = createDashboardReportContent(definition, contentOverview)
      const source: ReportSource = { kind: 'analysis_page', dashboardId: reportPageId, loadCaseId: selectedLoadCaseId, runId }
      setReportRunId(runId)
      setReportOverview(scopedOverview)
      setReportDraft(createDefaultReportOptions(scopedOverview))
      setReportContents(contents)
      setReportSource(source)
      setReportLayoutDraft((current) => current ? prepareContentReportLayout(withReportVariables(current, scopedOverview), source, contents, true) : current)
    } catch (reason) {
      setReportError(reason instanceof Error ? reason.message : '선택한 Run을 보고서에 연결하지 못했습니다.')
    }
  }

  const selectComparisonReportRun = async (side: 'baseline' | 'target', runId: string) => {
    if (reportSource?.kind !== 'run_compare_review' || !runId) return
    const baselineRunId = side === 'baseline' ? runId : reportSource.baselineRunId
    const targetRunId = side === 'target' ? runId : reportSource.targetRunId
    if (baselineRunId === targetRunId) return
    setReportError('')
    try {
      const { createDefaultReportOptions, createRunComparisonReportContent, prepareContentReportLayout } = await import('./reportExport')
      const [comparison, trust, reviews, targetOverview] = await Promise.all([
        api.runComparison(reportSource.loadCaseId, baselineRunId, targetRunId),
        api.runTrust(targetRunId),
        api.reviewItems(targetRunId),
        api.overview(reportSource.loadCaseId, targetRunId),
      ])
      const contents = createRunComparisonReportContent(comparison, trust, reviews)
      const source: ReportSource = { kind: 'run_compare_review', loadCaseId: reportSource.loadCaseId, baselineRunId, targetRunId }
      setComparisonReportContext({ loadCaseId: reportSource.loadCaseId, baselineRunId, targetRunId, comparison, trust, reviews })
      setReportOverview(targetOverview)
      setReportDraft(createDefaultReportOptions(targetOverview))
      setReportContents(contents)
      setReportSource(source)
      setReportLayoutDraft((current) => current ? prepareContentReportLayout(withReportVariables(current, targetOverview), source, contents, true) : current)
    } catch (reason) {
      setReportError(reason instanceof Error ? reason.message : '선택한 Run 비교를 보고서에 연결하지 못했습니다.')
    }
  }

  const updateReportDraft = (field: keyof ReportExportOptions, value: string) => {
    setReportDraft((current) => current ? { ...current, [field]: value } : current)
  }

  const downloadReport = async () => {
    if (!reportDraft || !reportOverview || !reportLayoutDraft) return
    setReportExporting(true)
    setReportError('')
    try {
      const { exportAnalysisReport, createReportTemplateReplacements, reportFilename } = await import('./reportExport')
      let filename: string
      if (reportLayoutDraft.templateSource === 'pptx_upload' && reportLayoutDraft.templateAssetId) {
        if (reportContents.length) throw new Error('페이지별 위젯·Run 비교 콘텐츠는 시각적 레이아웃에서 내보내 주세요. 업로드 PPTX 바인딩은 아직 이 콘텐츠 형식을 지원하지 않습니다.')
        filename = reportFilename(reportOverview, reportDraft)
        const blob = await api.renderReportTemplate(reportLayoutDraft.templateAssetId, createReportTemplateReplacements(reportOverview, reportDraft, reportLayoutDraft, reportTemplates.find((item) => item.id === reportLayoutDraft.templateAssetId)), filename)
        const href = URL.createObjectURL(blob)
        const anchor = document.createElement('a')
        anchor.href = href; anchor.download = filename; document.body.appendChild(anchor); anchor.click(); anchor.remove()
        setTimeout(() => URL.revokeObjectURL(href), 1000)
      } else filename = await exportAnalysisReport(reportOverview, reportDraft, reportLayoutDraft, reportContents)
      setReportDraft(null)
      setReportOverview(null)
      setReportLayoutDraft(null)
      setReportContents([])
      setReportSource(null)
      setNotice(`${filename} 생성 완료`)
    } catch (reason) {
      setReportError(reason instanceof Error ? reason.message : 'PPTX 보고서를 생성하지 못했습니다.')
    } finally {
      setReportExporting(false)
    }
  }

  const selectReportLayout = async (layoutId: string) => {
    const selected = reportLayouts.find((item) => item.id === layoutId)
    if (selected && reportOverview && reportSource) {
      const { normalizeReportLayout, prepareContentReportLayout } = await import('./reportExport')
      setReportLayoutDraft(prepareContentReportLayout(withReportVariables(normalizeReportLayout(selected.definition), reportOverview), reportSource, reportContents))
      setReportLayoutVersions(await api.reportLayoutVersions(layoutId))
    }
  }

  const selectReportLayoutVersion = async (version: number) => {
    if (!reportLayoutDraft || !reportOverview || !reportSource) return
    try {
      const stored = await api.reportLayoutVersion(reportLayoutDraft.id, version)
      const { normalizeReportLayout, prepareContentReportLayout } = await import('./reportExport')
      setReportLayoutDraft(prepareContentReportLayout(withReportVariables(normalizeReportLayout(stored.definition), reportOverview), reportSource, reportContents))
    } catch (reason) {
      setReportError(reason instanceof Error ? reason.message : '보고서 레이아웃 버전을 불러오지 못했습니다.')
    }
  }

  const saveReportLayout = async (asNew: boolean) => {
    if (!reportLayoutDraft) return
    setReportError('')
    try {
      const payload = { name: reportLayoutDraft.name, description: reportLayoutDraft.description, definition: reportLayoutDraft, updated_by: '보고서 편집자' }
      const saved = asNew ? await api.createReportLayout(payload) : await api.updateReportLayout(reportLayoutDraft.id, payload)
      const layouts = await api.reportLayouts()
      const layoutVersions = await api.reportLayoutVersions(saved.id)
      setReportLayouts(layouts)
      const { normalizeReportLayout } = await import('./reportExport')
      setReportLayoutDraft(withReportVariables(normalizeReportLayout(saved.definition), reportOverview!))
      setReportLayoutVersions(layoutVersions)
      setNotice(`${saved.name} v${saved.version} 저장 완료`)
    } catch (reason) {
      setReportError(reason instanceof Error ? reason.message : '보고서 레이아웃을 저장하지 못했습니다.')
    }
  }

  const uploadReportTemplate = async (file: File) => {
    if (!reportLayoutDraft) return
    setReportError('')
    try {
      const created = await api.uploadReportTemplate(file.name.replace(/\.pptx$/i, ''), file)
      setReportTemplates((items) => [created, ...items])
      setReportLayoutDraft({ ...reportLayoutDraft, templateSource: 'pptx_upload', templateAssetId: created.id, templateBindings: {} })
      setNotice(`${created.name} 템플릿의 플레이스홀더 ${created.definition.placeholders.length}개를 인식했습니다.`)
    } catch (reason) { setReportError(reason instanceof Error ? reason.message : 'PPTX 템플릿을 업로드하지 못했습니다.') }
  }

  const removeReportTemplate = async (templateId: string) => {
    try {
      await api.deleteReportTemplate(templateId)
      setReportTemplates((items) => items.filter((item) => item.id !== templateId))
      if (reportLayoutDraft?.templateAssetId === templateId) setReportLayoutDraft({ ...reportLayoutDraft, templateSource: 'native', templateAssetId: undefined, templateBindings: {} })
    } catch (reason) { setReportError(reason instanceof Error ? reason.message : 'PPTX 템플릿을 삭제하지 못했습니다.') }
  }

  const removeReportLayout = async () => {
    if (!reportLayoutDraft || reportLayoutDraft.id.startsWith('report-layout-standard') || reportLayouts.find((item) => item.id === reportLayoutDraft.id)?.is_system) return
    try {
      await api.deleteReportLayout(reportLayoutDraft.id)
      const layouts = await api.reportLayouts()
      setReportLayouts(layouts)
      if (reportOverview && layouts[0]) setReportLayoutDraft(withReportVariables(layouts[0].definition, reportOverview))
    } catch (reason) {
      setReportError(reason instanceof Error ? reason.message : '보고서 레이아웃을 삭제하지 못했습니다.')
    }
  }

  const openWorkflowAnalysis = async (workflow: Workflow) => {
    try {
      await loadContext(workflow.request.project_id, workflow.request.id, 'open_cell')
    } catch (reason) { setError(reason instanceof Error ? reason.message : '상세 분석을 열지 못했습니다.') }
  }

  const refreshOperationalData = async () => {
    const [projectData, workflowData] = await Promise.all([api.projects(), api.workflows()])
    setProjects(projectData)
    setWorkflows(workflowData)
    setOperationalRefreshToken((value) => value + 1)
  }

  const handleIntakeCreated = async (projectId: string, request: AnalysisRequest) => {
    const [projectData, workflowData, requestData] = await Promise.all([api.projects(), api.workflows(), api.requests(projectId)])
    setProjects(projectData); setWorkflows(workflowData); setRequests(requestData)
    setSelectedProjectId(projectId); setSelectedRequestId(request.id); setLoadCases([]); setSelectedLoadCaseId('')
    setOperationalRefreshToken((value) => value + 1)
    setNotice(`${request.title} 의뢰를 접수했습니다.`)
  }

  const openIntakeWorkbench = (requestId: string) => {
    setSelectedRequestId(requestId)
    setWorkspacePage('workbench')
  }

  const openImportedResult = async (projectId: string, requestId: string, loadCaseId: string) => {
    const [requestData, caseData, thresholdData, overviewData, pageData] = await Promise.all([
      api.requests(projectId), api.loadCases(requestId), api.qualityThresholds(projectId), api.overview(loadCaseId), api.dashboardPages(loadCaseId),
    ])
    const selectedPage = preferredPage(pageData, overviewData)
    const dashboardData = selectedPage ? await api.dashboard(selectedPage.id) : null
    setRequests(requestData); setLoadCases(caseData); setThresholds(thresholdData)
    setSelectedProjectId(projectId); setSelectedRequestId(requestId); setSelectedLoadCaseId(loadCaseId); setOverview(overviewData)
    setAnalysisPages(pageData)
    if (selectedPage) { setActiveDashboardId(selectedPage.id); setActiveView(pageView(selectedPage)); if (dashboardData) setDashboard(dashboardData) }
    setWorkspacePage('dashboard')
  }

  const openPortfolioRequest = async (projectId: string, requestId: string) => {
    try {
      await loadMonitoringContext(projectId, requestId)
      setWorkspacePage('dashboard')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '의뢰 진행 상태를 열지 못했습니다.')
    }
  }

  const openFeatureExample = async (example: FeatureExample) => {
    try {
      if (example.project_id && example.request_id) {
        await loadContext(example.project_id, example.request_id, example.preferred_view)
      }
      if (example.preferred_view) setActiveView(example.preferred_view)
      setWorkspacePage(example.workspace_page)
      setNotice(`${example.title} 예제를 열었습니다.`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '예제를 열지 못했습니다.')
    }
  }

  if (!authReady) {
    return <div className="full-state"><LoaderCircle className="spin" /> 인증 설정을 확인하고 있습니다.</div>
  }

  if (authRequired && !authUser) {
    return <LoginScreen error={authError} onLogin={handleLogin} theme={theme} onThemeChange={setTheme} />
  }

  if (loading) {
    return <div className="full-state"><LoaderCircle className="spin" /> 데이터와 레이아웃을 준비하고 있습니다.</div>
  }

  if (error || !overview || workflows.length === 0 || !dashboard) {
    return <div className="full-state error"><AlertTriangle /> {error || '대시보드를 불러오지 못했습니다.'}</div>
  }

  const projectWorkflows = workflows.filter((workflow) => workflow.request.project_id === selectedProjectId)
  const selectedWorkflow = workflows.find((workflow) => workflow.request.id === selectedRequestId)
  // The monitoring tab is scoped to the request selected in the context
  // controls.  Showing a project-wide average here made the same request read
  // differently from its monitoring card and the operations dashboard.
  const workflowProgress = selectedWorkflow?.progress ?? 0
  const chassisThreshold = thresholds.find((item) => item.criterion_key === 'chassis_rear_permanent_deformation_mm')
  const openCellThreshold = thresholds.find((item) => item.criterion_key === 'open_cell_stress_mpa')
  const canEdit = !authRequired || authUser?.role === 'editor' || authUser?.role === 'admin'
  const canManagePages = !authRequired || authUser?.role === 'admin'
  const analysisTabs = visiblePages(analysisPages, overview)
  const activeAnalysisPage = analysisPages.find((page) => page.id === activeDashboardId)

  return (
    <div className={`app-shell ${theme === 'light' ? 'light-theme' : 'dark-theme'} ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`} data-theme={theme} data-sidebar-collapsed={sidebarCollapsed} style={{ '--ui-font-size': `${uiFontSize}pt` } as CSSProperties}>
      <aside className="sidebar" aria-label="주 메뉴">
        <div className="brand" title="VD simulation workbench"><span className="brand-mark"><Activity /></span><span>VD simulation<br /><strong>workbench</strong></span></div>
        <nav className="nav-main">
          <button aria-label="운영 대시보드" title="운영 대시보드" className={workspacePage === 'portfolio' ? 'active' : ''} onClick={() => setWorkspacePage('portfolio')}><LayoutDashboard /><span>운영 대시보드</span></button>
          <button aria-label="해석 의뢰 현황" title="해석 의뢰 현황" className={workspacePage === 'dashboard' ? 'active' : ''} onClick={openDashboardWorkspace}><Activity /><span>해석 의뢰 현황</span></button>
          <button aria-label="의뢰 접수" title="의뢰 접수" className={workspacePage === 'intake' ? 'active' : ''} onClick={() => setWorkspacePage('intake')}><ClipboardPlus /><span>의뢰 접수</span></button>
          <button aria-label="해석 작업 실행" title="해석 작업 실행" className={workspacePage === 'workbench' ? 'active' : ''} onClick={() => setWorkspacePage('workbench')}><FlaskConical /><span>해석 작업 실행</span></button>
          {(!authRequired || authUser?.role === 'admin') && <button aria-label="작업 유형 관리" title="작업 유형 관리" className={workspacePage === 'workbench_admin' ? 'active' : ''} onClick={() => setWorkspacePage('workbench_admin')}><Settings2 /><span>작업 유형 관리</span></button>}
          <button aria-label="해석 데이터" title="해석 데이터" className={workspacePage === 'data' ? 'active' : ''} onClick={() => setWorkspacePage('data')}><Database /><span>해석 데이터</span></button>
          <button aria-label="변수 카탈로그" title="변수 카탈로그" className={workspacePage === 'variables' ? 'active' : ''} onClick={() => setWorkspacePage('variables')}><BarChart3 /><span>변수 카탈로그</span></button>
          <button aria-label="자동화 템플릿" title="자동화 템플릿" className={workspacePage === 'templates' ? 'active' : ''} onClick={() => setWorkspacePage('templates')}><Settings2 /><span>자동화 템플릿</span></button>
          <button aria-label="폴더 스키마" title="폴더 스키마" className={workspacePage === 'schemas' ? 'active' : ''} onClick={() => setWorkspacePage('schemas')}><GripVertical /><span>폴더 스키마</span></button>
          <button aria-label="예제 갤러리" title="예제 갤러리" className={workspacePage === 'examples' ? 'active' : ''} onClick={() => setWorkspacePage('examples')}><Play /><span>예제 갤러리</span></button>
          <button aria-label="도움말" title="도움말" className={workspacePage === 'help' ? 'active' : ''} onClick={() => setWorkspacePage('help')}><BookOpen /><span>도움말</span></button>
        </nav>
        <div className="sidebar-foot">
          <div className="global-font-control" aria-label="전체 글자 크기 조절">
            <span className="sidebar-label">글자 크기</span>
            <button type="button" aria-label="전체 글자 크기 줄이기" onClick={() => setUiFontSize((value) => Math.max(11, value - 1))} disabled={uiFontSize <= 11}><Minus /></button>
            <output>{uiFontSize}pt</output>
            <button type="button" aria-label="전체 글자 크기 늘리기" onClick={() => setUiFontSize((value) => Math.min(18, value + 1))} disabled={uiFontSize >= 18}><Plus /></button>
          </div>
          {authUser && <div className="signed-user"><strong>{authUser.display_name}</strong><span>{authUser.role.toUpperCase()}</span></div>}
          <div className="system-pill"><span className="live-dot" /> {databaseBackend.toUpperCase()} · {databaseBackend === 'postgresql' ? 'SERVER' : 'LOCAL'}</div>
          <button type="button" className="sidebar-toggle" aria-label={sidebarCollapsed ? '메뉴 펼치기' : '메뉴 접기'} aria-expanded={!sidebarCollapsed} onClick={() => setSidebarCollapsed((value) => !value)}>{sidebarCollapsed ? <PanelLeftOpen /> : <PanelLeftClose />}<span>{sidebarCollapsed ? '메뉴 펼치기' : '메뉴 접기'}</span></button>
          {authUser && <button onClick={() => void logout()}><LogOut /><span>로그아웃</span></button>}
        </div>
      </aside>

      <main className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">{workspacePage === 'portfolio' ? <><span>운영</span><b>/</b><strong>해석 운영 현황</strong></> : workspacePage === 'intake' ? <><span>의뢰</span><b>/</b><strong>해석 의뢰 접수</strong></> : workspacePage === 'workbench' ? <><span>실행</span><b>/</b><strong>해석 작업 실행 · DEMO ONLY</strong></> : workspacePage === 'workbench_admin' ? <><span>관리</span><b>/</b><strong>작업 유형 관리</strong></> : workspacePage === 'data' ? <><span>운영</span><b>/</b><strong>해석 데이터 등록</strong></> : workspacePage === 'schemas' ? <><span>데이터 설계</span><b>/</b><strong>폴더 스키마</strong></> : workspacePage === 'variables' ? <><span>설계</span><b>/</b><strong>변수 카탈로그</strong></> : workspacePage === 'templates' ? <><span>자동화</span><b>/</b><strong>모델링 템플릿</strong></> : workspacePage === 'examples' ? <><span>지원</span><b>/</b><strong>기능 예제 갤러리</strong></> : workspacePage === 'help' ? <><span>지원</span><b>/</b><strong>사용 시나리오 도움말</strong></> : activeView === 'workflow' ? <><span>의뢰</span><b>/</b><span>{selectedWorkflow?.request.project_name ?? '프로젝트 미지정'}</span><b>/</b><strong>{selectedWorkflow?.request.title ?? '의뢰 선택'}</strong></> : <><span>프로젝트</span><b>/</b><span>{overview.load_case.project_name}</span><b>/</b><strong>{overview.load_case.name}</strong></>}</div>
          <div className="top-actions">
            <div className="theme-switch" role="group" aria-label="화면 테마 선택"><button type="button" className={theme === 'light' ? 'active' : ''} aria-pressed={theme === 'light'} onClick={() => setTheme('light')}><Sun /><span>라이트</span></button><button type="button" className={theme === 'dark' ? 'active' : ''} aria-pressed={theme === 'dark'} onClick={() => setTheme('dark')}><Moon /><span>다크</span></button></div>
            {workspacePage === 'dashboard' && activeView !== 'workflow' && <button className="ghost-button" title={!overview?.run && activeView !== 'compare' ? '완료된 Run이 있어야 보고서를 내보낼 수 있습니다.' : undefined} disabled={!dashboardReady || (!overview?.run && activeView !== 'compare')} onClick={() => void openReportExport()}><Download /> 보고서 내보내기</button>}
            {canEdit && workspacePage === 'dashboard' && activeView !== 'workflow' && <button className="ghost-button" disabled={!dashboardReady} onClick={() => setAssistantOpen(true)}><Sparkles /> 자연어로 개선</button>}
            {canEdit && (workspacePage === 'dashboard' && activeView === 'workflow' ? (editMode ? (
              <button className="primary-button" onClick={save}><Save /> {workflowEditorMode === 'layout' ? '대시보드 레이아웃 저장' : '단계 변경 저장'}</button>
            ) : <div className="workflow-top-edit-actions">
              <button className="edit-button" onClick={beginWorkflowLayoutEditing}><LayoutDashboard /> 대시보드 편집</button>
              {!selectedWorkflow?.work_plan && <button className="edit-button" onClick={beginWorkflowStageEditing}><Settings2 /> 진행 단계 편집</button>}
            </div>) : (workspacePage === 'dashboard' || workspacePage === 'portfolio') && (editMode ? (
              <button className="primary-button" onClick={save}><Save /> {workspacePage === 'portfolio' ? '운영 설정 저장' : '레이아웃 저장'}</button>
            ) : <button className="edit-button" disabled={workspacePage === 'dashboard' && !dashboardReady} onClick={beginEditing}><Settings2 /> 대시보드 편집</button>))}
            <div className="avatar">{authUser ? authUser.display_name.slice(0, 2) : 'HK'}</div>
          </div>
        </header>

        {workspacePage === 'portfolio' ? <PortfolioDashboard refreshToken={operationalRefreshToken} editMode={editMode} layout={portfolioLayout} layoutVersion={portfolioLayoutVersion} onLayoutChange={setPortfolioLayout} onCancelEdit={cancelEditing} onResetLayout={resetPortfolioLayout} onOpen={(projectId, requestId) => void openPortfolioRequest(projectId, requestId)} /> : workspacePage === 'intake' ? <RequestIntakePage projects={projects} createdBy={authUser?.display_name ?? '데모 사용자'} canCreate={canEdit} onCreated={handleIntakeCreated} onOpenWorkbench={openIntakeWorkbench} /> : workspacePage === 'workbench' ? <SimulationWorkbench workflows={workflows} initialRequestId={selectedRequestId} createdBy={authUser?.display_name ?? '데모 사용자'} canExecute={canEdit} isAdmin={!authRequired || authUser?.role === 'admin'} onRequestSelected={setSelectedRequestId} onChanged={async (message) => { setWorkflows(await api.workflows()); setOperationalRefreshToken((value) => value + 1); setNotice(message) }} /> : workspacePage === 'workbench_admin' ? <WorkbenchTypeAdmin /> : workspacePage === 'schemas' ? <FolderSchemaWorkspace /> : workspacePage === 'variables' ? <VariableCatalogPage variables={variables} overview={overview} loadCaseId={selectedLoadCaseId} onChanged={(items) => { setVariables(items); setCatalogVariable((current) => items.some((item) => item.id === current) ? current : items[0]?.id ?? '') }} /> : workspacePage === 'templates' ? <AutomationTemplatesPage /> : workspacePage === 'examples' ? <FeatureExampleGallery onOpen={openFeatureExample} /> : workspacePage === 'help' ? <HelpCenter onNavigate={setWorkspacePage} /> : workspacePage === 'data' ? (
          <DataWorkspace projects={projects} initialProjectId={selectedProjectId} onDataChanged={refreshOperationalData} onOpenAnalysis={openImportedResult} onOpenIntake={() => setWorkspacePage('intake')} />
        ) : <>
        <section className="content-head">
          <div>
            <div className="eyebrow"><span>{activeView === 'workflow' ? 'REQUEST MONITORING' : 'PROJECT 24-071'}</span><span>•</span><span>{activeView === 'workflow' ? selectedWorkflow?.request.category ?? 'UNASSIGNED' : overview.load_case.analysis_type}</span></div>
            <h1>{activeView === 'workflow' ? `${selectedWorkflow?.request.project_name ?? overview.load_case.project_name} 해석 의뢰 현황` : `${overview.load_case.product_name} 불량 분석`}</h1>
            <p>{activeView === 'workflow' ? selectedWorkflow?.request.title ?? '의뢰를 선택하세요.' : overview.load_case.request_title}</p>
          </div>
          <div className="context-selectors">
            <label><span>프로젝트</span><select aria-label="프로젝트 선택" value={selectedProjectId} onChange={(event) => handleProjectChange(event.target.value)}>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
            <label><span>의뢰</span><select aria-label="의뢰 선택" value={selectedRequestId} onChange={(event) => handleRequestChange(event.target.value)}>{requests.map((request) => <option key={request.id} value={request.id}>{request.title}</option>)}</select></label>
            <label><span>하중 경우</span><select aria-label="하중 경우 선택" value={selectedLoadCaseId} disabled={loadCases.length === 0} onChange={(event) => handleLoadCaseChange(event.target.value)}>{loadCases.length === 0 ? <option value="">미지정</option> : loadCases.map((loadCase) => <option key={loadCase.id} value={loadCase.id}>{loadCase.name}</option>)}</select></label>
          </div>
        </section>

        <section className="view-tabs">
          <button data-testid="selected-request-progress-tab" className={activeView === 'workflow' ? 'active' : ''} onClick={() => switchDashboardView('workflow')}><CircleDot /> 의뢰 진행 상태 <span>{workflowProgress}%</span></button>
          <button className={activeView !== 'workflow' ? 'active' : ''} disabled={!selectedLoadCaseId || analysisTabs.length === 0} onClick={() => { const page = analysisTabs[0]; if (page) switchAnalysisPage(page) }}><LayoutDashboard /> 상세 분석 <span>{selectedLoadCaseId ? overview.load_case.analysis_type.replace('_', ' ') : '하중 경우 미지정'}</span></button>
          <div className="tab-line" />
        </section>

        {activeView !== 'workflow' && <section className="analysis-subtabs"><div><span>상세 분석</span><b>/</b><strong>{overview.load_case.request_title}</strong></div><nav aria-label="불량 분석 하위 탭">
          {analysisTabs.map((page) => <div className="analysis-tab-group" key={page.id}><button className={`analysis-tab ${activeDashboardId === page.id ? 'active' : ''}`} onClick={() => switchAnalysisPage(page)}>{page.page.analysis_key === 'open_cell' ? <Activity /> : page.page.analysis_key === 'chassis_rear' ? <BarChart3 /> : page.page.analysis_key === 'run_comparison' ? <MessageSquareText /> : <LayoutDashboard />} {page.name} <span>{page.page.analysis_key === 'open_cell' ? overview.analysis_verdicts.open_cell : page.page.analysis_key === 'chassis_rear' ? overview.analysis_verdicts.chassis_rear : page.page.analysis_key === 'run_comparison' ? 'SYSTEM' : page.page.status.toUpperCase()}</span></button></div>)}
          <label className="analysis-page-jump"><span>페이지</span><select aria-label="상세 분석 페이지 선택" value={activeAnalysisPage?.id ?? analysisTabs[0]?.id ?? ''} onChange={(event) => { const page = analysisTabs.find((item) => item.id === event.target.value); if (page) switchAnalysisPage(page) }}>{analysisTabs.map((page) => <option key={page.id} value={page.id}>{page.name}</option>)}</select></label>
          {canManagePages && <button className="analysis-page-manage-button" onClick={() => setPageManagerOpen(true)}><Plus /> 분석 페이지 관리</button>}
        </nav>{activeAnalysisPage?.page.analysis_key === 'open_cell' && activeView !== 'compare' && <EdgeFilter selected={selectedEdges} setSelected={setSelectedEdges} />}</section>}

        {editMode && (
          <div className="edit-banner" data-testid={workspaceEditor.mode ?? undefined}><GripVertical /><span><strong>{activeView === 'workflow' && workflowEditorMode === 'layout' ? '대시보드 레이아웃 편집' : activeView === 'workflow' ? '진행 단계 편집' : '편집 모드'}</strong> {activeView === 'workflow' && workflowEditorMode === 'layout' ? '의뢰 위젯을 이동·리사이즈하고 색상과 글자 크기를 조절한 뒤 상단에서 저장하세요.' : activeView === 'workflow' ? '단계 내용·순서·추가·삭제를 편집한 뒤 상단의 단계 변경 저장을 누르세요.' : '위젯을 드래그하거나 모서리를 잡아 크기를 조절하고 설정 버튼으로 그래프, 변수, 글자 크기를 바꾸세요.'}</span><button onClick={cancelEditing}>편집 취소</button></div>
        )}
        {editMode && activeView === 'workflow' && workflowEditorMode === 'layout' && <div className="workflow-layout-toolbar">
          <label><span>강조 색상</span><input aria-label="진행 현황 강조 색상" type="color" value={workflowDashboardLayout.accentColor} onChange={(event) => setWorkflowDashboardLayout({ ...workflowDashboardLayout, accentColor: event.target.value })}/><code>{workflowDashboardLayout.accentColor}</code></label>
          <div><span>글자 크기</span><button aria-label="진행 현황 글자 크기 줄이기" onClick={() => setWorkflowDashboardLayout({ ...workflowDashboardLayout, fontSize: Math.max(8, workflowDashboardLayout.fontSize - 1) })} disabled={workflowDashboardLayout.fontSize <= 8}><Minus /></button><output>{workflowDashboardLayout.fontSize}px</output><button aria-label="진행 현황 글자 크기 늘리기" onClick={() => setWorkflowDashboardLayout({ ...workflowDashboardLayout, fontSize: Math.min(18, workflowDashboardLayout.fontSize + 1) })} disabled={workflowDashboardLayout.fontSize >= 18}><Plus /></button></div>
          <button onClick={resetWorkflowDashboardLayout}><RotateCcw /> 기본 레이아웃</button>
        </div>}
        {editMode && activeView !== 'workflow' && (
          <div className="dashboard-edit-toolbar">
            <div><strong>{dashboard.name}</strong><span>v{dashboard.version ?? 1} · 카드 상단의 손잡이로 이동하고 오른쪽 아래 모서리로 크기를 조절합니다.</span></div>
            <button onClick={() => setAssistantOpen(true)}><Plus /> 위젯 추가·버전 관리</button>
          </div>
        )}

        <section className="canvas-area">
          {activeView === 'compare' && overview.run && dashboard.page?.analysis_key === 'run_comparison' ? (
            <ResponsiveGridLayout
              className="layout comparison-dashboard-layout"
              layouts={{ lg: dashboard.widgets.map((item) => ({ i: item.id, x: item.x, y: item.y, w: item.w, h: item.h })) }}
              breakpoints={{ lg: 900, md: 600, sm: 0 }}
              cols={{ lg: 12, md: 8, sm: 1 }}
              rowHeight={74}
              margin={[16, 16]}
              isDraggable={editMode}
              isResizable={editMode}
              draggableHandle=".widget-drag-handle"
              compactType="vertical"
              onLayoutChange={handleLayoutChange}
            >
              {dashboard.widgets.map((widget) => <div key={widget.id} className="comparison-dashboard-widget"><header className="widget-head"><div>{editMode && <span className="widget-drag-handle"><GripVertical /> 이동</span>}<h3>{widget.title}</h3></div>{editMode && <button onClick={() => setSelectedWidgetId(widget.id)}><Settings2 /> 설정</button>}</header>{widget.type === 'run_comparison' ? <ComparisonWorkspace loadCaseId={selectedLoadCaseId} currentRunId={overview.run ?? ''} onContextChange={setComparisonReportContext} /> : <WidgetCard widget={widget} overview={overview} selectedEdges={selectedEdges} editMode={editMode} canManageThresholds={canManagePages} threshold={chassisThreshold} openCellThreshold={openCellThreshold} onSaveThreshold={saveChassisThreshold} onSaveOpenCellThreshold={saveOpenCellThreshold} onRemove={() => removeWidget(widget.id)} onConfigure={() => setSelectedWidgetId(widget.id)} />}</div>)}
            </ResponsiveGridLayout>
          ) : activeView === 'compare' ? (
            <div className="comparison-state"><LoaderCircle className="spin" /> Run 비교 대시보드를 불러오고 있습니다.</div>
          ) : activeView !== 'workflow' && dashboard.widgets.length === 0 ? (
            <div className="analysis-empty-canvas"><LayoutDashboard /><h2>{dashboard.name}</h2><p>아직 배치된 위젯이 없습니다. 위젯을 추가해 이 분석 페이지를 구성하세요.</p>{canEdit && <button onClick={() => { if (!editMode) beginEditing(); setAssistantOpen(true) }}><Plus /> 첫 위젯 추가</button>}</div>
          ) : activeView !== 'workflow' ? (
            <ResponsiveGridLayout
              className="layout"
              layouts={{ lg: dashboard.widgets.map((item) => ({ i: item.id, x: item.x, y: item.y, w: item.w, h: item.h })) }}
              breakpoints={{ lg: 900, md: 600, sm: 0 }}
              cols={{ lg: 12, md: 8, sm: 1 }}
              rowHeight={74}
              margin={[16, 16]}
              isDraggable={editMode}
              isResizable={editMode}
              draggableHandle=".widget-drag-handle"
              compactType="vertical"
              onLayoutChange={handleLayoutChange}
            >
              {dashboard.widgets.map((widget) => (
                <div key={widget.id}>
                  <WidgetCard widget={widget} overview={overview} selectedEdges={selectedEdges} editMode={editMode} canManageThresholds={canManagePages} threshold={chassisThreshold} openCellThreshold={openCellThreshold} onSaveThreshold={saveChassisThreshold} onSaveOpenCellThreshold={saveOpenCellThreshold} onRemove={() => removeWidget(widget.id)} onConfigure={() => setSelectedWidgetId(widget.id)} />
                </div>
              ))}
            </ResponsiveGridLayout>
          ) : (
            <WorkflowView workflows={projectWorkflows} stageEditMode={editMode && workflowEditorMode === 'stages'} layoutEditMode={editMode && workflowEditorMode === 'layout'} dashboardLayout={workflowDashboardLayout} layoutVersion={workflowLayoutVersion} onDashboardLayoutChange={setWorkflowDashboardLayout} onStepChange={updateWorkflowStepDraft} onAddStep={addWorkflowStepDraft} onDeleteStep={deleteWorkflowStepDraft} onMoveStep={moveWorkflowStepDraft} onOpenAnalysis={openWorkflowAnalysis} activeRequestId={selectedRequestId} />
          )}
        </section>
        </>}
      </main>

      {assistantOpen && (
        <div className="drawer-backdrop" onMouseDown={() => setAssistantOpen(false)}>
          <aside className="assistant-drawer" onMouseDown={(event) => event.stopPropagation()}>
            <div className="drawer-head"><div><span><Sparkles /></span><div><strong>Canvas Copilot</strong><small>자연어 대시보드 편집</small></div></div><button onClick={() => setAssistantOpen(false)}><X /></button></div>
            <div className="assistant-copy"><h2>어떤 시각화가 필요하세요?</h2><p>등록된 변수와 허용된 위젯만 사용해 안전한 변경안을 만듭니다. 적용 전 내용을 확인할 수 있습니다.</p></div>
            <div className="suggestions">
              {['응력-시간 그래프를 추가해', '상하좌우 최대 응력 막대그래프를 추가해', '패스/실패 판정 카드를 추가해'].map((text) => <button key={text} onClick={() => setCommand(text)}>{text}<Plus /></button>)}
            </div>
            <div className="catalog-editor">
              <strong>위젯 카탈로그</strong>
              <select aria-label="위젯 변수" value={catalogVariable} onChange={(event) => setCatalogVariable(event.target.value)}>{variables.map((item) => <option key={item.id} value={item.id}>{item.display_name} ({item.unit})</option>)}</select>
              <div>{widgetCatalog.filter((item) => ANALYSIS_WIDGET_TYPES.has(item.type)).map((item) => <button key={item.type} onClick={() => addCatalogWidget(item)}><Plus /> {item.label}</button>)}</div>
            </div>
            <textarea value={command} onChange={(event) => setCommand(event.target.value)} placeholder="예: 응력-시간 그래프에 기준선을 넣어줘" />
            <button className="assistant-submit" onClick={previewCommand} disabled={!command.trim()}><Sparkles /> 변경안 만들기</button>
            {proposal && (
              <div className={`proposal ${proposal.recognized ? 'recognized' : ''}`}>
                <span>{proposal.recognized ? <Check /> : <AlertTriangle />}</span>
                <div><strong>{proposal.recognized ? '적용 전 미리보기' : '요청 확인 필요'}</strong><p>{proposal.message}</p>{proposal.proposal && <code>{proposal.proposal.action === 'add_widget' ? `${proposal.proposal.widget.title} · ${proposal.proposal.widget.type}` : `${proposal.proposal.updates.length}개 위젯 설정 변경`}</code>}</div>
                {proposal.recognized && <button onClick={applyProposal}>변경안 적용</button>}
              </div>
            )}
            <div className="assistant-safe"><Lock /><span><strong>안전한 변경</strong>자연어 명령은 SQL이나 코드를 직접 실행하지 않습니다.</span></div>
            <div className="layout-history"><button onClick={cloneLayout}>다른 이름으로 복제</button><button onClick={restorePrevious}>최근 정상 버전 복구</button><small>현재 v{dashboard.version ?? 1} · 유효 저장 이력 {versions.length}개</small><div className="dashboard-version-list">{versions.map((item) => <article key={item.version}><span><strong>v{item.version}</strong><small>{new Date(item.created_at).toLocaleString('ko-KR')} · {item.created_by}</small></span><button onClick={() => void loadDashboardVersionDraft(item.version)} disabled={item.version === dashboard.version}>초안으로 불러오기</button>{canManagePages && item.version !== dashboard.version && !(dashboard.page?.is_system && item.version === 1) && <button className="danger" aria-label={`v${item.version} 버전 삭제`} onClick={() => void removeDashboardVersion(item.version)}><Trash2 /> 삭제</button>}</article>)}</div></div>
            <label className="saved-layouts"><span>저장된 레이아웃 불러오기</span><select value={dashboard.id} onChange={(event) => void loadSavedDashboard(event.target.value)}>{savedDashboards.map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.version}</option>)}</select></label>
          </aside>
        </div>
      )}
      {selectedWidgetId && dashboard && <WidgetSettingsPanel widget={dashboard.widgets.find((item) => item.id === selectedWidgetId)!} variables={variables} onChange={(patch) => updateWidget(selectedWidgetId, patch)} onClose={() => setSelectedWidgetId(null)} />}
      {pageManagerOpen && <AnalysisPageManager loadCaseId={selectedLoadCaseId} activePageId={activeDashboardId} visiblePageIds={analysisTabs.map((page) => page.id)} onClose={() => setPageManagerOpen(false)} onPagesChanged={setAnalysisPages} onActiveDefinitionChanged={(definition) => setDashboard(definition)} onDeleted={(deletedId) => {
        if (reportPageId === deletedId) { setReportDraft(null); setReportOverview(null); setReportLayoutDraft(null); setReportPageId(''); reportLayoutEditor.close() }
        dashboardBeforeEdit.current = null; setSelectedWidgetId(null); setAssistantOpen(false)
      }} onActivate={(definition, startEditing = false) => {
        if (!definition.page) return
        if (editMode) cancelEditing()
        const summary: DashboardPageSummary = { id: definition.id, project_id: selectedProjectId, request_id: selectedRequestId, load_case_id: selectedLoadCaseId, name: definition.name, description: definition.description, version: definition.version ?? 1, updated_at: definition.updated_at ?? new Date().toISOString(), page: definition.page }
        setAnalysisPages((items) => [...items.filter((item) => item.id !== summary.id), summary].sort((left, right) => left.page.display_order - right.page.display_order))
        setDashboard(definition); setActiveDashboardId(definition.id); setActiveView(pageView(summary)); setPageManagerOpen(false)
        if (startEditing) { dashboardBeforeEdit.current = structuredClone(definition); workspaceEditor.open('analysis-dashboard'); setAssistantOpen(true) }
      }} />}
      {reportDraft && reportOverview && reportLayoutDraft && reportSource && (
        <div className="drawer-backdrop report-backdrop" onMouseDown={() => { if (!reportExporting) { setReportDraft(null); setReportOverview(null); setReportLayoutDraft(null); setReportContents([]); setReportSource(null) } }}>
          <section className="report-export-dialog" role="dialog" aria-modal="true" aria-labelledby="report-export-title" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div><span>POWERPOINT EXPORT</span><h2 id="report-export-title">{reportOverview.load_case.request_title} 보고서</h2><p>선택한 평가 데이터와 아래 문구로 편집 가능한 PPTX를 생성합니다.</p></div>
              <button aria-label="닫기" onClick={() => { setReportDraft(null); setReportOverview(null); setReportLayoutDraft(null); setReportContents([]); setReportSource(null) }} disabled={reportExporting}><X /></button>
            </header>
            <div className="report-layout-toolbar">
              {reportSource.kind === 'run_compare_review' ? <><label><span>기준 Run</span><select aria-label="보고서 기준 Run 선택" value={reportSource.baselineRunId} onChange={(event) => void selectComparisonReportRun('baseline', event.target.value)}>{reportRuns.filter((run) => run.id !== reportSource.targetRunId).map((run) => <option key={run.id} value={run.id}>{`Run ${run.run_no} · ${run.overall_verdict}`}</option>)}</select></label><label><span>대상 Run</span><select aria-label="보고서 대상 Run 선택" value={reportSource.targetRunId} onChange={(event) => void selectComparisonReportRun('target', event.target.value)}>{reportRuns.filter((run) => run.id !== reportSource.baselineRunId).map((run) => <option key={run.id} value={run.id}>{`Run ${run.run_no} · ${run.overall_verdict}`}</option>)}</select></label></> : <><label><span>분석 페이지</span><select aria-label="보고서 분석 페이지" value={reportPageId} onChange={(event) => void selectReportPage(event.target.value)}>{analysisTabs.filter((page) => page.page.analysis_key !== 'run_comparison' && (page.page.is_system || page.page.status === 'published' || (page.id === activeDashboardId && canManagePages))).map((page) => <option key={page.id} value={page.id}>{page.name}</option>)}</select></label><label><span>Run</span><select aria-label="보고서 Run 선택" value={reportRunId} onChange={(event) => void selectReportRun(event.target.value)}>{reportRuns.map((run) => <option key={run.id} value={run.id}>{`Run ${run.run_no} · ${run.overall_verdict} · ${run.completed_at ? new Date(run.completed_at).toLocaleString('ko-KR') : run.status}`}</option>)}</select></label></>}
              <label><span>출력 레이아웃</span><select value={reportLayoutDraft.id} onChange={(event) => void selectReportLayout(event.target.value)}>{reportLayouts.map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.version}</option>)}</select></label>
              <label className="report-version-select"><span>출력 버전</span><select value={reportLayoutDraft.version} onChange={(event) => void selectReportLayoutVersion(Number(event.target.value))}>{reportLayoutVersions.map((item) => <option key={item.version} value={item.version}>v{item.version} · {new Date(item.created_at).toLocaleDateString('ko-KR')}</option>)}</select></label>
              <button onClick={reportLayoutEditor.toggle}><Settings2 /> {reportLayoutEditor.isEditing ? '편집 닫기' : '레이아웃 편집·관리'}</button>
            </div>
            {reportLayoutEditor.isEditing && <div data-testid="ppt-layout-editor"><ReportLayoutEditor layout={reportLayoutDraft} overview={reportOverview} contents={reportContents} templates={reportTemplates} isSystem={Boolean(reportLayouts.find((item) => item.id === reportLayoutDraft.id)?.is_system)} onChange={setReportLayoutDraft} onTemplateUpload={(file) => void uploadReportTemplate(file)} onTemplateDelete={(id) => void removeReportTemplate(id)} onSave={() => void saveReportLayout(false)} onSaveAs={() => void saveReportLayout(true)} onDelete={() => void removeReportLayout()} /></div>}
            <div className="report-export-grid">
              <label><span>작성자 *</span><input value={reportDraft.author} onChange={(event) => updateReportDraft('author', event.target.value)} placeholder="홍길동" /></label>
              <label><span>개발단계 *</span><input value={reportDraft.developmentStage} onChange={(event) => updateReportDraft('developmentStage', event.target.value)} placeholder="DV 1차" /></label>
              <label><span>작성날짜 *</span><input value={reportDraft.reportDate} onChange={(event) => updateReportDraft('reportDate', event.target.value)} placeholder="2026.07.23." /></label>
              <label><span>시뮬레이션 종류 *</span><input value={reportDraft.reliabilityName} onChange={(event) => updateReportDraft('reliabilityName', event.target.value)} placeholder="낙하 / Side Clamp / 적재" /></label>
              <label className="wide"><span>검토 목적 *</span><input value={reportDraft.reviewPurpose} onChange={(event) => updateReportDraft('reviewPurpose', event.target.value)} placeholder="오픈셀 응력 평가" /></label>
              <label><span>검토 사양 조건 *</span><textarea value={reportDraft.reviewConditions} onChange={(event) => updateReportDraft('reviewConditions', event.target.value)} /></label>
              <label><span>검토 결과 *</span><textarea value={reportDraft.reviewResult} onChange={(event) => updateReportDraft('reviewResult', event.target.value)} /></label>
              <label className="wide"><span>검토 결론 *</span><textarea value={reportDraft.reviewConclusion} onChange={(event) => updateReportDraft('reviewConclusion', event.target.value)} /></label>
            </div>
            <aside><strong>자동 포함 자료</strong><span>시간 이력 그래프 {new Set(reportOverview.time_series.map((item) => item.variable_key)).size}개 · 정량 결과 {reportOverview.scalar_results.length}개 · 이미지/미디어 {reportOverview.media.length}개</span><small>선택한 평가에 연결된 데이터만 포함합니다. 영상·애니메이션은 호환성을 위해 자산 정보 페이지로 생성됩니다.</small></aside>
            {reportError && <div className="report-export-error"><AlertTriangle /> {reportError}</div>}
            <footer>
              <button onClick={() => { setReportDraft(null); setReportOverview(null); setReportLayoutDraft(null) }} disabled={reportExporting}>취소</button>
              <button className="primary" onClick={() => void downloadReport()} disabled={reportExporting || !reportDraft.author.trim() || (reportSource.kind === 'analysis_page' && !reportRunId) || (reportLayoutDraft.templateSource === 'pptx_upload' && !reportLayoutDraft.templateAssetId)}>{reportExporting ? <LoaderCircle className="spin" /> : <Download />} PPTX 생성</button>
            </footer>
          </section>
        </div>
      )}
      {notice && <div className="toast" role="status"><Check /><span>{notice}</span><button aria-label="알림 닫기" onClick={() => setNotice('')}><X /></button></div>}
    </div>
  )
}

function ReportLayoutEditor({ layout, overview, contents, templates, isSystem, onChange, onTemplateUpload, onTemplateDelete, onSave, onSaveAs, onDelete }: {
  layout: ReportLayoutDefinition
  overview: Overview
  contents: ReportContentItem[]
  templates: ReportTemplateAsset[]
  isSystem: boolean
  onChange: (layout: ReportLayoutDefinition) => void
  onTemplateUpload: (file: File) => void
  onTemplateDelete: (id: string) => void
  onSave: () => void
  onSaveAs: () => void
  onDelete: () => void
}) {
  const slides = layout.slides ?? []
  const variables = reportVariables(overview)
  const [activeSlideId, setActiveSlideId] = useState(slides[0]?.id ?? '')
  const [selectedElementId, setSelectedElementId] = useState<string | null>(null)
  const [editingTextElementId, setEditingTextElementId] = useState<string | null>(null)
  const [templateSlide, setTemplateSlide] = useState(1)
  const activeSlide = slides.find((item) => item.id === activeSlideId) ?? slides[0]
  const selectedElement = activeSlide?.elements.find((item) => item.id === selectedElementId)
  const activeTemplate = templates.find((item) => item.id === layout.templateAssetId)
  const selectedVariables = new Map(layout.variablePlacements.map((item) => [item.variableKey, item]))
  const master = layout.slideMaster ?? { backgroundColor: 'F8FBFD', design: 'frame' as const, accentColor: layout.accentColor }
  const usesMaster = activeSlide?.style?.useMaster !== false
  const activeSlideStyle = usesMaster ? master : {
    backgroundColor: activeSlide?.style?.backgroundColor ?? master.backgroundColor,
    design: activeSlide?.style?.design ?? master.design,
    accentColor: activeSlide?.style?.accentColor ?? master.accentColor,
  }
  const assignedVariable = variables.find((item) => item.key === selectedElement?.binding?.variableKey)

  useEffect(() => {
    if (!slides.some((item) => item.id === activeSlideId)) setActiveSlideId(slides[0]?.id ?? '')
  }, [layout.id, layout.version, slides.length, activeSlideId])

  useEffect(() => {
    const selectOrEditDirectText = (event: MouseEvent) => {
      const target = event.target instanceof Element ? event.target.closest('.report-slide-widget.direct-text') : null
      const canvas = target?.closest('.report-slide-layout')
      if (!target || !canvas || !activeSlide) return
      const index = Array.from(canvas.querySelectorAll('.report-slide-widget')).indexOf(target)
      const elementId = activeSlide.elements[index]?.id
      if (!elementId) return
      if (selectedElementId === elementId) {
        event.preventDefault()
        setEditingTextElementId(elementId)
      }
      setSelectedElementId(elementId)
    }
    document.addEventListener('mousedown', selectOrEditDirectText, true)
    return () => document.removeEventListener('mousedown', selectOrEditDirectText, true)
  }, [activeSlide, selectedElementId])

  const updateSlide = (slideId: string, updater: (slide: ReportSlideDefinition) => ReportSlideDefinition) => {
    onChange({ ...layout, slides: slides.map((slide) => slide.id === slideId ? updater(slide) : slide) })
  }
  const updateElement = (patch: Partial<ReportElementDefinition>) => {
    if (!activeSlide || !selectedElement) return
    updateElementById(selectedElement.id, patch)
  }
  const updateElementById = (elementId: string, patch: Partial<ReportElementDefinition>) => {
    if (!activeSlide) return
    updateSlide(activeSlide.id, (slide) => ({ ...slide, elements: slide.elements.map((item) => item.id === elementId ? { ...item, ...patch, style: { ...item.style, ...patch.style }, binding: patch.binding ?? item.binding } : item) }))
  }
  const addElement = (type: ReportElementType, variableKey?: string, position?: { x: number; y: number }) => {
    if (!activeSlide) return
    const size: Record<ReportElementType, [number, number]> = { title: [20, 2], text: [10, 5], verdict: [5, 2], 'scalar-card': [7, 4], chart: [16, 9], table: [13, 8], image: [16, 10] }
    const [w, h] = size[type]
    const element: ReportElementDefinition = {
      id: `report-element-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`, type,
      label: variableKey ? variables.find((item) => item.key === variableKey)?.name ?? variableKey : ({ title: '제목', text: '텍스트', verdict: '종합 판정', 'scalar-card': '결과 카드', chart: '차트', table: '결과표', image: '이미지' } as Record<ReportElementType, string>)[type],
      x: Math.min(32 - w, Math.max(0, position?.x ?? 1)), y: Math.min(18 - h, Math.max(0, position?.y ?? 4)), w, h, z: activeSlide.elements.length + 1,
      text: type === 'title' ? '제목을 입력하세요' : type === 'text' ? '텍스트를 입력하세요' : undefined,
      binding: variableKey ? { source: 'variable', variableKey, key: variableKey } : type === 'verdict' ? { source: 'field', key: 'verdict' } : { source: 'static' },
      rules: { visibleWhenData: true },
    }
    updateSlide(activeSlide.id, (slide) => ({ ...slide, elements: [...slide.elements, element] }))
    setSelectedElementId(element.id)
  }
  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    if (!activeSlide) return
    try {
      const payload = JSON.parse(event.dataTransfer.getData('application/json')) as { type?: ReportElementType; variableKey?: string }
      const rect = event.currentTarget.getBoundingClientRect()
      addElement(payload.type ?? (payload.variableKey && overview.time_series.some((item) => item.variable_key === payload.variableKey) ? 'chart' : 'scalar-card'), payload.variableKey, { x: Math.floor((event.clientX - rect.left) / rect.width * 32), y: Math.floor((event.clientY - rect.top) / rect.height * 18) })
    } catch { /* unsupported external drop */ }
  }
  const updateCanvasLayout = (items: Layout[]) => {
    if (!activeSlide) return
    updateSlide(activeSlide.id, (slide) => ({ ...slide, elements: slide.elements.map((element) => {
      const item = items.find((entry) => entry.i === element.id)
      return item ? { ...element, x: item.x, y: item.y, w: item.w, h: item.h } : element
    }) }))
  }
  const duplicateSlide = () => {
    if (!activeSlide) return
    const id = `slide-${Date.now()}`
    const copy = { ...activeSlide, id, name: `${activeSlide.name} 복사본`, kind: 'custom' as ReportSlideKind, repeat: 'none' as const, elements: activeSlide.elements.map((item) => ({ ...item, id: `${item.id}-${Date.now()}` })) }
    const index = slides.indexOf(activeSlide)
    onChange({ ...layout, slides: [...slides.slice(0, index + 1), copy, ...slides.slice(index + 1)] })
    setActiveSlideId(id)
  }
  const addSlide = () => {
    const id = `slide-custom-${Date.now()}`
    const slide: ReportSlideDefinition = { id, name: '사용자 슬라이드', kind: 'custom', repeat: 'none', elements: [] }
    onChange({ ...layout, slides: [...slides, slide] }); setActiveSlideId(id); setSelectedElementId(null)
  }
  const contentElements = (content: ReportContentItem): ReportElementDefinition[] => {
    const bodyType: ReportElementType = content.defaultPresentation === 'card' ? 'scalar-card' : content.defaultPresentation
    const suffix = `${Date.now()}-${Math.random().toString(16).slice(2, 6)}`
    return [
      { id: `content-title-${suffix}`, type: 'title', label: content.title, x: 1, y: 1, w: 30, h: 2, z: 1, binding: { source: 'content', contentId: content.contentId, key: 'title' }, rules: { visibleWhenData: true } },
      { id: `content-body-${suffix}`, type: bodyType, label: content.title, x: 1, y: 4, w: 30, h: 12, z: 2, binding: { source: 'content', contentId: content.contentId, key: 'body' }, rules: { visibleWhenData: true } },
    ]
  }
  const contentSlideId = (contentId: string) => slides.find((slide) => slide.elements.some((element) => element.binding?.contentId === contentId))?.id ?? ''
  const toggleContent = (content: ReportContentItem) => {
    const existingSlideId = contentSlideId(content.contentId)
    if (existingSlideId) {
      const nextSlides = slides.map((slide) => ({ ...slide, elements: slide.elements.filter((element) => element.binding?.contentId !== content.contentId) }))
        .filter((slide) => slide.kind === 'cover' || slide.elements.length > 0)
      onChange({ ...layout, contentMode: 'manual', slides: nextSlides })
      return
    }
    const id = `slide-content-${Date.now()}`
    onChange({ ...layout, contentMode: 'manual', slides: [...slides, { id, name: content.title, kind: 'custom', repeat: 'none', elements: contentElements(content) }] })
    setActiveSlideId(id)
  }
  const moveContentToSlide = (content: ReportContentItem, slideId: string) => {
    const elements = contentElements(content)
    onChange({ ...layout, contentMode: 'manual', slides: slides.map((slide) => ({
      ...slide,
      elements: [...slide.elements.filter((element) => element.binding?.contentId !== content.contentId), ...(slide.id === slideId ? elements : [])],
    })).filter((slide) => slide.kind === 'cover' || slide.id === slideId || slide.elements.length > 0) })
    setActiveSlideId(slideId)
  }
  const deleteSlide = (slideId: string) => {
    const next = slides.filter((slide) => slide.id !== slideId)
    onChange({ ...layout, contentMode: 'manual', slides: next })
    setActiveSlideId(next[0]?.id ?? '')
    setSelectedElementId(null)
  }
  const moveSlide = (index: number, direction: -1 | 1) => {
    const target = index + direction
    if (target < 0 || target >= slides.length) return
    const next = [...slides]; [next[index], next[target]] = [next[target], next[index]]; onChange({ ...layout, slides: next })
  }
  const toggleVariable = (key: string) => {
    const exists = selectedVariables.has(key)
    onChange({ ...layout, variablePlacements: exists ? layout.variablePlacements.filter((item) => item.variableKey !== key) : [...layout.variablePlacements, { variableKey: key, presentation: 'both', order: layout.variablePlacements.length }] })
  }
  const fieldOptions = [
    ['field:report_title', '보고서 제목'], ['field:project_name', '프로젝트명'], ['field:author_stage', '작성자 · 개발단계'], ['field:report_date', '작성날짜'], ['field:verdict', '종합 판정'], ['field:reliability_name', '시뮬레이션 종류'], ['field:review_purpose', '검토 목적'], ['field:review_conditions', '검토 사양 조건'], ['field:review_result', '검토 결과'], ['field:review_conclusion', '검토 결론'],
  ]
  const previewText = (element: ReportElementDefinition) => {
    if (element.binding?.source === 'field') return fieldOptions.find(([value]) => value === `field:${element.binding?.key}`)?.[1] ?? element.label
    if (element.binding?.source === 'variable') return variables.find((item) => item.key === element.binding?.variableKey)?.preview ?? element.label
    if (element.binding?.source === 'static') return element.text ?? element.label
    if (element.binding?.source === 'content') return contents.find((item) => item.contentId === element.binding?.contentId)?.title ?? element.label
    return element.label
  }

  return <section className="report-layout-editor visual-report-editor">
    <div className="report-layout-fields">
      <label><span>레이아웃 이름</span><input value={layout.name} onChange={(event) => onChange({ ...layout, name: event.target.value })} /></label>
      <label><span>출력 원본</span><select value={layout.templateSource ?? 'native'} onChange={(event) => onChange({ ...layout, templateSource: event.target.value as 'native' | 'pptx_upload' })}><option value="native">시각적 레이아웃</option><option value="pptx_upload">업로드 PPTX</option></select></label>
      <label><span>강조색</span><input type="color" value={`#${layout.accentColor}`} onChange={(event) => onChange({ ...layout, accentColor: event.target.value.slice(1).toUpperCase() })} /></label>
      <label className="wide"><span>설명</span><input value={layout.description} onChange={(event) => onChange({ ...layout, description: event.target.value })} /></label>
    </div>
    {layout.templateSource !== 'pptx_upload' && <div className="report-master-toolbar">
      <strong>슬라이드 마스터</strong><small>새 슬라이드와 마스터 사용 슬라이드에 공통 적용됩니다.</small>
      <label><span>마스터 디자인</span><select aria-label="슬라이드 마스터 디자인" value={master.design} onChange={(event) => onChange({ ...layout, slideMaster: { ...master, design: event.target.value as typeof master.design } })}><option value="plain">배경만</option><option value="frame">프레임</option><option value="header-band">상단 띠</option><option value="split">분할 패널</option></select></label>
      <label><span>배경색</span><input aria-label="슬라이드 마스터 배경색" type="color" value={`#${master.backgroundColor}`} onChange={(event) => onChange({ ...layout, slideMaster: { ...master, backgroundColor: event.target.value.slice(1).toUpperCase() } })} /></label>
      <label><span>디자인 색상</span><input aria-label="슬라이드 마스터 디자인 색상" type="color" value={`#${master.accentColor}`} onChange={(event) => onChange({ ...layout, slideMaster: { ...master, accentColor: event.target.value.slice(1).toUpperCase() } })} /></label>
      <button onClick={() => onChange({ ...layout, slides: slides.map((slide) => ({ ...slide, style: { ...slide.style, useMaster: true } })) })}>전체 슬라이드에 적용</button>
    </div>}

    {layout.templateSource === 'pptx_upload' ? <div className="pptx-template-editor">
      <aside className="report-slide-list"><header><strong>업로드 템플릿</strong><label><Upload /> PPTX 추가<input type="file" accept=".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation" onChange={(event) => { const file = event.target.files?.[0]; if (file) onTemplateUpload(file); event.currentTarget.value = '' }} /></label></header>{templates.map((template) => <button key={template.id} className={template.id === layout.templateAssetId ? 'active' : ''} onClick={() => { onChange({ ...layout, templateAssetId: template.id, templateBindings: {} }); setTemplateSlide(1) }}><span>{template.name}</span><small>{template.slide_count}장 · 태그 {template.definition.placeholders.length}개</small></button>)}</aside>
      <div className="pptx-preview-column"><div className="pptx-slide-tabs">{Array.from({ length: activeTemplate?.slide_count ?? 0 }, (_, index) => <button key={index} className={templateSlide === index + 1 ? 'active' : ''} onClick={() => setTemplateSlide(index + 1)}>{index + 1}</button>)}</div><div className="pptx-placeholder-canvas">{activeTemplate?.definition.placeholders.filter((item) => item.slideIndex === templateSlide).map((placeholder) => <div key={placeholder.id} className={`pptx-placeholder ${placeholder.kind}`} style={{ left: `${placeholder.x * 100}%`, top: `${placeholder.y * 100}%`, width: `${placeholder.w * 100}%`, height: `${placeholder.h * 100}%` }}><b>{placeholder.kind.toUpperCase()}</b><span>{placeholder.token}</span></div>)}{activeTemplate && !activeTemplate.definition.placeholders.some((item) => item.slideIndex === templateSlide) && <p>이 슬라이드에는 인식된 태그가 없습니다.</p>}</div></div>
      <aside className="report-properties"><header><strong>태그 변수 연결</strong><small>PowerPoint의 도형 이름 또는 &#123;&#123;태그&#125;&#125;를 연결합니다.</small></header>{activeTemplate?.definition.placeholders.map((placeholder) => <label key={placeholder.id}><span>{placeholder.slideIndex}쪽 · {placeholder.shapeName}</span><code>{placeholder.token}</code><select value={layout.templateBindings?.[placeholder.id] ?? placeholder.token} onChange={(event) => onChange({ ...layout, templateBindings: { ...layout.templateBindings, [placeholder.id]: event.target.value } })}><option value={placeholder.token}>태그 기본 연결 · {placeholder.token}</option>{fieldOptions.filter(([value]) => value !== placeholder.token).map(([value, label]) => <option key={value} value={value}>{label}</option>)}{variables.map((variable) => <option key={variable.key} value={`variable:${variable.key}`}>{variable.name}</option>)}</select></label>)}{activeTemplate && <button className="danger" onClick={() => onTemplateDelete(activeTemplate.id)}><Trash2 /> 템플릿 삭제</button>}</aside>
    </div> : <>
      <div className="report-designer-toolbar"><span>32 × 18 그리드 · 16:9</span><button onClick={addSlide}><Plus /> 빈 슬라이드</button><button onClick={duplicateSlide} disabled={!activeSlide}><Plus /> 슬라이드 복제</button><label className="media-toggle"><input type="checkbox" checked={layout.includeMedia} onChange={(event) => onChange({ ...layout, includeMedia: event.target.checked })} /> 미디어 포함</label></div>
      {activeSlide && <div className="report-slide-style-toolbar"><label><input aria-label="현재 슬라이드에 마스터 사용" type="checkbox" checked={usesMaster} onChange={(event) => updateSlide(activeSlide.id, (slide) => ({ ...slide, style: { ...slide.style, useMaster: event.target.checked } }))} /> 현재 슬라이드에 마스터 사용</label>{!usesMaster && <><label><span>디자인</span><select aria-label="현재 슬라이드 디자인" value={activeSlideStyle.design} onChange={(event) => updateSlide(activeSlide.id, (slide) => ({ ...slide, style: { ...slide.style, useMaster: false, design: event.target.value as typeof master.design } }))}><option value="plain">배경만</option><option value="frame">프레임</option><option value="header-band">상단 띠</option><option value="split">분할 패널</option></select></label><label><span>배경색</span><input aria-label="현재 슬라이드 배경색" type="color" value={`#${activeSlideStyle.backgroundColor}`} onChange={(event) => updateSlide(activeSlide.id, (slide) => ({ ...slide, style: { ...slide.style, useMaster: false, backgroundColor: event.target.value.slice(1).toUpperCase() } }))} /></label><label><span>디자인 색상</span><input aria-label="현재 슬라이드 디자인 색상" type="color" value={`#${activeSlideStyle.accentColor}`} onChange={(event) => updateSlide(activeSlide.id, (slide) => ({ ...slide, style: { ...slide.style, useMaster: false, accentColor: event.target.value.slice(1).toUpperCase() } }))} /></label></>}</div>}
      {contents.length > 0 && <section className="report-content-palette"><header><strong>보고서 콘텐츠</strong><small>포함 여부와 배치 슬라이드를 선택합니다.</small></header>{contents.map((content) => { const assignedSlideId = contentSlideId(content.contentId); return <div key={content.contentId}><label><input type="checkbox" checked={Boolean(assignedSlideId)} onChange={() => toggleContent(content)} /><span>{content.title}</span><b>{content.defaultPresentation}</b></label><select aria-label={`${content.title} 배치 슬라이드`} value={assignedSlideId} disabled={!assignedSlideId} onChange={(event) => moveContentToSlide(content, event.target.value)}>{slides.filter((slide) => slide.kind !== 'cover').map((slide, index) => <option key={slide.id} value={slide.id}>{index + 1} · {slide.name}</option>)}</select></div> })}</section>}
      <div className="report-designer-grid">
        <aside className="report-slide-list"><header><strong>슬라이드</strong><small>위아래로 출력 순서를 바꿉니다.</small></header>{slides.map((slide, index) => <div key={slide.id} className={slide.id === activeSlide?.id ? 'active' : ''} onClick={() => { setActiveSlideId(slide.id); setSelectedElementId(null) }}><span>{String(index + 1).padStart(2, '0')}</span><button>{slide.name}<small>{slide.kind}{slide.repeat !== 'none' ? ' · 반복' : ''}</small></button><nav><button onClick={(event) => { event.stopPropagation(); moveSlide(index, -1) }} disabled={index === 0}>↑</button><button onClick={(event) => { event.stopPropagation(); moveSlide(index, 1) }} disabled={index === slides.length - 1}>↓</button>{slide.kind !== 'cover' && <button aria-label={`${slide.name} 슬라이드 삭제`} onClick={(event) => { event.stopPropagation(); deleteSlide(slide.id) }}><Trash2 /></button>}</nav></div>)}</aside>
        <div className="report-canvas-column"><div className="report-widget-palette">{(['title', 'text', 'verdict', 'scalar-card', 'chart', 'table', 'image'] as ReportElementType[]).map((type) => <button key={type} draggable onDragStart={(event) => event.dataTransfer.setData('application/json', JSON.stringify({ type }))} onClick={() => addElement(type)}>{({ title: '제목', text: '텍스트 상자', verdict: '판정', 'scalar-card': '결과 카드', chart: '차트', table: '표', image: '이미지' } as Record<ReportElementType, string>)[type]}</button>)}</div>{activeSlide && <div className={`report-slide-canvas design-${activeSlideStyle.design}`} style={{ '--slide-background': `#${activeSlideStyle.backgroundColor}`, '--slide-accent': `#${activeSlideStyle.accentColor}` } as CSSProperties} onDragOver={(event) => event.preventDefault()} onDrop={handleDrop}><div className="report-slide-design"/><ResponsiveGridLayout className="report-slide-layout" layouts={{ lg: activeSlide.elements.map((item) => ({ i: item.id, x: item.x, y: item.y, w: item.w, h: item.h })) }} breakpoints={{ lg: 0 }} cols={{ lg: 32 }} rowHeight={20} margin={[0, 0]} containerPadding={[0, 0]} maxRows={18} compactType={null} preventCollision isDraggable isResizable onLayoutChange={updateCanvasLayout}>{activeSlide.elements.map((element) => { const directText = ['title', 'text'].includes(element.type) && element.binding?.source === 'static'; return <div key={element.id} className={`report-slide-widget ${element.type} ${directText ? 'direct-text' : ''} ${selectedElementId === element.id ? 'selected' : ''}`} style={{ color: element.style?.color, backgroundColor: element.style?.fill, textAlign: element.style?.align, fontSize: element.style?.fontSize ? `${element.style.fontSize}px` : undefined }} onMouseDown={() => setSelectedElementId(element.id)} onDoubleClick={(event) => { if (!directText) return; event.stopPropagation(); setSelectedElementId(element.id); setEditingTextElementId(element.id) }}>{!directText && <b>{element.label}</b>}{editingTextElementId === element.id ? <textarea aria-label="슬라이드 텍스트 직접 편집" autoFocus value={element.text ?? element.label} onChange={(event) => updateElementById(element.id, { text: event.target.value })} onMouseDown={(event) => event.stopPropagation()} onBlur={() => setEditingTextElementId(null)} /> : <span className={directText ? 'direct-text-content' : ''}>{previewText(element)}</span>}{element.binding?.variableKey && <code>{element.binding.variableKey}</code>}</div> })}</ResponsiveGridLayout></div>}</div>
        <aside className="report-properties"><header><strong>속성 · 변수</strong><small>변수를 끌어 캔버스에 놓거나 샘플값을 미리 볼 수 있습니다.</small></header>{selectedElement ? <div className="report-element-properties"><label><span>표시 이름</span><input value={selectedElement.label} onChange={(event) => updateElement({ label: event.target.value })} /></label>{['title', 'text'].includes(selectedElement.type) && selectedElement.binding?.source === 'static' && <label><span>텍스트 내용</span><textarea aria-label="텍스트 상자 내용" value={selectedElement.text ?? selectedElement.label} onChange={(event) => updateElement({ text: event.target.value })} /></label>}<label><span>위젯 형식</span><select value={selectedElement.type} onChange={(event) => updateElement({ type: event.target.value as ReportElementType })}>{(['title', 'text', 'verdict', 'scalar-card', 'chart', 'table', 'image'] as ReportElementType[]).map((type) => <option key={type}>{type}</option>)}</select></label><label><span>데이터 연결</span><select value={selectedElement.binding?.source === 'variable' ? `variable:${selectedElement.binding.variableKey}` : selectedElement.binding?.source === 'field' ? `field:${selectedElement.binding.key}` : 'static:'} onChange={(event) => { const [source, key] = event.target.value.split(':'); updateElement({ binding: source === 'variable' ? { source: 'variable', key, variableKey: key } : source === 'field' ? { source: 'field', key } : { source: 'static' } }) }}><option value="static:">고정 텍스트</option>{fieldOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}{variables.map((variable) => <option key={variable.key} value={`variable:${variable.key}`}>{variable.name} · {variable.kind === 'series' ? '시계열' : '정량'} · {variable.unit}</option>)}</select></label>{assignedVariable && <div className="report-binding-preview"><span><b>{assignedVariable.name}</b><em>{assignedVariable.kind === 'series' ? '시계열' : '정량'} · {assignedVariable.unit}</em></span><code>{assignedVariable.key}</code><strong>{assignedVariable.preview}</strong></div>}<div className="report-property-row"><label><span>글자 크기</span><input type="number" min="7" max="40" value={selectedElement.style?.fontSize ?? 10} onChange={(event) => updateElement({ style: { fontSize: Number(event.target.value) } })} /></label><label><span>정렬</span><select value={selectedElement.style?.align ?? 'left'} onChange={(event) => updateElement({ style: { align: event.target.value as 'left' | 'center' | 'right' } })}><option value="left">왼쪽</option><option value="center">가운데</option><option value="right">오른쪽</option></select></label></div><div className="report-property-row"><label><span>글자색</span><input type="color" value={selectedElement.style?.color ?? '#172033'} onChange={(event) => updateElement({ style: { color: event.target.value } })} /></label><label><span>채우기</span><input type="color" value={selectedElement.style?.fill ?? '#FFFFFF'} onChange={(event) => updateElement({ style: { fill: event.target.value } })} /></label></div><button className="danger" onClick={() => { updateSlide(activeSlide.id, (slide) => ({ ...slide, elements: slide.elements.filter((item) => item.id !== selectedElement.id) })); setSelectedElementId(null) }}><Trash2 /> 위젯 삭제</button></div> : <p className="report-empty-properties">캔버스의 위젯을 선택하면 데이터와 스타일을 편집할 수 있습니다.</p>}<div className="report-variable-palette"><strong>변수 카탈로그 · 현재 값 미리보기</strong>{variables.map((variable) => { const placement = selectedVariables.get(variable.key); return <div key={variable.key} draggable onDragStart={(event) => event.dataTransfer.setData('application/json', JSON.stringify({ variableKey: variable.key }))}><label><input type="checkbox" checked={Boolean(placement)} onChange={() => toggleVariable(variable.key)} /><span>{variable.name}</span><b>{variable.kind === 'series' ? '시계열' : '정량'} · {variable.unit}</b></label><code>{variable.key}</code><small>{variable.preview}</small>{placement && <select value={placement.presentation} onChange={(event) => onChange({ ...layout, variablePlacements: layout.variablePlacements.map((item) => item.variableKey === variable.key ? { ...item, presentation: event.target.value as 'chart' | 'table' | 'both' } : item) })}><option value="both">차트+표</option><option value="chart">차트</option><option value="table">표</option></select>}</div>})}</div></aside>
      </div>
    </>}
    <footer><button onClick={onSave}><Save /> 현재 레이아웃 새 버전 저장</button><button onClick={onSaveAs}><Plus /> 다른 이름으로 저장</button><button className="danger" disabled={isSystem} title={isSystem ? '기본 레이아웃은 삭제할 수 없습니다.' : ''} onClick={onDelete}><Trash2 /> 삭제</button></footer>
  </section>
}

function FeatureExampleGallery({ onOpen }: { onOpen: (example: FeatureExample) => Promise<void> }) {
  const [items, setItems] = useState<FeatureExample[]>([])
  const [category, setCategory] = useState('전체')
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  useEffect(() => { api.featureExamples().then(setItems).catch((reason) => setError(reason instanceof Error ? reason.message : '예제 목록을 불러오지 못했습니다.')) }, [])
  const categories = ['전체', ...Array.from(new Set(items.map((item) => item.category)))]
  const filtered = items.filter((item) => (category === '전체' || item.category === category) && (!query || `${item.title} ${item.summary} ${item.features.join(' ')}`.toLowerCase().includes(query.toLowerCase())))
  const profileEntries = (profile: FeatureExample['data_profile']) => ([['Run', profile.runs], ['수치', profile.scalars], ['시계열', profile.series], ['곡선', profile.curves], ['미디어', profile.media], ['검토', profile.reviews]] as const).filter(([, value]) => value > 0)
  return <section className="example-gallery">
    <header><div><span>FEATURE SHOWCASE</span><h1>기능 예제 갤러리</h1><p>기존 데이터를 건드리지 않고, 보고 싶은 기능과 상태를 골라 바로 체험하세요.</p></div><aside><strong>{items.length || '-'}개</strong><small>독립 시나리오</small></aside></header>
    <div className="example-guide"><div><Play /><span><strong>추천 순서</strong> Run 비교 → 신뢰도 정상/경고 → 검토 → 다중 결과형 → PPT 편집</span></div><p>각 카드의 ‘확인할 것’은 그 예제에서 재현되는 기대 결과입니다. WARN·NO DATA·BLOCKED도 의도된 정상 예제입니다.</p></div>
    <div className="example-filters"><label><Search /><input aria-label="예제 검색" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="기능, 상태, 데이터형 검색" /></label><nav>{categories.map((item) => <button key={item} className={category === item ? 'active' : ''} onClick={() => setCategory(item)}>{item}</button>)}</nav></div>
    {error ? <div className="portfolio-state error"><AlertTriangle />{error}</div> : !items.length ? <div className="portfolio-state"><LoaderCircle className="spin" />예제를 준비하고 있습니다.</div> : <div className="example-grid">{filtered.map((item) => <article key={item.id} className={`example-card ${item.badge.toLowerCase().replaceAll(' ', '-')}`}>
      <header><span>{String(item.order).padStart(2, '0')} · {item.category}</span><b>{item.badge}</b></header><h2>{item.title}</h2><p>{item.summary}</p>
      <div className="example-tags">{item.features.map((feature) => <span key={feature}>{feature}</span>)}</div>
      {profileEntries(item.data_profile).length > 0 && <div className="example-profile">{profileEntries(item.data_profile).map(([label, value]) => <span key={label}><small>{label}</small><strong>{value}</strong></span>)}</div>}
      <section><strong>확인할 것</strong><ol>{item.checks.map((check) => <li key={check}>{check}</li>)}</ol></section>
      {item.action_hint && <small className="example-action-hint">{item.action_hint}</small>}
      <button onClick={() => void onOpen(item)}><Play /> 이 예제 열기</button>
    </article>)}</div>}
    {items.length > 0 && filtered.length === 0 && <div className="portfolio-empty"><Search /><h2>조건에 맞는 예제가 없습니다.</h2><button onClick={() => { setCategory('전체'); setQuery('') }}>필터 초기화</button></div>}
  </section>
}

function HelpCenter({ onNavigate }: { onNavigate: (page: WorkspacePage) => void }) {
  const scenarios = [
    { title: '새 해석 결과를 등록하고 확인하기', steps: ['해석 데이터에서 프로젝트·의뢰·하중 경우를 선택합니다.', 'CSV 또는 폴더 스키마 기반 결과를 검증 후 적재합니다.', '해석 상세에서 판정, 시간 이력, 위치별 결과를 확인합니다.'], action: 'data' as const, label: '해석 데이터 열기' },
    { title: '폴더 결과를 변수와 대시보드에 연결하기', steps: ['폴더 스키마에서 파일 패턴과 variable_key 매핑을 정의합니다.', '변수 카탈로그에서 같은 variable_key의 표시 이름·단위·기준·허용 위젯을 선언합니다.', '대시보드 편집에서 변수를 위젯에 바인딩합니다. 값은 결과 테이블에서 조회됩니다.'], action: 'schemas' as const, label: '폴더 스키마 열기' },
    { title: '편집 가능한 PPT 보고서 만들기', steps: ['해석 상세의 평가 탭 옆 보고서 내보내기를 누릅니다.', '레이아웃 편집·관리에서 슬라이드를 선택하고 위젯을 이동·크기 조절하거나 변수를 끌어 놓습니다.', '회사 PPTX를 사용하려면 도형 이름 또는 {{variable:key}} 태그를 지정해 업로드하고 변수를 연결합니다.', '레이아웃과 버전을 선택한 뒤 PPTX를 생성합니다.'], action: 'dashboard' as const, label: '해석 상세 열기' },
    { title: '대시보드 레이아웃을 추가·복구하기', steps: ['해석 상세에서 대시보드 편집을 시작합니다.', '위젯을 추가하고 변수·집계·크기·위치를 설정합니다.', '레이아웃을 저장하거나 복제하고, 필요하면 정상 버전을 복구합니다.'], action: 'dashboard' as const, label: '대시보드 편집 열기' },
    { title: '이전 Run과 비교하고 검토 의견 남기기', steps: ['해석 상세에서 Run 비교·검토 탭을 엽니다.', '기준 Run과 대상 Run을 선택해 회귀·개선 및 공통 시계열을 확인합니다.', '데이터 신뢰도에서 출처·카탈로그·단위·검증 경고를 확인합니다.', '변수와 시점을 선택해 북마크·검토 의견을 저장하고 상태를 관리합니다.'], action: 'dashboard' as const, label: 'Run 비교 열기' },
    { title: '예제로 전체 기능 빠르게 둘러보기', steps: ['예제 갤러리에서 확인할 기능이나 상태를 고릅니다.', '카드의 기대 결과와 데이터 구성을 먼저 읽습니다.', '예제 열기로 이동해 확인 목록을 따라 기능을 직접 사용합니다.'], action: 'examples' as const, label: '예제 갤러리 열기' },
  ]
  return <section className="help-center"><header><span>SCENARIO GUIDE</span><h1>VD simulation workbench 사용 도움말</h1><p>하려는 작업을 기준으로 필요한 화면과 데이터 흐름을 안내합니다.</p></header><div className="help-flow"><strong>핵심 데이터 흐름</strong><div><span>폴더 스키마</span><b>→</b><span>결과 테이블</span><b>→</b><span>변수 카탈로그</span><b>→</b><span>대시보드·PPT</span></div><p>변수 카탈로그는 값을 저장하지 않습니다. 결과 테이블의 <code>variable_key</code>를 해석하고 표시하는 계약입니다.</p></div><div className="help-scenarios">{scenarios.map((scenario, index) => <article key={scenario.title}><span>0{index + 1}</span><h2>{scenario.title}</h2><ol>{scenario.steps.map((step) => <li key={step}>{step}</li>)}</ol><button onClick={() => onNavigate(scenario.action)}>{scenario.label}</button></article>)}</div><aside><AlertTriangle /><div><strong>외부 배포 전 확인</strong><p>PostgreSQL 어댑터·Alembic·데이터 검증·로그인·역할 권한·감사·백업 도구가 준비되어 있습니다. 외부 공개 시에는 HTTPS, <code>AUTH_MODE=password</code>, 별도 DB 역할과 복구 시험을 반드시 적용하세요.</p></div></aside></section>
}

function ComparisonWorkspace({ loadCaseId, currentRunId, onContextChange }: { loadCaseId: string; currentRunId: string; onContextChange?: (context: RunComparisonReportContext | null) => void }) {
  const [runs, setRuns] = useState<AnalysisRunSummary[]>([])
  const [baselineRunId, setBaselineRunId] = useState('')
  const [targetRunId, setTargetRunId] = useState(currentRunId)
  const [seriesKey, setSeriesKey] = useState('')
  const [comparison, setComparison] = useState<RunComparison | null>(null)
  const [trust, setTrust] = useState<RunTrust | null>(null)
  const [reviews, setReviews] = useState<ReviewItem[]>([])
  const [busy, setBusy] = useState(true)
  const [message, setMessage] = useState('')
  const [form, setForm] = useState({ title: '', body: '', variableKey: '', timeValue: '', entityType: '' as '' | 'NODE' | 'ELEMENT', entityId: '' })

  useEffect(() => {
    onContextChange?.(null)
    setBusy(true); setMessage(''); setSeriesKey('')
    api.analysisRuns(loadCaseId).then((items) => {
      setRuns(items)
      const target = items.find((item) => item.id === currentRunId) ?? items[0]
      const baseline = items.find((item) => item.id !== target?.id)
      setTargetRunId(target?.id ?? '')
      setBaselineRunId(baseline?.id ?? '')
    }).catch((reason) => setMessage(reason instanceof Error ? reason.message : 'Run 목록을 불러오지 못했습니다.')).finally(() => setBusy(false))
  }, [loadCaseId, currentRunId])

  useEffect(() => {
    if (!baselineRunId || !targetRunId || baselineRunId === targetRunId) return
    setBusy(true); setMessage('')
    Promise.all([
      api.runComparison(loadCaseId, baselineRunId, targetRunId, seriesKey || undefined),
      api.runTrust(targetRunId),
      api.reviewItems(targetRunId),
    ]).then(([comparisonData, trustData, reviewData]) => {
      setComparison(comparisonData); setTrust(trustData); setReviews(reviewData)
      if (!seriesKey && comparisonData.time_series) setSeriesKey(comparisonData.time_series.variable_key)
      setForm((current) => ({ ...current, variableKey: current.variableKey || comparisonData.scalar_comparison[0]?.variable_key || '' }))
    }).catch((reason) => setMessage(reason instanceof Error ? reason.message : '비교 데이터를 불러오지 못했습니다.')).finally(() => setBusy(false))
  }, [loadCaseId, baselineRunId, targetRunId, seriesKey])

  useEffect(() => {
    if (!comparison || !trust || !baselineRunId || !targetRunId) return
    onContextChange?.({ loadCaseId, baselineRunId, targetRunId, comparison, trust, reviews })
  }, [loadCaseId, baselineRunId, targetRunId, comparison, trust, reviews, onContextChange])

  const submitReview = async (event: FormEvent) => {
    event.preventDefault()
    if (!form.title.trim() || !form.body.trim()) return
    try {
      const created = await api.createReviewItem(targetRunId, {
        title: form.title.trim(), body: form.body.trim(), variable_key: form.variableKey || null,
        time_value: form.timeValue ? Number(form.timeValue) : null,
        entity_type: form.entityType || null, entity_id: form.entityId.trim() || null,
        review_status: 'OPEN', created_by: '대시보드 검토자',
      })
      setReviews((items) => [created, ...items])
      setForm((current) => ({ ...current, title: '', body: '', timeValue: '', entityType: '', entityId: '' }))
      setMessage('검토 의견을 결과 문맥에 저장했습니다.')
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : '검토 의견을 저장하지 못했습니다.') }
  }

  const updateReviewStatus = async (item: ReviewItem, status: ReviewItem['review_status']) => {
    try {
      const updated = await api.updateReviewItem(item.id, status)
      setReviews((items) => items.map((entry) => entry.id === updated.id ? updated : entry))
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : '검토 상태를 변경하지 못했습니다.') }
  }

  const runLabel = (run: AnalysisRunSummary) => `Run ${run.run_no} · ${run.overall_verdict} · ${run.completed_at ? new Date(run.completed_at).toLocaleDateString('ko-KR') : run.status}`
  const valueLabel = (value: number | null, unit: string | null) => value == null ? '-' : `${value.toFixed(Math.abs(value) >= 100 ? 0 : 2)} ${unit ?? ''}`.trim()
  const changeLabel: Record<string, string> = { REGRESSION: '회귀', IMPROVED: '개선', UNCHANGED: '유지', ADDED: '추가', REMOVED: '제거', NOT_COMPARABLE: '비교 불가' }

  if (busy && runs.length === 0) return <div className="comparison-state"><LoaderCircle className="spin" /> Run 비교 데이터를 준비하고 있습니다.</div>
  if (runs.length < 2) return <div className="comparison-state"><AlertTriangle /><strong>비교할 Run이 하나뿐입니다.</strong><span>같은 하중 경우에 결과를 한 번 더 적재하면 기준 Run과 비교할 수 있습니다.</span></div>

  return <section className="comparison-workspace">
    <header className="comparison-hero"><div><span>RUN DIFF · TRUST · REVIEW</span><h2>Run 비교·검토</h2><p>기존 판정은 유지하고, 선택한 두 Run의 변화와 데이터 근거를 별도로 확인합니다.</p></div><div className="run-pickers"><label><span>기준 Run</span><select value={baselineRunId} onChange={(event) => { setBaselineRunId(event.target.value); setSeriesKey('') }}>{runs.filter((item) => item.id !== targetRunId).map((item) => <option key={item.id} value={item.id}>{runLabel(item)}</option>)}</select></label><b>→</b><label><span>대상 Run</span><select value={targetRunId} onChange={(event) => { setTargetRunId(event.target.value); setSeriesKey('') }}>{runs.filter((item) => item.id !== baselineRunId).map((item) => <option key={item.id} value={item.id}>{runLabel(item)}</option>)}</select></label></div></header>
    {message && <div className="comparison-message">{message}</div>}
    {comparison && <>
      <div className="comparison-kpis"><article className={comparison.summary.regression ? 'danger' : ''}><span>REGRESSION</span><strong>{comparison.summary.regression}</strong><small>PASS → FAIL</small></article><article className="positive"><span>IMPROVED</span><strong>{comparison.summary.improved}</strong><small>FAIL → PASS</small></article><article><span>COMPARABLE</span><strong>{comparison.summary.comparable}</strong><small>동일 키·단위</small></article><article className={`trust-${trust?.trust_status.toLowerCase()}`}><span>DATA TRUST</span><strong>{trust?.trust_status ?? '-'}</strong><small>{trust?.is_latest ? '최신 Run' : '과거 Run'} · {trust?.age_days ?? '-'}일</small></article></div>
      <div className="comparison-grid">
        <article className="comparison-card scalar-diff"><header><div><span>SCALAR DIFFERENCE</span><h3>정량 결과 변화</h3></div><small>Δ = 대상 − 기준</small></header><div className="comparison-table"><div className="head"><span>변수</span><span>기준</span><span>대상</span><span>차이</span><span>판정 변화</span></div>{comparison.scalar_comparison.map((item) => <div key={item.variable_key}><span><strong>{item.display_name}</strong><code>{item.variable_key}</code></span><span>{valueLabel(item.baseline_value, item.unit)}<small>{item.baseline_verdict ?? '-'}</small></span><span>{valueLabel(item.target_value, item.unit)}<small>{item.target_verdict ?? '-'}</small></span><span>{item.delta == null ? '-' : `${item.delta >= 0 ? '+' : ''}${item.delta.toFixed(2)}`}<small>{item.delta_percent == null ? '' : `${item.delta_percent >= 0 ? '+' : ''}${item.delta_percent.toFixed(1)}%`}</small></span><span><b className={`change-${item.change.toLowerCase()}`}>{changeLabel[item.change]}</b></span></div>)}</div></article>
        <article className="comparison-card series-diff"><header><div><span>SYNCED TIME SERIES</span><h3>공통 시계열 비교</h3></div><select value={seriesKey} onChange={(event) => setSeriesKey(event.target.value)}>{comparison.available_series.map((item) => <option key={item.variable_key} value={item.variable_key}>{item.display_name}</option>)}</select></header><div className="comparison-chart">{comparison.time_series ? <ResponsiveContainer width="100%" height="100%"><LineChart data={comparison.time_series.points} margin={{ top: 12, right: 18, left: -10, bottom: 2 }}><CartesianGrid vertical={false} stroke="#26394c" strokeDasharray="3 3"/><XAxis dataKey="time_value" tick={{ fill: '#70889d', fontSize: 10.8 }} axisLine={false}/><YAxis tick={{ fill: '#70889d', fontSize: 10.8 }} axisLine={false}/><Tooltip contentStyle={{ background: '#102235', border: '1px solid #2d465c', borderRadius: 8 }}/><Legend/><Line type="monotone" dataKey="baseline_value" name={`기준 Run ${comparison.baseline_run.run_no}`} stroke="#61d4ff" dot={false} strokeWidth={2}/><Line type="monotone" dataKey="target_value" name={`대상 Run ${comparison.target_run.run_no}`} stroke="#ff9948" dot={false} strokeWidth={2}/></LineChart></ResponsiveContainer> : <div className="comparison-empty">공통 시계열 변수가 없습니다.</div>}</div></article>
        {trust && <aside className="trust-card"><header><div><span>DATA TRUST</span><h3>대상 Run 신뢰도</h3></div><b className={`trust-${trust.trust_status.toLowerCase()}`}>{trust.trust_status}</b></header><div className="trust-source"><span>출처</span><strong>{trust.metadata?.source_name ?? trust.import_job?.source_folder ?? '추적 정보 없음'}</strong><code>{trust.metadata?.source_checksum ? trust.metadata.source_checksum.slice(0, 16) : 'NO CHECKSUM'}</code><small>{trust.metadata?.parser_version ?? '-'}{trust.metadata?.schema_id ? ` · ${trust.metadata.schema_id} v${trust.metadata.schema_version}` : ''}</small></div><div className="trust-counts"><span>정량 <b>{trust.counts.scalar}</b></span><span>시계열 <b>{trust.counts.time_series}</b></span><span>커브 <b>{trust.counts.curve}</b></span><span>미디어 <b>{trust.counts.media}</b></span></div><div className="trust-checks">{trust.checks.map((check) => <div key={check.code}><i className={check.status.toLowerCase()}>{check.status === 'PASS' ? <Check /> : <AlertTriangle />}</i><span><strong>{check.label}</strong><small>{check.detail}</small></span></div>)}</div></aside>}
      </div>
      <article className="review-card"><header><div><span>PINNED REVIEW</span><h3>결과 북마크·검토 의견</h3><p>대상 Run의 변수·시점·엔티티 문맥에 의견을 고정합니다.</p></div><strong>{reviews.filter((item) => item.review_status !== 'RESOLVED').length} OPEN</strong></header><div className="review-layout"><form onSubmit={submitReview}><label><span>제목</span><input value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} placeholder="예: 하단 엣지 회귀 원인 확인" required /></label><label><span>결과 변수</span><select value={form.variableKey} onChange={(event) => setForm({ ...form, variableKey: event.target.value })}><option value="">Run 전체</option>{comparison.scalar_comparison.map((item) => <option key={item.variable_key} value={item.variable_key}>{item.display_name}</option>)}</select></label><div className="review-context"><label><span>시점</span><input type="number" step="any" value={form.timeValue} onChange={(event) => setForm({ ...form, timeValue: event.target.value })} placeholder="선택 사항" /></label><label><span>엔티티</span><select value={form.entityType} onChange={(event) => setForm({ ...form, entityType: event.target.value as '' | 'NODE' | 'ELEMENT' })}><option value="">없음</option><option value="NODE">NODE</option><option value="ELEMENT">ELEMENT</option></select></label><label><span>ID</span><input value={form.entityId} onChange={(event) => setForm({ ...form, entityId: event.target.value })} disabled={!form.entityType} /></label></div><label><span>검토 의견</span><textarea value={form.body} onChange={(event) => setForm({ ...form, body: event.target.value })} placeholder="관찰 내용과 후속 조치를 기록하세요." required /></label><button className="primary-button" type="submit"><Plus /> 북마크 저장</button></form><div className="review-list">{reviews.length ? reviews.map((item) => <article key={item.id}><header><span className={`review-status ${item.review_status.toLowerCase()}`}>{item.review_status}</span><small>{new Date(item.updated_at).toLocaleString('ko-KR')}</small></header><strong>{item.title}</strong><p>{item.body}</p><div><code>{item.variable_key ?? 'RUN'}</code>{item.time_value != null && <span>t={item.time_value}</span>}{item.entity_type && <span>{item.entity_type} {item.entity_id}</span>}</div><footer><span>{item.created_by}</span><select value={item.review_status} onChange={(event) => void updateReviewStatus(item, event.target.value as ReviewItem['review_status'])}><option value="OPEN">OPEN</option><option value="IN_REVIEW">IN REVIEW</option><option value="RESOLVED">RESOLVED</option></select></footer></article>) : <div className="comparison-empty">아직 저장된 검토 의견이 없습니다.</div>}</div></div></article>
    </>}
  </section>
}

function DataWorkspace({ projects, initialProjectId, onDataChanged, onOpenAnalysis, onOpenIntake }: { projects: Project[]; initialProjectId: string; onDataChanged: () => Promise<void>; onOpenAnalysis: (projectId: string, requestId: string, loadCaseId: string) => Promise<void>; onOpenIntake: () => void }) {
  const [managedProjects, setManagedProjects] = useState(projects)
  const [projectId, setProjectId] = useState(initialProjectId || projects[0]?.id || '')
  const [requests, setRequests] = useState<AnalysisRequest[]>([])
  const [requestId, setRequestId] = useState('')
  const [loadCases, setLoadCases] = useState<LoadCase[]>([])
  const [loadCaseId, setLoadCaseId] = useState('')
  const [message, setMessage] = useState('')
  const [formError, setFormError] = useState('')
  const [busy, setBusy] = useState(false)
  const [projectForm, setProjectForm] = useState({ name: '', product_name: '', manufacturer: '', display_size_inch: 65 as number | null, description: '' })
  const [caseForm, setCaseForm] = useState({ name: '', analysis_type: 'DROP' as 'DROP' | 'SIDE_CLAMP', primary: '800', secondary: 'BOTTOM' })
  const [resultFile, setResultFile] = useState<{ filename: string; content: string } | null>(null)
  const [resultAuthor, setResultAuthor] = useState('해석 담당자')
  const [importPreview, setImportPreview] = useState<Awaited<ReturnType<typeof api.importResults>> | null>(null)
  const [imported, setImported] = useState(false)

  useEffect(() => {
    setManagedProjects(projects)
    if (!projectId && projects[0]) setProjectId(initialProjectId || projects[0].id)
  }, [projects, initialProjectId, projectId])

  useEffect(() => {
    if (!projectId) return
    api.requests(projectId).then((items) => {
      setRequests(items)
      setRequestId((current) => items.some((item) => item.id === current) ? current : items[0]?.id || '')
    }).catch((reason) => setFormError(reason instanceof Error ? reason.message : '의뢰 목록을 불러오지 못했습니다.'))
  }, [projectId])

  useEffect(() => {
    if (!requestId) { setLoadCases([]); return }
    api.loadCases(requestId).then((items) => {
      setLoadCases(items)
      setLoadCaseId((current) => items.some((item) => item.id === current) ? current : items[0]?.id || '')
      setResultFile(null); setImportPreview(null); setImported(false)
    }).catch((reason) => setFormError(reason instanceof Error ? reason.message : '하중 경우 목록을 불러오지 못했습니다.'))
  }, [requestId])

  const complete = async (label: string, action: () => Promise<void>) => {
    setBusy(true); setFormError(''); setMessage('')
    try {
      await action()
      setMessage(`${label} 등록이 완료되었습니다.`)
      await onDataChanged()
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : `${label} 등록에 실패했습니다.`)
    } finally { setBusy(false) }
  }

  const submitProject = (event: FormEvent) => {
    event.preventDefault()
    void complete('프로젝트', async () => {
      const created = await api.createProject(projectForm)
      setManagedProjects((items) => [created, ...items])
      setProjectId(created.id); setRequests([]); setRequestId(''); setLoadCases([])
      setProjectForm({ name: '', product_name: '', manufacturer: '', display_size_inch: 65, description: '' })
    })
  }

  const submitLoadCase = (event: FormEvent) => {
    event.preventDefault()
    if (!requestId) return
    void complete('하중 경우', async () => {
      const parameters: Record<string, string | number | string[]> = caseForm.analysis_type === 'DROP'
        ? { drop_height_mm: Number(caseForm.primary), impact_direction: caseForm.secondary }
        : { clamp_pressure_kpa: Number(caseForm.primary), hold_time_s: Number(caseForm.secondary) || 10 }
      const created = await api.createLoadCase(requestId, { name: caseForm.name, analysis_type: caseForm.analysis_type, parameters })
      setLoadCases((items) => [created, ...items])
      setLoadCaseId(created.id)
      setCaseForm({ name: '', analysis_type: caseForm.analysis_type, primary: caseForm.analysis_type === 'DROP' ? '800' : '25', secondary: caseForm.analysis_type === 'DROP' ? 'BOTTOM' : '10' })
    })
  }

  const selectedRequest = requests.find((item) => item.id === requestId)

  const validateResultFile = async (selected: { filename: string; content: string }) => {
    setResultFile(selected); setBusy(true)
    try {
      setImportPreview(await api.importResults(loadCaseId, { ...selected, author: resultAuthor, validate_only: true }))
    } catch (reason) { setFormError(reason instanceof Error ? reason.message : '결과 파일 검증에 실패했습니다.') }
    finally { setBusy(false) }
  }

  const chooseResultFile = async (file?: File) => {
    setFormError(''); setMessage(''); setImportPreview(null); setImported(false)
    if (!file) { setResultFile(null); return }
    if (!/\.(csv|json)$/i.test(file.name)) { setFormError('CSV 또는 JSON 파일만 선택할 수 있습니다.'); return }
    if (file.size > 4_500_000) { setFormError('파일은 4.5 MB 이하여야 합니다.'); return }
    const selected = { filename: file.name, content: await file.text() }
    await validateResultFile(selected)
  }

  const loadRadiossExample = async () => {
    setFormError(''); setMessage(''); setImportPreview(null); setImported(false); setBusy(true)
    try {
      const response = await fetch('/api/result-import/template/radioss-csv')
      if (!response.ok) throw new Error('Radioss 예제 파일을 불러오지 못했습니다.')
      await validateResultFile({ filename: 'radioss-tv-result-example.csv', content: await response.text() })
    } catch (reason) { setFormError(reason instanceof Error ? reason.message : 'Radioss 예제 검증에 실패했습니다.') }
    finally { setBusy(false) }
  }

  const importTypedFolderExample = async () => {
    if (!loadCaseId) return
    setBusy(true); setFormError(''); setMessage(''); setImported(false)
    try {
      const result = await api.importTypedFolderExample(loadCaseId)
      setImported(true)
      setMessage(`예제 폴더 스키마(${result.schema_id})를 적용해 실수·정수·텍스트 ${result.summary.scalar_count}개, 커브 ${result.summary.curve_count}개, 미디어 ${result.summary.media_count}개를 Run #${result.run_no}로 등록했습니다.`)
      await onDataChanged()
    } catch (reason) { setFormError(reason instanceof Error ? reason.message : '예제 폴더를 등록하지 못했습니다.') }
    finally { setBusy(false) }
  }

  const submitResultImport = async () => {
    if (!resultFile || !loadCaseId) return
    setBusy(true); setFormError(''); setMessage('')
    try {
      const result = await api.importResults(loadCaseId, { ...resultFile, author: resultAuthor, validate_only: false })
      setImportPreview(result); setImported(true)
      setMessage(`Run #${result.run_no} 결과를 등록했습니다. 결과 검토 단계가 시작되었습니다.`)
      await onDataChanged()
    } catch (reason) { setFormError(reason instanceof Error ? reason.message : '해석 결과 등록에 실패했습니다.') }
    finally { setBusy(false) }
  }

  return <section className="data-workspace">
    <header className="data-workspace-head">
      <div><span>OPERATIONS / FILE DATABASE</span><h1>해석 데이터 등록</h1><p>프로젝트와 하중 경우를 구성하고, 완료된 해석 결과를 검증해 DB에 등록합니다.</p></div>
      <div className="data-count"><strong>{managedProjects.length}</strong><span>PROJECTS</span></div>
    </header>

    <div className="data-hierarchy-bar">
      <label><span>1 · 프로젝트</span><select aria-label="등록 프로젝트 선택" value={projectId} onChange={(event) => setProjectId(event.target.value)}>{managedProjects.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.product_name}</option>)}</select></label>
      <i>›</i>
      <label><span>2 · 접수된 의뢰</span><select aria-label="등록 의뢰 선택" value={requestId} onChange={(event) => setRequestId(event.target.value)} disabled={!requests.length}>{requests.length ? requests.map((item) => <option key={item.id} value={item.id}>{item.title}</option>) : <option>의뢰 접수 탭에서 먼저 접수하세요</option>}</select></label>
      <i>›</i>
      <label><span>3 · 하중 경우</span><select aria-label="등록 하중 경우 선택" value={loadCaseId} onChange={(event) => { setLoadCaseId(event.target.value); setResultFile(null); setImportPreview(null); setImported(false) }} disabled={!loadCases.length}>{loadCases.length ? loadCases.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.analysis_type}</option>) : <option value="">하중 경우 없음</option>}</select></label>
    </div>

    {(message || formError) && <div className={`data-message ${formError ? 'error' : ''}`}>{formError ? <AlertTriangle /> : <Check />}{formError || message}</div>}
    <div className="data-intake-handoff"><ClipboardPlus /><span><strong>새 해석 의뢰는 별도 접수 절차를 사용합니다.</strong>외부 시스템 전달·부서장 지시와 작업 시나리오를 기록한 뒤 이 화면에서 하중 경우와 결과를 연결하세요.</span><button onClick={onOpenIntake}>의뢰 접수 열기 <ChevronDown /></button></div>

    <div className="data-form-grid">
      <article className="data-form-card"><header><span>01</span><div><h2>새 프로젝트</h2><p>제품 단위 최상위 분류</p></div></header><form onSubmit={submitProject}>
        <label><span>프로젝트 이름</span><input required value={projectForm.name} onChange={(e) => setProjectForm({ ...projectForm, name: e.target.value })} placeholder="예: 2026 OLED 신뢰성" /></label>
        <label><span>제품 모델명</span><input required value={projectForm.product_name} onChange={(e) => setProjectForm({ ...projectForm, product_name: e.target.value })} placeholder="예: OLED77X26" /></label>
        <label><span>제조사</span><input value={projectForm.manufacturer} onChange={(e) => setProjectForm({ ...projectForm, manufacturer: e.target.value })} placeholder="예: NeoView Display" /></label>
        <label><span>화면 크기 (inch)</span><input type="number" min="1" max="200" value={projectForm.display_size_inch ?? ''} onChange={(e) => setProjectForm({ ...projectForm, display_size_inch: e.target.value ? Number(e.target.value) : null })} /></label>
        <label><span>설명</span><textarea value={projectForm.description} onChange={(e) => setProjectForm({ ...projectForm, description: e.target.value })} placeholder="제품과 해석 목적을 입력하세요." /></label>
        <button className="data-submit" disabled={busy}><Plus /> 프로젝트 등록</button>
      </form></article>

      <article className="data-form-card"><header><span>02</span><div><h2>새 하중 경우</h2><p>{selectedRequest?.title || '접수된 의뢰를 선택하세요'}</p></div></header><form onSubmit={submitLoadCase}>
        <label><span>하중 경우 이름</span><input required disabled={!requestId} value={caseForm.name} onChange={(e) => setCaseForm({ ...caseForm, name: e.target.value })} placeholder="예: Bottom Drop 800 mm" /></label>
        <label><span>해석 유형</span><select value={caseForm.analysis_type} onChange={(e) => { const type = e.target.value as 'DROP' | 'SIDE_CLAMP'; setCaseForm({ name: caseForm.name, analysis_type: type, primary: type === 'DROP' ? '800' : '25', secondary: type === 'DROP' ? 'BOTTOM' : '10' }) }}><option value="DROP">포장 낙하 (DROP)</option><option value="SIDE_CLAMP">Side Clamp</option></select></label>
        <div className="data-form-row"><label><span>{caseForm.analysis_type === 'DROP' ? '낙하 높이 (mm)' : '압력 (kPa)'}</span><input type="number" min="0" required value={caseForm.primary} onChange={(e) => setCaseForm({ ...caseForm, primary: e.target.value })} /></label><label><span>{caseForm.analysis_type === 'DROP' ? '충격 방향' : '유지 시간 (s)'}</span>{caseForm.analysis_type === 'DROP' ? <select value={caseForm.secondary} onChange={(e) => setCaseForm({ ...caseForm, secondary: e.target.value })}><option>BOTTOM</option><option>TOP</option><option>LEFT</option><option>RIGHT</option></select> : <input type="number" min="0" value={caseForm.secondary} onChange={(e) => setCaseForm({ ...caseForm, secondary: e.target.value })} />}</label></div>
        <button className="data-submit" disabled={busy || !requestId}><Plus /> 하중 경우 등록</button>
      </form></article>
    </div>

    <article className="result-import-card">
      <header>
        <div><span>03 · RESULT INGESTION</span><h2>해석 결과 가져오기</h2><p>선택한 하중 경우에 CSV 또는 JSON 결과를 검증한 뒤 새 Analysis Run으로 저장합니다.</p></div>
        <div className="template-links"><button onClick={() => void loadRadiossExample()} disabled={!loadCaseId || busy}><Play /> 예제로 검증</button><button onClick={() => void importTypedFolderExample()} disabled={!loadCaseId || busy}><Database /> 형식별 폴더 예제 등록</button><a href="/api/result-import/template/radioss-csv" download><Download /> Radioss CSV</a><a href="/api/result-import/template/csv" download><Download /> 요약 CSV</a><a href="/api/result-import/template/json" download><Download /> JSON</a></div>
      </header>
      <div className="result-import-body">
        <div className="result-drop-zone">
          <input id="result-file" type="file" accept=".csv,.json,text/csv,application/json" onChange={(event) => void chooseResultFile(event.target.files?.[0])} disabled={!loadCaseId || busy} />
          <label htmlFor="result-file"><Upload /><strong>{resultFile?.filename || '결과 파일 선택'}</strong><span>CSV / JSON · 최대 4.5 MB · 선택 즉시 사전 검증</span></label>
          <div className="result-import-meta"><label><span>수행자</span><input value={resultAuthor} onChange={(event) => setResultAuthor(event.target.value)} /></label><div><span>등록 대상</span><strong>{loadCases.find((item) => item.id === loadCaseId)?.name || '하중 경우를 선택하세요'}</strong></div></div>
        </div>
        <div className="result-preview">
          {importPreview ? <>
            <div className="result-preview-head"><div><span>{imported ? 'IMPORTED' : 'VALIDATED'}</span><strong>{importPreview.overall_verdict}</strong></div><small>{importPreview.filename}</small></div>
            <div className="result-preview-kpis"><div><strong>{importPreview.node_count}</strong><span>노드 행</span></div><div><strong>{importPreview.element_count}</strong><span>요소 행</span></div><div><strong>{importPreview.frame_count}</strong><span>프레임</span></div><div><strong>{importPreview.scalar_count}</strong><span>파생 결과</span></div><div className={importPreview.fail_count ? 'fail' : ''}><strong>{importPreview.fail_count}</strong><span>FAIL 항목</span></div></div>
            <div className="result-preview-list">{importPreview.results.map((item) => <div key={item.variable_key}><span>{item.display_name}</span><strong>{item.value.toFixed(2)} {item.unit}</strong><b className={item.verdict.toLowerCase()}>{item.verdict}</b></div>)}</div>
            {importPreview.warnings.length > 0 && <div className="result-warnings">{importPreview.warnings.map((warning) => <span key={warning}><AlertTriangle />{warning}</span>)}</div>}
          </> : <div className="result-preview-empty"><Database /><strong>검증 결과가 여기에 표시됩니다.</strong><span>허용 변수 외 데이터와 중복 시점은 저장 전에 차단됩니다.</span></div>}
        </div>
      </div>
      <footer><div><strong>판정 규칙</strong><span>Open Cell 응력 및 Chassis Rear 영구변형 모두 값이 기준 이상이면 FAIL</span></div>{imported ? <button className="open-result-button" onClick={() => void onOpenAnalysis(projectId, requestId, loadCaseId)}><LayoutDashboard /> 분석 대시보드에서 확인</button> : <button className="data-submit import-button" onClick={() => void submitResultImport()} disabled={!importPreview || !resultFile || busy}><Upload /> 검증된 결과 등록</button>}</footer>
    </article>
  </section>
}

function EdgeFilter({ selected, setSelected }: { selected: string[]; setSelected: (value: string[]) => void }) {
  const options = [['top', '상단'], ['bottom', '하단'], ['left', '좌측'], ['right', '우측']]
  const toggle = (key: string) => setSelected(selected.includes(key) ? selected.filter((item) => item !== key) : [...selected, key])
  return <div className="edge-filter"><span>표시 엣지</span>{options.map(([key, label]) => <button className={selected.includes(key) ? 'on' : ''} key={key} onClick={() => toggle(key)}><i />{label}</button>)}</div>
}

type DiscoveredFolderFile = { path: string; kind: 'typed_scalars' | 'curve_csv' | 'media'; dataType: 'FLOAT' | 'CURVE' | 'IMAGE' | 'VIDEO' | 'MODEL_3D'; variableKey: string }

function FolderSchemaWorkspace() {
  const [files, setFiles] = useState<DiscoveredFolderFile[]>([])
  const [schemas, setSchemas] = useState<ImportSchema[]>([])
  const [name, setName] = useState('새 해석 결과 폴더')
  const [description, setDescription] = useState('')
  const [message, setMessage] = useState('')
  const [saving, setSaving] = useState(false)
  const [contextMode, setContextMode] = useState<'folder_levels' | 'manifest'>('folder_levels')
  const [projectLevel, setProjectLevel] = useState(0)
  const [requestLevel, setRequestLevel] = useState(1)
  const [loadCaseLevel, setLoadCaseLevel] = useState(2)
  const directoryInput = useRef<HTMLInputElement>(null)
  useEffect(() => { api.importSchemas().then(setSchemas).catch(() => setSchemas([])) }, [])
  useEffect(() => { directoryInput.current?.setAttribute('webkitdirectory', '') }, [])
  const discover = (selected: FileList | null) => {
    const discovered = Array.from(selected ?? []).map((file) => {
      const path = file.webkitRelativePath || file.name
      const extension = path.toLowerCase().split('.').pop() ?? ''
      const stem = path.split('/').pop()?.replace(/\.[^.]+$/, '') ?? 'result'
      if (['png', 'jpg', 'jpeg', 'webp', 'svg'].includes(extension)) return { path, kind: 'media' as const, dataType: 'IMAGE' as const, variableKey: stem.replace(/[^a-z0-9]+/gi, '_').toLowerCase() }
      if (['mp4', 'webm'].includes(extension)) return { path, kind: 'media' as const, dataType: 'VIDEO' as const, variableKey: stem.replace(/[^a-z0-9]+/gi, '_').toLowerCase() }
      if (['glb', 'gltf'].includes(extension)) return { path, kind: 'media' as const, dataType: 'MODEL_3D' as const, variableKey: stem.replace(/[^a-z0-9]+/gi, '_').toLowerCase() }
      if (extension === 'csv') return { path, kind: 'curve_csv' as const, dataType: 'CURVE' as const, variableKey: stem.replace(/[^a-z0-9]+/gi, '_').toLowerCase() }
      return { path, kind: 'typed_scalars' as const, dataType: 'FLOAT' as const, variableKey: stem.replace(/[^a-z0-9]+/gi, '_').toLowerCase() }
    })
    setFiles(discovered); setMessage(discovered.length ? `${discovered.length}개 파일을 발견했습니다. 각 항목의 유형과 변수 키를 확인한 뒤 스키마를 저장하세요.` : '')
  }
  const save = async () => {
    if (!files.length) return
    setSaving(true); setMessage('')
    try {
      const definition = { context_mapping: { mode: contextMode, project_level: projectLevel, request_level: requestLevel, load_case_level: loadCaseLevel, sample_path: files[0]?.path }, mappings: files.map((file) => file.kind === 'curve_csv' ? { kind: file.kind, path: file.path, variable_key: file.variableKey, display_name: file.variableKey, x_column: 'time_ms', y_column: 'value', x_unit: 'ms', y_unit: '-' } : file.kind === 'media' ? { kind: file.kind, path: file.path, variable_key: file.variableKey, display_name: file.variableKey, asset_type: file.dataType, mime_type: file.dataType === 'VIDEO' ? 'video/mp4' : file.dataType === 'IMAGE' ? 'image/png' : 'model/gltf-binary' } : { kind: file.kind, path: file.path, variable_key: file.variableKey, data_type: file.dataType }) }
      const created = await api.createImportSchema({ name, description, definition, updated_by: '관리자' })
      setSchemas((current) => [created, ...current]); setMessage(`스키마 v1을 저장했습니다. 다음 단계에서 이 스키마를 선택해 실제 폴더를 업로드합니다.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : '스키마 저장에 실패했습니다.') }
    finally { setSaving(false) }
  }
  const sampleParts = (files[0]?.path ?? '').split('/').filter(Boolean)
  return <section className="catalog-page">
    <header><div><span>FOLDER INGESTION DESIGN</span><h1>폴더 스키마</h1><p>최상위 결과 폴더의 하위 파일을 탐색하고 변수 카탈로그로 연결할 규칙을 저장합니다.</p></div><div className="catalog-header-actions"><strong>{schemas.length}개 저장됨</strong></div></header>
    <SchemaCatalog schemas={schemas} onUpdate={(updated) => setSchemas((current) => current.map((item) => item.id === updated.id ? updated : item))} onDelete={(schemaId) => setSchemas((current) => current.filter((item) => item.id !== schemaId))} />
    <div className="data-form-card"><header><div><span>HIERARCHY MAPPING</span><h2>프로젝트·의뢰·하중 경우 매핑</h2></div></header><label><span>메타데이터 출처</span><select value={contextMode} onChange={(event) => setContextMode(event.target.value as 'folder_levels' | 'manifest')}><option value="folder_levels">폴더 이름 단계</option><option value="manifest">manifest.json context</option></select></label>{contextMode === 'folder_levels' ? <><div className="data-form-row"><label><span>프로젝트(제품) 단계</span><input type="number" min="0" value={projectLevel} onChange={(event) => setProjectLevel(Number(event.target.value))}/></label><label><span>의뢰 단계</span><input type="number" min="0" value={requestLevel} onChange={(event) => setRequestLevel(Number(event.target.value))}/></label><label><span>하중 경우 단계</span><input type="number" min="0" value={loadCaseLevel} onChange={(event) => setLoadCaseLevel(Number(event.target.value))}/></label></div>{sampleParts.length > 0 && <div className="result-preview-kpis"><div><strong>{sampleParts[projectLevel] ?? '미지정'}</strong><span>프로젝트(제품)</span></div><div><strong>{sampleParts[requestLevel] ?? '미지정'}</strong><span>의뢰</span></div><div><strong>{sampleParts[loadCaseLevel] ?? '미지정'}</strong><span>하중 경우</span></div></div>}</> : <p>선택 폴더의 manifest.json 안 `context.project`, `context.request`, `context.load_case`를 사용합니다.</p>}<small>0은 선택한 최상위 폴더입니다. 예: 제품/의뢰/하중경우/results에서 0·1·2로 설정합니다.</small></div>
    <div className="data-form-card"><label><span>스키마 이름</span><input value={name} onChange={(event) => setName(event.target.value)} /></label><label><span>설명</span><input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="TV 낙하 결과 폴더 v1" /></label><label><span>최상위 결과 폴더 선택</span><input ref={directoryInput} type="file" multiple onChange={(event) => discover(event.target.files)} /><small>브라우저가 선택한 폴더의 하위 파일 목록만 읽어 트리 규칙 초안을 만듭니다.</small></label></div>
    {files.length ? <div className="variable-table"><div className="variable-row head"><span>발견 경로</span><span>적재 방식</span><span>자료형</span><span>변수 키</span><span>작업</span><span>상태</span></div>{files.map((file, index) => <article className="variable-row" key={file.path}><span><code>{file.path}</code></span><span>{file.kind}</span><span><b>{file.dataType}</b></span><span><input value={file.variableKey} onChange={(event) => setFiles((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, variableKey: event.target.value } : item))} /></span><span><button onClick={() => setFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}>제외</button></span><span><small className="catalog-data-wait">매핑 확인 필요</small></span></article>)}</div> : <div className="portfolio-empty"><GripVertical/><h2>결과 폴더를 선택하세요.</h2><p>CSV, JSON, 이미지, 영상, GLB/GLTF를 자료형별 후보로 자동 분류합니다.</p></div>}
    <footer className="catalog-header-actions"><span>{message}</span><button className="primary-button" disabled={!files.length || saving} onClick={() => void save()}><Save /> {saving ? '저장 중' : '스키마 JSON 저장'}</button></footer>
  </section>
}

function SchemaCatalog({ schemas, onUpdate, onDelete }: { schemas: ImportSchema[]; onUpdate: (schema: ImportSchema) => void; onDelete: (schemaId: string) => void }) {
  const [selectedId, setSelectedId] = useState('')
  const selected = schemas.find((item) => item.id === selectedId) ?? schemas[0]
  const [editName, setEditName] = useState('')
  const [editDescription, setEditDescription] = useState('')
  const [editJson, setEditJson] = useState('')
  const [editing, setEditing] = useState(false)
  const [status, setStatus] = useState('')
  useEffect(() => {
    if (!selected) return
    setSelectedId(selected.id); setEditName(selected.name); setEditDescription(selected.description); setEditJson(JSON.stringify(selected.definition, null, 2)); setEditing(false); setStatus('')
  }, [selected?.id, selected?.updated_at])
  if (!schemas.length) return <section className="data-form-card"><h2>저장된 폴더 스키마</h2><p>아직 저장된 스키마가 없습니다.</p></section>
  const saveChanges = async () => {
    if (!selected) return
    try {
      const definition = JSON.parse(editJson)
      if (!Array.isArray(definition.mappings)) throw new Error('JSON에는 mappings 배열이 필요합니다.')
      const updated = await api.updateImportSchema(selected.id, { name: editName, description: editDescription, definition, updated_by: '관리자' })
      onUpdate(updated); setEditing(false); setStatus(`v${updated.definition.version}으로 저장했습니다.`)
    } catch (reason) { setStatus(reason instanceof Error ? reason.message : '스키마 수정에 실패했습니다.') }
  }
  const remove = async () => {
    if (!selected || !window.confirm(`${selected.name} 스키마를 목록에서 삭제할까요?`)) return
    try { await api.deleteImportSchema(selected.id); onDelete(selected.id); setSelectedId(''); setStatus('삭제했습니다.') }
    catch (reason) { setStatus(reason instanceof Error ? reason.message : '스키마 삭제에 실패했습니다.') }
  }
  return <section className="data-form-card"><header><div><span>SCHEMA LIBRARY</span><h2>저장된 폴더 스키마</h2></div><div className="catalog-row-actions"><button onClick={() => setEditing((value) => !value)}>{editing ? '편집 취소' : '편집'}</button><button className="danger" onClick={() => void remove()}>삭제</button></div></header><label><span>스키마 선택</span><select value={selected?.id ?? ''} onChange={(event) => setSelectedId(event.target.value)}>{schemas.map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.definition.version ?? 1}</option>)}</select></label>{selected && <div className="result-preview"><div className="result-preview-head"><div><span>SCHEMA DETAIL</span><strong>{selected.name}</strong></div><small>{new Date(selected.updated_at).toLocaleString('ko-KR')}</small></div><div className="result-preview-kpis"><div><strong>{selected.definition.mappings.length}</strong><span>파일 매핑</span></div><div><strong>v{selected.definition.version ?? 1}</strong><span>스키마 버전</span></div></div>{editing ? <div className="schema-editor-fields"><label><span>이름</span><input value={editName} onChange={(event) => setEditName(event.target.value)}/></label><label><span>설명</span><input value={editDescription} onChange={(event) => setEditDescription(event.target.value)}/></label><label><span>스키마 JSON</span><textarea className="schema-json-editor" value={editJson} onChange={(event) => setEditJson(event.target.value)}/></label><button className="primary-button" onClick={() => void saveChanges()}><Save/> 변경 저장</button></div> : <><p>{selected.description || '설명 없음'}</p><pre className="schema-json">{JSON.stringify(selected.definition, null, 2)}</pre></>}{status && <p>{status}</p>}</div>}</section>
}

function WidgetCard({ widget, overview, selectedEdges, editMode, canManageThresholds, threshold, openCellThreshold, onSaveThreshold, onSaveOpenCellThreshold, onRemove, onConfigure }: { widget: DashboardWidget; overview: Overview; selectedEdges: string[]; editMode: boolean; canManageThresholds: boolean; threshold?: QualityThreshold; openCellThreshold?: QualityThreshold; onSaveThreshold: (value: number) => void; onSaveOpenCellThreshold: (value: number) => void; onRemove: () => void; onConfigure: () => void }) {
  const customFontSize = Number(widget.settings?.fontSize)
  return (
    <article
      className={`widget-card widget-${widget.type} ${editMode ? 'editable' : ''}`}
      data-custom-font={Number.isFinite(customFontSize) ? 'true' : undefined}
      style={Number.isFinite(customFontSize) ? { '--widget-font-size': `${customFontSize * 1.2}px` } as CSSProperties : undefined}
    >
      <header>
        <div className={editMode ? 'widget-drag-handle' : undefined} aria-label={editMode ? `${widget.title} 이동 손잡이` : undefined}>
          {editMode && <GripVertical />}
          <div><span className="widget-kicker">{widget.type.replace('_', ' ')}</span><h3>{widget.title}</h3></div>
        </div>
        {editMode ? <div className="widget-edit-actions"><button aria-label={`${widget.title} 설정`} onClick={onConfigure}><Settings2 /></button><button aria-label={`${widget.title} 삭제`} onClick={onRemove}><X /></button></div> : <button className="widget-menu">•••</button>}
      </header>
      <div className="widget-body"><WidgetContent widget={widget} overview={overview} selectedEdges={selectedEdges} canManageThresholds={canManageThresholds} threshold={threshold} openCellThreshold={openCellThreshold} onSaveThreshold={onSaveThreshold} onSaveOpenCellThreshold={onSaveOpenCellThreshold} /></div>
    </article>
  )
}

function WidgetContent({ widget, overview, selectedEdges, canManageThresholds, threshold, openCellThreshold, onSaveThreshold, onSaveOpenCellThreshold }: { widget: DashboardWidget; overview: Overview; selectedEdges: string[]; canManageThresholds: boolean; threshold?: QualityThreshold; openCellThreshold?: QualityThreshold; onSaveThreshold: (value: number) => void; onSaveOpenCellThreshold: (value: number) => void }) {
  const type = widget.type
  const edgeOrder = ['top', 'bottom', 'left', 'right']
  const openCellScalars = overview.scalar_results.filter((item) => item.result_group === 'OPEN_CELL' && item.unit.toLowerCase() === 'mpa' && item.variable_key.toLowerCase().includes('stress') && hasNumericValue(item.value_double))
  const filteredScalars = openCellScalars
    .filter((item) => selectedEdges.some((edge) => item.variable_key.startsWith(edge)))
    .sort((a, b) => edgeOrder.indexOf(a.variable_key.split('_')[0]) - edgeOrder.indexOf(b.variable_key.split('_')[0]))
  const edgeLabel = (key: string) => ({ top: '상단', bottom: '하단', left: '좌측', right: '우측' }[key.split('_')[0]] ?? key)
  const configuredScalars = widget.settings?.variableId ? overview.scalar_results.filter((item) => item.variable_key === widget.settings?.variableId && hasNumericValue(item.value_double)) : filteredScalars
  const barData = configuredScalars.map((item) => ({ name: edgeLabel(item.variable_key), value: item.value_double, verdict: item.verdict }))
  const openCellLimit = openCellThreshold?.threshold_double ?? openCellScalars.find((item) => hasNumericValue(item.threshold_double))?.threshold_double ?? 75
  const openCellVerdict = openCellScalars.some((item) => item.verdict === 'FAIL') ? 'FAIL' : openCellScalars.length ? 'PASS' : 'NO_DATA'
  const boundScalar = widget.settings?.variableId ? configuredScalars[0] : undefined
  const widgetThreshold = widget.settings?.variableId ? (hasNumericValue(boundScalar?.threshold_double) ? boundScalar.threshold_double : null) : openCellLimit
  const widgetUnit = boundScalar?.unit ?? configuredScalars[0]?.unit ?? 'MPa'
  const widgetVerdict = boundScalar?.verdict ?? openCellVerdict
  const chartMaximum = Math.max(...barData.map((item) => item.value), widgetThreshold ?? 0, 1)
  const resultLocation = (key: string) => overview.result_locations.find((item) => item.variable_key === key)
  const seriesData = useMemo(() => {
    const grouped = new Map<number, Record<string, number>>()
    overview.time_series.filter((item) => hasNumericValue(item.time_value) && hasNumericValue(item.value) && (widget.settings?.variableId ? item.variable_key === widget.settings.variableId : selectedEdges.some((edge) => item.variable_key.startsWith(edge)))).forEach((item) => {
      const point = grouped.get(item.time_value) ?? { time: item.time_value }
      point[item.variable_key] = item.value
      grouped.set(item.time_value, point)
    })
    return [...grouped.values()]
  }, [overview.time_series, selectedEdges, widget.settings?.variableId])

  if (type.startsWith('chassis_')) return <ChassisWidgetContent type={type} overview={overview} threshold={threshold} canManageThreshold={canManageThresholds} onSaveThreshold={onSaveThreshold} variableId={String(widget.settings?.variableId ?? '')} />
  if (widget.settings?.variableId && type === 'time_series' && !overview.time_series.some((item)=>item.variable_key===widget.settings?.variableId)) return <div className="widget-empty"><Database/><strong>선언된 변수에 결과 데이터가 없습니다.</strong><small>{String(widget.settings.variableId)} 키의 시간 이력을 가져오면 자동 표시됩니다.</small></div>
  if (widget.settings?.variableId && ['kpi','gauge','edge_bar','scatter','result_table'].includes(type) && !overview.scalar_results.some((item)=>item.variable_key===widget.settings?.variableId)) return <div className="widget-empty"><Database/><strong>선언된 변수에 결과 데이터가 없습니다.</strong><small>{String(widget.settings.variableId)} 키의 숫자 결과를 가져오면 자동 표시됩니다.</small></div>

  if (type === 'open_cell_map') return <OpenCellMap overview={overview} />
  if (type === 'open_cell_summary') return <OpenCellSummary overview={overview} threshold={openCellThreshold} canManageThreshold={canManageThresholds} onSaveThreshold={onSaveOpenCellThreshold} />
  if (type === 'verdict') return <div className={`verdict-block ${widgetVerdict.toLowerCase()}`}><div className="verdict-icon">{widgetVerdict === 'PASS' ? <Check /> : <X />}</div><div><strong>{widgetVerdict}</strong><span>{widgetVerdict === 'PASS' ? '허용 기준 만족' : widgetVerdict === 'FAIL' ? '기준 초과 감지' : '판정 데이터 없음'}</span></div><small>{widgetThreshold == null ? widgetUnit : `LIMIT ${widgetThreshold} ${widgetUnit}`}</small></div>
  if (type === 'summary') return overview.load_case.analysis_type === 'SIDE_CLAMP' ? <div className="summary-grid"><div><span>클램프 압력</span><strong>{overview.load_case.parameters.pressure_mpa ?? overview.load_case.parameters.clamp_pressure_kpa ?? '-'}<em>MPa</em></strong></div><div><span>유지 시간</span><strong>{overview.load_case.parameters.hold_time_sec ?? overview.load_case.parameters.hold_time_s ?? '-'}<em>s</em></strong></div><div><span>클램프 면</span><strong>{Array.isArray(overview.load_case.parameters.faces) ? overview.load_case.parameters.faces.join(' / ') : 'LEFT / RIGHT'}</strong></div><div><span>요소 수</span><strong>{overview.template_execution?.generated_model.elements.toLocaleString() ?? '-'}</strong></div></div> : <div className="summary-grid"><div><span>낙하 높이</span><strong>{overview.load_case.parameters.drop_height_mm}<em>mm</em></strong></div><div><span>낙하 방향</span><strong>{overview.load_case.parameters.direction ?? overview.load_case.parameters.impact_direction ?? '-'}</strong></div><div><span>자동화 템플릿</span><strong>{overview.template_execution?.template_version ?? '-'}</strong></div><div><span>요소 수</span><strong>{overview.template_execution?.generated_model.elements.toLocaleString() ?? '-'}</strong></div></div>
  if (type === 'edge_bar') return <ResponsiveContainer width="100%" height="100%"><BarChart data={barData} margin={{ top: 12, right: 18, left: -12, bottom: 0 }}><CartesianGrid vertical={false} stroke="#26394c" strokeDasharray="3 3"/><XAxis dataKey="name" tick={{ fill: '#8fa6bb', fontSize: 14.4 }} axisLine={false} tickLine={false}/><YAxis domain={[0, chartMaximum * 1.15]} tick={{ fill: '#6f879d', fontSize: 13.2 }} axisLine={false} tickLine={false} unit=""/><Tooltip contentStyle={{ background: '#102235', border: '1px solid #2d465c', borderRadius: 10 }} formatter={(value: number) => [`${value} ${widgetUnit}`, '결과']}/>{widget.settings?.showThreshold !== false && widgetThreshold != null && <ReferenceLine y={widgetThreshold} stroke="#ffbf57" strokeDasharray="5 5" label={{ value: `기준 ${widgetThreshold}`, fill: '#ffbf57', fontSize: 13.2, position: 'insideTopRight' }}/>}<Bar dataKey="value" radius={[5,5,1,1]}>{barData.map((entry) => <Cell key={entry.name} fill={entry.verdict === 'FAIL' ? '#ff5d73' : '#4fd6a0'} />)}</Bar></BarChart></ResponsiveContainer>
  if (type === 'time_series') { const seriesKeys = widget.settings?.variableId ? [String(widget.settings.variableId)] : selectedEdges.map((edge) => `${edge}_edge_stress_time`); return <ResponsiveContainer width="100%" height="100%"><LineChart data={seriesData} margin={{ top: 10, right: 22, left: -8, bottom: 2 }}><CartesianGrid stroke="#24384b" strokeDasharray="3 3"/><XAxis dataKey="time" tick={{ fill: '#71899f', fontSize: 13.2 }} axisLine={{ stroke: '#31485b' }} tickLine={false} label={{ value: `TIME (${overview.time_series.find((item)=>seriesKeys.includes(item.variable_key))?.time_unit ?? 'ms'})`, fill: '#6f879d', fontSize: 12, position: 'insideBottomRight', offset: -2 }}/><YAxis tick={{ fill: '#71899f', fontSize: 13.2 }} axisLine={false} tickLine={false}/><Tooltip contentStyle={{ background: '#102235', border: '1px solid #2d465c', borderRadius: 10 }}/><Legend wrapperStyle={{ fontSize: 13.2, paddingTop: 5 }}/>{widget.settings?.showThreshold !== false && widgetThreshold != null && <ReferenceLine y={widgetThreshold} stroke="#ffbf57" strokeDasharray="6 4"/>}{seriesKeys.map((key, index) => <Line key={key} type="monotone" dataKey={key} name={overview.time_series.find((item) => item.variable_key === key)?.display_name ?? edgeLabel(key)} dot={false} stroke={String(widget.settings?.color ?? SERIES_COLORS[index % SERIES_COLORS.length])} strokeWidth={2}/>)}</LineChart></ResponsiveContainer> }
  if (type === 'note') return <div className="note-block"><MessageSquareText /><blockquote>{overview.notes[0]?.body ?? '등록된 의견이 없습니다.'}</blockquote><footer><span>{overview.notes[0]?.author ?? '-'}</span><small>ANALYSIS ENGINEER</small></footer></div>
  if (type === 'result_table') return <div className="result-table"><div className="table-head"><span>측정 위치</span><span>결과</span><span>허용 기준</span><span>여유율</span><span>판정</span></div>{configuredScalars.map((item) => { const location = resultLocation(item.variable_key); const hasThreshold = hasNumericValue(item.threshold_double) && item.threshold_double !== 0; return <div className="table-row" key={item.id}><strong><i className={`edge-${item.variable_key.split('_')[0]}`} /><span>{item.display_name.replace(' 최대 응력','')}{location && <small>{location.entity_type} {location.entity_id} · ({location.x.toFixed(1)}, {location.y.toFixed(1)}, {location.z.toFixed(1)})</small>}</span></strong><span>{item.value_double.toFixed(1)} <small>{item.unit}</small></span><span>{hasThreshold ? item.threshold_double.toFixed(1) : ''} <small>{hasThreshold ? item.unit : ''}</small></span><span className={item.verdict === 'FAIL' ? 'negative' : 'positive'}>{hasThreshold ? `${((item.threshold_double - item.value_double) / item.threshold_double * 100).toFixed(1)}%` : ''}</span><b className={item.verdict.toLowerCase()}>{item.verdict}</b></div> })}</div>
  if (type === 'contour') { const variableId = String(widget.settings?.variableId ?? ''); const asset = overview.media.find((item) => item.asset_type === 'IMAGE' && (!variableId || item.metadata?.variable_key === variableId)) ?? overview.media.find((item) => item.asset_type === 'IMAGE'); return asset ? <div className="contour"><img src={asset.asset_url ?? `/assets/${asset.file_path}`} alt={asset.title} /></div> : <div className="empty-widget">등록된 컨투어 이미지가 없습니다.</div> }
  if (type === 'kpi' || type === 'gauge') {
    const bound = overview.scalar_results.find((item) => item.variable_key === widget.settings?.variableId && hasNumericValue(item.value_double)) ?? openCellScalars[0]
    return bound ? <div className="verdict-card"><span>{bound.display_name}</span><strong>{bound.value_double.toFixed(1)} {bound.unit}</strong><small>{hasNumericValue(bound.threshold_double) ? `기준 ${bound.threshold_double.toFixed(1)} ${bound.unit} · ` : ''}{bound.verdict}</small></div> : <div className="empty-widget">선택한 변수의 데이터가 없습니다.</div>
  }
  if (type === 'scatter') return <ResponsiveContainer width="100%" height="100%"><LineChart data={barData}><CartesianGrid stroke="#193447" /><XAxis dataKey="name" /><YAxis /><Tooltip /><Line dataKey="value" stroke="#61d4ff" /></LineChart></ResponsiveContainer>
  if (type === 'video_grid') return <DropVideoGrid loadCaseId={overview.load_case.id} pageSize={Number(widget.settings?.pageSize ?? 20)} />
  if (type === 'video') { const variableId = String(widget.settings?.variableId ?? ''); const asset = overview.media.find((item) => item.asset_type === 'VIDEO' && (!variableId || item.metadata?.variable_key === variableId)) ?? overview.media.find((item) => item.asset_type === 'VIDEO'); return asset ? <video controls className="result-video" src={asset.asset_url ?? `/assets/${asset.file_path}`} /> : <div className="empty-widget">등록된 안전한 영상 파일이 없습니다.</div> }
  if (type === 'model3d') return <div className="empty-widget">GLB/glTF 경량 파일을 등록하면 여기에 표시됩니다.</div>
  return <div className="empty-widget">표시할 데이터가 없습니다.</div>
}

function OpenCellMap({ overview }: { overview: Overview }) {
  const productValue = (category: string) => overview.product_information.find((item) => item.category === category)?.value_text ?? '-'
  const edgeResult = (edge: string) => overview.scalar_results.find((item) => item.variable_key.startsWith(`${edge}_`) && hasNumericValue(item.value_double))
  const edgeLabels = { top: '상', bottom: '하', left: '좌', right: '우' }
  const isClamp = overview.load_case.analysis_type === 'SIDE_CLAMP'
  const scene = isClamp ? `SIDE CLAMP · ${overview.load_case.parameters.pressure_mpa ?? '-'} MPa` : `${overview.load_case.parameters.direction ?? '-'} FACE · ${overview.load_case.parameters.drop_height_mm ?? '-'} mm`
  const threshold = overview.scalar_results.find((item) => !item.variable_key.startsWith('chassis_rear_') && hasNumericValue(item.threshold_double))?.threshold_double ?? 75

  return <div className="open-cell-layout">
    <div className="open-cell-visual">
      <div className="open-cell-frame">
        {Object.entries(edgeLabels).map(([edge, label]) => {
          const result = edgeResult(edge)
          return <div key={edge} className={`open-cell-edge ${edge} ${(result?.verdict ?? 'PASS').toLowerCase()}`}><span>{label}</span><strong>{result?.value_double.toFixed(1) ?? '-'}<small> MPa</small></strong></div>
        })}
        <div className="open-cell-glass"><span>OPEN CELL</span><strong>{productValue('SPEC').replace(' inch', '\"')}</strong><small>GLASS PANEL · 16:9</small></div>
      </div>
      <div className="edge-legend"><span><i className="pass" />기준 이내</span><span><i className="fail" />기준 초과</span><b>LIMIT {threshold} MPa</b></div>
    </div>
    <div className="open-cell-meta">
      <div><span>인치</span><strong>{productValue('SPEC')}</strong></div>
      <div><span>하중 씬</span><strong>{scene}</strong></div>
      <div><span>일자</span><strong>{new Date(overview.load_case.created_at).toLocaleDateString('ko-KR')}</strong></div>
      <div><span>제조사</span><strong>{productValue('MANUFACTURER')}</strong></div>
      <div><span>제품 모델명</span><strong>{productValue('MODEL')}</strong></div>
    </div>
  </div>
}

function WidgetSettingsPanel({ widget, variables, onChange, onClose }: { widget: DashboardWidget; variables: VariableDefinition[]; onChange: (patch: Partial<DashboardWidget>) => void; onClose: () => void }) {
  const chartOptions: Array<[DashboardWidget['type'],string]> = [...(widget.type === 'run_comparison' ? [['run_comparison','Run 비교·검토 패널'] as [DashboardWidget['type'], string]] : []),['summary','하중 조건 요약'],['kpi','KPI 카드'],['verdict','패스/실패 카드'],['gauge','임계값 게이지'],['edge_bar','막대그래프'],['time_series','시계열 그래프'],['scatter','산점도'],['result_table','데이터 테이블'],['open_cell_map','Open Cell 맵'],['open_cell_summary','Open Cell 판정 요약'],['chassis_summary','Chassis 판정 요약'],['chassis_diagram','Chassis 위치도'],['chassis_bar','Chassis 비교 그래프'],['chassis_table','Chassis 상세 표'],['contour','컨투어 이미지'],['video','영상 플레이어'],['video_grid','낙하 영상 비교'],['note','수행자 의견']]
  const currentVariable = variables.find((item) => item.id === widget.settings?.variableId)
  const compatibleVariables = variables.filter((item) => item.allowed_widgets.includes(widget.type) || item.id === currentVariable?.id)
  const aggregations = currentVariable?.allowed_aggregations ?? ['MAX','MIN','AVG','LATEST','RAW']
  return <div className="drawer-backdrop" onMouseDown={onClose}><aside className="widget-settings-drawer" onMouseDown={(e)=>e.stopPropagation()}><header><div><span>WIDGET SETTINGS</span><h2>위젯 설정</h2></div><button onClick={onClose}><X/></button></header><label><span>제목</span><input value={widget.title} onChange={(e)=>onChange({title:e.target.value})}/></label><label><span>글자 크기 (px)</span><input type="number" min="8" max="24" step="1" value={Number(widget.settings?.fontSize ?? 10)} onChange={(e)=>onChange({settings:{fontSize:Math.min(24,Math.max(8,Number(e.target.value)||10))}})}/></label><label><span>시각화 유형</span><select value={widget.type} disabled={widget.type === 'run_comparison'} onChange={(e)=>onChange({type:e.target.value as DashboardWidget['type']})}>{chartOptions.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>{widget.type !== 'run_comparison' && <><label><span>데이터 변수</span><select value={String(widget.settings?.variableId ?? '')} onChange={(e)=>onChange({settings:{variableId:e.target.value||undefined}})}><option value="">전체/위젯 기본 변수</option>{compatibleVariables.map((item)=><option key={item.id} value={item.id}>{item.display_name} ({item.unit}){item.has_data?'':' · 데이터 대기'}</option>)}</select></label><label><span>집계 방식</span><select value={String(widget.settings?.aggregation ?? aggregations[0])} onChange={(e)=>onChange({settings:{aggregation:e.target.value}})}>{aggregations.map((item)=><option key={item}>{item}</option>)}</select></label></>}<label><span>강조 색상</span><div className="color-setting"><input type="color" value={String(widget.settings?.color ?? '#50d5ff')} onChange={(e)=>onChange({settings:{color:e.target.value}})}/><code>{String(widget.settings?.color ?? '#50d5ff')}</code></div></label><label className="check-setting"><input type="checkbox" checked={widget.settings?.showThreshold !== false} onChange={(e)=>onChange({settings:{showThreshold:e.target.checked}})}/><span>기준선 표시</span></label><label className="check-setting"><input type="checkbox" checked={widget.settings?.includeInReport !== false} onChange={(e)=>onChange({settings:{includeInReport:e.target.checked}})}/><span>보고서 포함</span></label><div className="settings-note"><Lock/><p>카탈로그에 선언되고 현재 그래프에 허용된 변수만 표시됩니다. 변수 키로 실제 결과 테이블과 연결됩니다.</p></div><button className="primary-button" onClick={onClose}><Check/> 설정 완료</button></aside></div>
}

function AnalysisPageManager({ loadCaseId, activePageId, visiblePageIds, onClose, onPagesChanged, onActiveDefinitionChanged, onActivate, onDeleted }: { loadCaseId: string; activePageId: string; visiblePageIds: string[]; onClose: () => void; onPagesChanged: (pages: DashboardPageSummary[]) => void; onActiveDefinitionChanged: (definition: DashboardDefinition) => void; onActivate: (definition: DashboardDefinition, startEditing?: boolean) => void; onDeleted: (dashboardId: string) => void }) {
  const [pages, setPages] = useState<DashboardPageSummary[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [newName, setNewName] = useState('')
  const [newDescription, setNewDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<DashboardPageSummary | null>(null)
  const [deleteConfirmation, setDeleteConfirmation] = useState('')
  const selected = pages.find((page) => page.id === selectedId)

  const reload = async (preferredId?: string) => {
    const [adminItems, publicItems] = await Promise.all([api.adminDashboardPages(loadCaseId, true), api.dashboardPages(loadCaseId)])
    const sorted = [...adminItems].sort((left, right) => left.page.display_order - right.page.display_order)
    setPages(sorted); onPagesChanged(publicItems)
    const nextId = preferredId ?? selectedId ?? activePageId ?? sorted[0]?.id ?? ''
    const next = sorted.find((page) => page.id === nextId) ?? sorted[0]
    setSelectedId(next?.id ?? ''); setName(next?.name ?? ''); setDescription(next?.description ?? '')
  }

  useEffect(() => { void reload(activePageId).catch((reason) => setError(reason instanceof Error ? reason.message : '분석 페이지를 불러오지 못했습니다.')) }, [loadCaseId])

  const choose = (page: DashboardPageSummary) => { setSelectedId(page.id); setName(page.name); setDescription(page.description) }
  const create = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const definition = await api.createDashboardPage({ load_case_id: loadCaseId, name: newName.trim(), description: newDescription.trim() })
      setNewName(''); setNewDescription(''); await reload(definition.id); onActivate(definition, true)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '분석 페이지를 만들지 못했습니다.') } finally { setBusy(false) }
  }
  const saveMetadata = async () => {
    if (!selected || selected.page.is_system) return
    setBusy(true); setError('')
    try { await api.updateDashboardPage(selected.id, { name: name.trim(), description: description.trim() }); await reload(selected.id); if (selected.id === activePageId) onActiveDefinitionChanged(await api.dashboard(selected.id)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '페이지 정보를 저장하지 못했습니다.') } finally { setBusy(false) }
  }
  const changeStatus = async (status: 'draft' | 'published' | 'archived') => {
    if (!selected || selected.page.is_system) return
    setBusy(true); setError('')
    try { await api.updateDashboardPage(selected.id, { status }); await reload(selected.id); if (selected.id === activePageId) onActiveDefinitionChanged(await api.dashboard(selected.id)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '페이지 상태를 변경하지 못했습니다.') } finally { setBusy(false) }
  }
  const move = async (offset: -1 | 1) => {
    if (!selected || selected.page.is_system || selected.page.status === 'archived') return
    const custom = pages.filter((page) => !page.page.is_system && page.page.status !== 'archived')
    const index = custom.findIndex((page) => page.id === selected.id); const target = index + offset
    if (index < 0 || target < 0 || target >= custom.length) return
    const reordered = [...custom]; [reordered[index], reordered[target]] = [reordered[target], reordered[index]]
    setBusy(true); setError('')
    try { await api.reorderDashboardPages(loadCaseId, reordered.map((page) => page.id)); await reload(selected.id); if (selected.id === activePageId) onActiveDefinitionChanged(await api.dashboard(selected.id)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '페이지 순서를 저장하지 못했습니다.') } finally { setBusy(false) }
  }
  const deletePermanently = async () => {
    if (!deleteTarget || deleteConfirmation !== deleteTarget.name || deleteTarget.page.is_system) return
    const activeCustom = pages.filter((page) => !page.page.is_system && page.page.analysis_key === 'custom' && page.page.status !== 'archived')
    const targetIndex = activeCustom.findIndex((page) => page.id === deleteTarget.id)
    const visibleIds = new Set(visiblePageIds)
    const fallbackIds = [activeCustom[targetIndex + 1]?.id, activeCustom[targetIndex - 1]?.id, pages.find((page) => page.page.analysis_key === 'open_cell' && visibleIds.has(page.id))?.id, pages.find((page) => page.page.analysis_key === 'chassis_rear' && visibleIds.has(page.id))?.id, pages.find((page) => page.page.analysis_key === 'run_comparison' && visibleIds.has(page.id))?.id].filter(Boolean) as string[]
    setBusy(true); setError('')
    try {
      await api.deleteDashboardPage(deleteTarget.id, loadCaseId)
      const [adminItems, publicItems] = await Promise.all([api.adminDashboardPages(loadCaseId, true), api.dashboardPages(loadCaseId)])
      const sorted = [...adminItems].sort((left, right) => left.page.display_order - right.page.display_order)
      setPages(sorted); onPagesChanged(publicItems); onDeleted(deleteTarget.id)
      const fallback = fallbackIds.map((id) => sorted.find((page) => page.id === id)).find(Boolean) ?? sorted.find((page) => visibleIds.has(page.id) && page.id !== deleteTarget.id)
      setDeleteTarget(null); setDeleteConfirmation('')
      if (deleteTarget.id === activePageId && fallback) {
        onActivate(await api.dashboard(fallback.id), false)
        onClose()
      } else {
        setSelectedId(fallback?.id ?? ''); setName(fallback?.name ?? ''); setDescription(fallback?.description ?? '')
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : '분석 페이지를 영구 삭제하지 못했습니다.') } finally { setBusy(false) }
  }

  return <div className="drawer-backdrop" onMouseDown={onClose}><section className="analysis-page-manager" role="dialog" aria-modal="true" aria-labelledby="analysis-page-manager-title" onMouseDown={(event)=>event.stopPropagation()}>
    <header><div><span>ANALYSIS PAGE LIBRARY</span><h2 id="analysis-page-manager-title">상세 분석 페이지 관리</h2><p>페이지 수와 내부 위젯 수에는 제한이 없습니다.</p></div><button aria-label="닫기" onClick={onClose}><X/></button></header>
    {error && <div className="catalog-error"><AlertTriangle/>{error}</div>}
    <div className="analysis-page-manager-grid">
      <aside><strong>페이지 {pages.length}개</strong><div>{pages.map((page)=><button key={page.id} className={page.id===selectedId?'active':''} onClick={()=>choose(page)}><span>{page.name}<small>{page.page.is_system?'SYSTEM':page.page.status.toUpperCase()}</small></span><b>v{page.version}</b></button>)}</div></aside>
      <main>
        <form className="analysis-page-create" onSubmit={(event)=>void create(event)}><h3>빈 페이지 추가</h3><label><span>페이지 이름</span><input required minLength={2} maxLength={120} value={newName} onChange={(event)=>setNewName(event.target.value)}/></label><label><span>설명</span><textarea maxLength={500} value={newDescription} onChange={(event)=>setNewDescription(event.target.value)}/></label><button className="primary-button" disabled={busy||newName.trim().length<2}><Plus/> 생성하고 위젯 배치</button></form>
        {selected && <section className="analysis-page-detail"><header><div><span>{selected.page.analysis_key.replace('_',' ')}</span><h3>{selected.name}</h3></div><button onClick={()=>void api.dashboard(selected.id).then((definition)=>onActivate(definition, false))} disabled={busy}><LayoutDashboard/> 페이지 열기</button></header><label><span>이름</span><input disabled={selected.page.is_system} value={name} onChange={(event)=>setName(event.target.value)}/></label><label><span>설명</span><textarea disabled={selected.page.is_system} value={description} onChange={(event)=>setDescription(event.target.value)}/></label>{!selected.page.is_system && <><div className="analysis-page-detail-actions"><button onClick={()=>void move(-1)} disabled={busy}><ArrowLeft/> 앞</button><button onClick={()=>void move(1)} disabled={busy}>뒤 <ArrowRight/></button><button onClick={()=>void saveMetadata()} disabled={busy||name.trim().length<2}><Save/> 정보 저장</button></div><div className="analysis-page-status-actions">{selected.page.status==='draft'&&<button className="primary-button" onClick={()=>void changeStatus('published')} disabled={busy}>게시</button>}{selected.page.status==='published'&&<button onClick={()=>void changeStatus('draft')} disabled={busy}>게시 해제</button>}{selected.page.status!=='archived'?<button className="danger" onClick={()=>void changeStatus('archived')} disabled={busy}>보관</button>:<button onClick={()=>void changeStatus('draft')} disabled={busy}>초안으로 복원</button>}<button className="danger permanent-delete" onClick={()=>{setDeleteTarget(selected);setDeleteConfirmation('')}} disabled={busy}><Trash2/> 영구 삭제</button></div></>}</section>}
      </main>
    </div>
    {deleteTarget && <div className="analysis-page-delete-confirm" role="alertdialog" aria-modal="true" aria-labelledby="analysis-page-delete-title"><div><AlertTriangle/><h3 id="analysis-page-delete-title">{deleteTarget.name} 영구 삭제</h3><p>이 페이지와 모든 버전이 삭제되며 복구할 수 없습니다. 계속하려면 페이지 이름을 정확히 입력하세요.</p><label><span>페이지 이름 확인</span><input autoFocus value={deleteConfirmation} onChange={(event)=>setDeleteConfirmation(event.target.value)} placeholder={deleteTarget.name}/></label><footer><button onClick={()=>{setDeleteTarget(null);setDeleteConfirmation('')}} disabled={busy}>취소</button><button className="danger" onClick={()=>void deletePermanently()} disabled={busy||deleteConfirmation!==deleteTarget.name}>{busy?<LoaderCircle className="spin"/>:<Trash2/>} 영구 삭제</button></footer></div></div>}
  </section></div>
}

function VariableCatalogPage({ variables, overview, loadCaseId, onChanged }: { variables: VariableDefinition[]; overview: Overview; loadCaseId: string; onChanged: (items: VariableDefinition[]) => void }) {
  const [query,setQuery]=useState(''); const [kind,setKind]=useState(''); const [editing,setEditing]=useState<VariableDefinition|null>(null); const [creating,setCreating]=useState(false)
  const filtered=variables.filter((item)=>(!kind||item.data_type===kind)&&(!query||`${item.display_name} ${item.id} ${item.description}`.toLowerCase().includes(query.toLowerCase())))
  const reload=async()=>onChanged(await api.variables(loadCaseId))
  const remove=async(item:VariableDefinition)=>{if(!window.confirm(`${item.display_name} 변수를 비활성화할까요? 결과 데이터는 삭제되지 않습니다.`))return; try{await api.deleteVariable(loadCaseId,item.id); await reload()}catch(reason){window.alert(reason instanceof Error?reason.message:'변수를 삭제하지 못했습니다.')}}
  return <section className="catalog-page"><header><div><span>SAFE SEMANTIC CATALOG</span><h1>변수 카탈로그</h1><p>{overview.load_case.project_name} · {overview.load_case.name}</p></div><div className="catalog-header-actions"><strong>{filtered.length} / {variables.length}개 변수</strong><button className="primary-button" onClick={()=>setCreating(true)}><Plus/> 변수 생성</button></div></header><div className="catalog-filters"><label><Search/><input aria-label="변수 검색" value={query} onChange={(e)=>setQuery(e.target.value)} placeholder="변수 이름 또는 ID 검색"/></label><select aria-label="변수 데이터 유형" value={kind} onChange={(e)=>setKind(e.target.value)}><option value="">전체 데이터 유형</option><option value="NUMBER">실수/정수 결과</option><option value="TIME_SERIES">시계열</option></select></div>{filtered.length?<div className="variable-table"><div className="variable-row head"><span>변수</span><span>유형</span><span>단위/기준</span><span>허용 집계</span><span>허용 위젯</span><span>출처/관리</span></div>{filtered.map((item)=><article className="variable-row" key={`${item.data_type}-${item.id}`}><span><strong>{item.display_name}</strong><code>{item.id}</code><small>{item.description}</small></span><span><b>{item.data_type}</b><small>{item.result_group} · {item.analysis_type}</small></span><span><strong>{item.unit}</strong><small>{item.threshold!=null?`기준 ${item.threshold} ${item.unit}`:'기준 없음'}</small></span><span>{item.allowed_aggregations.join(' · ')}</span><span>{item.allowed_widgets.join(' · ')}</span><span><code>{item.source}</code><small className={item.has_data?'catalog-data-ready':'catalog-data-wait'}>{item.has_data?'결과 데이터 연결됨':'결과 데이터 대기'}</small><div className="catalog-row-actions"><button onClick={()=>setEditing(item)}>수정</button><button className="danger" disabled={item.dashboard_usage_count>0} title={item.dashboard_usage_count?`대시보드 ${item.dashboard_usage_count}곳에서 사용 중`:''} onClick={()=>void remove(item)}>삭제</button></div></span></article>)}</div>:<div className="portfolio-empty"><Database/><h2>조건에 맞는 변수가 없습니다.</h2></div>}{(creating||editing)&&<VariableEditor loadCaseId={loadCaseId} variable={editing} onClose={()=>{setCreating(false);setEditing(null)}} onSaved={async()=>{await reload();setCreating(false);setEditing(null)}}/>}</section>
}

const variableOptions:Record<string,{widgets:string[];aggregations:string[]}>={NUMBER:{widgets:['kpi','gauge','edge_bar','scatter','result_table','chassis_bar','chassis_table'],aggregations:['MAX','MIN','AVG','LATEST']},FLOAT:{widgets:['kpi','gauge','edge_bar','scatter','result_table','chassis_bar','chassis_table'],aggregations:['MAX','MIN','AVG','LATEST']},INTEGER:{widgets:['kpi','edge_bar','scatter','result_table'],aggregations:['MAX','MIN','AVG','LATEST']},TIME_SERIES:{widgets:['time_series','scatter','result_table'],aggregations:['RAW','MAX_BY_TIME']},CURVE:{widgets:['time_series','scatter','result_table'],aggregations:['RAW','MAX_BY_TIME']},TEXT:{widgets:['note','result_table','verdict'],aggregations:['LATEST']},VERDICT:{widgets:['verdict','note','result_table'],aggregations:['LATEST']},STATUS:{widgets:['verdict','note','result_table'],aggregations:['LATEST']},BOOLEAN:{widgets:['verdict','result_table'],aggregations:['LATEST']},IMAGE:{widgets:['contour','result_table'],aggregations:['LATEST']},VIDEO:{widgets:['video','result_table'],aggregations:['LATEST']},MODEL_3D:{widgets:['model3d','result_table'],aggregations:['LATEST']}}
function newVariableDraft():VariableDefinitionInput{return{variable_key:'',display_name:'',data_type:'NUMBER',unit:'MPa',description:'',filterable:true,threshold:75,allowed_widgets:[...variableOptions.NUMBER.widgets],allowed_aggregations:[...variableOptions.NUMBER.aggregations],result_group:'CUSTOM',updated_by:'관리자'}}
function VariableEditor({loadCaseId,variable,onClose,onSaved}:{loadCaseId:string;variable:VariableDefinition|null;onClose:()=>void;onSaved:()=>Promise<void>}){
  const [draft,setDraft]=useState<VariableDefinitionInput>(()=>variable?{variable_key:variable.id,display_name:variable.display_name,data_type:variable.data_type,unit:variable.unit,description:variable.description,filterable:variable.filterable,threshold:variable.threshold??null,allowed_widgets:[...variable.allowed_widgets],allowed_aggregations:[...variable.allowed_aggregations],result_group:variable.result_group,updated_by:'관리자'}:newVariableDraft()); const [saving,setSaving]=useState(false); const [error,setError]=useState('')
  const toggle=(field:'allowed_widgets'|'allowed_aggregations',value:string)=>setDraft((current)=>({...current,[field]:current[field].includes(value)?current[field].filter((item)=>item!==value):[...current[field],value]}))
  const changeType=(data_type:VariableDefinitionInput['data_type'])=>setDraft((current)=>({...current,data_type,unit:['NUMBER','FLOAT','INTEGER','TIME_SERIES','CURVE'].includes(data_type)?'MPa':'-',threshold:['NUMBER','FLOAT','INTEGER'].includes(data_type)?75:null,allowed_widgets:[...variableOptions[data_type].widgets],allowed_aggregations:[...variableOptions[data_type].aggregations]}))
  const submit=async(event:FormEvent)=>{event.preventDefault();setSaving(true);setError('');try{if(variable){const{variable_key:_,data_type:__,...payload}=draft;await api.updateVariable(loadCaseId,variable.id,payload)}else await api.createVariable(loadCaseId,draft);await onSaved()}catch(reason){setError(reason instanceof Error?reason.message:'변수를 저장하지 못했습니다.')}finally{setSaving(false)}}
  const options=variableOptions[draft.data_type]
  return <div className="drawer-backdrop" onMouseDown={onClose}><form className="variable-editor" onSubmit={(event)=>void submit(event)} onMouseDown={(event)=>event.stopPropagation()}><header><div><span>SQL-BACKED VARIABLE</span><h2>{variable?'변수 정의 수정':'변수 생성'}</h2></div><button type="button" onClick={onClose}><X/></button></header>{error&&<div className="catalog-error"><AlertTriangle/>{error}</div>}<div className="variable-form-grid"><label><span>변수 키</span><input required pattern="[a-z][a-z0-9_]{2,79}" disabled={!!variable} value={draft.variable_key} onChange={(e)=>setDraft({...draft,variable_key:e.target.value})}/><small>생성 후 변경 불가 · 결과 CSV의 variable_key</small></label><label><span>표시 이름</span><input required value={draft.display_name} onChange={(e)=>setDraft({...draft,display_name:e.target.value})}/></label><label><span>데이터 유형</span><select disabled={!!variable} value={draft.data_type} onChange={(e)=>changeType(e.target.value as 'NUMBER'|'TIME_SERIES')}><option value="NUMBER">숫자 결과</option><option value="TIME_SERIES">시간 이력</option></select></label><label><span>단위</span><input required value={draft.unit} onChange={(e)=>setDraft({...draft,unit:e.target.value})}/></label><label><span>결과 그룹</span><select value={draft.result_group} onChange={(e)=>setDraft({...draft,result_group:e.target.value as VariableDefinitionInput['result_group']})}><option value="OPEN_CELL">Open Cell</option><option value="CHASSIS_REAR">Chassis Rear</option><option value="CUSTOM">사용자 정의</option></select></label><label><span>판정 기준</span><input type="number" step="any" required={draft.data_type==='NUMBER'} disabled={draft.data_type!=='NUMBER'} value={draft.threshold??''} onChange={(e)=>setDraft({...draft,threshold:e.target.value===''?null:Number(e.target.value)})}/></label></div><label><span>설명</span><textarea value={draft.description} onChange={(e)=>setDraft({...draft,description:e.target.value})}/></label><fieldset><legend>허용 그래프</legend><div className="catalog-checks">{options.widgets.map((item)=><label key={item}><input type="checkbox" checked={draft.allowed_widgets.includes(item)} onChange={()=>toggle('allowed_widgets',item)}/>{item}</label>)}</div></fieldset><fieldset><legend>허용 집계</legend><div className="catalog-checks">{options.aggregations.map((item)=><label key={item}><input type="checkbox" checked={draft.allowed_aggregations.includes(item)} onChange={()=>toggle('allowed_aggregations',item)}/>{item}</label>)}</div></fieldset><label className="check-setting"><input type="checkbox" checked={draft.filterable} onChange={(e)=>setDraft({...draft,filterable:e.target.checked})}/><span>대시보드 필터 허용</span></label><footer><button type="button" onClick={onClose}>취소</button><button className="primary-button" disabled={saving}>{saving?<LoaderCircle className="spin"/>:<Save/>} SQL에 저장</button></footer></form></div>
}

function AutomationTemplatesPage() {
  const [items,setItems]=useState<AutomationTemplate[]>([]); const [error,setError]=useState('')
  useEffect(()=>{api.automationTemplates().then(setItems).catch((reason)=>setError(reason instanceof Error?reason.message:'템플릿을 불러오지 못했습니다.'))},[])
  return <section className="template-page"><header><div><span>MODELING AUTOMATION</span><h1>자동화 템플릿</h1><p>하중 경우 아래의 모델링 자동화 버전·입력·실행 결과를 추적합니다.</p></div><strong>{items.length}개 실행 이력</strong></header>{error?<div className="portfolio-state error"><AlertTriangle/>{error}</div>:items.length?<div className="template-grid">{items.map((item)=><article key={item.id}><header><div><span>{item.analysis_type.replace('_',' ')}</span><h2>{item.template_name}</h2><p>{item.project_name} / {item.request_title}</p></div><b>{item.status}</b></header><div className="template-meta"><span>버전<strong>v{item.template_version}</strong></span><span>하중 경우<strong>{item.load_case_name}</strong></span><span>실행 일시<strong>{new Date(item.executed_at).toLocaleString('ko-KR')}</strong></span></div><section><div><h3>입력 파라미터</h3>{Object.entries(item.input).map(([key,value])=><p key={key}><code>{key}</code><span>{String(value)}</span></p>)}</div><div><h3>생성 모델</h3>{Object.entries(item.generated_model||{}).map(([key,value])=><p key={key}><code>{key}</code><span>{typeof value==='number'?value.toLocaleString():String(value)}</span></p>)}</div></section></article>)}</div>:<div className="portfolio-empty"><Settings2/><h2>선택 프로젝트의 자동화 실행 이력이 없습니다.</h2><p>하중 경우에 템플릿 실행을 연결하면 여기에 표시됩니다.</p></div>}</section>
}

function OpenCellSummary({ overview, threshold, canManageThreshold, onSaveThreshold }: { overview: Overview; threshold?: QualityThreshold; canManageThreshold: boolean; onSaveThreshold: (value: number) => void }) {
  const results = overview.scalar_results.filter((item) => item.result_group === 'OPEN_CELL' && item.unit.toLowerCase() === 'mpa' && item.variable_key.toLowerCase().includes('stress') && hasNumericValue(item.value_double))
  const limit = threshold?.threshold_double ?? results.find((item) => hasNumericValue(item.threshold_double))?.threshold_double ?? 75
  const [draftLimit, setDraftLimit] = useState(limit)
  useEffect(() => setDraftLimit(limit), [limit])
  const maximum = Math.max(...results.map((item) => item.value_double), 0)
  const minimum = results.length ? Math.min(...results.map((item) => item.value_double)) : 0
  const verdict = results.length ? (maximum >= limit ? 'FAIL' : 'PASS') : 'NO_DATA'
  return <div className="chassis-widget-summary open-cell-widget-summary"><div><span>전체 판정</span><strong className={verdict.toLowerCase()}>{verdict}</strong></div><div><span>최대 응력</span><strong>{maximum.toFixed(2)} MPa</strong></div>{canManageThreshold ? <label><span>응력 관리 기준</span><div><input aria-label="Open Cell 응력 기준값" type="number" min="0.1" step="0.1" value={draftLimit} onChange={(event) => setDraftLimit(Number(event.target.value))}/><button disabled={!Number.isFinite(draftLimit) || draftLimit <= 0} onClick={() => onSaveThreshold(draftLimit)}>저장</button></div></label> : <div><span>응력 관리 기준</span><strong>{limit.toFixed(1)} MPa</strong></div>}<p>적용 대상 {results.length}개 · MPa 범위 {minimum.toFixed(2)}~{maximum.toFixed(2)} · 기준 이상은 FAIL입니다.</p></div>
}

function ChassisWidgetContent({ type, overview, threshold, canManageThreshold, onSaveThreshold, variableId }: { type: DashboardWidget['type']; overview: Overview; threshold?: QualityThreshold; canManageThreshold: boolean; onSaveThreshold: (value: number) => void; variableId: string }) {
  const allResults = overview.scalar_results.filter((item) => item.result_group === 'CHASSIS_REAR' && item.unit.toLowerCase() === 'mm' && item.variable_key.includes('permanent_deformation') && hasNumericValue(item.value_double))
  const results = type === 'chassis_summary' ? allResults : variableId ? allResults.filter((item)=>item.variable_key===variableId) : allResults
  const limit = threshold?.threshold_double ?? results.find((item) => hasNumericValue(item.threshold_double))?.threshold_double ?? 5
  const [draftLimit, setDraftLimit] = useState(limit)
  useEffect(() => setDraftLimit(limit), [limit])
  const verdict = results.some((item) => item.verdict === 'FAIL') ? 'FAIL' : results.length ? 'PASS' : 'NO_DATA'
  const maximum = Math.max(...results.map((item) => item.value_double), 0)
  const minimum = results.length ? Math.min(...results.map((item) => item.value_double)) : 0
  if (type !== 'chassis_summary' && variableId && !results.length) return <div className="widget-empty"><Database/><strong>선언된 변수에 결과 데이터가 없습니다.</strong><small>{variableId} 키의 숫자 결과를 가져오면 자동 표시됩니다.</small></div>
  const markerClasses: Record<string, string> = { chassis_rear_top_edge_gap_permanent_deformation:'top-edge', chassis_rear_bottom_edge_gap_permanent_deformation:'bottom-edge', chassis_rear_corner_top_left_permanent_deformation:'top-left', chassis_rear_corner_top_right_permanent_deformation:'top-right', chassis_rear_corner_bottom_left_permanent_deformation:'bottom-left', chassis_rear_corner_bottom_right_permanent_deformation:'bottom-right' }
  const labels: Record<string,string> = { top_edge_gap:'상단 엣지', bottom_edge_gap:'하단 엣지', corner_top_left:'좌상단', corner_top_right:'우상단', corner_bottom_left:'좌하단', corner_bottom_right:'우하단' }
  const chartData = results.map((item) => ({ name: labels[item.variable_key.replace('chassis_rear_','').replace('_permanent_deformation','')] ?? item.display_name, value:item.value_double, verdict:item.verdict }))
  const location = (key:string) => overview.result_locations.find((item) => item.variable_key === key)
  if (!results.length) return <div className="empty-widget">Chassis Rear 결과가 없습니다.</div>
  if (type === 'chassis_summary') return <div className="chassis-widget-summary"><div><span>전체 판정</span><strong className={verdict.toLowerCase()}>{verdict}</strong></div><div><span>최대 영구변형</span><strong>{maximum.toFixed(2)} mm</strong></div>{canManageThreshold ? <label><span>관리 기준</span><div><input aria-label="Chassis Rear 영구변형 기준값" type="number" min="0.1" step="0.1" value={draftLimit} onChange={(e)=>setDraftLimit(Number(e.target.value))}/><button disabled={!Number.isFinite(draftLimit) || draftLimit <= 0} onClick={()=>onSaveThreshold(draftLimit)}>저장</button></div></label> : <div><span>관리 기준</span><strong>{limit.toFixed(1)} mm</strong></div>}<p>적용 대상 {results.length}개 · mm 범위 {minimum.toFixed(2)}~{maximum.toFixed(2)} · 기준 이상은 FAIL입니다.</p></div>
  if (type === 'chassis_diagram') return <div className="chassis-diagram-body compact"><div className="chassis-shell"><div className="chassis-ribs"/><div className="chassis-center"><i/><i/><i/><i/></div>{results.map((item)=><div key={item.id} className={`chassis-marker ${markerClasses[item.variable_key] ?? ''} ${item.verdict.toLowerCase()}`}><span>{item.value_double.toFixed(1)} mm</span><small>{chartData.find((entry)=>entry.value===item.value_double)?.name}</small></div>)}</div><div className="chassis-legend"><span><i className="pass"/>기준 미만</span><span><i className="fail"/>기준 이상</span></div></div>
  if (type === 'chassis_bar') return <ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} layout="vertical" margin={{top:8,right:24,left:8,bottom:4}}><CartesianGrid horizontal={false} stroke="#24384b"/><XAxis type="number" domain={[0,Math.max(8,limit+2)]}/><YAxis type="category" dataKey="name" width={60} tick={{fill:'#8fa6bb',fontSize:10.8}}/><Tooltip formatter={(value:number)=>[`${value} mm`,'영구변형']}/><ReferenceLine x={limit} stroke="#ffbf57" strokeDasharray="5 4"/><Bar dataKey="value">{chartData.map((entry)=><Cell key={entry.name} fill={entry.verdict==='FAIL'?'#ff5d73':'#4fd6a0'}/>)}</Bar></BarChart></ResponsiveContainer>
  if (type === 'chassis_table') return <div className="chassis-result-table"><div className="chassis-result-head"><span>측정 위치</span><span>영구변형</span><span>기준</span><span>판정</span></div>{results.map((item)=>{const point=location(item.variable_key); return <div className="chassis-result-row" key={item.id}><strong>{item.display_name}{point&&<small>NODE {point.entity_id} · ({point.x.toFixed(1)}, {point.y.toFixed(1)}, {point.z.toFixed(1)})</small>}</strong><span>{item.value_double.toFixed(1)} mm</span><span>{hasNumericValue(item.threshold_double) ? `${item.threshold_double.toFixed(1)} mm` : ''}</span><b className={item.verdict.toLowerCase()}>{item.verdict}</b></div>})}</div>
  return <div className="empty-widget">지원하지 않는 Chassis 위젯입니다.</div>
}

function ChassisRearDashboard({ overview, threshold, onSaveThreshold }: { overview: Overview; threshold?: QualityThreshold; onSaveThreshold: (value: number) => void }) {
  const results = overview.scalar_results.filter((item) => item.result_group === 'CHASSIS_REAR' && item.unit.toLowerCase() === 'mm' && item.variable_key.includes('permanent_deformation') && hasNumericValue(item.value_double))
  const limit = threshold?.threshold_double ?? results.find((item) => hasNumericValue(item.threshold_double))?.threshold_double ?? 5
  const [draftLimit, setDraftLimit] = useState(limit)
  useEffect(() => setDraftLimit(limit), [limit])
  const verdict = results.some((item) => item.verdict === 'FAIL') ? 'FAIL' : 'PASS'
  const maximum = Math.max(...results.map((item) => item.value_double), 0)
  const markerClasses: Record<string, string> = {
    chassis_rear_top_edge_gap_permanent_deformation: 'top-edge',
    chassis_rear_bottom_edge_gap_permanent_deformation: 'bottom-edge',
    chassis_rear_corner_top_left_permanent_deformation: 'top-left',
    chassis_rear_corner_top_right_permanent_deformation: 'top-right',
    chassis_rear_corner_bottom_left_permanent_deformation: 'bottom-left',
    chassis_rear_corner_bottom_right_permanent_deformation: 'bottom-right',
  }
  const shortLabels: Record<string, string> = {
    top_edge_gap: '상단 엣지', bottom_edge_gap: '하단 엣지', corner_top_left: '좌상단', corner_top_right: '우상단', corner_bottom_left: '좌하단', corner_bottom_right: '우하단',
  }
  const chartData = results.map((item) => {
    const key = item.variable_key.replace('chassis_rear_', '').replace('_permanent_deformation', '')
    return { name: shortLabels[key] ?? item.display_name, value: item.value_double, verdict: item.verdict }
  })
  const resultLocation = (key: string) => overview.result_locations.find((item) => item.variable_key === key)

  return <div className="chassis-dashboard">
    <section className="chassis-summary-strip"><div><span>CHASSIS REAR RESULT</span><strong className={verdict.toLowerCase()}>{verdict}</strong></div><div><span>최대 영구변형</span><strong>{maximum.toFixed(1)} <small>mm</small></strong></div><div><span>관리 기준값</span><strong>{limit.toFixed(1)} <small>mm</small></strong></div><p>해석 결과는 응력이 아닌 영구변형만 판정에 사용합니다. 측정값이 기준 이상이면 FAIL입니다.</p></section>
    <div className="chassis-dashboard-grid">
      <article className="chassis-card chassis-diagram-card"><header><div><span className="widget-kicker">PERMANENT DEFORMATION MAP</span><h3>Chassis Rear 변형 위치</h3></div><span className="diagram-scene">{overview.load_case.analysis_type.replace('_', ' ')}</span></header><div className="chassis-diagram-body"><div className="chassis-shell"><div className="chassis-ribs"/><div className="chassis-center"><i/><i/><i/><i/></div>{results.map((item) => <div key={item.id} className={`chassis-marker ${markerClasses[item.variable_key] ?? ''} ${item.verdict.toLowerCase()}`}><span>{item.value_double.toFixed(1)} mm</span><small>{chartData.find((entry) => entry.value === item.value_double)?.name}</small></div>)}</div><div className="chassis-legend"><span><i className="pass"/>기준 미만</span><span><i className="fail"/>기준 이상</span><b>Open Cell 기준면 대비 거리 / 모서리 영구변형</b></div></div></article>
      <article className="chassis-card chassis-chart-card"><header><div><span className="widget-kicker">LOCATION COMPARISON</span><h3>위치별 영구변형</h3></div></header><div className="chassis-chart-body"><ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} layout="vertical" margin={{ top: 7, right: 22, left: 10, bottom: 4 }}><CartesianGrid horizontal={false} stroke="#24384b"/><XAxis type="number" domain={[0, Math.max(8, limit + 2)]} tick={{ fill: '#71899f', fontSize: 10.8 }} axisLine={false} tickLine={false}/><YAxis type="category" dataKey="name" width={58} tick={{ fill: '#8fa6bb', fontSize: 10.8 }} axisLine={false} tickLine={false}/><Tooltip contentStyle={{ background: '#102235', border: '1px solid #2d465c', borderRadius: 8 }} formatter={(value: number) => [`${value} mm`, '영구변형']}/><ReferenceLine x={limit} stroke="#ffbf57" strokeDasharray="5 4" label={{ value: `기준 ${limit}`, fill: '#ffbf57', fontSize: 10.8 }}/><Bar dataKey="value" radius={[0,4,4,0]}>{chartData.map((entry) => <Cell key={entry.name} fill={entry.verdict === 'FAIL' ? '#ff5d73' : '#4fd6a0'}/>)}</Bar></BarChart></ResponsiveContainer></div></article>
      <article className="chassis-card threshold-card"><header><div><span className="widget-kicker">ADMIN CRITERION</span><h3>관리자 판정 기준</h3></div></header><div className="threshold-body"><label><span>목표값</span><div><input aria-label="Chassis Rear 영구변형 기준값" type="number" min="0.1" step="0.1" value={draftLimit} onChange={(event) => setDraftLimit(Number(event.target.value))}/><b>mm</b></div></label><button onClick={() => onSaveThreshold(draftLimit)} disabled={!Number.isFinite(draftLimit) || draftLimit <= 0}>기준값 저장</button><p>상·하 엣지 이격 및 네 모서리 영구변형에 동일 기준을 적용합니다.</p></div></article>
      <article className="chassis-card chassis-result-card"><header><div><span className="widget-kicker">FAILURE JUDGEMENT 02</span><h3>Chassis Rear 상세 판정</h3></div></header><div className="chassis-result-table"><div className="chassis-result-head"><span>측정 위치</span><span>영구변형</span><span>기준</span><span>판정</span></div>{results.map((item) => { const location = resultLocation(item.variable_key); return <div className="chassis-result-row" key={item.id}><strong>{item.display_name}{location && <small>NODE {location.entity_id} · ({location.x.toFixed(1)}, {location.y.toFixed(1)}, {location.z.toFixed(1)})</small>}</strong><span>{item.value_double.toFixed(1)} mm</span><span>{hasNumericValue(item.threshold_double) ? `${item.threshold_double.toFixed(1)} mm` : ''}</span><b className={item.verdict.toLowerCase()}>{item.verdict}</b></div> })}</div></article>
    </div>
  </div>
}

function WorkflowView({ workflows, stageEditMode, layoutEditMode, dashboardLayout, layoutVersion, onDashboardLayoutChange, onStepChange, onAddStep, onDeleteStep, onMoveStep, onOpenAnalysis, activeRequestId }: {
  workflows: Workflow[]
  stageEditMode: boolean
  layoutEditMode: boolean
  dashboardLayout: WorkflowDashboardLayout
  layoutVersion: number
  onDashboardLayoutChange: (layout: WorkflowDashboardLayout) => void
  onStepChange: (stepId: string, patch: Partial<WorkflowStep>) => void
  onAddStep: (requestId: string) => void
  onDeleteStep: (requestId: string, stepId: string) => void
  onMoveStep: (requestId: string, stepId: string, offset: -1 | 1) => void
  onOpenAnalysis: (workflow: Workflow) => void
  activeRequestId: string
}) {
  const [sortKey, setSortKey] = useState<'project' | 'category' | 'product' | 'owner' | 'time'>('time')
  const ordered = useMemo(() => [...workflows].sort((a, b) => {
    if (sortKey === 'time') return new Date(b.request.requested_at).getTime() - new Date(a.request.requested_at).getTime()
    const values = {
      project: [a.request.project_name, b.request.project_name],
      category: [a.request.category, b.request.category],
      product: [a.request.product_name, b.request.product_name],
      owner: [a.request.owner, b.request.owner],
    }[sortKey]
    return values[0].localeCompare(values[1], 'ko')
  }), [workflows, sortKey])
  const activeCount = workflows.filter((workflow) => workflow.steps.some((step) => step.status === 'IN_PROGRESS')).length
  const storedItems = new Map(dashboardLayout.items.map((item) => [item.requestId, item]))
  const gridLayout = ordered.map((workflow, index) => {
    const item = storedItems.get(workflow.request.id)
    return { i: workflow.request.id, x: item?.x ?? 0, y: item?.y ?? index * 4, w: item?.w ?? 12, h: item?.h ?? 4, minW: 4, minH: 3 }
  })
  const updateGridLayout = (current: Layout[]) => {
    if (!layoutEditMode) return
    const activeIds = new Set(ordered.map((workflow) => workflow.request.id))
    const items = [
      ...dashboardLayout.items.filter((item) => !activeIds.has(item.requestId)),
      ...current.map((item) => ({ requestId: item.i, x: item.x, y: item.y, w: item.w, h: item.h })),
    ]
    if (JSON.stringify(items) !== JSON.stringify(dashboardLayout.items)) onDashboardLayoutChange({ ...dashboardLayout, items })
  }

  const renderLane = (workflow: Workflow) => {
    const editableStages = stageEditMode && !workflow.work_plan
    return <section className={`workflow-lane ${workflow.request.id === activeRequestId ? 'active-request' : ''}`}>
    <header>
      <div>{layoutEditMode && <span className="workflow-lane-drag-handle"><GripVertical /> 위젯 이동</span>}<span className="workflow-category">{workflow.work_plan?.scenario_name ?? workflow.request.category}</span><h3>{workflow.request.title}</h3><p>{workflow.request.project_name} · {workflow.request.product_name}{workflow.work_plan ? ` · ${workflow.work_plan.source_type === 'EXTERNAL_SYSTEM' ? '외부 시스템' : '부서장 지시'}` : ''}</p></div>
      <div className="workflow-lane-meta"><span>{workflow.request.owner}</span><small>{workflow.work_plan ? `${workflow.completed_count ?? 0} / ${workflow.total_count ?? workflow.steps.length} 작업 완료` : new Date(workflow.request.requested_at).toLocaleDateString('ko-KR')}</small><b>{workflow.progress}%</b>{editableStages ? <button onClick={() => onAddStep(workflow.request.id)}><Plus /> 단계 추가</button> : <button onClick={() => onOpenAnalysis(workflow)}>상세 분석 열기</button>}</div>
    </header>
    {!editableStages && <RequestDemoRunSummary run={workflow.latest_demo_run} />}
    <div className={`workflow-horizontal ${editableStages ? 'editing' : ''}`}>{workflow.steps.map((step, index) => <WorkflowStepItem key={step.id} step={step} last={index === workflow.steps.length - 1} editMode={editableStages} onChange={(patch) => onStepChange(step.id, patch)} onMove={(offset) => onMoveStep(workflow.request.id, step.id, offset)} onDelete={() => onDeleteStep(workflow.request.id, step.id)} />)}</div>
  </section>
  }

  return <div className={`workflow-board ${layoutEditMode ? 'layout-editing' : ''}`} style={{ '--workflow-accent': dashboardLayout.accentColor, '--workflow-font-size': `${dashboardLayout.fontSize * 1.2}px` } as CSSProperties}>
    <section className="workflow-board-head"><div><span>CONCURRENT REQUEST BOARD · LAYOUT v{layoutVersion}</span><h2>의뢰 작업 진행 현황</h2><p>{workflows.length}개 의뢰 · {activeCount}개 동시 진행</p></div><label>정렬 기준<select value={sortKey} onChange={(event) => setSortKey(event.target.value as typeof sortKey)}><option value="project">프로젝트(제품)별</option><option value="category">의뢰별 카테고리</option><option value="product">제품 이름순</option><option value="owner">작업자 이름</option><option value="time">시간순</option></select></label></section>
    <ResponsiveGridLayout className="workflow-dashboard-grid" layouts={{ lg: gridLayout }} breakpoints={{ lg: 900, md: 600, sm: 0 }} cols={{ lg: 12, md: 8, sm: 1 }} rowHeight={84} margin={[14, 14]} isDraggable={layoutEditMode} isResizable={layoutEditMode} draggableHandle=".workflow-lane-drag-handle" compactType="vertical" onLayoutChange={updateGridLayout}>
      {ordered.map((workflow) => <div key={workflow.request.id}>{renderLane(workflow)}</div>)}
    </ResponsiveGridLayout>
  </div>
}

function WorkflowStepItem({ step, last, editMode, onChange, onMove, onDelete }: { step: WorkflowStep; last: boolean; editMode: boolean; onChange: (patch: Partial<WorkflowStep>) => void; onMove: (offset: -1 | 1) => void; onDelete: () => void }) {
  const statusText = { READY: '시작 대기', COMPLETED: '완료', IN_PROGRESS: '진행 중', WAITING: '대기', BLOCKED: '차단', FAILED: '실패' }[step.status]
  return <div className={`workflow-step-horizontal ${step.status.toLowerCase()} ${editMode ? 'editing' : ''}`} data-testid={step.id.startsWith('draft-step-') ? 'draft-workflow-step' : undefined}>
    <div className="step-track-horizontal"><span>{step.status === 'COMPLETED' ? <Check /> : step.sequence_no}</span>{!last && <i />}</div>
    {editMode ? <div className="workflow-step-editor">
      <div className="workflow-step-actions"><button aria-label={`${step.sequence_no}단계 앞으로 이동`} onClick={() => onMove(-1)} disabled={step.sequence_no === 1}><ArrowLeft /></button><button aria-label={`${step.sequence_no}단계 뒤로 이동`} onClick={() => onMove(1)} disabled={last}><ArrowRight /></button><button className="danger" aria-label={`${step.sequence_no}단계 삭제`} onClick={onDelete}><Trash2 /></button></div>
      <label><span>단계명</span><input value={step.name} onChange={(event) => onChange({ name: event.target.value })} aria-label={`${step.sequence_no}단계 이름`} /></label>
      <label><span>담당자</span><input value={step.owner} onChange={(event) => onChange({ owner: event.target.value })} aria-label={`${step.sequence_no}단계 담당자`} /></label>
      <div>
        <label><span>상태</span><select value={step.status} onChange={(event) => onChange({ status: event.target.value as WorkflowStep['status'] })} aria-label={`${step.sequence_no}단계 상태`}><option value="READY">시작 대기</option><option value="WAITING">대기</option><option value="IN_PROGRESS">진행 중</option><option value="BLOCKED">차단</option><option value="FAILED">실패</option><option value="COMPLETED">완료</option></select></label>
        <label><span>진행률</span><input type="number" min="0" max="100" value={step.progress} onChange={(event) => onChange({ progress: Math.max(0, Math.min(100, Number(event.target.value))) })} aria-label={`${step.sequence_no}단계 진행률`} /></label>
      </div>
      <label className="workflow-optional"><input type="checkbox" checked={step.is_optional} onChange={(event) => onChange({ is_optional: event.target.checked })} /><span>선택 단계</span></label>
      <label><span>메모</span><textarea value={step.note ?? ''} onChange={(event) => onChange({ note: event.target.value })} aria-label={`${step.sequence_no}단계 메모`} /></label>
    </div> : <><div className="step-copy-horizontal"><strong title={step.name}>{step.name}</strong><span>{step.owner}</span>{step.is_optional && <em>선택</em>}</div><div className="step-progress-horizontal"><i><b style={{ width: `${step.progress}%` }} /></i><span>{step.progress}%</span></div><b className="status-badge">{statusText}</b></>}
  </div>
}

export default App
