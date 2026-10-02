import { Link } from 'react-router-dom'
import type { FolderEnvironmentRegistration } from '../../shared/api/folderEnvironment'

// D8: the usage-environment "평가" level is now SCENE; legacy EVALUATION snapshots are shown as Scene too.
export const environmentRoleLabels: Record<string, string> = { PROJECT: '프로젝트', REQUEST: '의뢰', WORKING: 'Working 작업 영역', FINAL: 'Final 보관 영역', FINAL_CAE: 'Final CAE', FINAL_REPORTS: 'Final Reports', FINAL_CAD: 'Final CAD', FINAL_VERSION: 'Final 버전', SIMULATION_CASE: '해석 Case', EVALUATION: 'Scene', LOAD_CASE: '하중경우', EXECUTION_RUN: 'Run Case', RUN_OPTION: 'Run Option', INPUT: '입력 폴더', RESULTS: '결과 폴더', SCENE: 'Scene', CONTAINER: '일반 중간 폴더', CONTENT: '내용물', EXCLUDE: '이번 등록에서 제외' }
export const roleLabel = (value?: string | null) => value ? environmentRoleLabels[value] ?? value : '확인 필요'
const states: Record<string, string> = { REGISTERED: '연결 완료', CAPTURING: '결과 읽는 중', COMPLETED: '결과 읽기 완료', FAILED: '결과 읽기 실패', PENDING: '결과 읽기 대기', RUNNING: '결과 읽는 중', PARTIAL: '일부 결과 확인 필요' }
const captureErrorMessages: Record<string, string> = {
  FOLDER_SCHEMA_REQUEST_BINDING_REQUIRED: '선택한 의뢰의 폴더 연결을 확인할 수 없습니다. 프로젝트·의뢰 연결과 확정 폴더를 확인하세요.',
  FOLDER_SCHEMA_REQUEST_BINDING_AMBIGUOUS: '같은 의뢰에 연결된 폴더가 여러 곳입니다. 사용할 의뢰 폴더 연결을 확인하세요.',
  CAPTURE_CONTEXT_MISMATCH: '현재 의뢰 밖의 Case입니다. 해당 의뢰 폴더를 선택해 별도로 등록하세요.',
}

export function FolderRegistrationResults({ value, busy, onRefresh, onRetry }: { value: FolderEnvironmentRegistration; busy: boolean; onRefresh: () => void; onRetry: () => void }) {
  return <div className="folder-registration-results" aria-label="등록 결과">
    <div className="folder-environment-footer"><strong>{states[value.status] ?? value.status}</strong><span>{new Date(value.created_at).toLocaleString('ko-KR')}</span><button type="button" className="ghost-button" disabled={busy} onClick={onRefresh}>상태 새로고침</button>
      {value.capture_jobs.some((job) => ['FAILED', 'PENDING', 'RUNNING'].includes(job.status)) && <button type="button" className="primary-button" disabled={busy} onClick={onRetry}>미완료 결과 다시 읽기</button>}</div>
    {value.capture_jobs.map((job, index) => {
      const query = new URLSearchParams({ project: job.project_id || value.project_id, request: job.request_id || value.request_id, view: 'case_results', result_environment: value.environment, case: job.case_id, capture: job.capture_id || '' })
      return <div className="folder-job" key={job.id}><span>Case {index + 1} · {states[job.status] ?? job.status}</span>{job.error_code && <span role="alert">{captureErrorMessages[job.error_code] ?? job.error_code}</span>}{job.status === 'COMPLETED' && job.capture_id && <Link className="primary-button" to={`/workspace/requests?${query}`}>결과 보기{value.capture_jobs.length > 1 ? ` ${index + 1}` : ''}</Link>}</div>
    })}
    {value.usage_source_reviews && <details className="folder-history-review"><summary>저장된 파일·값 검수 {Object.keys(value.usage_source_reviews).length}건</summary>{Object.entries(value.usage_source_reviews).map(([casePath, review]) => <div key={casePath}><strong>{casePath}</strong><span> · 확인 필요 {review.blocking_count ?? 0} · 자료 없음 {review.missing_count ?? 0}</span>{review.entries?.flatMap((entry) => entry.metrics.map((metric) => <p key={`${entry.evaluation}:${entry.direction}:${metric.key}`}>{entry.evaluation} / {entry.direction} · {metric.key}: {metric.value == null ? '—' : String(metric.value)} ({metric.status})</p>))}</div>)}</details>}
  </div>
}
