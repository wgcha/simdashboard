import { Clock3, Database } from 'lucide-react'

export function WorkflowPendingState({ hasWorkflows, onOpenData }: { hasWorkflows: boolean; onOpenData: () => void }) {
  return <div className="workflow-pending-state" data-testid="workflow-result-pending"><Clock3 /><div><strong>결과 대기 중</strong><p>{hasWorkflows ? '의뢰 작업은 생성됐지만 하중 경우와 분석 결과가 아직 연결되지 않았습니다.' : '의뢰 작업이 아직 작업 보드에 연결되지 않았습니다.'}</p><small>하중 경우를 선택하거나 새로 만들면 기본 분석 위젯은 결과를 기다리는 상태로 유지됩니다.</small></div><button type="button" onClick={onOpenData}><Database /> 하중 경우 설정</button></div>
}
