import { RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { folderEnvironmentApi, type RequestResultsCheck } from '../../shared/api/folderEnvironment'
import { ApiError } from '../../shared/api/errors'

/** Fired after a check found new results; result screens listen and reload (detail: request id). */
export const RESULTS_CHANGED_EVENT = 'simdash:results-changed'

function clock(value: string | number) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false })
}

function summary(result: RequestResultsCheck): string {
  if (!result.registered) return '등록된 결과 폴더가 없습니다'
  if (result.failed.length) return `일부 확인 실패 · ${result.failed[0].message ?? result.failed[0].code ?? ''}`.trim()
  if (result.changed) return result.added ? `새 항목 ${result.added}개 반영` : '변경 사항 반영'
  return '새 결과 없음'
}

/**
 * [새 결과 확인] (user decision 2026-10-14): checks every registered folder of the request now.
 * The server also checks every 5 minutes on its own; this button is for "right now".
 */
export function RequestNewResultsCheck({ projectId, requestId, disabled = false, onChanged }: {
  projectId: string
  requestId: string
  disabled?: boolean
  onChanged?: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [text, setText] = useState('')
  const [tone, setTone] = useState<'ok' | 'new' | 'bad'>('ok')
  const scope = useRef('')
  const current = `${projectId}\u0000${requestId}`
  useEffect(() => { scope.current = current; setText(''); setTone('ok') }, [current])

  const check = async () => {
    if (!projectId || !requestId || busy) return
    const started = current
    setBusy(true)
    try {
      const result = await folderEnvironmentApi.syncRequest({ project_id: projectId, request_id: requestId })
      if (scope.current !== started) return
      setText(`${summary(result)} · ${clock(result.checked_at)} 확인`)
      setTone(result.failed.length ? 'bad' : result.changed ? 'new' : 'ok')
      if (result.changed) {
        window.dispatchEvent(new CustomEvent(RESULTS_CHANGED_EVENT, { detail: requestId }))
        onChanged?.()
      }
    } catch (reason) {
      if (scope.current !== started) return
      setTone('bad')
      setText(reason instanceof ApiError && reason.status === 403 ? '이 의뢰를 확인할 권한이 없습니다' : '확인하지 못했습니다. 잠시 후 다시 시도하세요')
    } finally {
      if (scope.current === started) setBusy(false)
    }
  }

  return <span className="request-new-results">
    <button type="button" className="ghost-button request-results-refresh" data-testid="request-new-results-check"
      title="이 의뢰의 결과 폴더를 지금 확인합니다. 서버도 5분마다 자동으로 확인합니다."
      disabled={disabled || busy || !projectId || !requestId} onClick={() => void check()}>
      <RefreshCw aria-hidden="true" className={busy ? 'spin' : undefined} /> {busy ? '확인 중…' : '새 결과 확인'}
    </button>
    {text ? <small role="status" className={`request-new-results-status ${tone}`} data-testid="request-new-results-status">{text}</small> : null}
  </span>
}
