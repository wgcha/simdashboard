import { useEffect, useMemo, useRef, useState, type ComponentType, type FormEvent } from 'react'
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Check,
  ChevronDown,
  CircleDot,
  Database,
  Download,
  GripVertical,
  LayoutDashboard,
  LoaderCircle,
  Lock,
  MessageSquareText,
  PanelLeftClose,
  Play,
  Plus,
  Save,
  Search,
  Settings2,
  Sparkles,
  Upload,
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
import { PortfolioDashboard } from './PortfolioDashboard'
import type { AnalysisRequest, AutomationTemplate, DashboardDefinition, DashboardSummary, DashboardVersion, DashboardWidget, ImportSchema, LoadCase, Overview, Project, QualityThreshold, VariableDefinition, VariableDefinitionInput, WidgetCatalogItem, Workflow, WorkflowStep } from './types'

const ResponsiveGridLayout = WidthProvider(Responsive) as unknown as ComponentType<any>
const SERIES_COLORS = ['#61d4ff', '#ff647d', '#70e0a8', '#ffbf57']
type ActiveView = 'open_cell' | 'chassis' | 'workflow'

function App() {
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
  const [activeView, setActiveView] = useState<ActiveView>('workflow')
  const [workspacePage, setWorkspacePage] = useState<'portfolio' | 'dashboard' | 'data' | 'schemas' | 'variables' | 'templates'>('portfolio')
  const [editMode, setEditMode] = useState(false)
  const [assistantOpen, setAssistantOpen] = useState(false)
  const [command, setCommand] = useState('')
  const [proposal, setProposal] = useState<Awaited<ReturnType<typeof api.previewCommand>> | null>(null)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [selectedEdges, setSelectedEdges] = useState<string[]>(['top', 'bottom', 'left', 'right'])
  const [widgetCatalog, setWidgetCatalog] = useState<WidgetCatalogItem[]>([])
  const [variables, setVariables] = useState<VariableDefinition[]>([])
  const [versions, setVersions] = useState<DashboardVersion[]>([])
  const [catalogVariable, setCatalogVariable] = useState('')
  const [savedDashboards, setSavedDashboards] = useState<DashboardSummary[]>([])
  const [selectedWidgetId, setSelectedWidgetId] = useState<string | null>(null)

  useEffect(() => {
    const bootstrap = async () => {
      try {
        const [projectData, workflowData, dashboardData] = await Promise.all([api.projects(), api.workflows(), api.dashboard()])
        const project = projectData[0]
        if (!project) throw new Error('등록된 프로젝트가 없습니다.')
        const requestData = await api.requests(project.id)
        const request = requestData.find((item) => item.id === 'request-drop-001') ?? requestData[0]
        if (!request) throw new Error('등록된 의뢰가 없습니다.')
        const [caseData, thresholdData] = await Promise.all([api.loadCases(request.id), api.qualityThresholds(project.id)])
        const loadCase = caseData[0]
        if (!loadCase) throw new Error('등록된 하중 경우가 없습니다.')
        const overviewData = await api.overview(loadCase.id)
        setProjects(projectData)
        setRequests(requestData)
        setLoadCases(caseData)
        setThresholds(thresholdData)
        setSelectedProjectId(project.id)
        setSelectedRequestId(request.id)
        setSelectedLoadCaseId(loadCase.id)
        setOverview(overviewData)
        setWorkflows(workflowData)
        setDashboard(dashboardData)
        setActiveView('workflow')
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '초기 데이터를 불러오지 못했습니다.')
      } finally {
        setLoading(false)
      }
    }
    bootstrap()
  }, [])

  useEffect(() => {
    if (activeView !== 'open_cell' && activeView !== 'chassis') return
    const id = activeView === 'chassis' ? 'dashboard-chassis-default' : 'dashboard-drop-default'
    api.dashboard(id).then(setDashboard).catch((reason) => setError(reason instanceof Error ? reason.message : '분석 레이아웃을 불러오지 못했습니다.'))
  }, [activeView])

  useEffect(() => {
    if ((!editMode && workspacePage !== 'variables') || !selectedLoadCaseId) return
    api.variables(selectedLoadCaseId).then((items) => { setVariables(items); setCatalogVariable((current) => current || items[0]?.id || '') }).catch(() => setVariables([]))
  }, [editMode, workspacePage, selectedLoadCaseId])

  useEffect(() => {
    if (!assistantOpen || !dashboard || !selectedLoadCaseId) return
    Promise.all([api.widgetCatalog(), api.variables(selectedLoadCaseId), api.dashboardVersions(dashboard.id), api.dashboards(selectedProjectId)])
      .then(([catalog, variableData, versionData, dashboardData]) => { setWidgetCatalog(catalog); setVariables(variableData); setVersions(versionData); setSavedDashboards(dashboardData); setCatalogVariable(variableData[0]?.id ?? '') })
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
    const overviewData = await api.overview(loadCase.id)
    setRequests(requestData)
    setLoadCases(caseData)
    setThresholds(thresholdData)
    setSelectedProjectId(projectId)
    setSelectedRequestId(request.id)
    setSelectedLoadCaseId(loadCase.id)
    setOverview(overviewData)
    const availableView = overviewData.analysis_verdicts.open_cell !== 'NO_DATA' ? 'open_cell' : 'chassis'
    setActiveView(preferredView === 'chassis' && overviewData.analysis_verdicts.chassis_rear !== 'NO_DATA' ? 'chassis' : preferredView === 'open_cell' && overviewData.analysis_verdicts.open_cell !== 'NO_DATA' ? 'open_cell' : availableView)
  }

  const handleProjectChange = async (projectId: string) => {
    try { await loadContext(projectId) } catch (reason) { setError(reason instanceof Error ? reason.message : '프로젝트를 변경하지 못했습니다.') }
  }

  const handleRequestChange = async (requestId: string) => {
    try { await loadContext(selectedProjectId, requestId) } catch (reason) { setError(reason instanceof Error ? reason.message : '의뢰를 변경하지 못했습니다.') }
  }

  const handleLoadCaseChange = async (loadCaseId: string) => {
    try {
      const overviewData = await api.overview(loadCaseId)
      setSelectedLoadCaseId(loadCaseId)
      setOverview(overviewData)
      setActiveView(overviewData.analysis_verdicts.open_cell !== 'NO_DATA' ? 'open_cell' : 'chassis')
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
    if (!dashboard) return
    if (activeView === 'workflow') {
      setEditMode(false)
      setNotice('작업 단계 편집을 완료했습니다.')
      setTimeout(() => setNotice(''), 2600)
      return
    }
    try {
      const result = await api.saveDashboard(dashboard)
      setDashboard({ ...dashboard, version: result.version, updated_at: result.updated_at })
      setNotice(`레이아웃 v${result.version} 저장 완료`)
      setTimeout(() => setNotice(''), 2600)
      setEditMode(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '저장하지 못했습니다.')
    }
  }

  const previewCommand = async () => {
    if (!command.trim()) return
    try {
      setProposal(await api.previewCommand(command))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '요청을 해석하지 못했습니다.')
    }
  }

  const applyProposal = () => {
    if (!dashboard || !proposal?.proposal) return
    if (proposal.proposal.action === 'add_widget') setDashboard({ ...dashboard, widgets: [...dashboard.widgets, proposal.proposal.widget] })
    else {
      const updates = proposal.proposal.updates
      setDashboard({ ...dashboard, widgets: dashboard.widgets.map((widget) => { const update = updates.find((item) => item.widget_type === widget.type); return update ? { ...widget, ...update, settings: { ...widget.settings, ...update.settings } } : widget }) })
    }
    setProposal(null)
    setCommand('')
    setAssistantOpen(false)
    setEditMode(true)
    setNotice('변경안을 적용했습니다. 저장하면 새 버전이 생성됩니다.')
    setTimeout(() => setNotice(''), 3000)
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
    const variable = variables.find((entry) => entry.id === catalogVariable)
    if (variable && !item.allowed_data_types.includes(variable.data_type)) { setNotice(`${variable.display_name}에는 ${item.label}을 사용할 수 없습니다.`); return }
    const [w, h] = item.default_size
    setDashboard({ ...dashboard, widgets: [...dashboard.widgets, { id: `${item.type}-${Date.now()}`, type: item.type, title: variable ? `${variable.display_name} · ${item.label}` : item.label, x: 0, y: 30, w, h, settings: { variableId: variable?.id } }] })
    setEditMode(true); setNotice('위젯을 추가했습니다. 위치를 조정한 뒤 저장하세요.')
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
    setDashboard(await api.dashboard()); setNotice(`v${previous.version} 내용을 새 버전으로 복구했습니다.`)
  }

  const loadSavedDashboard = async (id: string) => { setDashboard(await api.dashboard(id)); setNotice('저장된 레이아웃을 불러왔습니다.') }

  const renameWorkflowStep = async (stepId: string, name: string) => {
    const current = workflows.flatMap((workflow) => workflow.steps).find((step) => step.id === stepId)
    if (!current || current.name === name.trim() || !name.trim()) return
    try {
      const result = await api.renameWorkflowStep(stepId, name.trim())
      setWorkflows((items) => items.map((workflow) => ({
        ...workflow,
        steps: workflow.steps.map((step) => step.id === stepId ? { ...step, name: result.name } : step),
      })))
      setNotice(`단계 이름 저장: ${result.name}`)
      setTimeout(() => setNotice(''), 2200)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '단계 이름을 저장하지 못했습니다.')
    }
  }

  const saveChassisThreshold = async (value: number) => {
    const criterion = thresholds.find((item) => item.criterion_key === 'chassis_rear_permanent_deformation_mm')
    if (!criterion || !overview) return
    try {
      const updated = await api.updateQualityThreshold(criterion.criterion_key, value)
      setThresholds((items) => items.map((item) => item.criterion_key === updated.criterion_key ? updated : item))
      setOverview(await api.overview(overview.load_case.id))
      setNotice(`관리자 판정 기준을 ${value.toFixed(1)} mm로 저장했습니다.`)
      setTimeout(() => setNotice(''), 2800)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '판정 기준을 저장하지 못했습니다.') }
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
  }

  const openImportedResult = async (projectId: string, requestId: string, loadCaseId: string) => {
    const [requestData, caseData, thresholdData, overviewData] = await Promise.all([
      api.requests(projectId), api.loadCases(requestId), api.qualityThresholds(projectId), api.overview(loadCaseId),
    ])
    setRequests(requestData); setLoadCases(caseData); setThresholds(thresholdData)
    setSelectedProjectId(projectId); setSelectedRequestId(requestId); setSelectedLoadCaseId(loadCaseId); setOverview(overviewData)
    setActiveView(overviewData.analysis_verdicts.open_cell !== 'NO_DATA' ? 'open_cell' : 'chassis')
    setWorkspacePage('dashboard')
  }

  if (loading) {
    return <div className="full-state"><LoaderCircle className="spin" /> 데이터와 레이아웃을 준비하고 있습니다.</div>
  }

  if (error || !overview || workflows.length === 0 || !dashboard) {
    return <div className="full-state error"><AlertTriangle /> {error || '대시보드를 불러오지 못했습니다.'}</div>
  }

  const projectWorkflows = workflows.filter((workflow) => workflow.request.project_id === selectedProjectId)
  const openCellAvailable = overview.analysis_verdicts.open_cell !== 'NO_DATA'
  const chassisAvailable = overview.analysis_verdicts.chassis_rear !== 'NO_DATA'
  const workflowProgress = Math.round(projectWorkflows.reduce((sum, workflow) => sum + workflow.progress, 0) / Math.max(projectWorkflows.length, 1))
  const chassisThreshold = thresholds.find((item) => item.criterion_key === 'chassis_rear_permanent_deformation_mm')

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark"><Activity /></span><span>ANALYSIS<br /><strong>CANVAS</strong></span></div>
        <nav className="nav-main">
          <button className={workspacePage === 'portfolio' ? 'active' : ''} onClick={() => setWorkspacePage('portfolio')}><LayoutDashboard /><span>운영 대시보드</span></button>
          <button className={workspacePage === 'dashboard' ? 'active' : ''} onClick={() => setWorkspacePage('dashboard')}><Activity /><span>해석 상세</span></button>
          <button className={workspacePage === 'data' ? 'active' : ''} onClick={() => setWorkspacePage('data')}><Database /><span>해석 데이터</span></button>
          <button className={workspacePage === 'variables' ? 'active' : ''} onClick={() => setWorkspacePage('variables')}><BarChart3 /><span>변수 카탈로그</span></button>
          <button className={workspacePage === 'templates' ? 'active' : ''} onClick={() => setWorkspacePage('templates')}><Settings2 /><span>자동화 템플릿</span></button>
          <button className={workspacePage === 'schemas' ? 'active' : ''} onClick={() => setWorkspacePage('schemas')}><GripVertical /><span>폴더 스키마</span></button>
        </nav>
        <div className="sidebar-foot">
          <div className="system-pill"><span className="live-dot" /> DUCKDB · LOCAL</div>
          <button><PanelLeftClose /> 메뉴 접기</button>
        </div>
      </aside>

      <main className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">{workspacePage === 'portfolio' ? <><span>운영</span><b>/</b><strong>해석 운영 현황</strong></> : workspacePage === 'data' ? <><span>운영</span><b>/</b><strong>해석 데이터 등록</strong></> : workspacePage === 'schemas' ? <><span>데이터 설계</span><b>/</b><strong>폴더 스키마</strong></> : workspacePage === 'variables' ? <><span>설계</span><b>/</b><strong>변수 카탈로그</strong></> : workspacePage === 'templates' ? <><span>자동화</span><b>/</b><strong>모델링 템플릿</strong></> : <><span>프로젝트</span><b>/</b><span>{overview.load_case.project_name}</span><b>/</b><strong>{overview.load_case.name}</strong></>}</div>
          <div className="top-actions">
            {workspacePage === 'dashboard' && <button className="ghost-button" onClick={() => setAssistantOpen(true)}><Sparkles /> 자연어로 개선</button>}
            {workspacePage === 'dashboard' && (editMode ? (
              <button className="primary-button" onClick={save}><Save /> {activeView === 'workflow' ? '편집 완료' : '레이아웃 저장'}</button>
            ) : (
              <button className="edit-button" onClick={() => setEditMode(true)}><Settings2 /> 대시보드 편집</button>
            ))}
            <div className="avatar">HK</div>
          </div>
        </header>

        {workspacePage === 'portfolio' ? <PortfolioDashboard onOpen={(projectId, requestId, loadCaseId) => void openImportedResult(projectId, requestId, loadCaseId)} /> : workspacePage === 'schemas' ? <FolderSchemaWorkspace /> : workspacePage === 'variables' ? <VariableCatalogPage variables={variables} overview={overview} loadCaseId={selectedLoadCaseId} onChanged={(items) => { setVariables(items); setCatalogVariable((current) => items.some((item) => item.id === current) ? current : items[0]?.id ?? '') }} /> : workspacePage === 'templates' ? <AutomationTemplatesPage /> : workspacePage === 'data' ? (
          <DataWorkspace projects={projects} initialProjectId={selectedProjectId} onDataChanged={refreshOperationalData} onOpenAnalysis={openImportedResult} />
        ) : <>
        <section className="content-head">
          <div>
            <div className="eyebrow"><span>PROJECT 24-071</span><span>•</span><span>{overview.load_case.analysis_type}</span></div>
            <h1>{overview.load_case.product_name} 불량 분석</h1>
            <p>{overview.load_case.request_title}</p>
          </div>
          <div className="context-selectors">
            <label><span>프로젝트</span><select aria-label="프로젝트 선택" value={selectedProjectId} onChange={(event) => handleProjectChange(event.target.value)}>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
            <label><span>의뢰</span><select aria-label="의뢰 선택" value={selectedRequestId} onChange={(event) => handleRequestChange(event.target.value)}>{requests.map((request) => <option key={request.id} value={request.id}>{request.title}</option>)}</select></label>
            <label><span>하중 경우</span><select aria-label="하중 경우 선택" value={selectedLoadCaseId} onChange={(event) => handleLoadCaseChange(event.target.value)}>{loadCases.map((loadCase) => <option key={loadCase.id} value={loadCase.id}>{loadCase.name}</option>)}</select></label>
          </div>
        </section>

        <section className="view-tabs">
          <button className={activeView === 'workflow' ? 'active' : ''} onClick={() => setActiveView('workflow')}><CircleDot /> 의뢰 진행 상태 <span>{workflowProgress}%</span></button>
          <button className={activeView !== 'workflow' ? 'active' : ''} onClick={() => setActiveView(openCellAvailable ? 'open_cell' : 'chassis')}><LayoutDashboard /> 상세 분석 <span>{overview.load_case.analysis_type.replace('_', ' ')}</span></button>
          <div className="tab-line" />
        </section>

        {activeView !== 'workflow' && <section className="analysis-subtabs"><div><span>상세 분석</span><b>/</b><strong>{overview.load_case.request_title}</strong></div><nav aria-label="불량 분석 하위 탭">{openCellAvailable && <button className={activeView === 'open_cell' ? 'active' : ''} onClick={() => setActiveView('open_cell')}><Activity /> 오픈셀 파손 분석 <span>{overview.analysis_verdicts.open_cell}</span></button>}{chassisAvailable && <button className={activeView === 'chassis' ? 'active' : ''} onClick={() => setActiveView('chassis')}><BarChart3 /> Chassis Rear 영구변형 평가 <span>{overview.analysis_verdicts.chassis_rear}</span></button>}</nav>{activeView === 'open_cell' && <EdgeFilter selected={selectedEdges} setSelected={setSelectedEdges} />}</section>}

        {editMode && (
          <div className="edit-banner"><GripVertical /><span><strong>편집 모드</strong> {activeView === 'workflow' ? '단계 이름 입력란을 수정하면 즉시 저장됩니다.' : '위젯을 드래그하거나 모서리를 잡아 크기를 조절하고 설정 버튼으로 그래프와 변수를 바꾸세요.'}</span><button onClick={() => setEditMode(false)}>편집 취소</button></div>
        )}

        <section className="canvas-area">
          {activeView !== 'workflow' ? (
            <ResponsiveGridLayout
              className="layout"
              layouts={{ lg: dashboard.widgets.map((item) => ({ i: item.id, x: item.x, y: item.y, w: item.w, h: item.h })) }}
              breakpoints={{ lg: 900, md: 600, sm: 0 }}
              cols={{ lg: 12, md: 8, sm: 1 }}
              rowHeight={74}
              margin={[16, 16]}
              isDraggable={editMode}
              isResizable={editMode}
              compactType="vertical"
              onLayoutChange={handleLayoutChange}
            >
              {dashboard.widgets.map((widget) => (
                <div key={widget.id}>
                  <WidgetCard widget={widget} overview={overview} selectedEdges={selectedEdges} editMode={editMode} threshold={chassisThreshold} onSaveThreshold={saveChassisThreshold} onRemove={() => removeWidget(widget.id)} onConfigure={() => setSelectedWidgetId(widget.id)} />
                </div>
              ))}
            </ResponsiveGridLayout>
          ) : (
            <WorkflowView workflows={projectWorkflows} editMode={editMode} onRename={renameWorkflowStep} onOpenAnalysis={openWorkflowAnalysis} activeRequestId={selectedRequestId} />
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
              <div>{widgetCatalog.filter((item) => ['kpi','verdict','gauge','edge_bar','time_series','scatter','result_table','contour','note'].includes(item.type)).map((item) => <button key={item.type} onClick={() => addCatalogWidget(item)}><Plus /> {item.label}</button>)}</div>
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
            <div className="layout-history"><button onClick={cloneLayout}>다른 이름으로 복제</button><button onClick={restorePrevious}>최근 정상 버전 복구</button><small>현재 v{dashboard.version ?? 1} · 저장 이력 {versions.length}개</small></div>
            <label className="saved-layouts"><span>저장된 레이아웃 불러오기</span><select value={dashboard.id} onChange={(event) => void loadSavedDashboard(event.target.value)}>{savedDashboards.map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.version}</option>)}</select></label>
          </aside>
        </div>
      )}
      {selectedWidgetId && dashboard && <WidgetSettingsPanel widget={dashboard.widgets.find((item) => item.id === selectedWidgetId)!} variables={variables} onChange={(patch) => updateWidget(selectedWidgetId, patch)} onClose={() => setSelectedWidgetId(null)} />}
      {notice && <div className="toast"><Check /> {notice}</div>}
    </div>
  )
}

function DataWorkspace({ projects, initialProjectId, onDataChanged, onOpenAnalysis }: { projects: Project[]; initialProjectId: string; onDataChanged: () => Promise<void>; onOpenAnalysis: (projectId: string, requestId: string, loadCaseId: string) => Promise<void> }) {
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
  const [requestForm, setRequestForm] = useState({ title: '', owner: '', due_in_days: 14, overall_note: '' })
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

  const submitRequest = (event: FormEvent) => {
    event.preventDefault()
    if (!projectId) return
    void complete('의뢰', async () => {
      const created = await api.createRequest(projectId, requestForm)
      setRequests((items) => [created, ...items]); setRequestId(created.id); setLoadCases([])
      setRequestForm({ title: '', owner: '', due_in_days: 14, overall_note: '' })
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

  const selectedProject = managedProjects.find((item) => item.id === projectId)
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
      <div><span>OPERATIONS / FILE DATABASE</span><h1>해석 데이터 등록</h1><p>프로젝트 → 의뢰 → 하중 경우 순서로 등록합니다. 의뢰 생성 시 기본 작업 순서가 자동으로 준비됩니다.</p></div>
      <div className="data-count"><strong>{managedProjects.length}</strong><span>PROJECTS</span></div>
    </header>

    <div className="data-hierarchy-bar">
      <label><span>1 · 프로젝트</span><select aria-label="등록 프로젝트 선택" value={projectId} onChange={(event) => setProjectId(event.target.value)}>{managedProjects.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.product_name}</option>)}</select></label>
      <i>›</i>
      <label><span>2 · 의뢰</span><select aria-label="등록 의뢰 선택" value={requestId} onChange={(event) => setRequestId(event.target.value)} disabled={!requests.length}>{requests.length ? requests.map((item) => <option key={item.id} value={item.id}>{item.title}</option>) : <option>의뢰를 먼저 등록하세요</option>}</select></label>
      <i>›</i>
      <label><span>3 · 하중 경우</span><select aria-label="등록 하중 경우 선택" value={loadCaseId} onChange={(event) => { setLoadCaseId(event.target.value); setResultFile(null); setImportPreview(null); setImported(false) }} disabled={!loadCases.length}>{loadCases.length ? loadCases.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.analysis_type}</option>) : <option value="">하중 경우 없음</option>}</select></label>
    </div>

    {(message || formError) && <div className={`data-message ${formError ? 'error' : ''}`}>{formError ? <AlertTriangle /> : <Check />}{formError || message}</div>}

    <div className="data-form-grid">
      <article className="data-form-card"><header><span>01</span><div><h2>새 프로젝트</h2><p>제품 단위 최상위 분류</p></div></header><form onSubmit={submitProject}>
        <label><span>프로젝트 이름</span><input required value={projectForm.name} onChange={(e) => setProjectForm({ ...projectForm, name: e.target.value })} placeholder="예: 2026 OLED 신뢰성" /></label>
        <label><span>제품 모델명</span><input required value={projectForm.product_name} onChange={(e) => setProjectForm({ ...projectForm, product_name: e.target.value })} placeholder="예: OLED77X26" /></label>
        <label><span>제조사</span><input value={projectForm.manufacturer} onChange={(e) => setProjectForm({ ...projectForm, manufacturer: e.target.value })} placeholder="예: NeoView Display" /></label>
        <label><span>화면 크기 (inch)</span><input type="number" min="1" max="200" value={projectForm.display_size_inch ?? ''} onChange={(e) => setProjectForm({ ...projectForm, display_size_inch: e.target.value ? Number(e.target.value) : null })} /></label>
        <label><span>설명</span><textarea value={projectForm.description} onChange={(e) => setProjectForm({ ...projectForm, description: e.target.value })} placeholder="제품과 해석 목적을 입력하세요." /></label>
        <button className="data-submit" disabled={busy}><Plus /> 프로젝트 등록</button>
      </form></article>

      <article className="data-form-card"><header><span>02</span><div><h2>새 의뢰</h2><p>{selectedProject?.name || '프로젝트를 선택하세요'}</p></div></header><form onSubmit={submitRequest}>
        <label><span>의뢰 제목</span><input required disabled={!projectId} value={requestForm.title} onChange={(e) => setRequestForm({ ...requestForm, title: e.target.value })} placeholder="예: 포장 낙하 내구 해석" /></label>
        <div className="data-form-row"><label><span>작업자</span><input required value={requestForm.owner} onChange={(e) => setRequestForm({ ...requestForm, owner: e.target.value })} placeholder="이름" /></label><label><span>기한 (일)</span><input type="number" min="1" value={requestForm.due_in_days} onChange={(e) => setRequestForm({ ...requestForm, due_in_days: Number(e.target.value) })} /></label></div>
        <label><span>요청 사항</span><textarea value={requestForm.overall_note} onChange={(e) => setRequestForm({ ...requestForm, overall_note: e.target.value })} placeholder="해석 조건과 검토 요청을 입력하세요." /></label>
        <button className="data-submit" disabled={busy || !projectId}><Plus /> 의뢰 및 작업 순서 생성</button>
      </form></article>

      <article className="data-form-card"><header><span>03</span><div><h2>새 하중 경우</h2><p>{selectedRequest?.title || '의뢰를 선택하세요'}</p></div></header><form onSubmit={submitLoadCase}>
        <label><span>하중 경우 이름</span><input required disabled={!requestId} value={caseForm.name} onChange={(e) => setCaseForm({ ...caseForm, name: e.target.value })} placeholder="예: Bottom Drop 800 mm" /></label>
        <label><span>해석 유형</span><select value={caseForm.analysis_type} onChange={(e) => { const type = e.target.value as 'DROP' | 'SIDE_CLAMP'; setCaseForm({ name: caseForm.name, analysis_type: type, primary: type === 'DROP' ? '800' : '25', secondary: type === 'DROP' ? 'BOTTOM' : '10' }) }}><option value="DROP">포장 낙하 (DROP)</option><option value="SIDE_CLAMP">Side Clamp</option></select></label>
        <div className="data-form-row"><label><span>{caseForm.analysis_type === 'DROP' ? '낙하 높이 (mm)' : '압력 (kPa)'}</span><input type="number" min="0" required value={caseForm.primary} onChange={(e) => setCaseForm({ ...caseForm, primary: e.target.value })} /></label><label><span>{caseForm.analysis_type === 'DROP' ? '충격 방향' : '유지 시간 (s)'}</span>{caseForm.analysis_type === 'DROP' ? <select value={caseForm.secondary} onChange={(e) => setCaseForm({ ...caseForm, secondary: e.target.value })}><option>BOTTOM</option><option>TOP</option><option>LEFT</option><option>RIGHT</option></select> : <input type="number" min="0" value={caseForm.secondary} onChange={(e) => setCaseForm({ ...caseForm, secondary: e.target.value })} />}</label></div>
        <button className="data-submit" disabled={busy || !requestId}><Plus /> 하중 경우 등록</button>
      </form></article>
    </div>

    <article className="result-import-card">
      <header>
        <div><span>04 · RESULT INGESTION</span><h2>해석 결과 가져오기</h2><p>선택한 하중 경우에 CSV 또는 JSON 결과를 검증한 뒤 새 Analysis Run으로 저장합니다.</p></div>
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

function WidgetCard({ widget, overview, selectedEdges, editMode, threshold, onSaveThreshold, onRemove, onConfigure }: { widget: DashboardWidget; overview: Overview; selectedEdges: string[]; editMode: boolean; threshold?: QualityThreshold; onSaveThreshold: (value: number) => void; onRemove: () => void; onConfigure: () => void }) {
  return (
    <article className={`widget-card widget-${widget.type} ${editMode ? 'editable' : ''}`}>
      <header><div><span className="widget-kicker">{widget.type.replace('_', ' ')}</span><h3>{widget.title}</h3></div>{editMode ? <div className="widget-edit-actions"><button aria-label={`${widget.title} 설정`} onClick={onConfigure}><Settings2 /></button><button aria-label={`${widget.title} 삭제`} onClick={onRemove}><X /></button></div> : <button className="widget-menu">•••</button>}</header>
      <div className="widget-body"><WidgetContent widget={widget} overview={overview} selectedEdges={selectedEdges} threshold={threshold} onSaveThreshold={onSaveThreshold} /></div>
    </article>
  )
}

function WidgetContent({ widget, overview, selectedEdges, threshold, onSaveThreshold }: { widget: DashboardWidget; overview: Overview; selectedEdges: string[]; threshold?: QualityThreshold; onSaveThreshold: (value: number) => void }) {
  const type = widget.type
  const edgeOrder = ['top', 'bottom', 'left', 'right']
  const openCellScalars = overview.scalar_results.filter((item) => !item.variable_key.startsWith('chassis_rear_'))
  const filteredScalars = openCellScalars
    .filter((item) => selectedEdges.some((edge) => item.variable_key.startsWith(edge)))
    .sort((a, b) => edgeOrder.indexOf(a.variable_key.split('_')[0]) - edgeOrder.indexOf(b.variable_key.split('_')[0]))
  const edgeLabel = (key: string) => ({ top: '상단', bottom: '하단', left: '좌측', right: '우측' }[key.split('_')[0]] ?? key)
  const configuredScalars = widget.settings?.variableId ? overview.scalar_results.filter((item) => item.variable_key === widget.settings?.variableId) : filteredScalars
  const barData = configuredScalars.map((item) => ({ name: edgeLabel(item.variable_key), value: item.value_double, verdict: item.verdict }))
  const openCellThreshold = openCellScalars[0]?.threshold_double ?? 75
  const openCellVerdict = openCellScalars.some((item) => item.verdict === 'FAIL') ? 'FAIL' : openCellScalars.length ? 'PASS' : 'NO_DATA'
  const resultLocation = (key: string) => overview.result_locations.find((item) => item.variable_key === key)
  const seriesData = useMemo(() => {
    const grouped = new Map<number, Record<string, number>>()
    overview.time_series.filter((item) => widget.settings?.variableId ? item.variable_key === widget.settings.variableId : selectedEdges.some((edge) => item.variable_key.startsWith(edge))).forEach((item) => {
      const point = grouped.get(item.time_value) ?? { time: item.time_value }
      point[item.variable_key] = item.value
      grouped.set(item.time_value, point)
    })
    return [...grouped.values()]
  }, [overview.time_series, selectedEdges, widget.settings?.variableId])

  if (type.startsWith('chassis_')) return <ChassisWidgetContent type={type} overview={overview} threshold={threshold} onSaveThreshold={onSaveThreshold} variableId={String(widget.settings?.variableId ?? '')} />
  if (widget.settings?.variableId && type === 'time_series' && !overview.time_series.some((item)=>item.variable_key===widget.settings?.variableId)) return <div className="widget-empty"><Database/><strong>선언된 변수에 결과 데이터가 없습니다.</strong><small>{String(widget.settings.variableId)} 키의 시간 이력을 가져오면 자동 표시됩니다.</small></div>
  if (widget.settings?.variableId && ['kpi','gauge','edge_bar','scatter','result_table'].includes(type) && !overview.scalar_results.some((item)=>item.variable_key===widget.settings?.variableId)) return <div className="widget-empty"><Database/><strong>선언된 변수에 결과 데이터가 없습니다.</strong><small>{String(widget.settings.variableId)} 키의 숫자 결과를 가져오면 자동 표시됩니다.</small></div>

  if (type === 'open_cell_map') return <OpenCellMap overview={overview} />
  if (type === 'verdict') return <div className={`verdict-block ${openCellVerdict.toLowerCase()}`}><div className="verdict-icon">{openCellVerdict === 'PASS' ? <Check /> : <X />}</div><div><strong>{openCellVerdict}</strong><span>{openCellVerdict === 'PASS' ? '허용 기준 만족' : '기준 초과 감지'}</span></div><small>LIMIT {openCellThreshold} MPa</small></div>
  if (type === 'summary') return overview.load_case.analysis_type === 'SIDE_CLAMP' ? <div className="summary-grid"><div><span>클램프 압력</span><strong>{overview.load_case.parameters.pressure_mpa ?? overview.load_case.parameters.clamp_pressure_kpa ?? '-'}<em>MPa</em></strong></div><div><span>유지 시간</span><strong>{overview.load_case.parameters.hold_time_sec ?? overview.load_case.parameters.hold_time_s ?? '-'}<em>s</em></strong></div><div><span>클램프 면</span><strong>{Array.isArray(overview.load_case.parameters.faces) ? overview.load_case.parameters.faces.join(' / ') : 'LEFT / RIGHT'}</strong></div><div><span>요소 수</span><strong>{overview.template_execution?.generated_model.elements.toLocaleString() ?? '-'}</strong></div></div> : <div className="summary-grid"><div><span>낙하 높이</span><strong>{overview.load_case.parameters.drop_height_mm}<em>mm</em></strong></div><div><span>낙하 방향</span><strong>{overview.load_case.parameters.direction ?? overview.load_case.parameters.impact_direction ?? '-'}</strong></div><div><span>자동화 템플릿</span><strong>{overview.template_execution?.template_version ?? '-'}</strong></div><div><span>요소 수</span><strong>{overview.template_execution?.generated_model.elements.toLocaleString() ?? '-'}</strong></div></div>
  if (type === 'edge_bar') return <ResponsiveContainer width="100%" height="100%"><BarChart data={barData} margin={{ top: 12, right: 18, left: -12, bottom: 0 }}><CartesianGrid vertical={false} stroke="#26394c" strokeDasharray="3 3"/><XAxis dataKey="name" tick={{ fill: '#8fa6bb', fontSize: 12 }} axisLine={false} tickLine={false}/><YAxis domain={[0, 100]} tick={{ fill: '#6f879d', fontSize: 11 }} axisLine={false} tickLine={false} unit=""/><Tooltip contentStyle={{ background: '#102235', border: '1px solid #2d465c', borderRadius: 10 }} formatter={(value: number) => [`${value} MPa`, '최대 응력']}/><ReferenceLine y={openCellThreshold} stroke="#ffbf57" strokeDasharray="5 5" label={{ value: `기준 ${openCellThreshold}`, fill: '#ffbf57', fontSize: 11, position: 'insideTopRight' }}/><Bar dataKey="value" radius={[5,5,1,1]}>{barData.map((entry) => <Cell key={entry.name} fill={entry.verdict === 'FAIL' ? '#ff5d73' : '#4fd6a0'} />)}</Bar></BarChart></ResponsiveContainer>
  if (type === 'time_series') { const seriesKeys = widget.settings?.variableId ? [String(widget.settings.variableId)] : selectedEdges.map((edge) => `${edge}_edge_stress_time`); return <ResponsiveContainer width="100%" height="100%"><LineChart data={seriesData} margin={{ top: 10, right: 22, left: -8, bottom: 2 }}><CartesianGrid stroke="#24384b" strokeDasharray="3 3"/><XAxis dataKey="time" tick={{ fill: '#71899f', fontSize: 11 }} axisLine={{ stroke: '#31485b' }} tickLine={false} label={{ value: `TIME (${overview.time_series[0]?.time_unit ?? 'ms'})`, fill: '#6f879d', fontSize: 10, position: 'insideBottomRight', offset: -2 }}/><YAxis tick={{ fill: '#71899f', fontSize: 11 }} axisLine={false} tickLine={false}/><Tooltip contentStyle={{ background: '#102235', border: '1px solid #2d465c', borderRadius: 10 }}/><Legend wrapperStyle={{ fontSize: 11, paddingTop: 5 }}/>{widget.settings?.showThreshold !== false && <ReferenceLine y={openCellThreshold} stroke="#ffbf57" strokeDasharray="6 4"/>}{seriesKeys.map((key, index) => <Line key={key} type="monotone" dataKey={key} name={overview.time_series.find((item) => item.variable_key === key)?.display_name ?? edgeLabel(key)} dot={false} stroke={String(widget.settings?.color ?? SERIES_COLORS[index % SERIES_COLORS.length])} strokeWidth={2}/>)}</LineChart></ResponsiveContainer> }
  if (type === 'note') return <div className="note-block"><MessageSquareText /><blockquote>{overview.notes[0]?.body ?? '등록된 의견이 없습니다.'}</blockquote><footer><span>{overview.notes[0]?.author ?? '-'}</span><small>ANALYSIS ENGINEER</small></footer></div>
  if (type === 'result_table') return <div className="result-table"><div className="table-head"><span>측정 위치</span><span>결과</span><span>허용 기준</span><span>여유율</span><span>판정</span></div>{configuredScalars.map((item) => { const location = resultLocation(item.variable_key); return <div className="table-row" key={item.id}><strong><i className={`edge-${item.variable_key.split('_')[0]}`} /><span>{item.display_name.replace(' 최대 응력','')}{location && <small>{location.entity_type} {location.entity_id} · ({location.x.toFixed(1)}, {location.y.toFixed(1)}, {location.z.toFixed(1)})</small>}</span></strong><span>{item.value_double.toFixed(1)} <small>{item.unit}</small></span><span>{item.threshold_double.toFixed(1)} <small>{item.unit}</small></span><span className={item.verdict === 'FAIL' ? 'negative' : 'positive'}>{((item.threshold_double - item.value_double) / item.threshold_double * 100).toFixed(1)}%</span><b className={item.verdict.toLowerCase()}>{item.verdict}</b></div> })}</div>
  if (type === 'contour') { const asset = overview.media.find((item) => item.asset_type === 'IMAGE') ?? overview.media[0]; return asset ? <div className="contour"><img src={asset.asset_url ?? `/assets/${asset.file_path}`} alt={asset.title} /></div> : <div className="empty-widget">등록된 컨투어 이미지가 없습니다.</div> }
  if (type === 'kpi' || type === 'gauge') {
    const bound = overview.scalar_results.find((item) => item.variable_key === widget.settings?.variableId) ?? openCellScalars[0]
    return bound ? <div className="verdict-card"><span>{bound.display_name}</span><strong>{bound.value_double.toFixed(1)} {bound.unit}</strong><small>기준 {bound.threshold_double.toFixed(1)} {bound.unit} · {bound.verdict}</small></div> : <div className="empty-widget">선택한 변수의 데이터가 없습니다.</div>
  }
  if (type === 'scatter') return <ResponsiveContainer width="100%" height="100%"><LineChart data={barData}><CartesianGrid stroke="#193447" /><XAxis dataKey="name" /><YAxis /><Tooltip /><Line dataKey="value" stroke="#61d4ff" /></LineChart></ResponsiveContainer>
  if (type === 'video') { const asset = overview.media.find((item) => item.asset_type === 'VIDEO'); return asset ? <video controls className="result-video" src={asset.asset_url ?? `/assets/${asset.file_path}`} /> : <div className="empty-widget">등록된 안전한 영상 파일이 없습니다.</div> }
  if (type === 'model3d') return <div className="empty-widget">GLB/glTF 경량 파일을 등록하면 여기에 표시됩니다.</div>
  return <div className="empty-widget">표시할 데이터가 없습니다.</div>
}

function OpenCellMap({ overview }: { overview: Overview }) {
  const productValue = (category: string) => overview.product_information.find((item) => item.category === category)?.value_text ?? '-'
  const edgeResult = (edge: string) => overview.scalar_results.find((item) => item.variable_key.startsWith(`${edge}_`))
  const edgeLabels = { top: '상', bottom: '하', left: '좌', right: '우' }
  const isClamp = overview.load_case.analysis_type === 'SIDE_CLAMP'
  const scene = isClamp ? `SIDE CLAMP · ${overview.load_case.parameters.pressure_mpa ?? '-'} MPa` : `${overview.load_case.parameters.direction ?? '-'} FACE · ${overview.load_case.parameters.drop_height_mm ?? '-'} mm`
  const threshold = overview.scalar_results.find((item) => !item.variable_key.startsWith('chassis_rear_'))?.threshold_double ?? 75

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
  const chartOptions: Array<[DashboardWidget['type'],string]> = [['kpi','KPI 카드'],['verdict','패스/실패 카드'],['gauge','임계값 게이지'],['edge_bar','막대그래프'],['time_series','시계열 그래프'],['scatter','산점도'],['result_table','데이터 테이블'],['open_cell_map','Open Cell 맵'],['chassis_summary','Chassis 판정 요약'],['chassis_diagram','Chassis 위치도'],['chassis_bar','Chassis 비교 그래프'],['chassis_table','Chassis 상세 표'],['contour','컨투어 이미지'],['note','수행자 의견']]
  const currentVariable = variables.find((item) => item.id === widget.settings?.variableId)
  const compatibleVariables = variables.filter((item) => item.allowed_widgets.includes(widget.type) || item.id === currentVariable?.id)
  const aggregations = currentVariable?.allowed_aggregations ?? ['MAX','MIN','AVG','LATEST','RAW']
  return <div className="drawer-backdrop" onMouseDown={onClose}><aside className="widget-settings-drawer" onMouseDown={(e)=>e.stopPropagation()}><header><div><span>WIDGET SETTINGS</span><h2>위젯 설정</h2></div><button onClick={onClose}><X/></button></header><label><span>제목</span><input value={widget.title} onChange={(e)=>onChange({title:e.target.value})}/></label><label><span>시각화 유형</span><select value={widget.type} onChange={(e)=>onChange({type:e.target.value as DashboardWidget['type']})}>{chartOptions.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label><label><span>데이터 변수</span><select value={String(widget.settings?.variableId ?? '')} onChange={(e)=>onChange({settings:{variableId:e.target.value||undefined}})}><option value="">전체/위젯 기본 변수</option>{compatibleVariables.map((item)=><option key={item.id} value={item.id}>{item.display_name} ({item.unit}){item.has_data?'':' · 데이터 대기'}</option>)}</select></label><label><span>집계 방식</span><select value={String(widget.settings?.aggregation ?? aggregations[0])} onChange={(e)=>onChange({settings:{aggregation:e.target.value}})}>{aggregations.map((item)=><option key={item}>{item}</option>)}</select></label><label><span>강조 색상</span><div className="color-setting"><input type="color" value={String(widget.settings?.color ?? '#50d5ff')} onChange={(e)=>onChange({settings:{color:e.target.value}})}/><code>{String(widget.settings?.color ?? '#50d5ff')}</code></div></label><label className="check-setting"><input type="checkbox" checked={widget.settings?.showThreshold !== false} onChange={(e)=>onChange({settings:{showThreshold:e.target.checked}})}/><span>기준선 표시</span></label><div className="settings-note"><Lock/><p>카탈로그에 선언되고 현재 그래프에 허용된 변수만 표시됩니다. 변수 키로 실제 결과 테이블과 연결됩니다.</p></div><button className="primary-button" onClick={onClose}><Check/> 설정 완료</button></aside></div>
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

function ChassisWidgetContent({ type, overview, threshold, onSaveThreshold, variableId }: { type: DashboardWidget['type']; overview: Overview; threshold?: QualityThreshold; onSaveThreshold: (value: number) => void; variableId: string }) {
  const allResults = overview.scalar_results.filter((item) => item.variable_key.startsWith('chassis_rear_'))
  const results = variableId ? allResults.filter((item)=>item.variable_key===variableId) : allResults
  const limit = threshold?.threshold_double ?? results[0]?.threshold_double ?? 5
  const [draftLimit, setDraftLimit] = useState(limit)
  useEffect(() => setDraftLimit(limit), [limit])
  const verdict = results.some((item) => item.verdict === 'FAIL') ? 'FAIL' : results.length ? 'PASS' : 'NO_DATA'
  const maximum = Math.max(...results.map((item) => item.value_double), 0)
  if (variableId && !results.length) return <div className="widget-empty"><Database/><strong>선언된 변수에 결과 데이터가 없습니다.</strong><small>{variableId} 키의 숫자 결과를 가져오면 자동 표시됩니다.</small></div>
  const markerClasses: Record<string, string> = { chassis_rear_top_edge_gap_permanent_deformation:'top-edge', chassis_rear_bottom_edge_gap_permanent_deformation:'bottom-edge', chassis_rear_corner_top_left_permanent_deformation:'top-left', chassis_rear_corner_top_right_permanent_deformation:'top-right', chassis_rear_corner_bottom_left_permanent_deformation:'bottom-left', chassis_rear_corner_bottom_right_permanent_deformation:'bottom-right' }
  const labels: Record<string,string> = { top_edge_gap:'상단 엣지', bottom_edge_gap:'하단 엣지', corner_top_left:'좌상단', corner_top_right:'우상단', corner_bottom_left:'좌하단', corner_bottom_right:'우하단' }
  const chartData = results.map((item) => ({ name: labels[item.variable_key.replace('chassis_rear_','').replace('_permanent_deformation','')] ?? item.display_name, value:item.value_double, verdict:item.verdict }))
  const location = (key:string) => overview.result_locations.find((item) => item.variable_key === key)
  if (!results.length) return <div className="empty-widget">Chassis Rear 결과가 없습니다.</div>
  if (type === 'chassis_summary') return <div className="chassis-widget-summary"><div><span>전체 판정</span><strong className={verdict.toLowerCase()}>{verdict}</strong></div><div><span>최대 영구변형</span><strong>{maximum.toFixed(2)} mm</strong></div><label><span>관리 기준</span><div><input aria-label="Chassis Rear 영구변형 기준값" type="number" min="0.1" step="0.1" value={draftLimit} onChange={(e)=>setDraftLimit(Number(e.target.value))}/><button onClick={()=>onSaveThreshold(draftLimit)}>저장</button></div></label><p>최종 프레임의 영구변형만 사용하며 기준 이상은 FAIL입니다.</p></div>
  if (type === 'chassis_diagram') return <div className="chassis-diagram-body compact"><div className="chassis-shell"><div className="chassis-ribs"/><div className="chassis-center"><i/><i/><i/><i/></div>{results.map((item)=><div key={item.id} className={`chassis-marker ${markerClasses[item.variable_key] ?? ''} ${item.verdict.toLowerCase()}`}><span>{item.value_double.toFixed(1)} mm</span><small>{chartData.find((entry)=>entry.value===item.value_double)?.name}</small></div>)}</div><div className="chassis-legend"><span><i className="pass"/>기준 미만</span><span><i className="fail"/>기준 이상</span></div></div>
  if (type === 'chassis_bar') return <ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} layout="vertical" margin={{top:8,right:24,left:8,bottom:4}}><CartesianGrid horizontal={false} stroke="#24384b"/><XAxis type="number" domain={[0,Math.max(8,limit+2)]}/><YAxis type="category" dataKey="name" width={60} tick={{fill:'#8fa6bb',fontSize:9}}/><Tooltip formatter={(value:number)=>[`${value} mm`,'영구변형']}/><ReferenceLine x={limit} stroke="#ffbf57" strokeDasharray="5 4"/><Bar dataKey="value">{chartData.map((entry)=><Cell key={entry.name} fill={entry.verdict==='FAIL'?'#ff5d73':'#4fd6a0'}/>)}</Bar></BarChart></ResponsiveContainer>
  if (type === 'chassis_table') return <div className="chassis-result-table"><div className="chassis-result-head"><span>측정 위치</span><span>영구변형</span><span>기준</span><span>판정</span></div>{results.map((item)=>{const point=location(item.variable_key); return <div className="chassis-result-row" key={item.id}><strong>{item.display_name}{point&&<small>NODE {point.entity_id} · ({point.x.toFixed(1)}, {point.y.toFixed(1)}, {point.z.toFixed(1)})</small>}</strong><span>{item.value_double.toFixed(1)} mm</span><span>{item.threshold_double.toFixed(1)} mm</span><b className={item.verdict.toLowerCase()}>{item.verdict}</b></div>})}</div>
  return <div className="empty-widget">지원하지 않는 Chassis 위젯입니다.</div>
}

function ChassisRearDashboard({ overview, threshold, onSaveThreshold }: { overview: Overview; threshold?: QualityThreshold; onSaveThreshold: (value: number) => void }) {
  const results = overview.scalar_results.filter((item) => item.variable_key.startsWith('chassis_rear_'))
  const limit = threshold?.threshold_double ?? results[0]?.threshold_double ?? 5
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
      <article className="chassis-card chassis-chart-card"><header><div><span className="widget-kicker">LOCATION COMPARISON</span><h3>위치별 영구변형</h3></div></header><div className="chassis-chart-body"><ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} layout="vertical" margin={{ top: 7, right: 22, left: 10, bottom: 4 }}><CartesianGrid horizontal={false} stroke="#24384b"/><XAxis type="number" domain={[0, Math.max(8, limit + 2)]} tick={{ fill: '#71899f', fontSize: 9 }} axisLine={false} tickLine={false}/><YAxis type="category" dataKey="name" width={58} tick={{ fill: '#8fa6bb', fontSize: 9 }} axisLine={false} tickLine={false}/><Tooltip contentStyle={{ background: '#102235', border: '1px solid #2d465c', borderRadius: 8 }} formatter={(value: number) => [`${value} mm`, '영구변형']}/><ReferenceLine x={limit} stroke="#ffbf57" strokeDasharray="5 4" label={{ value: `기준 ${limit}`, fill: '#ffbf57', fontSize: 9 }}/><Bar dataKey="value" radius={[0,4,4,0]}>{chartData.map((entry) => <Cell key={entry.name} fill={entry.verdict === 'FAIL' ? '#ff5d73' : '#4fd6a0'}/>)}</Bar></BarChart></ResponsiveContainer></div></article>
      <article className="chassis-card threshold-card"><header><div><span className="widget-kicker">ADMIN CRITERION</span><h3>관리자 판정 기준</h3></div></header><div className="threshold-body"><label><span>목표값</span><div><input aria-label="Chassis Rear 영구변형 기준값" type="number" min="0.1" step="0.1" value={draftLimit} onChange={(event) => setDraftLimit(Number(event.target.value))}/><b>mm</b></div></label><button onClick={() => onSaveThreshold(draftLimit)} disabled={!Number.isFinite(draftLimit) || draftLimit <= 0}>기준값 저장</button><p>상·하 엣지 이격 및 네 모서리 영구변형에 동일 기준을 적용합니다.</p></div></article>
      <article className="chassis-card chassis-result-card"><header><div><span className="widget-kicker">FAILURE JUDGEMENT 02</span><h3>Chassis Rear 상세 판정</h3></div></header><div className="chassis-result-table"><div className="chassis-result-head"><span>측정 위치</span><span>영구변형</span><span>기준</span><span>판정</span></div>{results.map((item) => { const location = resultLocation(item.variable_key); return <div className="chassis-result-row" key={item.id}><strong>{item.display_name}{location && <small>NODE {location.entity_id} · ({location.x.toFixed(1)}, {location.y.toFixed(1)}, {location.z.toFixed(1)})</small>}</strong><span>{item.value_double.toFixed(1)} mm</span><span>{item.threshold_double.toFixed(1)} mm</span><b className={item.verdict.toLowerCase()}>{item.verdict}</b></div> })}</div></article>
    </div>
  </div>
}

function WorkflowView({ workflows, editMode, onRename, onOpenAnalysis, activeRequestId }: { workflows: Workflow[]; editMode: boolean; onRename: (stepId: string, name: string) => void; onOpenAnalysis: (workflow: Workflow) => void; activeRequestId: string }) {
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

  return <div className="workflow-board">
    <section className="workflow-board-head"><div><span>CONCURRENT REQUEST BOARD</span><h2>의뢰 작업 진행 현황</h2><p>{workflows.length}개 의뢰 · {activeCount}개 동시 진행</p></div><label>정렬 기준<select value={sortKey} onChange={(event) => setSortKey(event.target.value as typeof sortKey)}><option value="project">프로젝트(제품)별</option><option value="category">의뢰별 카테고리</option><option value="product">제품 이름순</option><option value="owner">작업자 이름</option><option value="time">시간순</option></select></label></section>
    <div className="workflow-lanes">{ordered.map((workflow) => <section className={`workflow-lane ${workflow.request.id === activeRequestId ? 'active-request' : ''}`} key={workflow.request.id}>
      <header><div><span className="workflow-category">{workflow.request.category}</span><h3>{workflow.request.title}</h3><p>{workflow.request.project_name} · {workflow.request.product_name}</p></div><div className="workflow-lane-meta"><span>{workflow.request.owner}</span><small>{new Date(workflow.request.requested_at).toLocaleDateString('ko-KR')}</small><b>{workflow.progress}%</b><button onClick={() => onOpenAnalysis(workflow)}>상세 분석 열기</button></div></header>
      <div className="workflow-horizontal">{workflow.steps.map((step, index) => <WorkflowStepItem key={step.id} step={step} last={index === workflow.steps.length - 1} editMode={editMode} onRename={onRename} />)}</div>
    </section>)}</div>
  </div>
}

function WorkflowStepItem({ step, last, editMode, onRename }: { step: WorkflowStep; last: boolean; editMode: boolean; onRename: (stepId: string, name: string) => void }) {
  const statusText = { COMPLETED: '완료', IN_PROGRESS: '진행 중', WAITING: '대기', BLOCKED: '차단', FAILED: '실패' }[step.status]
  return <div className={`workflow-step-horizontal ${step.status.toLowerCase()}`}><div className="step-track-horizontal"><span>{step.status === 'COMPLETED' ? <Check /> : step.sequence_no}</span>{!last && <i />}</div><div className="step-copy-horizontal">{editMode ? <input defaultValue={step.name} onBlur={(event) => onRename(step.id, event.target.value)} aria-label={`${step.name} 단계 이름`} /> : <strong>{step.name}</strong>}<span>{step.owner}</span>{step.is_optional && <em>선택</em>}</div><div className="step-progress-horizontal"><i><b style={{ width: `${step.progress}%` }} /></i><span>{step.progress}%</span></div><b className="status-badge">{statusText}</b></div>
}

export default App
