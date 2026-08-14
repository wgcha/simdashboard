import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Check,
  ChevronDown,
  CircleDot,
  ClipboardPlus,
  Database,
  Download,
  GripVertical,
  LayoutDashboard,
  LoaderCircle,
  Lock,
  MessageSquareText,
  Minus,
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
import { type Layout, type Layouts } from 'react-grid-layout'
import { api } from './api'
import { clearSession, saveSession, type AuthUser } from './auth'
import { useReportLayoutEditorState, useWorkspaceEditorCoordinator } from './editorState'
import { BootstrapWorkspaceShell } from './features/bootstrap/BootstrapWorkspaceShell'
import type { InitialWorkspace } from './features/bootstrap/loadInitialWorkspace'
import { useWorkspaceBootstrap } from './features/bootstrap/useWorkspaceBootstrap'
import { AppShell, AppShellMain, AppTopbar } from './app/shell/AppShell'
import { AppSidebar } from './app/shell/AppSidebar'
import { loadWorkspacePreferences, saveWorkspacePreference, type WorkspaceTheme } from './app/preferences/workspacePreferences'
import { pageView, preferredPage, visiblePages, type ActiveView } from './features/analysis/pageSelection'
import { DEFAULT_PORTFOLIO_LAYOUT, DEFAULT_WORKFLOW_DASHBOARD_LAYOUT, loadPortfolioLayout, loadWorkflowDashboardLayout } from './features/layouts/layoutDefaults'
import { withReportVariables } from './features/reports/reportLayoutUtils'
import { ApprovalPendingScreen, LoginScreen } from './features/auth/LoginScreen'
import { firstAllowedWorkspacePage, hasPermission, visibleMenuItems, type MenuId, type MenuPolicy, type WorkspacePage } from './features/auth/access'
import { WORKSPACE_ROUTES_BY_ID } from './features/navigation/workspaceRouteRegistry'
import { AccessAdminPage, AuditAdminPage, MenuPolicyAdminPage } from './features/access/AccessAdministration'
import { SimulationWorkbench, WorkbenchTypeAdmin } from './features/workbench/SimulationWorkbench'
import { RequestIntakePage } from './features/workbench/RequestIntakePage'
import { PortfolioDashboard } from './PortfolioDashboard'
import type { RunComparisonReportContext } from './features/results/AnalysisWidgets'
import type { ReportExportOptions } from './reportExport'
import type { AnalysisRequest, AnalysisRunSummary, AutomationTemplate, DashboardDefinition, DashboardPageSummary, DashboardSummary, DashboardVersion, DashboardWidget, FeatureExample, ImportSchema, LoadCase, Overview, PortfolioLayout, Project, QualityThreshold, ReportContentItem, ReportElementDefinition, ReportElementType, ReportLayout, ReportLayoutDefinition, ReportLayoutVersion, ReportSection, ReportSlideDefinition, ReportSlideKind, ReportSource, ReportTemplateAsset, ReviewItem, RunComparison, RunTrust, VariableDefinition, VariableDefinitionInput, WidgetCatalogItem, Workflow, WorkflowDashboardLayout, WorkflowStep } from './types'

const DataWorkspace = lazy(() => import('./features/data/DataWorkspace').then(({ DataWorkspace }) => ({ default: DataWorkspace })))
const FolderSchemaWorkspace = lazy(() => import('./features/data/FolderSchemaWorkspace').then(({ FolderSchemaWorkspace }) => ({ default: FolderSchemaWorkspace })))
const VariableCatalogPage = lazy(() => import('./features/data/VariableCatalogPage').then(({ VariableCatalogPage }) => ({ default: VariableCatalogPage })))
const AutomationTemplatesPage = lazy(() => import('./features/workbench/AutomationTemplatesPage').then(({ AutomationTemplatesPage }) => ({ default: AutomationTemplatesPage })))
const FeatureExampleGallery = lazy(() => import('./features/examples/FeatureExampleGallery').then(({ FeatureExampleGallery }) => ({ default: FeatureExampleGallery })))
const HelpCenter = lazy(() => import('./features/help/HelpCenter').then(({ HelpCenter }) => ({ default: HelpCenter })))
const WorkflowView = lazy(() => import('./features/requests/WorkflowView').then(({ WorkflowView }) => ({ default: WorkflowView })))
const ResultsWorkspace = lazy(() => import('./features/results/ResultsWorkspace').then(({ ResultsWorkspace }) => ({ default: ResultsWorkspace })))
const WidgetSettingsPanel = lazy(() => import('./features/results/AnalysisWidgets').then(({ WidgetSettingsPanel }) => ({ default: WidgetSettingsPanel })))
const ReportLayoutEditor = lazy(() => import('./features/reports/ReportLayoutEditor').then(({ ReportLayoutEditor }) => ({ default: ReportLayoutEditor })))
const AnalysisPageManager = lazy(() => import('./features/analysis/AnalysisPageManager').then(({ AnalysisPageManager }) => ({ default: AnalysisPageManager })))

function FeatureScreenFallback() {
  return <div className="full-state"><LoaderCircle className="spin" /> 화면을 준비하고 있습니다.</div>
}

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

function App() {
  const [preferences] = useState(loadWorkspacePreferences)
  const [theme, setTheme] = useState<WorkspaceTheme>(preferences.theme)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(preferences.sidebarCollapsed)
  const [uiFontSize, setUiFontSize] = useState(preferences.uiFontSize)
  const [authReady, setAuthReady] = useState(false)
  const [authRequired, setAuthRequired] = useState(false)
  const [authMode, setAuthMode] = useState<'disabled' | 'password' | 'oidc'>('disabled')
  const [authUser, setAuthUser] = useState<AuthUser | null>(null)
  const [menuPolicy, setMenuPolicy] = useState<MenuPolicy | null>(null)
  const [menuPolicyReady, setMenuPolicyReady] = useState(false)
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
  const visibleMenus = useMemo(() => {
    const validated = menuPolicy ? {
      ...menuPolicy,
      menus: menuPolicy.menus.filter((menu) => {
        const local = WORKSPACE_ROUTES_BY_ID.get(menu.id)
        return local?.requiredPermission === menu.required_permission && local.contextKind === menu.context_kind
      }),
    } : null
    if (validated) return visibleMenuItems(validated, authUser, selectedProjectId)
    if (menuPolicyReady && authUser?.account_status === 'ACTIVE' && authUser.is_global_admin) {
      return (['menu_policy_admin', 'audit_admin'] as MenuId[]).map((id, index) => {
        const local = WORKSPACE_ROUTES_BY_ID.get(id)!
        return { id, label: local.label, required_permission: local.requiredPermission, context_kind: local.contextKind, sequence_no: 900 + index, is_policy_editable: false, visibility: { general: false, power: false, admin: false } }
      })
    }
    return []
  }, [authUser, menuPolicy, menuPolicyReady, selectedProjectId])
  const allowedPages = useMemo(() => new Set(visibleMenus.map((menu) => menu.id)), [visibleMenus])

  useEffect(() => {
    if (!authUser || authUser.account_status !== 'ACTIVE') return
    if (!menuPolicyReady) return
    if (allowedPages.has(workspacePage)) return
    const fallback = firstAllowedWorkspacePage(menuPolicy, authUser, selectedProjectId) ?? visibleMenus[0]?.id
    if (fallback) setWorkspacePage(fallback)
  }, [allowedPages, authUser?.id, authUser?.account_status, menuPolicy?.version, menuPolicyReady, selectedProjectId, workspacePage])

  useEffect(() => {
    if (!notice) return
    const timeout = window.setTimeout(() => setNotice((current) => current === notice ? '' : current), 4000)
    return () => window.clearTimeout(timeout)
  }, [notice])

  useEffect(() => {
    saveWorkspacePreference('sidebarCollapsed', sidebarCollapsed)
  }, [sidebarCollapsed])

  useEffect(() => {
    saveWorkspacePreference('uiFontSize', uiFontSize)
  }, [uiFontSize])

  useEffect(() => {
    saveWorkspacePreference('theme', theme)
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
  }, [theme])

  useEffect(() => {
    const prepareAuthentication = async () => {
      try {
        const status = await api.authStatus()
        setAuthMode(status.mode)
        setAuthRequired(status.authentication_required)
        try {
          const verified = await api.me()
          setAuthUser(verified)
        } catch {
          if (!status.authentication_required) throw new Error('로컬 관리자 세션을 만들지 못했습니다.')
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

  const refreshAccess = async () => {
    if (!authUser) return
    const [verified, policy] = await Promise.all([api.me(), api.menuPolicy()])
    setAuthUser(verified)
    setMenuPolicy(policy)
    setMenuPolicyReady(true)
  }

  useEffect(() => {
    const changed = () => { void refreshAccess().catch(() => { setMenuPolicy(null); setMenuPolicyReady(true) }) }
    window.addEventListener('analysis-access-changed', changed)
    return () => window.removeEventListener('analysis-access-changed', changed)
  }, [authUser?.id])

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

  const applyInitialWorkspace = useCallback((initial: InitialWorkspace) => {
        setProjects(initial.projects)
        setWorkflows(initial.workflows)
        setMenuPolicy(initial.menuPolicy)
        setMenuPolicyReady(true)
        setDatabaseBackend(initial.databaseBackend)
        setRequests([])
        setLoadCases([])
        setThresholds([])
        setSelectedProjectId('')
        setSelectedRequestId('')
        setSelectedLoadCaseId('')
        setOverview(null)
        setDashboard(null)
        setAnalysisPages([])
        if (initial.kind === 'empty') return
        if (initial.kind === 'setup') {
          setSelectedProjectId(initial.selectedProjectId)
          setRequests(initial.requests)
          setSelectedRequestId(initial.selectedRequestId)
          return
        }
        setRequests(initial.requests)
        setLoadCases(initial.loadCases)
        setThresholds(initial.thresholds)
        setSelectedProjectId(initial.selectedProjectId)
        setSelectedRequestId(initial.selectedRequestId)
        setSelectedLoadCaseId(initial.selectedLoadCaseId)
        setOverview(initial.overview)
        setDashboard(initial.dashboard)
        setAnalysisPages(initial.analysisPages)
        setActiveDashboardId(initial.dashboardId)
        setPortfolioLayout(initial.portfolioLayout.definition)
        setPortfolioLayoutVersion(initial.portfolioLayout.version)
        setWorkflowDashboardLayout(initial.workflowLayout.definition)
        setWorkflowLayoutVersion(initial.workflowLayout.version)
        setActiveView('workflow')
  }, [])

  const { state: workspaceBootstrap, invalidate: invalidateWorkspaceBootstrap } = useWorkspaceBootstrap({
    userKey: authReady && authUser?.account_status === 'ACTIVE' ? authUser.id : null,
    onStart: () => setError(''),
    onResolved: applyInitialWorkspace,
  })

  useEffect(() => {
    const expired = () => {
      invalidateWorkspaceBootstrap()
      setAuthUser(null)
      setAuthError('로그인 세션이 만료되었습니다. 다시 로그인하세요.')
    }
    window.addEventListener('analysis-auth-expired', expired)
    return () => window.removeEventListener('analysis-auth-expired', expired)
  }, [invalidateWorkspaceBootstrap])

  const handleLogin = async (username: string, password: string) => {
    setAuthError('')
    try {
      const result = await api.login(username, password)
      const verified = await api.me()
      saveSession(result.access_token, verified)
      setAuthUser(verified)
    } catch (reason) {
      setAuthError(reason instanceof Error ? reason.message : '로그인하지 못했습니다.')
      throw reason
    }
  }

  const logout = async () => {
    const logoutRequest = api.logout()
    invalidateWorkspaceBootstrap()
    setAuthUser(null)
    try { await logoutRequest } catch { /* clear the local session even if the server is unavailable */ }
    clearSession()
    setMenuPolicy(null)
    setMenuPolicyReady(false)
    setOverview(null)
  }

  useEffect(() => {
    if (!activeDashboardId || !selectedLoadCaseId || !authReady || (authRequired && !authUser)) return
    const requestSequence = ++dashboardRequestSequence.current
    setDashboardLoading(true)
    api.dashboard(activeDashboardId)
      .then((definition) => {
        if (requestSequence === dashboardRequestSequence.current && definition.id === activeDashboardId) setDashboard(definition)
      })
      .catch((reason) => {
        if (requestSequence === dashboardRequestSequence.current) {
          setDashboard(null)
          setNotice(reason instanceof Error ? reason.message : '분석 레이아웃을 불러오지 못했습니다.')
        }
      })
      .finally(() => {
        if (requestSequence === dashboardRequestSequence.current) setDashboardLoading(false)
      })
    return () => {
      if (requestSequence === dashboardRequestSequence.current) dashboardRequestSequence.current += 1
    }
  }, [activeDashboardId, selectedLoadCaseId, authReady, authRequired, authUser?.id])

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
    const [requestData, storedPortfolioLayout, storedWorkflowLayout] = await Promise.all([
      api.requests(projectId),
      api.workspaceLayout(projectId, 'portfolio'),
      api.workspaceLayout(projectId, 'workflow'),
    ])
    const request = requestData.find((item) => item.id === requestId) ?? requestData[0]
    if (!request) throw new Error('선택한 프로젝트에 의뢰가 없습니다.')
    const [caseData, thresholdData] = await Promise.all([api.loadCases(request.id), api.qualityThresholds(projectId)])
    const loadCase = caseData[0]
    if (!loadCase) throw new Error('선택한 의뢰에 하중 경우가 없습니다.')
    const [overviewData, pageData] = await Promise.all([api.overview(loadCase.id), api.dashboardPages(loadCase.id)])
    const selectedPage = preferredPage(pageData, overviewData, preferredView)
    const dashboardData = selectedPage ? await api.dashboard(selectedPage.id).catch(() => null) : null
    setRequests(requestData)
    setLoadCases(caseData)
    setThresholds(thresholdData)
    setSelectedProjectId(projectId)
    setPortfolioLayout(storedPortfolioLayout.definition)
    setPortfolioLayoutVersion(storedPortfolioLayout.version)
    setWorkflowDashboardLayout(storedWorkflowLayout.definition)
    setWorkflowLayoutVersion(storedWorkflowLayout.version)
    setSelectedRequestId(request.id)
    setSelectedLoadCaseId(loadCase.id)
    setOverview(overviewData)
    setAnalysisPages(pageData)
    if (selectedPage) { setActiveDashboardId(selectedPage.id); setActiveView(pageView(selectedPage)); if (dashboardData) setDashboard(dashboardData) }
  }

  const loadMonitoringContext = async (projectId: string, requestId?: string) => {
    setError('')
    const [requestData, storedPortfolioLayout, storedWorkflowLayout] = await Promise.all([
      api.requests(projectId),
      api.workspaceLayout(projectId, 'portfolio'),
      api.workspaceLayout(projectId, 'workflow'),
    ])
    const request = requestData.find((item) => item.id === requestId) ?? requestData[0]
    if (!request) throw new Error('선택한 프로젝트에 의뢰가 없습니다.')
    const [caseData, thresholdData] = await Promise.all([api.loadCases(request.id), api.qualityThresholds(projectId)])
    setRequests(requestData)
    setLoadCases(caseData)
    setThresholds(thresholdData)
    setSelectedProjectId(projectId)
    setPortfolioLayout(storedPortfolioLayout.definition)
    setPortfolioLayoutVersion(storedPortfolioLayout.version)
    setWorkflowDashboardLayout(storedWorkflowLayout.definition)
    setWorkflowLayoutVersion(storedWorkflowLayout.version)
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
        const stored = await api.saveWorkspaceLayout(selectedProjectId, 'portfolio', portfolioLayout)
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
          const stored = await api.saveWorkspaceLayout(selectedProjectId, 'workflow', workflowDashboardLayout)
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
      const signature = (steps: WorkflowStep[]) => JSON.stringify(steps.map((step) => ({ id: step.id, name: step.name, status: step.status, owner_user_id: step.owner_user_id, progress: step.progress, is_optional: step.is_optional, note: step.note ?? '' })))
      const originalByRequest = new Map(original.map((workflow) => [workflow.request.id, workflow]))
      const changed = workflows.filter((workflow) => signature(workflow.steps) !== signature(originalByRequest.get(workflow.request.id)?.steps ?? []))
      const invalid = changed.flatMap((workflow) => workflow.steps).find((step) => step.name.trim().length < 2 || !step.owner.trim() || step.progress < 0 || step.progress > 100)
      if (invalid) { setError('단계 이름은 두 글자 이상, 담당자는 필수이며 진행률은 0~100이어야 합니다.'); return }
      try {
        await Promise.all(changed.map((workflow) => api.replaceWorkflowSteps(workflow.request.id, workflow.steps.map((step) => ({ id: step.id.startsWith('draft-step-') ? null : step.id, name: step.name.trim(), status: step.status, owner_user_id: step.owner_user_id ?? workflow.request.owner_user_id ?? '', progress: step.progress, is_optional: step.is_optional, note: step.note ?? '' })))))
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

  const navigateWorkspace = (menuId: MenuId) => {
    const route = WORKSPACE_ROUTES_BY_ID.get(menuId)
    if (!route) return
    if (route.navigationKind === 'open-dashboard') {
      openDashboardWorkspace()
      return
    }
    setWorkspacePage(route.page)
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
      const steps = [...workflow.steps, { id: `draft-step-${crypto.randomUUID()}`, sequence_no: sequence, name: '새 진행 단계', status: 'WAITING' as const, owner: workflow.request.owner || '미지정', owner_user_id: workflow.request.owner_user_id ?? null, progress: 0, planned_end: workflow.request.due_at, is_optional: false, note: '' }]
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
    setSelectedProjectId((current) => current || projectData[0]?.id || '')
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
    return <LoginScreen mode={authMode === 'oidc' ? 'oidc' : 'password'} error={authError} onLogin={handleLogin} theme={theme} onThemeChange={setTheme} />
  }

  if (authUser?.account_status === 'PENDING') {
    return <ApprovalPendingScreen displayName={authUser.display_name} onLogout={() => void logout()} />
  }

  if (workspaceBootstrap.status === 'idle' || workspaceBootstrap.status === 'loading') {
    return <div className="full-state"><LoaderCircle className="spin" /> 데이터와 레이아웃을 준비하고 있습니다.</div>
  }

  if (workspaceBootstrap.status === 'failed') {
    return <div className="full-state error"><AlertTriangle /> {workspaceBootstrap.message}</div>
  }

  if (error) {
    return <div className="full-state error"><AlertTriangle /> {error}</div>
  }

  if (!overview || !dashboard) {
    const canCreateProject = hasPermission(authUser, 'system.user.approve', selectedProjectId)
    const canCreateRequest = hasPermission(authUser, 'request.create', selectedProjectId)
    const canRegisterData = canCreateProject || hasPermission(authUser, 'result.import', selectedProjectId)
    const setupPage = workspacePage === 'intake' && projects.length > 0 ? 'intake' : 'data'
    return <BootstrapWorkspaceShell
      theme={theme}
      activePage={setupPage}
      displayName={authUser?.display_name ?? '사용자'}
      databaseBackend={databaseBackend}
      canOpenIntake={projects.length > 0 && canCreateRequest}
      onPageChange={setWorkspacePage}
      onThemeChange={setTheme}
      onLogout={() => void logout()}
    >
      {!canRegisterData && projects.length === 0 ? <div className="bootstrap-empty-access"><AlertTriangle /><h1>접근 가능한 프로젝트가 없습니다.</h1><p>전역 관리자에게 프로젝트 생성 또는 멤버십 할당을 요청하세요.</p></div> : setupPage === 'intake' ? (
        <RequestIntakePage projects={projects} createdBy={authUser?.display_name ?? '사용자'} canCreate={canCreateRequest} onCreated={handleIntakeCreated} onOpenWorkbench={() => setWorkspacePage('data')} />
      ) : (
        <Suspense fallback={<FeatureScreenFallback />}><DataWorkspace canCreateProject={canCreateProject} projects={projects} initialProjectId={selectedProjectId} onDataChanged={refreshOperationalData} onOpenAnalysis={openImportedResult} onOpenIntake={() => setWorkspacePage('intake')} /></Suspense>
      )}
    </BootstrapWorkspaceShell>
  }

  if (!allowedPages.has(workspacePage)) {
    return <div className="full-state"><LoaderCircle className="spin" /> 허용된 첫 화면으로 이동하고 있습니다.</div>
  }
  if (workspacePage === 'menu_policy_admin' && !menuPolicy) {
    return <div className="full-state error"><AlertTriangle /> 메뉴 정책을 불러오지 못했습니다. 감사로그와 서버 상태를 확인하세요.</div>
  }

  const projectWorkflows = workflows.filter((workflow) => workflow.request.project_id === selectedProjectId)
  const selectedWorkflow = workflows.find((workflow) => workflow.request.id === selectedRequestId)
  // The monitoring tab is scoped to the request selected in the context
  // controls.  Showing a project-wide average here made the same request read
  // differently from its monitoring card and the operations dashboard.
  const workflowProgress = selectedWorkflow?.progress ?? 0
  const chassisThreshold = thresholds.find((item) => item.criterion_key === 'chassis_rear_permanent_deformation_mm')
  const openCellThreshold = thresholds.find((item) => item.criterion_key === 'open_cell_stress_mpa')
  const canDashboardEdit = hasPermission(authUser, 'dashboard.edit', selectedProjectId)
  const canWorkflowEdit = hasPermission(authUser, 'workflow.edit', selectedProjectId)
  const canLayoutEdit = hasPermission(authUser, 'project.layout.edit', selectedProjectId)
  const canEdit = workspacePage === 'portfolio' ? canLayoutEdit : activeView === 'workflow' ? canWorkflowEdit || canLayoutEdit : canDashboardEdit
  const canManagePages = canDashboardEdit
  const canExecuteAssigned = hasPermission(authUser, 'work.execute_assigned', selectedProjectId)
  const canExecuteAny = hasPermission(authUser, 'work.execute_any', selectedProjectId)
  const analysisTabs = visiblePages(analysisPages, overview)
  const activeAnalysisPage = analysisPages.find((page) => page.id === activeDashboardId)
  const staticBreadcrumb = WORKSPACE_ROUTES_BY_ID.get(workspacePage)?.breadcrumb
  const breadcrumb = workspacePage === 'dashboard' ? activeView === 'workflow' ? <><span>의뢰</span><b>/</b><span>{selectedWorkflow?.request.project_name ?? '프로젝트 미지정'}</span><b>/</b><strong>{selectedWorkflow?.request.title ?? '의뢰 선택'}</strong></> : <><span>프로젝트</span><b>/</b><span>{overview.load_case.project_name}</span><b>/</b><strong>{overview.load_case.name}</strong></> : <><span>{staticBreadcrumb?.section}</span><b>/</b><strong>{staticBreadcrumb?.title}</strong></>

  return (
    <AppShell
      className={`app-shell ${theme === 'light' ? 'light-theme' : 'dark-theme'} ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}
      sidebar={<AppSidebar
        activePage={workspacePage}
        collapsed={sidebarCollapsed}
        databaseBackend={databaseBackend}
        fontSize={uiFontSize}
        menus={visibleMenus}
        signedIn={Boolean(authUser)}
        userBadge={authUser ? authUser.is_global_admin ? 'GLOBAL ADMIN' : authUser.memberships.find((item) => item.project_id === selectedProjectId)?.role.toUpperCase() ?? 'NONMEMBER' : undefined}
        userDisplayName={authUser?.display_name}
        onDecreaseFontSize={() => setUiFontSize((value) => Math.max(11, value - 1))}
        onIncreaseFontSize={() => setUiFontSize((value) => Math.min(18, value + 1))}
        onLogout={() => void logout()}
        onNavigate={navigateWorkspace}
        onToggleCollapsed={() => setSidebarCollapsed((value) => !value)}
      />}
      sidebarCollapsed={sidebarCollapsed}
      style={{ '--ui-font-size': `${uiFontSize}pt` } as CSSProperties}
      theme={theme}
    >

      <AppShellMain topbar={<AppTopbar breadcrumb={breadcrumb} actions={<>
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
          </>} />}>

        {workspacePage === 'portfolio' ? <PortfolioDashboard refreshToken={operationalRefreshToken} editMode={editMode} layout={portfolioLayout} layoutVersion={portfolioLayoutVersion} onLayoutChange={setPortfolioLayout} onCancelEdit={cancelEditing} onResetLayout={resetPortfolioLayout} onOpen={(projectId, requestId) => void openPortfolioRequest(projectId, requestId)} /> : workspacePage === 'intake' ? <RequestIntakePage projects={projects} createdBy={authUser?.display_name ?? '데모 사용자'} canCreate={hasPermission(authUser, 'request.create', selectedProjectId)} onCreated={handleIntakeCreated} onOpenWorkbench={openIntakeWorkbench} /> : workspacePage === 'workbench' ? <SimulationWorkbench workflows={workflows} initialRequestId={selectedRequestId} currentUserId={authUser?.id ?? ''} createdBy={authUser?.display_name ?? '데모 사용자'} canExecute={canExecuteAssigned || canExecuteAny} isAdmin={canExecuteAny} onRequestSelected={setSelectedRequestId} onChanged={async (message) => { setWorkflows(await api.workflows()); setOperationalRefreshToken((value) => value + 1); setNotice(message) }} /> : workspacePage === 'workbench_admin' ? <WorkbenchTypeAdmin /> : workspacePage === 'schemas' ? <Suspense fallback={<FeatureScreenFallback />}><FolderSchemaWorkspace /></Suspense> : workspacePage === 'variables' ? <Suspense fallback={<FeatureScreenFallback />}><VariableCatalogPage variables={variables} overview={overview} loadCaseId={selectedLoadCaseId} onChanged={(items) => { setVariables(items); setCatalogVariable((current) => items.some((item) => item.id === current) ? current : items[0]?.id ?? '') }} /></Suspense> : workspacePage === 'templates' ? <Suspense fallback={<FeatureScreenFallback />}><AutomationTemplatesPage /></Suspense> : workspacePage === 'examples' ? <Suspense fallback={<FeatureScreenFallback />}><FeatureExampleGallery onOpen={openFeatureExample} /></Suspense> : workspacePage === 'help' ? <Suspense fallback={<FeatureScreenFallback />}><HelpCenter onNavigate={setWorkspacePage} /></Suspense> : workspacePage === 'access_admin' ? <AccessAdminPage projectId={selectedProjectId} canApproveUsers={hasPermission(authUser, 'system.user.approve', selectedProjectId)} onAccessChanged={refreshAccess} /> : workspacePage === 'menu_policy_admin' && menuPolicy ? <MenuPolicyAdminPage policy={menuPolicy} onPolicyChanged={setMenuPolicy} /> : workspacePage === 'audit_admin' ? <AuditAdminPage /> : workspacePage === 'data' ? (
          <Suspense fallback={<FeatureScreenFallback />}><DataWorkspace canCreateProject={hasPermission(authUser, 'system.user.approve', selectedProjectId)} projects={projects} initialProjectId={selectedProjectId} onDataChanged={refreshOperationalData} onOpenAnalysis={openImportedResult} onOpenIntake={() => setWorkspacePage('intake')} /></Suspense>
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

        {activeView !== 'workflow' ? <Suspense fallback={<FeatureScreenFallback />}><ResultsWorkspace
          activeView={activeView}
          canEdit={canEdit}
          canManageThresholds={canManagePages}
          chassisThreshold={chassisThreshold}
          dashboard={dashboard}
          editMode={editMode}
          loadCaseId={selectedLoadCaseId}
          onBeginEditing={beginEditing}
          onComparisonContext={setComparisonReportContext}
          onConfigureWidget={setSelectedWidgetId}
          onLayoutChange={handleLayoutChange}
          onOpenAssistant={() => setAssistantOpen(true)}
          onRemoveWidget={removeWidget}
          onSaveChassisThreshold={saveChassisThreshold}
          onSaveOpenCellThreshold={saveOpenCellThreshold}
          openCellThreshold={openCellThreshold}
          overview={overview}
          selectedEdges={selectedEdges}
        /></Suspense> : (
            <Suspense fallback={<FeatureScreenFallback />}><WorkflowView workflows={projectWorkflows} stageEditMode={editMode && workflowEditorMode === 'stages'} layoutEditMode={editMode && workflowEditorMode === 'layout'} dashboardLayout={workflowDashboardLayout} layoutVersion={workflowLayoutVersion} onDashboardLayoutChange={setWorkflowDashboardLayout} onStepChange={updateWorkflowStepDraft} onAddStep={addWorkflowStepDraft} onDeleteStep={deleteWorkflowStepDraft} onMoveStep={moveWorkflowStepDraft} onOpenAnalysis={openWorkflowAnalysis} activeRequestId={selectedRequestId} /></Suspense>
          )}
        </>}
      </AppShellMain>

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
      {selectedWidgetId && dashboard && <Suspense fallback={<FeatureScreenFallback />}><WidgetSettingsPanel widget={dashboard.widgets.find((item) => item.id === selectedWidgetId)!} variables={variables} onChange={(patch) => updateWidget(selectedWidgetId, patch)} onClose={() => setSelectedWidgetId(null)} /></Suspense>}
      {pageManagerOpen && <Suspense fallback={null}><AnalysisPageManager loadCaseId={selectedLoadCaseId} activePageId={activeDashboardId} visiblePageIds={analysisTabs.map((page) => page.id)} onClose={() => setPageManagerOpen(false)} onPagesChanged={setAnalysisPages} onActiveDefinitionChanged={(definition) => setDashboard(definition)} onDeleted={(deletedId) => {
        if (reportPageId === deletedId) { setReportDraft(null); setReportOverview(null); setReportLayoutDraft(null); setReportPageId(''); reportLayoutEditor.close() }
        dashboardBeforeEdit.current = null; setSelectedWidgetId(null); setAssistantOpen(false)
      }} onActivate={(definition, startEditing = false) => {
        if (!definition.page) return
        if (editMode) cancelEditing()
        const summary: DashboardPageSummary = { id: definition.id, project_id: selectedProjectId, request_id: selectedRequestId, load_case_id: selectedLoadCaseId, name: definition.name, description: definition.description, version: definition.version ?? 1, updated_at: definition.updated_at ?? new Date().toISOString(), page: definition.page }
        setAnalysisPages((items) => [...items.filter((item) => item.id !== summary.id), summary].sort((left, right) => left.page.display_order - right.page.display_order))
        setDashboard(definition); setActiveDashboardId(definition.id); setActiveView(pageView(summary)); setPageManagerOpen(false)
        if (startEditing) { dashboardBeforeEdit.current = structuredClone(definition); workspaceEditor.open('analysis-dashboard'); setAssistantOpen(true) }
      }} /></Suspense>}
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
            {reportLayoutEditor.isEditing && <div data-testid="ppt-layout-editor"><Suspense fallback={<FeatureScreenFallback />}><ReportLayoutEditor layout={reportLayoutDraft} overview={reportOverview} contents={reportContents} templates={reportTemplates} isSystem={Boolean(reportLayouts.find((item) => item.id === reportLayoutDraft.id)?.is_system)} onChange={setReportLayoutDraft} onTemplateUpload={(file) => void uploadReportTemplate(file)} onTemplateDelete={(id) => void removeReportTemplate(id)} onSave={() => void saveReportLayout(false)} onSaveAs={() => void saveReportLayout(true)} onDelete={() => void removeReportLayout()} /></Suspense></div>}
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
    </AppShell>
  )
}

function EdgeFilter({ selected, setSelected }: { selected: string[]; setSelected: (value: string[]) => void }) {
  const options = [['top', '상단'], ['bottom', '하단'], ['left', '좌측'], ['right', '우측']]
  const toggle = (key: string) => setSelected(selected.includes(key) ? selected.filter((item) => item !== key) : [...selected, key])
  return <div className="edge-filter"><span>표시 엣지</span>{options.map(([key, label]) => <button className={selected.includes(key) ? 'on' : ''} key={key} onClick={() => toggle(key)}><i />{label}</button>)}</div>
}




export default App
