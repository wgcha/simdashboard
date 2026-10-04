import { Check, ChevronRight, Circle, Info, Play } from 'lucide-react'
import { folderProgressStatusLabel, type RequestFolderProgress } from '../../shared/api/simulationDashboard'
import type { Workflow, WorkflowStep } from '../../types'
import { useRequestFolderProgress } from './useRequestFolderProgress'

type FocusedRequestOverviewProps = {
  workflow: Workflow
  otherWorkflows: Workflow[]
  onOpenWorkbench?: (requestId: string) => void
  onOpenAnalysis: (workflow: Workflow) => void
  onOpenCaseResults?: () => void
  onSelectRequest?: (requestId: string) => void
  sortKey?: 'project' | 'category' | 'product' | 'owner' | 'time'
  onSortChange?: (value: 'project' | 'category' | 'product' | 'owner' | 'time') => void
}

function statusLabel(status: WorkflowStep['status']) {
  return ({ READY: '시작 대기', IN_PROGRESS: '진행 중', COMPLETED: '완료', WAITING: '대기', BLOCKED: '차단', FAILED: '실패' } as Record<WorkflowStep['status'], string>)[status]
}

function runStatusLabel(status: string) {
  return ({ SUCCEEDED: '완료', COMPLETED: '완료', RUNNING: '진행 중', IN_PROGRESS: '진행 중', PENDING: '대기', FAILED: '실패' } as Record<string, string>)[status] ?? status
}

function currentStep(workflow: Workflow) {
  return workflow.steps.find((step) => step.status === 'IN_PROGRESS')
    ?? workflow.steps.find((step) => step.status === 'READY')
    ?? workflow.steps.find((step) => step.status === 'BLOCKED' || step.status === 'FAILED' || step.status === 'WAITING')
}

const FOLDER_STEP_CLASS = { DONE: 'completed', IN_PROGRESS: 'in_progress', WAITING: 'waiting' } as const

/** Folder-registered request: steps computed from the SPDM folder (folder-request-progress.md §4). */
function FolderProgressSections({ progress, onOpen }: { progress: RequestFolderProgress; onOpen: () => void }) {
  const current = progress.steps.find((step) => step.key === progress.current_key)
  const finalStage = progress.current_key === 'FINAL' || progress.current_key === 'REPORT'
  return <>
    <section className="focused-current-task" aria-labelledby="focused-current-title" data-testid="folder-request-progress">
      <div className="focused-current-copy">
        <span className="focused-section-label">현재 할 일</span>
        <h2 id="focused-current-title">{progress.next_action || current?.label || '완료'}</h2>
        <div className="focused-status-line"><span className={`focused-status status-${FOLDER_STEP_CLASS[current?.status ?? 'DONE']}`}>{current ? `${current.label} · ${folderProgressStatusLabel(current.status)}` : '완료'}</span><span>{progress.completed} / {progress.total} 단계 완료</span></div>
        <p>{current?.detail ?? (current ? 'SPDM 폴더 상태에서 자동으로 계산한 진척입니다.' : '이 의뢰의 모든 단계를 완료했습니다.')}</p>
      </div>
      <button className="focused-primary-action" type="button" onClick={onOpen}><Play aria-hidden="true" /> {finalStage ? 'Case 결과에서 Final 지정' : 'Case 결과 열기'}</button>
    </section>
    <section className="focused-timeline" aria-label="의뢰 진척 단계">
      {progress.steps.map((step, index) => <div className={`focused-timeline-step ${FOLDER_STEP_CLASS[step.status]} ${step.key === progress.current_key ? 'current' : ''}`} key={step.key} data-step-key={step.key}>
        <span className="focused-step-marker">{step.status === 'DONE' ? <Check aria-hidden="true" /> : step.key === progress.current_key ? <b>{index + 1}</b> : <Circle aria-hidden="true" />}</span>
        <div><strong>{step.label}</strong><span>{folderProgressStatusLabel(step.status)}{step.detail && step.status !== 'DONE' ? ` · ${step.detail}` : ''}</span></div>
        {index < progress.steps.length - 1 && <i aria-hidden="true" />}
      </div>)}
    </section>
  </>
}

export function FocusedRequestOverview({ workflow, otherWorkflows, onOpenWorkbench, onOpenAnalysis, onOpenCaseResults, onSelectRequest, sortKey = 'time', onSortChange }: FocusedRequestOverviewProps) {
  const { progress: folderProgress, pending: folderProgressPending } = useRequestFolderProgress(workflow.request.project_id, workflow.request.id)
  const hasSteps = workflow.steps.length > 0
  const current = currentStep(workflow)
  const availableSteps = workflow.steps.filter((step) => step.status === 'IN_PROGRESS' || step.status === 'READY')
  const completed = workflow.steps.filter((step) => step.status === 'COMPLETED').length
  const openWorkbench = () => onOpenWorkbench ? onOpenWorkbench(workflow.request.id) : onOpenAnalysis(workflow)

  return <div className="focused-request-view" data-ui-density="v1" data-testid="focused-request-overview">
    {folderProgressPending ? <section className="focused-current-task" aria-busy="true" data-testid="folder-request-progress-pending"><div className="focused-current-copy"><span className="focused-section-label">현재 할 일</span><p>진척을 확인하는 중입니다.</p></div></section>
    : folderProgress ? <FolderProgressSections progress={folderProgress} onOpen={onOpenCaseResults ?? (() => onOpenAnalysis(workflow))} /> : <>
    <section className="focused-current-task" aria-labelledby="focused-current-title">
      <div className="focused-current-copy">
        <span className="focused-section-label">현재 할 일</span>
        <h2 id="focused-current-title">{current?.name ?? (hasSteps ? '모든 작업 완료' : '작업 계획 확인')}</h2>
        <div className="focused-status-line"><span className={`focused-status status-${(current?.status ?? 'COMPLETED').toLowerCase()}`}>{availableSteps.length > 1 ? `현재 가능한 작업 ${availableSteps.length}개` : current ? statusLabel(current.status) : hasSteps ? '완료' : '작업 없음'}</span><span>{completed} / {workflow.total_count ?? workflow.steps.length} 작업 완료</span></div>
        <p>{!hasSteps ? '등록된 작업 단계가 없습니다.' : current?.status === 'IN_PROGRESS' ? '담당된 작업을 이어서 진행할 수 있습니다.' : current?.status === 'READY' ? '현재 작업을 시작하면 진행도와 실행 도구가 열립니다.' : current?.status === 'WAITING' ? '선행 작업이 끝나면 다음 작업을 시작할 수 있습니다.' : current ? '선행 작업 또는 입력 조건을 확인해 주세요.' : '이 의뢰의 작업 계획을 모두 완료했습니다.'}</p>
      </div>
      <button className="focused-primary-action" type="button" onClick={openWorkbench} disabled={!current && workflow.steps.length === 0}><Play aria-hidden="true" /> {current?.status === 'WAITING' ? '작업 상태 확인' : current ? '작업 이어하기' : '작업 기록 보기'}</button>
    </section>

    <section className="focused-timeline" aria-label="의뢰 작업 단계">
      {workflow.steps.map((step, index) => <div className={`focused-timeline-step ${step.status.toLowerCase()} ${step.id === current?.id ? 'current' : ''}`} key={step.id}>
        <span className="focused-step-marker">{step.status === 'COMPLETED' ? <Check aria-hidden="true" /> : step.status === current?.status && step.id === current?.id ? <b>{step.sequence_no}</b> : <Circle aria-hidden="true" />}</span>
        <div><strong>{step.name}</strong><span>{statusLabel(step.status)}{step.progress > 0 && step.status !== 'COMPLETED' ? ` · ${step.progress}%` : ''}</span></div>
        {index < workflow.steps.length - 1 && <i aria-hidden="true" />}
      </div>)}
    </section>
    </>}

    <details className="focused-disclosure" id="request-info"><summary><Info aria-hidden="true" /><span>의뢰 정보</span><ChevronRight aria-hidden="true" /></summary><div className="focused-disclosure-content"><dl><div><dt>시나리오</dt><dd>{workflow.work_plan?.scenario_name ?? workflow.request.category}</dd></div><div><dt>하중 경우</dt><dd>{workflow.request.load_case_name || '미지정'}</dd></div><div><dt>요청자</dt><dd>{workflow.work_plan?.requested_by ?? workflow.request.owner}</dd></div><div><dt>요청일</dt><dd>{new Date(workflow.request.requested_at).toLocaleDateString('ko-KR')}</dd></div></dl></div></details>
    {!folderProgress && !folderProgressPending && <details className="focused-disclosure" id="work-history"><summary><Info aria-hidden="true" /><span>전체 작업 이력</span><ChevronRight aria-hidden="true" /></summary><div className="focused-disclosure-content"><div className="focused-history-list">{workflow.steps.map((step) => <article key={step.id}><strong>{step.name}</strong><span>{statusLabel(step.status)} · {step.owner}</span><small>{step.started_at ? `시작 ${new Date(step.started_at).toLocaleString('ko-KR')}` : '아직 시작하지 않음'}{step.completed_at ? ` · 완료 ${new Date(step.completed_at).toLocaleString('ko-KR')}` : ''}</small>{step.note && <p>{step.note}</p>}</article>)}</div></div></details>}
    {otherWorkflows.length > 0 && <details className="focused-disclosure" id="other-requests"><summary><span>다른 의뢰</span><ChevronRight aria-hidden="true" /></summary><div className="focused-other-toolbar"><label>정렬 기준<select aria-label="다른 의뢰 정렬 기준" value={sortKey} onChange={(event) => onSortChange?.(event.target.value as typeof sortKey)}><option value="time">최근 요청순</option><option value="project">프로젝트별</option><option value="category">카테고리별</option><option value="product">제품 이름순</option><option value="owner">작업자 이름순</option></select></label></div><div className="focused-other-list">{otherWorkflows.map((item) => <button type="button" key={item.request.id} onClick={() => onSelectRequest?.(item.request.id)}><span><strong>{item.request.title}</strong><small>{item.request.project_name} · {item.progress}%</small></span><ChevronRight aria-hidden="true" /></button>)}</div></details>}
    {workflow.latest_demo_run && <details className="focused-disclosure focused-demo-history"><summary><span>DEMO 실행 기록</span><b>DEMO ONLY</b><ChevronRight aria-hidden="true" /></summary><div className="focused-disclosure-content"><p>{workflow.latest_demo_run.name} · {runStatusLabel(workflow.latest_demo_run.status)} · {new Date(workflow.latest_demo_run.completed_at || workflow.latest_demo_run.created_at).toLocaleString('ko-KR')}</p></div></details>}
  </div>
}
