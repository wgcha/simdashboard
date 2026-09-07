import { useMemo, useState, type CSSProperties } from 'react'
import { GripVertical, Plus } from 'lucide-react'
import { Responsive, WidthProvider, type Layout } from 'react-grid-layout'
import type { Workflow, WorkflowDashboardLayout, WorkflowStep } from '../../types'
import { RequestDemoRunSummary } from './RequestDemoRunSummary'; import { WorkflowPendingState } from './WorkflowPendingState'
import { FocusedRequestOverview } from './FocusedRequestOverview'
import { WorkflowStepItem } from './WorkflowStepItem'
const ResponsiveGridLayout = WidthProvider(Responsive) as unknown as React.ComponentType<any>
export function WorkflowView({ workflows, stageEditMode, layoutEditMode, dashboardLayout, layoutVersion, onDashboardLayoutChange, onStepChange, onAddStep, onDeleteStep, onMoveStep, onOpenAnalysis, activeRequestId, loadCaseReady = true, onOpenData = () => {}, onOpenWorkbench, onSelectRequest }: {
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
  onOpenWorkbench?: (requestId: string) => void
  onSelectRequest?: (requestId: string) => void
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

  if (!layoutEditMode && !stageEditMode) {
    const focusedWorkflow = workflows.find((workflow) => workflow.request.id === activeRequestId) ?? ordered[0]
    if (!focusedWorkflow) return <WorkflowPendingState hasWorkflows={false} onOpenData={onOpenData} />
    const otherWorkflows = ordered.filter((workflow) => workflow.request.id !== focusedWorkflow?.request.id)
    return <FocusedRequestOverview workflow={focusedWorkflow} otherWorkflows={otherWorkflows} onOpenWorkbench={onOpenWorkbench} onOpenAnalysis={onOpenAnalysis} onSelectRequest={onSelectRequest} sortKey={sortKey} onSortChange={setSortKey} />
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
