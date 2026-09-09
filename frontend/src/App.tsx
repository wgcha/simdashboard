import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Check,
  ChevronDown,
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
import { api } from './api'; import { workbenchApi } from './features/workbench/api'
import { useRequestResultSnapshot } from './features/results/useRequestResultSnapshot'
import { createWorkflowAnalysisOpener, isPendingResultAnalysis, useResultAnalysisIntent } from './features/results/resultLayoutRouting'; import { explicitCustomAnalysisPage, preservesResultLayoutOnLoadCaseChange } from './features/results/resultLayoutRuntime'
import type { AuthUser } from './auth'
import { useWorkspaceEditorCoordinator } from './editorState'
import { BootstrapWorkspaceShell } from './features/bootstrap/BootstrapWorkspaceShell'
import type { InitialWorkspace } from './features/bootstrap/loadInitialWorkspace'
import { useWorkspaceBootstrap } from './features/bootstrap/useWorkspaceBootstrap'
import { AppShell, AppShellMain, AppTopbar } from './app/shell/AppShell'
import { AppSidebar } from './app/shell/AppSidebar'
import { PersonalPcRoute } from './app/workspace/PersonalPcRoute'
import { ProjectSetupState } from './app/workspace/ProjectSetupState'
import { loadWorkspacePreferences, saveWorkspacePreference, type WorkspaceTheme } from './app/preferences/workspacePreferences'
import { useWorkspaceNavigation } from './app/routing/useWorkspaceNavigation'; import { useWorkspaceContextRestore } from './app/routing/useWorkspaceContextRestore'
import { pageView, preferredPage, visiblePages, type ActiveView } from './features/analysis/pageSelection'
import { DEFAULT_PORTFOLIO_LAYOUT, DEFAULT_WORKFLOW_DASHBOARD_LAYOUT, loadPortfolioLayout, loadWorkflowDashboardLayout } from './features/layouts/layoutDefaults'
import { ReportExportDialog } from './features/reports/ReportExportDialog'
import { useReportExportController } from './features/reports/useReportExportController'
import { ApprovalPendingScreen, AuthStatusErrorScreen, LoginScreen, ServerSetupScreen } from './features/auth/LoginScreen'
import { useAuthSession } from './features/auth/useAuthSession'
import { accountApi } from './shared/api/account'
import { hasPermission, isPersonalOnlyAccount, visibleMenuItems, type MenuId, type MenuPolicy } from './features/auth/access'
import { WORKSPACE_ROUTES_BY_ID } from './features/navigation/workspaceRouteRegistry'
import { AccessAdminPage, AuditAdminPage, MenuPolicyAdminPage, ProjectResultProfileBinding, preloadWorkspaceRouteModule, SimulationWorkbench, WorkbenchTypeAdmin } from './app/routing/workspaceRouteModules'
import { RequestIntakePage } from './features/workbench/RequestIntakePage'
import { PortfolioDashboard } from './PortfolioDashboard'
import { ResultsWorkspaceOverlays } from './features/results/ResultsWorkspaceOverlays'
import { useResultVersionSelection } from './features/results/useResultVersionSelection'
import { AnalysisPageManager, AutomationTemplatesPage, DataWorkspace, FeatureExampleGallery, FeatureScreenFallback, FolderSchemaWorkspace, HelpCenter, PendingAnalysisWorkspace, ResultsWorkspace, SPECIAL_WIDGET_CATALOG, VariableCatalogPage, WorkflowView } from './app/AppDependencies'
import { EdgeFilter } from './features/results/EdgeFilter'
import { RequestJourneyCompact } from './features/request-workspace/RequestWorkspaceHeader'
import { RequestWorkspaceShellHeader } from './app/workspace/RequestWorkspaceShellHeader'
import { RequestResultSummary } from './features/request-workspace/RequestResultSummary'
import { StorageWorkspacePanel } from './features/storage/StorageWorkspacePanel'
import { StorageRefreshControl } from './features/storage/StorageRefreshControl'
import { resetWorkspaceContext } from './app/workspace/resetWorkspaceContext'
import type { AnalysisRequest, AutomationTemplate, DashboardDefinition, DashboardPageSummary, DashboardSummary, DashboardVersion, DashboardWidget, FeatureExample, ImportSchema, LoadCase, Overview, PortfolioLayout, Project, QualityThreshold, ReportContentItem, ReportElementDefinition, ReportElementType, ReportLayout, ReportLayoutDefinition, ReportLayoutVersion, ReportSection, ReportSlideDefinition, ReportSlideKind, ReportSource, ReportTemplateAsset, ReviewItem, RunComparison, RunComparisonReportContext, RunTrust, VariableDefinition, VariableDefinitionInput, WidgetCatalogItem, Workflow, WorkflowDashboardLayout, WorkflowStep } from './types'
function App() {
  const [preferences] = useState(loadWorkspacePreferences)
  const [theme, setTheme] = useState<WorkspaceTheme>(preferences.theme)
  const [uiFontSize, setUiFontSize] = useState(preferences.uiFontSize)
  const [menuPolicy, setMenuPolicy] = useState<MenuPolicy | null>(null)
  const [menuPolicyReady, setMenuPolicyReady] = useState(false)
  const { authCheckFailed, authError, authMode, authReady, authRequired, authUser, expire, login: handleLogin, logout: authLogout, refreshAccess, registrationEnabled, retryAuth, setupReason, setupRequired } = useAuthSession({
    onAccessChanged: (_user, policy) => { setMenuPolicy(policy); setMenuPolicyReady(true) },
    onAccessRefreshFailed: () => { setMenuPolicy(null); setMenuPolicyReady(true) },
  })
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
  const workspaceEditor = useWorkspaceEditorCoordinator()
  const editMode = workspaceEditor.isEditing
  const cancelEditingRef = useRef<() => void>(() => {})
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
  const workspaceContextHydrated = useRef(false)
  const restoredWorkspaceContext = useRef('')
  const workspaceContextWritePending = useRef('')
  const userWorkspaceContextChange = useRef(false); const workspaceContextChangePending = useRef(false); const [workspaceContextTransitioning, setWorkspaceContextTransitioning] = useState(false); const [, setWorkspaceContextRestoreEpoch] = useState(0)
  const dashboardRequestSequence = useRef(0); const { beginContextEntry, beginMonitoringContext, isCurrentContextEntry, isCurrentMonitoringContext, requestContextLoading, runRequestSelection } = useResultAnalysisIntent()
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
      return [...visibleMenuItems(null, authUser, selectedProjectId), ...(['menu_policy_admin', 'audit_admin'] as MenuId[]).map((id, index) => {
        const local = WORKSPACE_ROUTES_BY_ID.get(id)!
        return { id, label: local.label, required_permission: local.requiredPermission, context_kind: local.contextKind, sequence_no: 900 + index, is_policy_editable: false, visibility: { general: false, power: false, admin: false } }
      })]
    }
    return visibleMenuItems(null, authUser, selectedProjectId)
  }, [authUser, menuPolicy, menuPolicyReady, selectedProjectId])
  const allowedPages = useMemo(() => new Set(visibleMenus.map((menu) => menu.id)), [visibleMenus])
  const cancelEditing = useCallback(() => cancelEditingRef.current(), [])
  const canChangeContext = () => {
    if (!editMode) return true
    if (!window.confirm('저장하지 않은 변경 사항이 있습니다. 변경을 취소하고 이동할까요?')) return false
    cancelEditing()
    return true
  }
  const resetDashboardWorkspace = useCallback(() => setActiveView('workflow'), [])
  const { isWorkspaceIndex, matchedWorkspaceRoute, navigateWorkspace, updateWorkspaceContext, workspaceContext, workspaceNavigationPending, workspacePage } = useWorkspaceNavigation({
    allowedPages,
    authUser,
    editMode,
    menuPolicyReady,
    onCancelEditing: cancelEditing,
    onDashboardRoute: resetDashboardWorkspace,
    onNotice: setNotice,
    personalOnly: isPersonalOnlyAccount(authUser),
    visibleMenus,
  }); const enterWorkspace = (...args: Parameters<typeof navigateWorkspace>) => { beginContextEntry(); navigateWorkspace(...args) }
  const { analysisRuns, selectedAnalysisRunId, analysisRunsLoading, analysisRunChanging, analysisRunError, selectAnalysisRun } = useResultVersionSelection({
    loadCaseId: selectedLoadCaseId,
    requestedRunId: workspaceContext.runId,
    enabled: authReady && (!authRequired || Boolean(authUser)),
    overview,
    setOverview,
  })
  const { snapshotDashboard, reportablePages, hydrateSnapshotDashboard, prepareEditableDashboard, openAssistant, clearSnapshotDashboard } = useRequestResultSnapshot({ activeDashboardId, selectedProjectId, selectedRequestId, selectedLoadCaseId, overview, analysisPages, visiblePages, dashboard, dashboardReady, shouldPrepareDashboard: workspacePage === 'dashboard' && activeView !== 'workflow', canEditActiveDashboard: hasPermission(authUser, 'dashboard.edit', selectedProjectId), setDashboard, setDashboardLoading, setAnalysisPages, setActiveDashboardId, setActiveView, setAssistantOpen, setError })
  const reportExport = useReportExportController({
    activeDashboardId,
    comparisonReportContext,
    dashboard: dashboard ?? snapshotDashboard,
    dashboardReady: Boolean(dashboard && !dashboardLoading && dashboard.id === activeDashboardId) || Boolean(snapshotDashboard && activeDashboardId === 'request-result-layout'),
    mode: activeView === 'compare' ? 'comparison' : 'analysis',
    onComparisonReportContextChanged: setComparisonReportContext,
    onError: setError,
    onNotice: setNotice,
    overview,
    reportablePages,
    selectedLoadCaseId,
  })
  useEffect(() => {
    if (!notice) return
    const timeout = window.setTimeout(() => setNotice((current) => current === notice ? '' : current), 4000)
    return () => window.clearTimeout(timeout)
  }, [notice])
  useEffect(() => {
    saveWorkspacePreference('uiFontSize', uiFontSize)
  }, [uiFontSize])
  useEffect(() => {
    saveWorkspacePreference('theme', theme)
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
  }, [theme])
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
    userKey: authReady && authUser?.account_status === 'ACTIVE' && !isPersonalOnlyAccount(authUser) ? authUser.id : null,
    onStart: () => setError(''),
    onResolved: applyInitialWorkspace,
  })
  const logout = async () => { invalidateWorkspaceBootstrap(); await authLogout(); setMenuPolicy(null); setMenuPolicyReady(false); setOverview(null) }
  useEffect(() => {
    const expired = () => {
      if (!authUser) return
      invalidateWorkspaceBootstrap()
      expire('로그인 세션이 만료되었습니다. 다시 로그인하세요.')
    }
    window.addEventListener('analysis-auth-expired', expired)
    return () => window.removeEventListener('analysis-auth-expired', expired)
  }, [authUser, expire, invalidateWorkspaceBootstrap])
  useEffect(() => {
    if (!activeDashboardId || !selectedLoadCaseId || !authReady || (authRequired && !authUser) || isPendingResultAnalysis(false, activeDashboardId) || (editMode && dashboard?.id === activeDashboardId)) return
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
  }, [activeDashboardId, selectedLoadCaseId, authReady, authRequired, authUser?.id, dashboard?.id, editMode])
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
  const loadContext = async (projectId: string, requestId?: string, preferredView?: ActiveView, preferredLoadCaseId?: string, preferredPageId?: string, preferredRunId?: string) => {
    setError(''); const intent = beginMonitoringContext()
    const [requestData, storedPortfolioLayout, storedWorkflowLayout] = await Promise.all([
      api.requests(projectId),
      api.workspaceLayout(projectId, 'portfolio'),
      api.workspaceLayout(projectId, 'workflow'),
    ])
    const request = requestData.find((item) => item.id === requestId) ?? requestData[0]
    const [caseData, thresholdData] = await Promise.all([request ? api.loadCases(request.id) : Promise.resolve([] as LoadCase[]), api.qualityThresholds(projectId)])
    if (!request) {
      if (!isCurrentMonitoringContext(intent)) return
      dashboardRequestSequence.current += 1
      resetWorkspaceContext({ projectId, thresholds: thresholdData, portfolioLayout: storedPortfolioLayout.definition, portfolioLayoutVersion: storedPortfolioLayout.version, workflowLayout: storedWorkflowLayout.definition, workflowLayoutVersion: storedWorkflowLayout.version, setDashboardLoading, setRequests, setLoadCases, setThresholds, setSelectedProjectId, setSelectedRequestId, setSelectedLoadCaseId, setOverview, setDashboard, setAnalysisPages, setActiveDashboardId, setActiveView, setPortfolioLayout, setPortfolioLayoutVersion, setWorkflowDashboardLayout, setWorkflowLayoutVersion })
      return
    }
    const loadCase = caseData.find((item) => item.id === preferredLoadCaseId) ?? caseData[0]
    const [overviewData, pageData] = loadCase ? await Promise.all([api.overview(loadCase.id, preferredRunId), api.dashboardPages(loadCase.id)]) : [null, [] as DashboardPageSummary[]]
    const selectedPage = overviewData ? visiblePages(pageData, overviewData).find((page) => page.id === preferredPageId) ?? preferredPage(pageData, overviewData, preferredView) : undefined
    const dashboardData = selectedPage ? await api.dashboard(selectedPage.id).catch(() => null) : null
    if (!isCurrentMonitoringContext(intent)) return
    setRequests(requestData)
    setLoadCases(caseData)
    setThresholds(thresholdData)
    setSelectedProjectId(projectId)
    setPortfolioLayout(storedPortfolioLayout.definition)
    setPortfolioLayoutVersion(storedPortfolioLayout.version)
    setWorkflowDashboardLayout(storedWorkflowLayout.definition)
    setWorkflowLayoutVersion(storedWorkflowLayout.version)
    setSelectedRequestId(request.id)
    setSelectedLoadCaseId(loadCase?.id ?? '')
    setOverview(overviewData)
    setAnalysisPages(pageData)
    if (selectedPage) { setActiveDashboardId(selectedPage.id); setActiveView(pageView(selectedPage)); if (dashboardData) setDashboard(dashboardData) } else { setDashboard(null); setActiveDashboardId('pending-open-cell'); setActiveView('custom') }; return selectedPage?.id
  }
  const loadMonitoringContext = async (projectId: string, requestId?: string, preferredLoadCaseId?: string) => {
    setError(''); const intent = beginMonitoringContext()
    const [requestData, storedPortfolioLayout, storedWorkflowLayout] = await Promise.all([
      api.requests(projectId),
      api.workspaceLayout(projectId, 'portfolio'),
      api.workspaceLayout(projectId, 'workflow'),
    ])
    const request = requestData.find((item) => item.id === requestId) ?? requestData[0]
    const [caseData, thresholdData] = await Promise.all([request ? api.loadCases(request.id) : Promise.resolve([] as LoadCase[]), api.qualityThresholds(projectId)])
    if (!request) {
      if (!isCurrentMonitoringContext(intent)) return false
      dashboardRequestSequence.current += 1
      resetWorkspaceContext({ projectId, thresholds: thresholdData, portfolioLayout: storedPortfolioLayout.definition, portfolioLayoutVersion: storedPortfolioLayout.version, workflowLayout: storedWorkflowLayout.definition, workflowLayoutVersion: storedWorkflowLayout.version, setDashboardLoading, setRequests, setLoadCases, setThresholds, setSelectedProjectId, setSelectedRequestId, setSelectedLoadCaseId, setOverview, setDashboard, setAnalysisPages, setActiveDashboardId, setActiveView, setPortfolioLayout, setPortfolioLayoutVersion, setWorkflowDashboardLayout, setWorkflowLayoutVersion })
      return false
    }
    let overviewData: Overview | null = null; let pageData: DashboardPageSummary[] = []; let selectedPage: DashboardPageSummary | undefined; let dashboardData: DashboardDefinition | null = null
    const loadCase = caseData.find((item) => item.id === preferredLoadCaseId) ?? caseData[0]
    if (loadCase) { [overviewData, pageData] = await Promise.all([api.overview(loadCase.id), api.dashboardPages(loadCase.id)]); selectedPage = preferredPage(pageData, overviewData); dashboardData = selectedPage ? await api.dashboard(selectedPage.id) : null }
    if (!isCurrentMonitoringContext(intent)) return false
    setRequests(requestData)
    setLoadCases(caseData)
    setThresholds(thresholdData)
    setSelectedProjectId(projectId)
    setPortfolioLayout(storedPortfolioLayout.definition)
    setPortfolioLayoutVersion(storedPortfolioLayout.version)
    setWorkflowDashboardLayout(storedWorkflowLayout.definition)
    setWorkflowLayoutVersion(storedWorkflowLayout.version)
    setSelectedRequestId(request.id)
    setSelectedLoadCaseId(loadCase?.id ?? '')
    if (loadCase) {
      setOverview(overviewData!)
      setAnalysisPages(pageData)
      if (selectedPage) { setActiveDashboardId(selectedPage.id); setDashboard(dashboardData) }
    } else {
      setOverview(null)
      setDashboard(null)
      setAnalysisPages([])
      setActiveDashboardId('pending-open-cell')
    }
    setActiveView('workflow')
    return true
  }
  const workspaceContextKey = [workspaceContext.projectId, workspaceContext.requestId, workspaceContext.loadCaseId, workspaceContext.runId, workspaceContext.view, workspaceContext.pageId].join('|')
  useEffect(() => {
    if (workspaceContextWritePending.current !== workspaceContextKey) return
    restoredWorkspaceContext.current = workspaceContextKey
    workspaceContextWritePending.current = ''
    setWorkspaceContextTransitioning(false)
  }, [workspaceContextKey])
  useEffect(() => { if (workspaceBootstrap.status === 'resolved' && !workspaceContext.projectId) { restoredWorkspaceContext.current = workspaceContextKey; workspaceContextHydrated.current = true } }, [workspaceBootstrap.status, workspaceContext.projectId, workspaceContextKey])
  useWorkspaceContextRestore({ enabled: workspaceBootstrap.status === 'resolved' && Boolean(workspaceContext.projectId) && !workspaceContextTransitioning && !workspaceContextWritePending.current && restoredWorkspaceContext.current !== workspaceContextKey, context: workspaceContext, projects, beginIntent: () => { workspaceContextChangePending.current = true; return beginContextEntry() }, isCurrentIntent: isCurrentContextEntry, onInvalid: (message, target) => { setSelectedProjectId(target?.projectId ?? ''); setRequests(target?.requests ?? []); setSelectedRequestId(target?.requestId ?? ''); setLoadCases(target?.loadCases ?? []); setSelectedLoadCaseId(''); setOverview(null); setDashboard(null); setAnalysisPages([]); setActiveDashboardId('pending-open-cell'); setActiveView('workflow'); setNotice(message) }, onMonitoringContext: loadMonitoringContext, onAnalysisContext: loadContext, onVirtualResultLayout: clearSnapshotDashboard, onSettled: () => { restoredWorkspaceContext.current = workspaceContextKey; workspaceContextHydrated.current = true; workspaceContextChangePending.current = false; setWorkspaceContextRestoreEpoch((value) => value + 1) } })
  useEffect(() => {
    const selectedRunMatchesOverview = !selectedAnalysisRunId || (overview?.load_case.id === selectedLoadCaseId && overview.run === selectedAnalysisRunId)
    if (workspaceBootstrap.status !== 'resolved' || isWorkspaceIndex || !matchedWorkspaceRoute || editMode || workspaceNavigationPending || !allowedPages.has(workspacePage) || !workspaceContextHydrated.current || workspaceContextWritePending.current || workspaceContextKey !== restoredWorkspaceContext.current || workspaceContextChangePending.current || requestContextLoading || analysisRunsLoading || analysisRunChanging || !selectedRunMatchesOverview) return
    const durableAnalysisPage = activeDashboardId !== 'request-result-layout' && !activeDashboardId.startsWith('pending-')
    const nextContext = {
      projectId: selectedProjectId || undefined,
      requestId: selectedRequestId || undefined,
      loadCaseId: selectedLoadCaseId || undefined,
      runId: durableAnalysisPage ? selectedAnalysisRunId || undefined : undefined,
      view: activeView,
      pageId: durableAnalysisPage ? activeDashboardId : undefined,
    }
    const nextContextKey = [nextContext.projectId, nextContext.requestId, nextContext.loadCaseId, nextContext.runId, nextContext.view, nextContext.pageId].join('|')
    if (nextContextKey === workspaceContextKey) { setWorkspaceContextTransitioning(false); return }
    workspaceContextWritePending.current = nextContextKey
    updateWorkspaceContext(nextContext, { replace: !userWorkspaceContextChange.current })
    userWorkspaceContextChange.current = false
  }, [activeDashboardId, activeView, allowedPages, analysisRunChanging, analysisRunsLoading, editMode, isWorkspaceIndex, matchedWorkspaceRoute, overview, requestContextLoading, selectedAnalysisRunId, selectedLoadCaseId, selectedProjectId, selectedRequestId, updateWorkspaceContext, workspaceBootstrap.status, workspaceContextKey, workspaceNavigationPending, workspacePage])
  const handleProjectChange = async (projectId: string) => {
    if (!canChangeContext()) return; const intent = beginContextEntry(); userWorkspaceContextChange.current = true; workspaceContextChangePending.current = true; setWorkspaceContextTransitioning(true)
    try { await runRequestSelection(() => {}, () => activeView === 'workflow' ? loadMonitoringContext(projectId) : loadContext(projectId)) } catch (reason) { userWorkspaceContextChange.current = false; if (isCurrentContextEntry(intent)) { setWorkspaceContextTransitioning(false); setError(reason instanceof Error ? reason.message : '프로젝트를 변경하지 못했습니다.') } } finally { if (isCurrentContextEntry(intent)) workspaceContextChangePending.current = false }
  }
  const handleRequestChange = async (requestId: string) => {
    if (!canChangeContext()) return; const intent = beginContextEntry(); userWorkspaceContextChange.current = true; workspaceContextChangePending.current = true; setWorkspaceContextTransitioning(true)
    try { await runRequestSelection(() => {}, () => activeView === 'workflow' ? loadMonitoringContext(selectedProjectId, requestId) : loadContext(selectedProjectId, requestId)) } catch (reason) { userWorkspaceContextChange.current = false; if (isCurrentContextEntry(intent)) { setWorkspaceContextTransitioning(false); setError(reason instanceof Error ? reason.message : '의뢰를 변경하지 못했습니다.') } } finally { if (isCurrentContextEntry(intent)) workspaceContextChangePending.current = false }
  }
  const handleLoadCaseChange = async (loadCaseId: string) => {
    if (!canChangeContext()) return; const intent = beginContextEntry(); userWorkspaceContextChange.current = true; workspaceContextChangePending.current = true; setWorkspaceContextTransitioning(true)
    try {
      const [overviewData, pageData] = await Promise.all([api.overview(loadCaseId), api.dashboardPages(loadCaseId)])
      const selectedPage = preservesResultLayoutOnLoadCaseChange(activeDashboardId) ? undefined : preferredPage(pageData, overviewData)
      const dashboardData = selectedPage ? await api.dashboard(selectedPage.id) : null
      if (!isCurrentContextEntry(intent)) return
      setSelectedLoadCaseId(loadCaseId)
      setOverview(overviewData)
      setAnalysisPages(pageData)
      if (preservesResultLayoutOnLoadCaseChange(activeDashboardId)) return
      if (selectedPage) { setActiveDashboardId(selectedPage.id); setActiveView(pageView(selectedPage)); setDashboard(dashboardData) }
    } catch (reason) { userWorkspaceContextChange.current = false; if (isCurrentContextEntry(intent)) { setWorkspaceContextTransitioning(false); setError(reason instanceof Error ? reason.message : '하중 경우를 변경하지 못했습니다.') } } finally { if (isCurrentContextEntry(intent)) workspaceContextChangePending.current = false }
  }
  const handleAnalysisRunChange = async (runId: string) => {
    if (!canChangeContext()) return
    userWorkspaceContextChange.current = true; workspaceContextChangePending.current = true; setWorkspaceContextTransitioning(true)
    try { await selectAnalysisRun(runId) } finally { workspaceContextChangePending.current = false }
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
  const beginEditing = async () => {
    const editableDashboard = await prepareEditableDashboard(); if (!editableDashboard && workspacePage !== 'portfolio') return
    if (workspacePage === 'portfolio') portfolioLayoutBeforeEdit.current = { ...portfolioLayout, chartOrder: [...portfolioLayout.chartOrder] }
    if (workspacePage === 'dashboard' && activeView !== 'workflow' && editableDashboard) dashboardBeforeEdit.current = structuredClone(editableDashboard)
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
  const cancelEditingImplementation = () => {
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
  cancelEditingRef.current = cancelEditingImplementation
  const resetPortfolioLayout = () => {
    setPortfolioLayout({ ...DEFAULT_PORTFOLIO_LAYOUT, chartOrder: [...DEFAULT_PORTFOLIO_LAYOUT.chartOrder] })
    setNotice('기본 배치를 미리 적용했습니다. 저장하거나 취소할 수 있습니다.')
  }
  const resetWorkflowDashboardLayout = () => {
    setWorkflowDashboardLayout({ ...DEFAULT_WORKFLOW_DASHBOARD_LAYOUT, items: [] })
    setNotice('진행 현황 기본 레이아웃을 미리 적용했습니다. 저장하거나 취소할 수 있습니다.')
  }
  const switchDashboardView = (view: ActiveView) => {
    if (!canChangeContext()) return; beginContextEntry()
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
    if (!canChangeContext()) return; beginContextEntry()
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
      setOverview(await api.overview(overview.load_case.id, selectedAnalysisRunId || undefined))
      setNotice(`관리자 판정 기준을 ${value.toFixed(1)} mm로 저장했습니다.`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '판정 기준을 저장하지 못했습니다.') }
  }
  const saveOpenCellThreshold = async (value: number) => {
    const criterion = thresholds.find((item) => item.criterion_key === 'open_cell_stress_mpa')
    if (!criterion || !overview) return
    try {
      const updated = await api.updateQualityThreshold(overview.load_case.project_id, criterion.criterion_key, value)
      setThresholds((items) => items.map((item) => item.criterion_key === updated.criterion_key ? updated : item))
      setOverview(await api.overview(overview.load_case.id, selectedAnalysisRunId || undefined))
      setNotice(`Open Cell 응력 관리 기준을 ${value.toFixed(1)} MPa로 저장했습니다.`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Open Cell 응력 기준을 저장하지 못했습니다.') }
  }
  const openWorkflowAnalysis = createWorkflowAnalysisOpener({
    selectRequestContext: (workflow, preferredView) => runRequestSelection(() => setSelectedRequestId(workflow.request.id), () => loadContext(workflow.request.project_id, workflow.request.id, preferredView)), loadLayout: workbenchApi.requestResultLayout, beginIntent: beginContextEntry, isCurrentIntent: isCurrentContextEntry,
    setActiveDashboardId, setActiveView, setError,
  })
  const refreshOperationalData = async () => { const [projectData, workflowData] = await Promise.all([api.projects(), api.workflows()]); setProjects(projectData); setWorkflows(workflowData); const projectId = selectedProjectId || projectData[0]?.id || ''; setSelectedProjectId(projectId); if (projectId) { const requestData = await api.requests(projectId); setRequests(requestData); const requestId = requestData.some((request) => request.id === selectedRequestId) ? selectedRequestId : requestData[0]?.id || ''; if (requestId) { const caseData = await api.loadCases(requestId); setLoadCases(caseData); if (!caseData.some((loadCase) => loadCase.id === selectedLoadCaseId)) setSelectedLoadCaseId(caseData[0]?.id || '') } else { setLoadCases([]); setSelectedLoadCaseId('') } if (!requestData.some((request) => request.id === selectedRequestId)) setSelectedRequestId(requestId) } setOperationalRefreshToken((value) => value + 1) }
  const handleIntakeCreated = async (projectId: string, request: AnalysisRequest) => {
    const intent = beginContextEntry(); const [projectData, workflowData, requestData] = await Promise.all([api.projects(), api.workflows(), api.requests(projectId)])
    if (!isCurrentContextEntry(intent)) return
    setProjects(projectData); setWorkflows(workflowData); setRequests(requestData)
    setSelectedProjectId(projectId); setSelectedRequestId(request.id); setLoadCases([]); setSelectedLoadCaseId('')
    setOperationalRefreshToken((value) => value + 1)
    setNotice(`${request.title} 의뢰를 접수했습니다.`)
  }
  const openIntakeWorkbench = (requestId: string) => {
    beginContextEntry(); setSelectedRequestId(requestId)
    navigateWorkspace('workbench')
  }
  const selectWorkbenchRequest = async (requestId: string) => {
    const workflow = workflows.find((item) => item.request.id === requestId)
    if (!workflow) { setError('선택한 의뢰의 작업 문맥을 찾지 못했습니다. 목록을 새로고침해 주세요.'); return }
    const intent = beginContextEntry(); setWorkspaceContextTransitioning(true)
    try {
      await loadMonitoringContext(workflow.request.project_id, requestId)
    } catch (reason) {
      if (isCurrentContextEntry(intent)) { setWorkspaceContextTransitioning(false); setError(reason instanceof Error ? reason.message : '선택한 의뢰의 작업 문맥을 열지 못했습니다.') }
    }
  }
  const openWorkbenchForRequest = async (requestId: string) => {
    if (workspaceContextTransitioning) return
    const workflow = workflows.find((item) => item.request.id === requestId)
    if (!workflow) { setError('선택한 의뢰의 작업 문맥을 찾지 못했습니다. 목록을 새로고침해 주세요.'); return }
    const intent = beginContextEntry(); setWorkspaceContextTransitioning(true)
    try {
      await loadMonitoringContext(workflow.request.project_id, requestId)
      if (isCurrentContextEntry(intent)) navigateWorkspace('workbench')
    } catch (reason) {
      if (isCurrentContextEntry(intent)) { setWorkspaceContextTransitioning(false); setError(reason instanceof Error ? reason.message : '작업 실행 화면을 열지 못했습니다.') }
    }
  }
  const openCurrentResultReview = () => {
    if (!selectedProjectId || !selectedRequestId || !selectedLoadCaseId) {
      setNotice('결과를 검토하려면 하중 경우와 등록된 결과를 먼저 선택해 주세요.')
      return
    }
    void openImportedResult(selectedProjectId, selectedRequestId, selectedLoadCaseId)
  }
  const openImportedResult = async (projectId: string, requestId: string, loadCaseId: string) => {
    const intent = beginContextEntry(); setWorkspaceContextTransitioning(true); try { const [requestData, caseData, thresholdData, overviewData, pageData] = await Promise.all([
      api.requests(projectId), api.loadCases(requestId), api.qualityThresholds(projectId), api.overview(loadCaseId), api.dashboardPages(loadCaseId),
    ])
    const selectedPage = preferredPage(pageData, overviewData)
    const dashboardData = selectedPage ? await api.dashboard(selectedPage.id) : null
    if (!isCurrentContextEntry(intent)) return
    setRequests(requestData); setLoadCases(caseData); setThresholds(thresholdData)
    setSelectedProjectId(projectId); setSelectedRequestId(requestId); setSelectedLoadCaseId(loadCaseId); setOverview(overviewData)
    setAnalysisPages(pageData)
    if (selectedPage) { setActiveDashboardId(selectedPage.id); setActiveView(pageView(selectedPage)); if (dashboardData) setDashboard(dashboardData) }
    navigateWorkspace('dashboard', { dashboardEntry: 'preserve' }) } catch (reason) { if (isCurrentContextEntry(intent)) { setWorkspaceContextTransitioning(false); setError(reason instanceof Error ? reason.message : '결과 검토 문맥을 열지 못했습니다.') } }
  }
  const openPortfolioRequest = async (projectId: string, requestId: string, loadCaseId?: string) => {
    const intent = beginContextEntry(); setWorkspaceContextTransitioning(true); try {
      const loaded = await loadMonitoringContext(projectId, requestId, loadCaseId)
      if (!loaded || !isCurrentContextEntry(intent)) { if (isCurrentContextEntry(intent)) setWorkspaceContextTransitioning(false); return }
      navigateWorkspace('dashboard')
    } catch (reason) {
      if (isCurrentContextEntry(intent)) { setWorkspaceContextTransitioning(false); setError(reason instanceof Error ? reason.message : '의뢰 진행 상태를 열지 못했습니다.') }
    }
  }
  const openPortfolioResult = async (projectId: string, requestId: string, loadCaseId: string, runId?: string) => {
    const intent = beginContextEntry(); setWorkspaceContextTransitioning(true)
    try {
      await loadContext(projectId, requestId, undefined, loadCaseId, undefined, runId)
      if (isCurrentContextEntry(intent)) navigateWorkspace('dashboard', { dashboardEntry: 'preserve' })
    } catch (reason) {
      if (isCurrentContextEntry(intent)) { setWorkspaceContextTransitioning(false); setError(reason instanceof Error ? reason.message : '결과 검토 문맥을 열지 못했습니다.') }
    }
  }
  const openFeatureExample = async (example: FeatureExample) => {
    const intent = beginContextEntry(); try {
      if (example.project_id && example.request_id) {
        await loadContext(example.project_id, example.request_id, example.preferred_view)
      }
      if (!isCurrentContextEntry(intent)) return
      if (example.preferred_view) setActiveView(example.preferred_view)
      navigateWorkspace(example.workspace_page, { dashboardEntry: example.workspace_page === 'dashboard' && example.preferred_view ? 'preserve' : undefined })
      setNotice(`${example.title} 예제를 열었습니다.`)
    } catch (reason) {
      if (isCurrentContextEntry(intent)) setError(reason instanceof Error ? reason.message : '예제를 열지 못했습니다.')
    }
  }
  if (!authReady) {
    return <div className="full-state"><LoaderCircle className="spin" /> 인증 설정을 확인하고 있습니다.</div>
  }
  if (authCheckFailed) {
    return <AuthStatusErrorScreen message={authError} onRetry={() => void retryAuth()} theme={theme} onThemeChange={setTheme} />
  }
  if (setupRequired) {
    return <ServerSetupScreen reason={setupReason} onRetry={() => void retryAuth()} theme={theme} onThemeChange={setTheme} />
  }
  if (authRequired && !authUser) {
    return <LoginScreen mode={authMode === 'oidc' ? 'oidc' : 'password'} error={authError} onLogin={handleLogin} onRegister={accountApi.register} registrationEnabled={registrationEnabled} theme={theme} onThemeChange={setTheme} />
  }
  if (authUser?.account_status === 'PENDING') {
    return <ApprovalPendingScreen displayName={authUser.display_name} onLogout={() => void logout()} />
  }
  if (workspacePage === 'local_pc' && authUser?.account_status === 'ACTIVE') {
    return <PersonalPcRoute user={authUser} menus={visibleMenus} databaseBackend={databaseBackend} theme={theme} fontSize={uiFontSize} onFontSizeChange={setUiFontSize} onThemeChange={setTheme} onLogout={() => void logout()} onNavigate={enterWorkspace} />
  }
  if (workspaceBootstrap.status === 'idle' || workspaceBootstrap.status === 'loading') {
    return <div className="full-state"><LoaderCircle className="spin" /> 데이터와 레이아웃을 준비하고 있습니다.</div>
  }
  if (workspaceBootstrap.status === 'failed') {
    return <div className="full-state error"><AlertTriangle /> <span>{workspaceBootstrap.message}</span><button type="button" onClick={invalidateWorkspaceBootstrap}>다시 시도</button></div>
  }
  if (error) {
    return <div className="full-state error"><AlertTriangle /> {error}</div>
  }
  if (!isWorkspaceIndex && !matchedWorkspaceRoute) {
    return <div className="full-state error" data-testid="workspace-not-found"><AlertTriangle /><h1>페이지를 찾을 수 없습니다.</h1><p>요청한 작업공간 경로가 존재하지 않습니다.</p></div>
  }
  const isRequestMonitoring = workspacePage === 'dashboard' && activeView === 'workflow'
  const resultLayoutPending = workspacePage === 'dashboard' && !selectedLoadCaseId && !overview && !dashboard; const pendingAnalysis = activeView !== 'workflow' && isPendingResultAnalysis(resultLayoutPending, activeDashboardId)
  const projectSetup = Boolean(selectedProjectId && requests.length === 0)
  const workspaceContextRestoring = workspaceBootstrap.status === 'resolved' && (workspaceContextTransitioning || (Boolean(workspaceContext.projectId) && !workspaceContextWritePending.current && workspaceContextKey !== restoredWorkspaceContext.current))
  if (workspaceContextRestoring) {
    return <div className="full-state" data-testid="workspace-context-restoring"><LoaderCircle className="spin" /> 요청한 의뢰 문맥을 확인하고 있습니다.</div>
  }
  if ((!overview || !dashboard) && !isRequestMonitoring && !pendingAnalysis && !(projects.length > 0 && !isWorkspaceIndex && (workspacePage === 'portfolio' || workspacePage === 'workbench' || workspacePage === 'data' || workspacePage === 'intake'))) {
    const canCreateProject = hasPermission(authUser, 'system.user.approve', selectedProjectId)
    const canCreateRequest = hasPermission(authUser, 'request.create', selectedProjectId)
    const canRegisterData = canCreateProject || hasPermission(authUser, 'result.import', selectedProjectId)
    const canRetryImports = hasPermission(authUser, 'system.catalog.manage', selectedProjectId)
    const setupPage = workspacePage === 'intake' && projects.length > 0 ? 'intake' : 'data'
    return <BootstrapWorkspaceShell
      theme={theme}
      activePage={setupPage}
      displayName={authUser?.display_name ?? '사용자'}
      databaseBackend={databaseBackend}
      canOpenIntake={projects.length > 0 && canCreateRequest}
      onPageChange={enterWorkspace}
      onThemeChange={setTheme}
      onLogout={() => void logout()}
    >
      {authUser?.is_global_admin && <StorageRefreshControl onChanged={refreshOperationalData} onMessage={setNotice} onError={setError} />}
      {!canRegisterData && projects.length === 0 ? <div className="bootstrap-empty-access"><AlertTriangle /><h1>접근 가능한 프로젝트가 없습니다.</h1><p>전역 관리자에게 프로젝트 생성 또는 멤버십 할당을 요청하세요.</p></div> : setupPage === 'intake' ? (
        <RequestIntakePage projects={projects} initialProjectId={selectedProjectId} createdBy={authUser?.display_name ?? '사용자'} canCreate={canCreateRequest} onCreated={handleIntakeCreated} onOpenWorkbench={() => enterWorkspace('data')} />
      ) : (
        <><RequestJourneyCompact active="data" disabled={!selectedRequestId} onOpenOverview={() => enterWorkspace('dashboard')} onOpenWorkbench={() => enterWorkspace('workbench')} onOpenData={() => {}} onOpenReview={openCurrentResultReview} /><Suspense fallback={<FeatureScreenFallback />}><DataWorkspace storagePanel={StorageWorkspacePanel} refreshToken={operationalRefreshToken} canCreateProject={canCreateProject} canRetryImports={canRetryImports} canUploadStorage={hasPermission(authUser, 'result.import', selectedProjectId)} canBindStorage={hasPermission(authUser, 'result.import', selectedProjectId)} canManageStorageRoot={Boolean(authUser?.is_global_admin)} projects={projects} initialProjectId={selectedProjectId} initialRequestId={selectedRequestId} initialLoadCaseId={selectedLoadCaseId} onContextChange={({ projectId, requestId, loadCaseId }) => { setSelectedProjectId(projectId); setSelectedRequestId(requestId); setSelectedLoadCaseId(loadCaseId) }} onDataChanged={refreshOperationalData} onOpenAnalysis={openImportedResult} onOpenIntake={() => enterWorkspace('intake')} /></Suspense></>
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
  const chassisThreshold = thresholds.find((item) => item.criterion_key === 'chassis_rear_permanent_deformation_mm')
  const openCellThreshold = thresholds.find((item) => item.criterion_key === 'open_cell_stress_mpa')
  const canDashboardEdit = hasPermission(authUser, 'dashboard.edit', selectedProjectId)
  const canWorkflowEdit = hasPermission(authUser, 'workflow.edit', selectedProjectId)
  const canLayoutEdit = hasPermission(authUser, 'project.layout.edit', selectedProjectId)
  const canEdit = projectSetup ? false : pendingAnalysis ? canDashboardEdit : workspacePage === 'portfolio' ? canLayoutEdit : activeView === 'workflow' ? canWorkflowEdit || canLayoutEdit : canDashboardEdit
  const canManagePages = canDashboardEdit && !pendingAnalysis && !projectSetup
  const canCreateRequest = hasPermission(authUser, 'request.create', selectedProjectId)
  const canExecuteAssigned = hasPermission(authUser, 'work.execute_assigned', selectedProjectId)
  const canExecuteAny = hasPermission(authUser, 'work.execute_any', selectedProjectId)
  const analysisTabs = overview ? visiblePages(analysisPages, overview) : []
  const activeAnalysisPage = analysisPages.find((page) => page.id === activeDashboardId)
  const workflowProjectName = selectedWorkflow?.request.project_name ?? projects.find((project) => project.id === selectedProjectId)?.name ?? '프로젝트 미지정'
  const workflowTitle = selectedWorkflow?.request.title ?? requests.find((request) => request.id === selectedRequestId)?.title ?? '결과 대기 중'
  const analysisProjectName = overview?.load_case.project_name ?? workflowProjectName
  const analysisTitle = overview?.load_case.request_title ?? workflowTitle
  const resultStorageDisclosure = activeView !== 'workflow' && selectedLoadCaseId && hasPermission(authUser, 'project.data.view', selectedProjectId) ? <details className="result-storage-disclosure"><summary>연결된 결과 원본 보기</summary><StorageWorkspacePanel refreshToken={operationalRefreshToken} compact readOnly canRefresh={false} loadCaseId={selectedLoadCaseId} projectLabel={analysisProjectName} requestLabel={analysisTitle} loadCaseLabel={overview?.load_case.name ?? '선택한 하중 경우'} /></details> : null
  const isRequestWorkspace = workspacePage === 'dashboard' || workspacePage === 'workbench' || workspacePage === 'data'; const requestWorkspaceTab = workspacePage === 'workbench' ? 'execution' : workspacePage === 'data' ? 'import' : activeView === 'workflow' ? 'overview' : 'review'
  const staticBreadcrumb = WORKSPACE_ROUTES_BY_ID.get(workspacePage)?.breadcrumb
  const breadcrumb = isRequestWorkspace ? requestWorkspaceTab === 'review' ? <><span>프로젝트</span><b>/</b><span>{analysisProjectName}</span><b>/</b><strong>{overview?.load_case.name ?? '하중 경우 설정 전'}</strong></> : <><span>의뢰</span><b>/</b><span>{workflowProjectName}</span><b>/</b><strong>{workflowTitle}</strong></> : <><span>{staticBreadcrumb?.section}</span><b>/</b><strong>{staticBreadcrumb?.title}</strong></>
  const requestWorkspaceHeader = isRequestWorkspace ? <RequestWorkspaceShellHeader model={{ activeDashboardId, activeTab: requestWorkspaceTab, activeView, analysisRuns, analysisRunsLoading, analysisRunChanging, analysisRunError, canOpenData: allowedPages.has('data'), canOpenWorkbench: allowedPages.has('workbench'), contextChanging: workspaceContextTransitioning, loadCases, overview, owner: selectedWorkflow?.request.owner ?? requests.find((request) => request.id === selectedRequestId)?.owner ?? '', projectId: selectedProjectId, projects, requestContextLoading, requestId: selectedRequestId, requests, selectedAnalysisRunId, selectedLoadCaseId, selectedWorkflow, status: selectedWorkflow?.request.status ?? (overview?.run ? overview.overall_verdict : '결과 대기'), title: requestWorkspaceTab === 'review' ? analysisTitle : workflowTitle }} actions={{ onLoadCaseChange: (id) => void handleLoadCaseChange(id), onOpenData: () => navigateWorkspace('data'), onOpenWorkbench: () => navigateWorkspace('workbench'), onProjectChange: (id) => void handleProjectChange(id), onRequestChange: (id) => void handleRequestChange(id), onReviewContextOpen: workspacePage === 'dashboard' || !selectedLoadCaseId ? undefined : async (intent) => { const nextOverview = await api.overview(selectedLoadCaseId, selectedAnalysisRunId || undefined); if (isCurrentContextEntry(intent)) setOverview(nextOverview) }, onReviewSnapshot: () => { if (workspacePage !== 'dashboard') navigateWorkspace('dashboard', { dashboardEntry: 'preserve' }); const page = explicitCustomAnalysisPage(analysisTabs, activeDashboardId); if (page) { switchAnalysisPage(page); return } clearSnapshotDashboard() }, onReviewDomain: () => { if (workspacePage !== 'dashboard') navigateWorkspace('dashboard', { dashboardEntry: 'preserve' }); const page = analysisTabs[0]; if (page) switchAnalysisPage(page) }, onReviewUnconfigured: () => { if (workspacePage !== 'dashboard') navigateWorkspace('dashboard', { dashboardEntry: 'preserve' }); const page = explicitCustomAnalysisPage(analysisTabs, activeDashboardId); if (page) { switchAnalysisPage(page); return } clearSnapshotDashboard() }, onViewOverview: () => { if (!canChangeContext()) return; beginContextEntry(); setActiveView('workflow'); if (workspacePage !== 'dashboard') navigateWorkspace('dashboard', { dashboardEntry: 'preserve' }) } }} onBeginReview={beginContextEntry} isCurrentReview={isCurrentContextEntry} loadLayout={workbenchApi.requestResultLayout} onReviewError={setError} onRunChange={(runId) => void handleAnalysisRunChange(runId)} /> : null
  return (
    <AppShell
      className={`app-shell ${theme === 'light' ? 'light-theme' : 'dark-theme'}`}
      sidebar={<AppSidebar
        activePage={isRequestWorkspace ? 'dashboard' : workspacePage}
        databaseBackend={databaseBackend}
        fontSize={uiFontSize}
        menus={visibleMenus}
        signedIn={Boolean(authUser)}
        userBadge={authUser ? authUser.is_global_admin ? 'GLOBAL ADMIN' : authUser.memberships.find((item) => item.project_id === selectedProjectId)?.role.toUpperCase() ?? 'NONMEMBER' : undefined}
        userDisplayName={authUser?.display_name}
        onDecreaseFontSize={() => setUiFontSize((value) => Math.max(11, value - 1))}
        onIncreaseFontSize={() => setUiFontSize((value) => Math.min(18, value + 1))}
        onLogout={() => void logout()}
        onNavigate={enterWorkspace}
        onPreloadPage={preloadWorkspaceRouteModule}
        workspacePathForMenu={(id) => WORKSPACE_ROUTES_BY_ID.get(id)?.path ?? '#'}
      />}
      style={{ '--ui-font-size': `${uiFontSize}pt` } as CSSProperties}
      theme={theme}
    >
      <AppShellMain topbar={<AppTopbar breadcrumb={breadcrumb} actions={<>
            <div className="theme-switch" role="group" aria-label="화면 테마 선택"><button type="button" aria-label="라이트" className={theme === 'light' ? 'active' : ''} aria-pressed={theme === 'light'} onClick={() => setTheme('light')}><Sun /><span>라이트</span></button><button type="button" aria-label="다크" className={theme === 'dark' ? 'active' : ''} aria-pressed={theme === 'dark'} onClick={() => setTheme('dark')}><Moon /><span>다크</span></button></div>
            {authUser?.is_global_admin && (workspacePage === 'dashboard' || workspacePage === 'portfolio' || workspacePage === 'data') && <StorageRefreshControl onChanged={refreshOperationalData} onMessage={setNotice} onError={setError} />}
            {workspacePage === 'dashboard' && activeView !== 'workflow' && <button className="ghost-button" title={!overview?.run && activeView !== 'compare' ? '완료된 Run이 있어야 보고서를 내보낼 수 있습니다.' : undefined} disabled={!dashboardReady || (!overview?.run && activeView !== 'compare')} onClick={() => void reportExport.open()}><Download /> 보고서 내보내기</button>}
            {canEdit && workspacePage === 'dashboard' && activeView !== 'workflow' && <button className="ghost-button" disabled={activeDashboardId === 'request-result-layout' ? !selectedLoadCaseId : !dashboardReady} onClick={() => void openAssistant()}><Sparkles /> 자연어로 개선</button>}
            {canEdit && (workspacePage === 'dashboard' && activeView === 'workflow' ? (editMode ? (
              <button className="primary-button" onClick={save}><Save /> {workflowEditorMode === 'layout' ? '대시보드 레이아웃 저장' : '단계 변경 저장'}</button>
            ) : <div className="workflow-top-edit-actions">
              <button className="edit-button" onClick={beginWorkflowLayoutEditing}><LayoutDashboard /> 대시보드 편집</button>
              {!selectedWorkflow?.work_plan && <button className="edit-button" onClick={beginWorkflowStageEditing}><Settings2 /> 진행 단계 편집</button>}
            </div>) : (workspacePage === 'dashboard' || workspacePage === 'portfolio') && (editMode ? (
              <button className="primary-button" onClick={save}><Save /> {workspacePage === 'portfolio' ? '운영 설정 저장' : '레이아웃 저장'}</button>
            ) : <button className="edit-button" disabled={workspacePage === 'dashboard' && (activeDashboardId === 'request-result-layout' ? !selectedLoadCaseId : !dashboardReady)} onClick={beginEditing}><Settings2 /> 대시보드 편집</button>))}
            <div className="avatar">{authUser ? authUser.display_name.slice(0, 2) : 'HK'}</div>
          </>} />}>
        {workspacePage === 'portfolio' ? <PortfolioDashboard refreshToken={operationalRefreshToken} editMode={editMode} layout={portfolioLayout} layoutVersion={portfolioLayoutVersion} onLayoutChange={setPortfolioLayout} onCancelEdit={cancelEditing} onResetLayout={resetPortfolioLayout} onOpen={(projectId, requestId, loadCaseId) => void openPortfolioRequest(projectId, requestId, loadCaseId)} onOpenResult={(projectId, requestId, loadCaseId, runId) => void openPortfolioResult(projectId, requestId, loadCaseId, runId)} /> : workspacePage === 'intake' ? <RequestIntakePage projects={projects} initialProjectId={selectedProjectId} createdBy={authUser?.display_name ?? '데모 사용자'} canCreate={hasPermission(authUser, 'request.create', selectedProjectId)} onCreated={handleIntakeCreated} onOpenWorkbench={openIntakeWorkbench} /> : workspacePage === 'workbench' ? <>{requestWorkspaceHeader}<Suspense fallback={<FeatureScreenFallback />}><SimulationWorkbench embedded workflows={workflows} initialRequestId={selectedRequestId} currentUserId={authUser?.id ?? ''} createdBy={authUser?.display_name ?? '데모 사용자'} canExecute={canExecuteAssigned || canExecuteAny} isAdmin={canExecuteAny} onRequestSelected={(requestId) => void selectWorkbenchRequest(requestId)} onChanged={async (message) => { setWorkflows(await api.workflows()); setOperationalRefreshToken((value) => value + 1); setNotice(message) }} /></Suspense></> : workspacePage === 'workbench_admin' ? <Suspense fallback={<FeatureScreenFallback />}><WorkbenchTypeAdmin /></Suspense> : workspacePage === 'project_result_profiles' ? <Suspense fallback={<FeatureScreenFallback />}><ProjectResultProfileBinding projectId={selectedProjectId} /></Suspense> : workspacePage === 'schemas' ? <Suspense fallback={<FeatureScreenFallback />}><FolderSchemaWorkspace /></Suspense> : workspacePage === 'variables' ? <Suspense fallback={<FeatureScreenFallback />}><VariableCatalogPage variables={variables} overview={overview!} loadCaseId={selectedLoadCaseId} onChanged={(items) => { setVariables(items); setCatalogVariable((current) => items.some((item) => item.id === current) ? current : items[0]?.id ?? '') }} /></Suspense> : workspacePage === 'templates' ? <Suspense fallback={<FeatureScreenFallback />}><AutomationTemplatesPage /></Suspense> : workspacePage === 'examples' ? <Suspense fallback={<FeatureScreenFallback />}><FeatureExampleGallery onOpen={openFeatureExample} /></Suspense> : workspacePage === 'help' ? <Suspense fallback={<FeatureScreenFallback />}><HelpCenter onNavigate={enterWorkspace} /></Suspense> : workspacePage === 'access_admin' ? <Suspense fallback={<FeatureScreenFallback />}><AccessAdminPage projectId={selectedProjectId} canApproveUsers={hasPermission(authUser, 'system.user.approve', selectedProjectId)} onAccessChanged={refreshAccess} /></Suspense> : workspacePage === 'menu_policy_admin' && menuPolicy ? <Suspense fallback={<FeatureScreenFallback />}><MenuPolicyAdminPage policy={menuPolicy} onPolicyChanged={setMenuPolicy} /></Suspense> : workspacePage === 'audit_admin' ? <Suspense fallback={<FeatureScreenFallback />}><AuditAdminPage /></Suspense> : workspacePage === 'data' ? (
          <>{requestWorkspaceHeader}<Suspense fallback={<FeatureScreenFallback />}><DataWorkspace storagePanel={StorageWorkspacePanel} refreshToken={operationalRefreshToken} embedded contextChanging={workspaceContextTransitioning} canCreateProject={hasPermission(authUser, 'system.user.approve', selectedProjectId)} canRetryImports={hasPermission(authUser, 'system.catalog.manage', selectedProjectId)} canUploadStorage={hasPermission(authUser, 'result.import', selectedProjectId)} canBindStorage={hasPermission(authUser, 'result.import', selectedProjectId)} canManageStorageRoot={Boolean(authUser?.is_global_admin)} projects={projects} initialProjectId={selectedProjectId} initialRequestId={selectedRequestId} initialLoadCaseId={selectedLoadCaseId} onContextChange={({ projectId, requestId, loadCaseId }) => { setSelectedProjectId(projectId); setSelectedRequestId(requestId); setSelectedLoadCaseId(loadCaseId); setOverview(null); setDashboard(null); setAnalysisPages([]); setActiveDashboardId('pending-open-cell'); setActiveView('workflow') }} onLoadCaseCreated={(loadCase) => { setLoadCases((items) => [loadCase, ...items.filter((item) => item.id !== loadCase.id)]); setSelectedLoadCaseId(loadCase.id); setOverview(null); setDashboard(null); setAnalysisPages([]); setActiveDashboardId('pending-open-cell'); setActiveView('workflow') }} onDataChanged={refreshOperationalData} onOpenAnalysis={openImportedResult} onOpenIntake={() => enterWorkspace('intake')} /></Suspense></>
        ) : <>
        {requestWorkspaceHeader}
        {projectSetup ? <ProjectSetupState projectName={projects.find((project) => project.id === selectedProjectId)?.name ?? '선택한 프로젝트'} canCreateRequest={canCreateRequest} onOpenIntake={() => enterWorkspace('intake')} /> : <>
        {activeView !== 'workflow' && !pendingAnalysis && <section className="analysis-subtabs"><div><span>상세 분석</span><b>/</b><strong>{overview!.load_case.request_title}</strong></div><nav aria-label="불량 분석 하위 탭">
          {analysisTabs.map((page) => <div className="analysis-tab-group" key={page.id}><button className={`analysis-tab ${activeDashboardId === page.id ? 'active' : ''}`} onClick={() => switchAnalysisPage(page)}>{page.page.analysis_key === 'open_cell' ? <Activity /> : page.page.analysis_key === 'chassis_rear' ? <BarChart3 /> : page.page.analysis_key === 'run_comparison' ? <MessageSquareText /> : <LayoutDashboard />} {page.name} <span className="analysis-tab-status" data-status={(page.page.analysis_key === 'open_cell' ? overview!.analysis_verdicts.open_cell : page.page.analysis_key === 'chassis_rear' ? overview!.analysis_verdicts.chassis_rear : page.page.analysis_key === 'run_comparison' ? 'SYSTEM' : page.page.status.toUpperCase()).toLowerCase()}>{page.page.analysis_key === 'open_cell' ? overview!.analysis_verdicts.open_cell : page.page.analysis_key === 'chassis_rear' ? overview!.analysis_verdicts.chassis_rear : page.page.analysis_key === 'run_comparison' ? 'SYSTEM' : page.page.status.toUpperCase()}</span></button></div>)}
          {canManagePages && <button className="analysis-page-manage-button" onClick={() => setPageManagerOpen(true)}><Plus /> 분석 페이지 관리</button>}
        </nav>{activeAnalysisPage?.page.analysis_key === 'open_cell' && activeView !== 'compare' && <EdgeFilter selected={selectedEdges} setSelected={setSelectedEdges} />}</section>}
        {editMode && (
          <div className="edit-banner" data-testid={activeView === 'workflow' ? workspaceEditor.mode ?? undefined : 'analysis-dashboard'}><GripVertical /><span><strong>{activeView === 'workflow' && workflowEditorMode === 'layout' ? '대시보드 레이아웃 편집' : activeView === 'workflow' ? '진행 단계 편집' : '편집 모드'}</strong> {activeView === 'workflow' && workflowEditorMode === 'layout' ? '의뢰 위젯을 이동·리사이즈하고 색상과 글자 크기를 조절한 뒤 상단에서 저장하세요.' : activeView === 'workflow' ? '단계 내용·순서·추가·삭제를 편집한 뒤 상단의 단계 변경 저장을 누르세요.' : '위젯을 드래그하거나 모서리를 잡아 크기를 조절하고 설정 버튼으로 그래프, 변수, 글자 크기를 바꾸세요.'}</span><button onClick={cancelEditing}>편집 취소</button></div>
        )}
        {editMode && activeView === 'workflow' && workflowEditorMode === 'layout' && <div className="workflow-layout-toolbar">
          <label><span>강조 색상</span><input aria-label="진행 현황 강조 색상" type="color" value={workflowDashboardLayout.accentColor} onChange={(event) => setWorkflowDashboardLayout({ ...workflowDashboardLayout, accentColor: event.target.value })}/><code>{workflowDashboardLayout.accentColor}</code></label>
          <div><span>글자 크기</span><button aria-label="진행 현황 글자 크기 줄이기" onClick={() => setWorkflowDashboardLayout({ ...workflowDashboardLayout, fontSize: Math.max(8, workflowDashboardLayout.fontSize - 1) })} disabled={workflowDashboardLayout.fontSize <= 8}><Minus /></button><output>{workflowDashboardLayout.fontSize}px</output><button aria-label="진행 현황 글자 크기 늘리기" onClick={() => setWorkflowDashboardLayout({ ...workflowDashboardLayout, fontSize: Math.min(18, workflowDashboardLayout.fontSize + 1) })} disabled={workflowDashboardLayout.fontSize >= 18}><Plus /></button></div>
          <button onClick={resetWorkflowDashboardLayout}><RotateCcw /> 기본 레이아웃</button>
        </div>}
        {editMode && activeView !== 'workflow' && dashboard && (
          <div className="dashboard-edit-toolbar">
            <div><strong>{dashboard.name}</strong><span>v{dashboard.version ?? 1} · 카드 상단의 손잡이로 이동하고 오른쪽 아래 모서리로 크기를 조절합니다.</span></div>
            <button onClick={() => setAssistantOpen(true)}><Plus /> 위젯 추가·버전 관리</button>
          </div>
        )}
        {resultStorageDisclosure}
        {(pendingAnalysis || (activeView !== 'workflow' && !dashboard)) ? <Suspense fallback={<FeatureScreenFallback />}><PendingAnalysisWorkspace projectId={selectedProjectId} projectName={analysisProjectName} requestId={selectedRequestId} requestTitle={analysisTitle} selectedLoadCaseId={selectedLoadCaseId} canOpenData={hasPermission(authUser, 'result.import', selectedProjectId)} onOpenData={() => enterWorkspace('data')} onLoadLayout={workbenchApi.requestResultLayout} onSnapshotPageChange={hydrateSnapshotDashboard} /></Suspense> : activeView !== 'workflow' ? <Suspense fallback={<FeatureScreenFallback />}><ResultsWorkspace
          activeView={activeView}
          canEdit={canEdit}
          canManageThresholds={canManagePages}
          chassisThreshold={chassisThreshold}
          dashboard={dashboard!}
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
          overview={overview!}
          selectedEdges={selectedEdges}
          resultSummary={activeView === 'compare' ? null : <RequestResultSummary activeView={activeView} comparisonAvailable={analysisRuns.length > 1 && analysisTabs.some((page) => page.page.analysis_key === 'run_comparison')} overview={overview!} onOpenComparison={() => { const page = analysisTabs.find((item) => item.page.analysis_key === 'run_comparison'); if (page) switchAnalysisPage(page) }} onSelectEdges={setSelectedEdges} runNo={analysisRuns.find((run) => run.id === overview?.run)?.run_no} />}
        /></Suspense> : (
            <Suspense fallback={<FeatureScreenFallback />}><WorkflowView workflows={projectWorkflows} stageEditMode={editMode && workflowEditorMode === 'stages'} layoutEditMode={editMode && workflowEditorMode === 'layout'} dashboardLayout={workflowDashboardLayout} layoutVersion={workflowLayoutVersion} onDashboardLayoutChange={setWorkflowDashboardLayout} onStepChange={updateWorkflowStepDraft} onAddStep={addWorkflowStepDraft} onDeleteStep={deleteWorkflowStepDraft} onMoveStep={moveWorkflowStepDraft} onOpenAnalysis={openWorkflowAnalysis} activeRequestId={selectedRequestId} loadCaseReady={loadCases.length > 0} onOpenData={() => enterWorkspace('data')} onOpenWorkbench={(requestId) => void openWorkbenchForRequest(requestId)} onSelectRequest={(requestId) => void handleRequestChange(requestId)} /></Suspense>
          )}
        </>}
        </>}
      </AppShellMain>
      <ResultsWorkspaceOverlays
        assistantOpen={assistantOpen}
        canManagePages={canManagePages}
        catalogVariable={catalogVariable}
        command={command}
        dashboard={dashboard}
        proposal={proposal}
        savedDashboards={savedDashboards}
        selectedWidgetId={selectedWidgetId}
        variables={variables}
        versions={versions}
        widgetCatalog={widgetCatalog}
        onAddCatalogWidget={addCatalogWidget}
        onApplyProposal={applyProposal}
        onCloneLayout={() => void cloneLayout()}
        onCloseAssistant={() => setAssistantOpen(false)}
        onCloseWidgetSettings={() => setSelectedWidgetId(null)}
        onCommandChange={setCommand}
        onLoadSavedDashboard={(id) => void loadSavedDashboard(id)}
        onLoadVersion={(version) => void loadDashboardVersionDraft(version)}
        onPreviewCommand={() => void previewCommand()}
        onRemoveVersion={(version) => void removeDashboardVersion(version)}
        onRestorePrevious={() => void restorePrevious()}
        onSelectCatalogVariable={setCatalogVariable}
        onUpdateWidget={updateWidget}
      />
      {pageManagerOpen && <Suspense fallback={null}><AnalysisPageManager loadCaseId={selectedLoadCaseId} activePageId={activeDashboardId} visiblePageIds={analysisTabs.map((page) => page.id)} onClose={() => setPageManagerOpen(false)} onPagesChanged={setAnalysisPages} onActiveDefinitionChanged={(definition) => setDashboard(definition)} onDeleted={(deletedId) => {
        reportExport.handlePageDeleted(deletedId)
        dashboardBeforeEdit.current = null; setSelectedWidgetId(null); setAssistantOpen(false); workspaceEditor.close()
      }} onActivate={(definition, startEditing = false) => {
        if (!definition.page) return
        if (editMode && dashboardBeforeEdit.current && !canChangeContext()) return
        const summary: DashboardPageSummary = { id: definition.id, project_id: selectedProjectId, request_id: selectedRequestId, load_case_id: selectedLoadCaseId, name: definition.name, description: definition.description, version: definition.version ?? 1, updated_at: definition.updated_at ?? new Date().toISOString(), page: definition.page }
        setAnalysisPages((items) => [...items.filter((item) => item.id !== summary.id), summary].sort((left, right) => left.page.display_order - right.page.display_order))
        setDashboard(definition); setActiveDashboardId(definition.id); setActiveView(pageView(summary)); setPageManagerOpen(false)
        if (startEditing) { dashboardBeforeEdit.current = structuredClone(definition); workspaceEditor.open('analysis-dashboard'); setAssistantOpen(true) }
      }} /></Suspense>}
      <ReportExportDialog controller={reportExport} />
      {notice && <div className="toast" role="status"><Check /><span>{notice}</span><button aria-label="알림 닫기" onClick={() => setNotice('')}><X /></button></div>}
    </AppShell>
  )
}
export default App
