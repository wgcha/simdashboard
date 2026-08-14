import { lazy, Suspense } from 'react'
import { Activity, BarChart3, CircleDot, GripVertical, LayoutDashboard, MessageSquareText, Minus, Plus, RotateCcw } from 'lucide-react'

import type { Layout, Layouts } from 'react-grid-layout'
import type { ActiveView } from '../../features/analysis/pageSelection'
import type { DashboardDefinition, DashboardPageSummary, Overview, Project, QualityThreshold, AnalysisRequest, LoadCase, Workflow, WorkflowDashboardLayout, WorkflowStep } from '../../types'
import type { RunComparisonReportContext } from '../../types'

const ResultsWorkspace = lazy(() => import('../../features/results/ResultsWorkspace').then(({ ResultsWorkspace: Component }) => ({ default: Component })))
const WorkflowView = lazy(() => import('../../features/requests/WorkflowView').then(({ WorkflowView: Component }) => ({ default: Component })))

type Props = {
  activeDashboardId: string
  activeView: ActiveView
  analysisPages: DashboardPageSummary[]
  canEdit: boolean
  canManagePages: boolean
  chassisThreshold?: QualityThreshold
  dashboard: DashboardDefinition
  editMode: boolean
  loadCases: LoadCase[]
  onAddStep: (requestId: string) => void
  onBeginEditing: () => void
  onCancelEditing: () => void
  onConfigureWidget: (id: string) => void
  onComparisonContext: (context: RunComparisonReportContext | null) => void
  onDashboardLayoutChange: (layout: WorkflowDashboardLayout) => void
  onDeleteStep: (requestId: string, stepId: string) => void
  onLayoutChange: (layout: Layout[], layouts: Layouts) => void
  onMoveStep: (requestId: string, stepId: string, offset: -1 | 1) => void
  onOpenAnalysis: (workflow: Workflow) => void
  onOpenAssistant: () => void
  onOpenPageManager: () => void
  onRemoveWidget: (id: string) => void
  onSaveChassisThreshold: (value: number) => void
  onSaveOpenCellThreshold: (value: number) => void
  onSelectLoadCase: (id: string) => void
  onSelectProject: (id: string) => void
  onSelectRequest: (id: string) => void
  onStepChange: (stepId: string, patch: Partial<WorkflowStep>) => void
  onSwitchAnalysisPage: (page: DashboardPageSummary) => void
  onSwitchView: (view: ActiveView) => void
  onUpdateWorkflowLayout: (layout: WorkflowDashboardLayout) => void
  openCellThreshold?: QualityThreshold
  overview: Overview
  projectWorkflows: Workflow[]
  projects: Project[]
  requests: AnalysisRequest[]
  resetWorkflowDashboardLayout: () => void
  selectedEdges: string[]
  selectedLoadCaseId: string
  selectedProjectId: string
  selectedRequestId: string
  selectedWorkflow?: Workflow
  setSelectedEdges: (edges: string[]) => void
  workflowDashboardLayout: WorkflowDashboardLayout
  workflowEditorMode: 'layout' | 'stages' | null
  workflowLayoutVersion: number
}

/** The route-level presentation adapter for monitoring and detailed analysis. */
export function RequestWorkspaceRoute({
  activeDashboardId, activeView, analysisPages, canEdit, canManagePages, chassisThreshold, dashboard,
  editMode, loadCases, onAddStep, onBeginEditing, onCancelEditing, onConfigureWidget,
  onComparisonContext, onDashboardLayoutChange, onDeleteStep, onLayoutChange, onMoveStep, onOpenAnalysis, onOpenAssistant,
  onOpenPageManager, onRemoveWidget, onSaveChassisThreshold, onSaveOpenCellThreshold,
  onSelectLoadCase, onSelectProject, onSelectRequest, onStepChange, onSwitchAnalysisPage,
  onSwitchView, onUpdateWorkflowLayout, openCellThreshold, overview, projectWorkflows, projects,
  requests, resetWorkflowDashboardLayout, selectedEdges, selectedLoadCaseId, selectedProjectId,
  selectedRequestId, selectedWorkflow, setSelectedEdges, workflowDashboardLayout, workflowEditorMode,
  workflowLayoutVersion,
}: Props) {
  const analysisTabs = analysisPages.filter((page) => page.page.is_system || page.page.status === 'published' || page.id === activeDashboardId)
  const activeAnalysisPage = analysisPages.find((page) => page.id === activeDashboardId)
  const workflowProgress = selectedWorkflow?.progress ?? 0
  const toggleEdge = (edge: string) => setSelectedEdges(selectedEdges.includes(edge) ? selectedEdges.filter((value) => value !== edge) : [...selectedEdges, edge])

  return <>
    <section className="content-head">
      <div><div className="eyebrow"><span>{activeView === 'workflow' ? 'REQUEST MONITORING' : 'PROJECT 24-071'}</span><span>•</span><span>{activeView === 'workflow' ? selectedWorkflow?.request.category ?? 'UNASSIGNED' : overview.load_case.analysis_type}</span></div><h1>{activeView === 'workflow' ? `${selectedWorkflow?.request.project_name ?? overview.load_case.project_name} 해석 의뢰 현황` : `${overview.load_case.product_name} 불량 분석`}</h1><p>{activeView === 'workflow' ? selectedWorkflow?.request.title ?? '의뢰를 선택하세요.' : overview.load_case.request_title}</p></div>
      <div className="context-selectors"><label><span>프로젝트</span><select aria-label="프로젝트 선택" value={selectedProjectId} onChange={(event) => void onSelectProject(event.target.value)}>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label><label><span>의뢰</span><select aria-label="의뢰 선택" value={selectedRequestId} onChange={(event) => void onSelectRequest(event.target.value)}>{requests.map((request) => <option key={request.id} value={request.id}>{request.title}</option>)}</select></label><label><span>하중 경우</span><select aria-label="하중 경우 선택" value={selectedLoadCaseId} disabled={loadCases.length === 0} onChange={(event) => void onSelectLoadCase(event.target.value)}>{loadCases.length === 0 ? <option value="">미지정</option> : loadCases.map((loadCase) => <option key={loadCase.id} value={loadCase.id}>{loadCase.name}</option>)}</select></label></div>
    </section>
    <section className="view-tabs"><button data-testid="selected-request-progress-tab" className={activeView === 'workflow' ? 'active' : ''} onClick={() => onSwitchView('workflow')}><CircleDot /> 의뢰 진행 상태 <span>{workflowProgress}%</span></button><button className={activeView !== 'workflow' ? 'active' : ''} disabled={!selectedLoadCaseId || analysisTabs.length === 0} onClick={() => { const page = analysisTabs[0]; if (page) onSwitchAnalysisPage(page) }}><LayoutDashboard /> 상세 분석 <span>{selectedLoadCaseId ? overview.load_case.analysis_type.replace('_', ' ') : '하중 경우 미지정'}</span></button><div className="tab-line" /></section>
    {activeView !== 'workflow' && <section className="analysis-subtabs"><div><span>상세 분석</span><b>/</b><strong>{overview.load_case.request_title}</strong></div><nav aria-label="불량 분석 하위 탭">{analysisTabs.map((page) => <div className="analysis-tab-group" key={page.id}><button className={`analysis-tab ${activeDashboardId === page.id ? 'active' : ''}`} onClick={() => onSwitchAnalysisPage(page)}>{page.page.analysis_key === 'open_cell' ? <Activity /> : page.page.analysis_key === 'chassis_rear' ? <BarChart3 /> : page.page.analysis_key === 'run_comparison' ? <MessageSquareText /> : <LayoutDashboard />} {page.name} <span>{page.page.analysis_key === 'open_cell' ? overview.analysis_verdicts.open_cell : page.page.analysis_key === 'chassis_rear' ? overview.analysis_verdicts.chassis_rear : page.page.analysis_key === 'run_comparison' ? 'SYSTEM' : page.page.status.toUpperCase()}</span></button></div>)}<label className="analysis-page-jump"><span>페이지</span><select aria-label="상세 분석 페이지 선택" value={activeAnalysisPage?.id ?? analysisTabs[0]?.id ?? ''} onChange={(event) => { const page = analysisTabs.find((item) => item.id === event.target.value); if (page) onSwitchAnalysisPage(page) }}>{analysisTabs.map((page) => <option key={page.id} value={page.id}>{page.name}</option>)}</select></label>{canManagePages && <button className="analysis-page-manage-button" onClick={onOpenPageManager}><Plus /> 분석 페이지 관리</button>}</nav>{activeAnalysisPage?.page.analysis_key === 'open_cell' && activeView !== 'compare' && <div className="edge-filter"><span>표시 엣지</span>{[['top', '상단'], ['bottom', '하단'], ['left', '좌측'], ['right', '우측']].map(([edge, label]) => <button className={selectedEdges.includes(edge) ? 'on' : ''} key={edge} onClick={() => toggleEdge(edge)}><i />{label}</button>)}</div>}</section>}
    {editMode && <div className="edit-banner" data-testid={workflowEditorMode ?? undefined}><GripVertical /><span><strong>{activeView === 'workflow' && workflowEditorMode === 'layout' ? '대시보드 레이아웃 편집' : activeView === 'workflow' ? '진행 단계 편집' : '편집 모드'}</strong> {activeView === 'workflow' && workflowEditorMode === 'layout' ? '의뢰 위젯을 이동·리사이즈하고 색상과 글자 크기를 조절한 뒤 상단에서 저장하세요.' : activeView === 'workflow' ? '단계 내용·순서·추가·삭제를 편집한 뒤 상단의 단계 변경 저장을 누르세요.' : '위젯을 드래그하거나 모서리를 잡아 크기를 조절하고 설정 버튼으로 그래프, 변수, 글자 크기를 바꾸세요.'}</span><button onClick={onCancelEditing}>편집 취소</button></div>}
    {editMode && activeView === 'workflow' && workflowEditorMode === 'layout' && <div className="workflow-layout-toolbar"><label><span>강조 색상</span><input aria-label="진행 현황 강조 색상" type="color" value={workflowDashboardLayout.accentColor} onChange={(event) => onUpdateWorkflowLayout({ ...workflowDashboardLayout, accentColor: event.target.value })}/><code>{workflowDashboardLayout.accentColor}</code></label><div><span>글자 크기</span><button aria-label="진행 현황 글자 크기 줄이기" onClick={() => onUpdateWorkflowLayout({ ...workflowDashboardLayout, fontSize: Math.max(8, workflowDashboardLayout.fontSize - 1) })} disabled={workflowDashboardLayout.fontSize <= 8}><Minus /></button><output>{workflowDashboardLayout.fontSize}px</output><button aria-label="진행 현황 글자 크기 늘리기" onClick={() => onUpdateWorkflowLayout({ ...workflowDashboardLayout, fontSize: Math.min(18, workflowDashboardLayout.fontSize + 1) })} disabled={workflowDashboardLayout.fontSize >= 18}><Plus /></button></div><button onClick={resetWorkflowDashboardLayout}><RotateCcw /> 기본 레이아웃</button></div>}
    {editMode && activeView !== 'workflow' && <div className="dashboard-edit-toolbar"><div><strong>{dashboard.name}</strong><span>v{dashboard.version ?? 1} · 카드 상단의 손잡이로 이동하고 오른쪽 아래 모서리로 크기를 조절합니다.</span></div><button onClick={onOpenAssistant}><Plus /> 위젯 추가·버전 관리</button></div>}
    {activeView !== 'workflow' ? <Suspense fallback={null}><ResultsWorkspace activeView={activeView} canEdit={canEdit} canManageThresholds={canManagePages} chassisThreshold={chassisThreshold} dashboard={dashboard} editMode={editMode} loadCaseId={selectedLoadCaseId} onBeginEditing={onBeginEditing} onComparisonContext={onComparisonContext} onConfigureWidget={onConfigureWidget} onLayoutChange={onLayoutChange} onOpenAssistant={onOpenAssistant} onRemoveWidget={onRemoveWidget} onSaveChassisThreshold={onSaveChassisThreshold} onSaveOpenCellThreshold={onSaveOpenCellThreshold} openCellThreshold={openCellThreshold} overview={overview} selectedEdges={selectedEdges} /></Suspense> : <Suspense fallback={null}><WorkflowView workflows={projectWorkflows} stageEditMode={editMode && workflowEditorMode === 'stages'} layoutEditMode={editMode && workflowEditorMode === 'layout'} dashboardLayout={workflowDashboardLayout} layoutVersion={workflowLayoutVersion} onDashboardLayoutChange={onDashboardLayoutChange} onStepChange={onStepChange} onAddStep={onAddStep} onDeleteStep={onDeleteStep} onMoveStep={onMoveStep} onOpenAnalysis={onOpenAnalysis} activeRequestId={selectedRequestId} /></Suspense>}
  </>
}
