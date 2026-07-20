import { useEffect, useMemo, useState, type ComponentType, type FormEvent } from 'react'
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Check,
  ChevronDown,
  CircleDot,
  Database,
  GripVertical,
  LayoutDashboard,
  LoaderCircle,
  Lock,
  MessageSquareText,
  PanelLeftClose,
  Play,
  Plus,
  Save,
  Settings2,
  Sparkles,
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
import type { AnalysisRequest, DashboardDefinition, DashboardWidget, LoadCase, Overview, Project, QualityThreshold, Workflow, WorkflowStep, AnalysisRun } from './types'

const ResponsiveGridLayout = WidthProvider(Responsive) as unknown as ComponentType<any>
const SERIES_COLORS = ['#61d4ff', '#ff647d', '#70e0a8', '#ffbf57']
type ActiveView = 'open_cell' | 'chassis' | 'workflow' | 'import'

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
  const [workspacePage, setWorkspacePage] = useState<'dashboard' | 'data'>('dashboard')
  const [editMode, setEditMode] = useState(false)
  const [assistantOpen, setAssistantOpen] = useState(false)
  const [command, setCommand] = useState('')
  const [proposal, setProposal] = useState<Awaited<ReturnType<typeof api.previewCommand>> | null>(null)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [selectedEdges, setSelectedEdges] = useState<string[]>(['top', 'bottom', 'left', 'right'])

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
    if (!dashboard || !editMode) return
    const positions = new Map((layouts.lg ?? []).map((item) => [item.i, item]))
    setDashboard({
      ...dashboard,
      widgets: dashboard.widgets.map((widget) => {
        const position = positions.get(widget.id)
        return position
          ? { ...widget, x: position.x, y: position.y, w: position.w, h: position.h }
          : widget
      }),
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
    const widget = proposal?.proposal?.widget
    if (!dashboard || !widget) return
    setDashboard({ ...dashboard, widgets: [...dashboard.widgets, widget] })
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
          <button className={workspacePage === 'dashboard' ? 'active' : ''} onClick={() => setWorkspacePage('dashboard')}><LayoutDashboard /><span>대시보드</span></button>
          <button className={workspacePage === 'data' ? 'active' : ''} onClick={() => setWorkspacePage('data')}><Database /><span>해석 데이터</span></button>
          <button><BarChart3 /><span>변수 카탈로그</span></button>
          <button><Settings2 /><span>자동화 템플릿</span></button>
        </nav>
        <div className="sidebar-foot">
          <div className="system-pill"><span className="live-dot" /> DUCKDB · LOCAL</div>
          <button><PanelLeftClose /> 메뉴 접기</button>
        </div>
      </aside>

      <main className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">{workspacePage === 'data' ? <><span>운영</span><b>/</b><strong>해석 데이터 등록</strong></> : <><span>프로젝트</span><b>/</b><span>{overview.load_case.project_name}</span><b>/</b><strong>{overview.load_case.name}</strong></>}</div>
          <div className="top-actions">
            {workspacePage === 'dashboard' && <button className="ghost-button" onClick={() => setAssistantOpen(true)}><Sparkles /> 자연어로 개선</button>}
            {workspacePage === 'dashboard' && (editMode ? (
              <button className="primary-button" onClick={save}><Save /> {activeView === 'workflow' ? '편집 완료' : '레이아웃 저장'}</button>
            ) : (
              activeView !== 'chassis' && <button className="edit-button" onClick={() => setEditMode(true)}><Settings2 /> 대시보드 편집</button>
            ))}
            <div className="avatar">HK</div>
          </div>
        </header>

        {workspacePage === 'data' ? (
          <DataWorkspace projects={projects} initialProjectId={selectedProjectId} onDataChanged={refreshOperationalData} />
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
          <button className={activeView !== 'workflow' && activeView !== 'import' ? 'active' : ''} onClick={() => setActiveView(openCellAvailable ? 'open_cell' : 'chassis')}><LayoutDashboard /> 상세 분석 <span>{overview.load_case.analysis_type.replace('_', ' ')}</span></button>
          <button className={activeView === 'import' ? 'active' : ''} onClick={() => setActiveView('import')}><Database /> 해석 결과 수집</button>
          <div className="tab-line" />
        </section>

        {activeView !== 'workflow' && activeView !== 'import' && <section className="analysis-subtabs"><div><span>상세 분석</span><b>/</b><strong>{overview.load_case.request_title}</strong></div><nav aria-label="불량 분석 하위 탭">{openCellAvailable && <button className={activeView === 'open_cell' ? 'active' : ''} onClick={() => setActiveView('open_cell')}><Activity /> 오픈셀 파손 분석 <span>{overview.analysis_verdicts.open_cell}</span></button>}{chassisAvailable && <button className={activeView === 'chassis' ? 'active' : ''} onClick={() => setActiveView('chassis')}><BarChart3 /> Chassis Rear 영구변형 평가 <span>{overview.analysis_verdicts.chassis_rear}</span></button>}</nav>{activeView === 'open_cell' && <EdgeFilter selected={selectedEdges} setSelected={setSelectedEdges} />}</section>}

        {editMode && (
          <div className="edit-banner"><GripVertical /><span><strong>편집 모드</strong> {activeView === 'open_cell' ? '위젯을 드래그하거나 모서리를 잡아 크기를 조절하세요.' : '단계 이름 입력란을 수정하면 즉시 저장됩니다.'}</span><button onClick={() => setEditMode(false)}>편집 취소</button></div>
        )}

        <section className="canvas-area">
          {activeView === 'open_cell' ? (
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
                  <WidgetCard widget={widget} overview={overview} selectedEdges={selectedEdges} editMode={editMode} onRemove={() => removeWidget(widget.id)} />
                </div>
              ))}
            </ResponsiveGridLayout>
          ) : activeView === 'import' ? (
            <ResultImportSection loadCaseId={overview.load_case.id} />
          ) : activeView === 'chassis' ? (
            <ChassisRearDashboard overview={overview} threshold={chassisThreshold} onSaveThreshold={saveChassisThreshold} />
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
            <textarea value={command} onChange={(event) => setCommand(event.target.value)} placeholder="예: 응력-시간 그래프에 기준선을 넣어줘" />
            <button className="assistant-submit" onClick={previewCommand} disabled={!command.trim()}><Sparkles /> 변경안 만들기</button>
            {proposal && (
              <div className={`proposal ${proposal.recognized ? 'recognized' : ''}`}>
                <span>{proposal.recognized ? <Check /> : <AlertTriangle />}</span>
                <div><strong>{proposal.recognized ? '적용 전 미리보기' : '요청 확인 필요'}</strong><p>{proposal.message}</p>{proposal.proposal && <code>{proposal.proposal.widget.title} · {proposal.proposal.widget.type}</code>}</div>
                {proposal.recognized && <button onClick={applyProposal}>변경안 적용</button>}
              </div>
            )}
            <div className="assistant-safe"><Lock /><span><strong>안전한 변경</strong>자연어 명령은 SQL이나 코드를 직접 실행하지 않습니다.</span></div>
          </aside>
        </div>
      )}
      {notice && <div className="toast"><Check /> {notice}</div>}
    </div>
  )
}

function DataWorkspace({ projects, initialProjectId, onDataChanged }: { projects: Project[]; initialProjectId: string; onDataChanged: () => Promise<void> }) {
  const [managedProjects, setManagedProjects] = useState(projects)
  const [projectId, setProjectId] = useState(initialProjectId || projects[0]?.id || '')
  const [requests, setRequests] = useState<AnalysisRequest[]>([])
  const [requestId, setRequestId] = useState('')
  const [loadCases, setLoadCases] = useState<LoadCase[]>([])
  const [message, setMessage] = useState('')
  const [formError, setFormError] = useState('')
  const [busy, setBusy] = useState(false)
  const [projectForm, setProjectForm] = useState({ name: '', product_name: '', description: '' })
  const [requestForm, setRequestForm] = useState({ title: '', owner: '', due_in_days: 14, overall_note: '' })
  const [caseForm, setCaseForm] = useState({ name: '', analysis_type: 'DROP' as 'DROP' | 'SIDE_CLAMP', primary: '800', secondary: 'BOTTOM' })

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
    api.loadCases(requestId).then(setLoadCases).catch((reason) => setFormError(reason instanceof Error ? reason.message : '하중 경우 목록을 불러오지 못했습니다.'))
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
      setProjectForm({ name: '', product_name: '', description: '' })
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
      setCaseForm({ name: '', analysis_type: caseForm.analysis_type, primary: caseForm.analysis_type === 'DROP' ? '800' : '25', secondary: caseForm.analysis_type === 'DROP' ? 'BOTTOM' : '10' })
    })
  }

  const selectedProject = managedProjects.find((item) => item.id === projectId)
  const selectedRequest = requests.find((item) => item.id === requestId)

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
      <label><span>3 · 하중 경우</span><select aria-label="등록 하중 경우 선택" disabled={!loadCases.length}>{loadCases.length ? loadCases.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.analysis_type}</option>) : <option>하중 경우 없음</option>}</select></label>
    </div>

    {(message || formError) && <div className={`data-message ${formError ? 'error' : ''}`}>{formError ? <AlertTriangle /> : <Check />}{formError || message}</div>}

    <div className="data-form-grid">
      <article className="data-form-card"><header><span>01</span><div><h2>새 프로젝트</h2><p>제품 단위 최상위 분류</p></div></header><form onSubmit={submitProject}>
        <label><span>프로젝트 이름</span><input required value={projectForm.name} onChange={(e) => setProjectForm({ ...projectForm, name: e.target.value })} placeholder="예: 2026 OLED 신뢰성" /></label>
        <label><span>제품 모델명</span><input required value={projectForm.product_name} onChange={(e) => setProjectForm({ ...projectForm, product_name: e.target.value })} placeholder="예: OLED77X26" /></label>
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
  </section>
}

function EdgeFilter({ selected, setSelected }: { selected: string[]; setSelected: (value: string[]) => void }) {
  const options = [['top', '상단'], ['bottom', '하단'], ['left', '좌측'], ['right', '우측']]
  const toggle = (key: string) => setSelected(selected.includes(key) ? selected.filter((item) => item !== key) : [...selected, key])
  return <div className="edge-filter"><span>표시 엣지</span>{options.map(([key, label]) => <button className={selected.includes(key) ? 'on' : ''} key={key} onClick={() => toggle(key)}><i />{label}</button>)}</div>
}

function WidgetCard({ widget, overview, selectedEdges, editMode, onRemove }: { widget: DashboardWidget; overview: Overview; selectedEdges: string[]; editMode: boolean; onRemove: () => void }) {
  return (
    <article className={`widget-card widget-${widget.type} ${editMode ? 'editable' : ''}`}>
      <header><div><span className="widget-kicker">{widget.type.replace('_', ' ')}</span><h3>{widget.title}</h3></div>{editMode ? <button className="remove-widget" onClick={onRemove}><X /></button> : <button className="widget-menu">•••</button>}</header>
      <div className="widget-body"><WidgetContent type={widget.type} overview={overview} selectedEdges={selectedEdges} /></div>
    </article>
  )
}

function WidgetContent({ type, overview, selectedEdges }: { type: DashboardWidget['type']; overview: Overview; selectedEdges: string[] }) {
  const edgeOrder = ['top', 'bottom', 'left', 'right']
  const openCellScalars = overview.scalar_results.filter((item) => !item.variable_key.startsWith('chassis_rear_'))
  const filteredScalars = openCellScalars
    .filter((item) => selectedEdges.some((edge) => item.variable_key.startsWith(edge)))
    .sort((a, b) => edgeOrder.indexOf(a.variable_key.split('_')[0]) - edgeOrder.indexOf(b.variable_key.split('_')[0]))
  const edgeLabel = (key: string) => ({ top: '상단', bottom: '하단', left: '좌측', right: '우측' }[key.split('_')[0]] ?? key)
  const barData = filteredScalars.map((item) => ({ name: edgeLabel(item.variable_key), value: item.value_double, verdict: item.verdict }))
  const openCellThreshold = openCellScalars[0]?.threshold_double ?? 75
  const openCellVerdict = openCellScalars.some((item) => item.verdict === 'FAIL') ? 'FAIL' : openCellScalars.length ? 'PASS' : 'NO_DATA'
  const seriesData = useMemo(() => {
    const grouped = new Map<number, Record<string, number>>()
    overview.time_series.filter((item) => selectedEdges.some((edge) => item.variable_key.startsWith(edge))).forEach((item) => {
      const point = grouped.get(item.time_value) ?? { time: item.time_value }
      point[item.variable_key] = item.value
      grouped.set(item.time_value, point)
    })
    return [...grouped.values()]
  }, [overview.time_series, selectedEdges])

  if (type === 'open_cell_map') return <OpenCellMap overview={overview} />
  if (type === 'verdict') return <div className={`verdict-block ${openCellVerdict.toLowerCase()}`}><div className="verdict-icon">{openCellVerdict === 'PASS' ? <Check /> : <X />}</div><div><strong>{openCellVerdict}</strong><span>{openCellVerdict === 'PASS' ? '허용 기준 만족' : '기준 초과 감지'}</span></div><small>LIMIT {openCellThreshold} MPa</small></div>
  if (type === 'summary') return overview.load_case.analysis_type === 'SIDE_CLAMP' ? <div className="summary-grid"><div><span>클램프 압력</span><strong>{overview.load_case.parameters.pressure_mpa ?? overview.load_case.parameters.clamp_pressure_kpa ?? '-'}<em>MPa</em></strong></div><div><span>유지 시간</span><strong>{overview.load_case.parameters.hold_time_sec ?? overview.load_case.parameters.hold_time_s ?? '-'}<em>s</em></strong></div><div><span>클램프 면</span><strong>{Array.isArray(overview.load_case.parameters.faces) ? overview.load_case.parameters.faces.join(' / ') : 'LEFT / RIGHT'}</strong></div><div><span>요소 수</span><strong>{overview.template_execution?.generated_model.elements.toLocaleString() ?? '-'}</strong></div></div> : <div className="summary-grid"><div><span>낙하 높이</span><strong>{overview.load_case.parameters.drop_height_mm}<em>mm</em></strong></div><div><span>낙하 방향</span><strong>{overview.load_case.parameters.direction ?? overview.load_case.parameters.impact_direction ?? '-'}</strong></div><div><span>자동화 템플릿</span><strong>{overview.template_execution?.template_version ?? '-'}</strong></div><div><span>요소 수</span><strong>{overview.template_execution?.generated_model.elements.toLocaleString() ?? '-'}</strong></div></div>
  if (type === 'edge_bar') return <ResponsiveContainer width="100%" height="100%"><BarChart data={barData} margin={{ top: 12, right: 18, left: -12, bottom: 0 }}><CartesianGrid vertical={false} stroke="#26394c" strokeDasharray="3 3"/><XAxis dataKey="name" tick={{ fill: '#8fa6bb', fontSize: 12 }} axisLine={false} tickLine={false}/><YAxis domain={[0, 100]} tick={{ fill: '#6f879d', fontSize: 11 }} axisLine={false} tickLine={false} unit=""/><Tooltip contentStyle={{ background: '#102235', border: '1px solid #2d465c', borderRadius: 10 }} formatter={(value: number) => [`${value} MPa`, '최대 응력']}/><ReferenceLine y={openCellThreshold} stroke="#ffbf57" strokeDasharray="5 5" label={{ value: `기준 ${openCellThreshold}`, fill: '#ffbf57', fontSize: 11, position: 'insideTopRight' }}/><Bar dataKey="value" radius={[5,5,1,1]}>{barData.map((entry) => <Cell key={entry.name} fill={entry.verdict === 'FAIL' ? '#ff5d73' : '#4fd6a0'} />)}</Bar></BarChart></ResponsiveContainer>
  if (type === 'time_series') return <ResponsiveContainer width="100%" height="100%"><LineChart data={seriesData} margin={{ top: 10, right: 22, left: -8, bottom: 2 }}><CartesianGrid stroke="#24384b" strokeDasharray="3 3"/><XAxis dataKey="time" tick={{ fill: '#71899f', fontSize: 11 }} axisLine={{ stroke: '#31485b' }} tickLine={false} label={{ value: `TIME (${overview.time_series[0]?.time_unit ?? 'ms'})`, fill: '#6f879d', fontSize: 10, position: 'insideBottomRight', offset: -2 }}/><YAxis domain={[0, 95]} tick={{ fill: '#71899f', fontSize: 11 }} axisLine={false} tickLine={false}/><Tooltip contentStyle={{ background: '#102235', border: '1px solid #2d465c', borderRadius: 10 }}/><Legend wrapperStyle={{ fontSize: 11, paddingTop: 5 }}/><ReferenceLine y={openCellThreshold} stroke="#ffbf57" strokeDasharray="6 4"/>{selectedEdges.map((edge, index) => <Line key={edge} type="monotone" dataKey={`${edge}_edge_stress_time`} name={`${edgeLabel(edge)} 엣지`} dot={false} stroke={SERIES_COLORS[index]} strokeWidth={2}/>)}</LineChart></ResponsiveContainer>
  if (type === 'note') return <div className="note-block"><MessageSquareText /><blockquote>{overview.notes[0]?.body ?? '등록된 의견이 없습니다.'}</blockquote><footer><span>{overview.notes[0]?.author ?? '-'}</span><small>ANALYSIS ENGINEER</small></footer></div>
  if (type === 'result_table') return <div className="result-table"><div className="table-head"><span>측정 위치</span><span>최대 응력</span><span>허용 기준</span><span>여유율</span><span>판정</span></div>{filteredScalars.map((item) => <div className="table-row" key={item.id}><strong><i className={`edge-${item.variable_key.split('_')[0]}`} />{item.display_name.replace(' 최대 응력','')}</strong><span>{item.value_double.toFixed(1)} <small>{item.unit}</small></span><span>{item.threshold_double.toFixed(1)} <small>{item.unit}</small></span><span className={item.verdict === 'FAIL' ? 'negative' : 'positive'}>{((item.threshold_double - item.value_double) / item.threshold_double * 100).toFixed(1)}%</span><b className={item.verdict.toLowerCase()}>{item.verdict}</b></div>)}</div>
  if (type === 'contour') return <div className="contour"><img src="/assets/sample-contour.svg" alt="Open Cell 응력 컨투어" /></div>
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

  return <div className="chassis-dashboard">
    <section className="chassis-summary-strip"><div><span>CHASSIS REAR RESULT</span><strong className={verdict.toLowerCase()}>{verdict}</strong></div><div><span>최대 영구변형</span><strong>{maximum.toFixed(1)} <small>mm</small></strong></div><div><span>관리 기준값</span><strong>{limit.toFixed(1)} <small>mm</small></strong></div><p>해석 결과는 응력이 아닌 영구변형만 판정에 사용합니다. 측정값이 기준 이상이면 FAIL입니다.</p></section>
    <div className="chassis-dashboard-grid">
      <article className="chassis-card chassis-diagram-card"><header><div><span className="widget-kicker">PERMANENT DEFORMATION MAP</span><h3>Chassis Rear 변형 위치</h3></div><span className="diagram-scene">{overview.load_case.analysis_type.replace('_', ' ')}</span></header><div className="chassis-diagram-body"><div className="chassis-shell"><div className="chassis-ribs"/><div className="chassis-center"><i/><i/><i/><i/></div>{results.map((item) => <div key={item.id} className={`chassis-marker ${markerClasses[item.variable_key] ?? ''} ${item.verdict.toLowerCase()}`}><span>{item.value_double.toFixed(1)} mm</span><small>{chartData.find((entry) => entry.value === item.value_double)?.name}</small></div>)}</div><div className="chassis-legend"><span><i className="pass"/>기준 미만</span><span><i className="fail"/>기준 이상</span><b>Open Cell 기준면 대비 거리 / 모서리 영구변형</b></div></div></article>
      <article className="chassis-card chassis-chart-card"><header><div><span className="widget-kicker">LOCATION COMPARISON</span><h3>위치별 영구변형</h3></div></header><div className="chassis-chart-body"><ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} layout="vertical" margin={{ top: 7, right: 22, left: 10, bottom: 4 }}><CartesianGrid horizontal={false} stroke="#24384b"/><XAxis type="number" domain={[0, Math.max(8, limit + 2)]} tick={{ fill: '#71899f', fontSize: 9 }} axisLine={false} tickLine={false}/><YAxis type="category" dataKey="name" width={58} tick={{ fill: '#8fa6bb', fontSize: 9 }} axisLine={false} tickLine={false}/><Tooltip contentStyle={{ background: '#102235', border: '1px solid #2d465c', borderRadius: 8 }} formatter={(value: number) => [`${value} mm`, '영구변형']}/><ReferenceLine x={limit} stroke="#ffbf57" strokeDasharray="5 4" label={{ value: `기준 ${limit}`, fill: '#ffbf57', fontSize: 9 }}/><Bar dataKey="value" radius={[0,4,4,0]}>{chartData.map((entry) => <Cell key={entry.name} fill={entry.verdict === 'FAIL' ? '#ff5d73' : '#4fd6a0'}/>)}</Bar></BarChart></ResponsiveContainer></div></article>
      <article className="chassis-card threshold-card"><header><div><span className="widget-kicker">ADMIN CRITERION</span><h3>관리자 판정 기준</h3></div></header><div className="threshold-body"><label><span>목표값</span><div><input aria-label="Chassis Rear 영구변형 기준값" type="number" min="0.1" step="0.1" value={draftLimit} onChange={(event) => setDraftLimit(Number(event.target.value))}/><b>mm</b></div></label><button onClick={() => onSaveThreshold(draftLimit)} disabled={!Number.isFinite(draftLimit) || draftLimit <= 0}>기준값 저장</button><p>상·하 엣지 이격 및 네 모서리 영구변형에 동일 기준을 적용합니다.</p></div></article>
      <article className="chassis-card chassis-result-card"><header><div><span className="widget-kicker">FAILURE JUDGEMENT 02</span><h3>Chassis Rear 상세 판정</h3></div></header><div className="chassis-result-table"><div className="chassis-result-head"><span>측정 위치</span><span>영구변형</span><span>기준</span><span>판정</span></div>{results.map((item) => <div className="chassis-result-row" key={item.id}><strong>{item.display_name}</strong><span>{item.value_double.toFixed(1)} mm</span><span>{item.threshold_double.toFixed(1)} mm</span><b className={item.verdict.toLowerCase()}>{item.verdict}</b></div>)}</div></article>
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

function ResultImportSection({ loadCaseId }: { loadCaseId: string }) {
  const [runs, setRuns] = useState<AnalysisRun[]>([])
  const [manifests, setManifests] = useState<string[]>([])
  const [loading, setLoading] = useState(false)

  const loadData = async () => {
    const data = await api.loadCaseRuns(loadCaseId)
    setRuns(data)
  }

  useEffect(() => {
    loadData()
  }, [loadCaseId])

  const scan = async () => {
    setLoading(true)
    try {
      const res = await api.scanImports()
      setManifests(res.manifests)
    } finally {
      setLoading(false)
    }
  }

  const importData = async (manifestPath: string) => {
    setLoading(true)
    try {
      await api.importResult(manifestPath)
      await loadData()
    } finally {
      setLoading(false)
    }
  }

  return <div className="result-import-section" style={{ padding: '20px' }}>
    <h2>해석 결과 수집</h2>
    <div style={{ display: 'flex', gap: '10px', marginBottom: '20px' }}>
      <button onClick={scan} disabled={loading} className="primary-button">결과 폴더 스캔</button>
      <button onClick={loadData} disabled={loading} className="ghost-button">결과 다시 불러오기</button>
    </div>
    
    {manifests.length > 0 && <div style={{ marginBottom: '20px' }}>
      <h3>발견된 Manifests</h3>
      <ul>
        {manifests.map(m => (
          <li key={m}>
            {m} <button onClick={() => importData(m)} disabled={loading}>수집</button>
          </li>
        ))}
      </ul>
    </div>}

    <h3>실행 이력</h3>
    {runs.length === 0 ? <p>등록된 실행(Run)이 없습니다.</p> : 
      <table style={{ width: '100%', textAlign: 'left', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th>Run 번호</th>
            <th>Solver</th>
            <th>수집 상태</th>
            <th>판정</th>
            <th>최종 수집일</th>
          </tr>
        </thead>
        <tbody>
          {runs.map(run => (
            <tr key={run.id} style={{ borderBottom: '1px solid #333' }}>
              <td>{run.run_no}</td>
              <td>{run.solver}</td>
              <td>{run.result_import_status || '미수집'}</td>
              <td>{run.overall_verdict || '-'}</td>
              <td>{run.last_imported_at ? new Date(run.last_imported_at).toLocaleString() : '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    }
  </div>
}

export default App
