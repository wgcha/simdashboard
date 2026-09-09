import { ArrowLeft, ArrowRight, Check, Trash2 } from 'lucide-react'
import type { WorkflowStep } from '../../types'

type WorkflowStepItemProps = {
  step: WorkflowStep
  last: boolean
  editMode: boolean
  onChange: (patch: Partial<WorkflowStep>) => void
  onMove: (offset: -1 | 1) => void
  onDelete: () => void
}

export function WorkflowStepItem({ step, last, editMode, onChange, onMove, onDelete }: WorkflowStepItemProps) {
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
