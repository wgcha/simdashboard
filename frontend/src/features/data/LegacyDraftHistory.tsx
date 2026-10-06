import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { resultDropApi, type DropEnvironment, type LegacyDraft } from '../../shared/api/resultDrop'
import { formatBytes } from '../../shared/api/resultDropModel'

// Drafts made with the pre-W8 registration screen (upload → checks → approval → publish).
// Read-only: nothing here changes a draft, its files or the SPDM folders.

const STATUS_LABELS: Record<string, string> = {
  DRAFT: '초안', UPLOAD_PENDING: '업로드 대기', INSPECTED: '검사 완료', APPROVED: '승인됨', PUBLISH_FAILED: '등록 실패',
  MIRROR_PENDING: 'DB 등록 · 폴더 반영 대기', MIRROR_CONFLICT: 'DB 등록 · 폴더 충돌', MIRROR_FAILED: 'DB 등록 · 폴더 반영 실패', PUBLISHED: '등록 완료',
}

function when(value: string | null) {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 16)
}

export function LegacyDraftHistory({ projectId, requestId, environment, resultsHref }: {
  projectId: string; requestId: string; environment: DropEnvironment; resultsHref: (caseId: string | null) => string
}) {
  const [open, setOpen] = useState(false)
  const [drafts, setDrafts] = useState<LegacyDraft[] | null>(null)
  const [error, setError] = useState('')
  useEffect(() => { setDrafts(null); setError('') }, [projectId, requestId, environment])
  useEffect(() => {
    if (!open || drafts) return
    const controller = new AbortController()
    resultDropApi.legacyDrafts({ project_id: projectId, request_id: requestId, environment }, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setDrafts(value.drafts) })
      .catch(() => { if (!controller.signal.aborted) setError('이전 등록 초안을 불러오지 못했습니다.') })
    return () => controller.abort()
  }, [drafts, environment, open, projectId, requestId])

  return <details className="result-drop__card result-drop__legacy" data-testid="legacy-draft-history" onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}>
    <summary>이전 등록 초안 (읽기 전용)</summary>
    <p className="result-drop__muted">예전 등록 화면(검사·승인 후 등록)으로 만든 초안입니다. 기록 확인용이며 여기서 바꾸거나 다시 등록하지 않습니다.</p>
    {error ? <p className="result-drop__error" role="alert">{error}</p> : null}
    {open && !drafts && !error ? <p className="result-drop__state" role="status">불러오는 중</p> : null}
    {drafts && !drafts.length ? <p className="result-drop__state">이 의뢰에는 이전 등록 초안이 없습니다.</p> : null}
    {drafts?.length ? <div className="result-drop__table-wrap"><table className="result-drop__table" aria-label="이전 등록 초안">
      <thead><tr><th>만든 때</th><th>상태</th><th>결과 위치</th><th>파일</th><th>결과</th></tr></thead>
      <tbody>{drafts.map((item) => <tr key={item.draft_id}>
        <td>{when(item.created_at)}</td>
        <td>{STATUS_LABELS[item.status] ?? '확인 필요'}</td>
        <td><code title={item.result_relative_path}>{item.result_relative_path}</code></td>
        <td>{item.file_count}개 · {formatBytes(item.total_bytes)}</td>
        <td>{item.capture_id ? <Link className="result-drop__link" to={resultsHref(item.case_id)}>Case 결과</Link> : '—'}</td>
      </tr>)}</tbody>
    </table></div> : null}
  </details>
}
