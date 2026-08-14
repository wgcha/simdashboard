import { FlaskConical } from 'lucide-react'
import type { Workflow } from '../../types'

function runStatusLabel(status: string) {
  return ({ SUCCEEDED: '완료', COMPLETED: '완료', RUNNING: '진행 중', IN_PROGRESS: '진행 중', PENDING: '대기', WAITING: '선행 작업 대기', READY: '실행 준비', BLOCKED: '차단', FAILED: '실패', SKIPPED: '건너뜀', CANCELLED: '취소' } as Record<string, string>)[status] ?? status
}

export function RequestDemoRunSummary({ run }: { run: Workflow['latest_demo_run'] }) {
  if (!run) return <div className="request-demo-summary empty"><FlaskConical /><span><strong>연결 실행 없음</strong><small>해석 작업 실행 탭에서 DEMO_ONLY 작업을 연결할 수 있습니다.</small></span></div>
  return <div className="request-demo-summary"><img src="/assets/demo-workbench.svg" alt="최근 데모 실행 결과" /><span><strong>{run.name}</strong><small>DEMO_ONLY · {runStatusLabel(run.status)} · {run.progress}%</small></span><time>{new Date(run.completed_at || run.created_at).toLocaleString('ko-KR')}</time></div>
}
