import { useMemo, useState, type CSSProperties } from 'react'
import { ArrowLeft, ArrowRight, Check, GripVertical, Plus, Trash2 } from 'lucide-react'
import { Responsive, WidthProvider, type Layout } from 'react-grid-layout'
import type { Workflow, WorkflowDashboardLayout, WorkflowStep } from '../../types'
import { RequestDemoRunSummary } from './RequestDemoRunSummary'; import { WorkflowPendingState } from './WorkflowPendingState'
const ResponsiveGridLayout = WidthProvider(Responsive) as unknown as React.ComponentType<any>
export function WorkflowView({ workflows, stageEditMode, layoutEditMode, dashboardLayout, layoutVersion, onDashboardLayoutChange, onStepChange, onAddStep, onDeleteStep, onMoveStep, onOpenAnalysis, activeRequestId, loadCaseReady = true, onOpenData = () => {} }: {
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
  loadCaseReady?: boolean
  onOpenData?: () => void
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
  const showPendingState = !loadCaseReady || ordered.length === 0
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
    {showPendingState && <WorkflowPendingState hasWorkflows={ordered.length > 0} onOpenData={onOpenData} />}
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
      <label><span>담당자</span><input value={step.owner} readOnly aria-label={`${step.sequence_no}단계 담당자`} /><small>담당자 변경은 임직원 계정 기반 재배정 기능에서 수행합니다.</small></label>
      <div>
        <label><span>상태</span><select value={step.status} onChange={(event) => onChange({ status: event.target.value as WorkflowStep['status'] })} aria-label={`${step.sequence_no}단계 상태`}><option value="READY">시작 대기</option><option value="WAITING">대기</option><option value="IN_PROGRESS">진행 중</option><option value="BLOCKED">차단</option><option value="FAILED">실패</option><option value="COMPLETED">완료</option></select></label>
        <label><span>진행률</span><input type="number" min="0" max="100" value={step.progress} onChange={(event) => onChange({ progress: Math.max(0, Math.min(100, Number(event.target.value))) })} aria-label={`${step.sequence_no}단계 진행률`} /></label>
      </div>
      <label className="workflow-optional"><input type="checkbox" checked={step.is_optional} onChange={(event) => onChange({ is_optional: event.target.checked })} /><span>선택 단계</span></label>
      <label><span>메모</span><textarea value={step.note ?? ''} onChange={(event) => onChange({ note: event.target.value })} aria-label={`${step.sequence_no}단계 메모`} /></label>
    </div> : <><div className="step-copy-horizontal"><strong title={step.name}>{step.name}</strong><span>{step.owner}</span>{step.is_optional && <em>선택</em>}</div><div className="step-progress-horizontal"><i><b style={{ width: `${step.progress}%` }} /></i><span>{step.progress}%</span></div><b className="status-badge">{statusText}</b></>}
  </div>
}
