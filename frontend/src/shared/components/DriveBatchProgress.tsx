import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, LoaderCircle, PauseCircle } from 'lucide-react'
import { DRIVE_BATCH_STATE_LABELS, driveApi, driveCodeMessage, driveErrorText, type DriveUploadBatch } from '../api/drive'
import './DriveBatchProgress.css'

const POLL_MS = 2000

function bytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`
  return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

/** Progress of one SCX drive upload batch (D3 queue): polls until it finished, then calls ``onFinished`` once. */
export function DriveBatchProgress({ batchId, initial, onFinished }: { batchId: string; initial?: DriveUploadBatch | null; onFinished?: (batch: DriveUploadBatch) => void }) {
  const [batch, setBatch] = useState<DriveUploadBatch | null>(initial ?? null)
  const [error, setError] = useState('')
  const finished = useRef(false)
  const notify = useRef(onFinished)
  notify.current = onFinished

  const initiallyFinished = Boolean(initial?.finished)
  useEffect(() => {
    // A batch already known to be finished (e.g. re-mounted after the caller stored the final summary) is not polled again.
    finished.current = initiallyFinished
    if (initiallyFinished) return undefined
    const controller = new AbortController()
    let timer = 0
    const poll = async () => {
      try {
        const next = await driveApi.uploadBatch(batchId, controller.signal)
        setBatch(next); setError('')
        if (next.finished) {
          if (!finished.current) { finished.current = true; notify.current?.(next) }
          return
        }
      } catch (reason) {
        if (controller.signal.aborted) return
        setError(driveErrorText(reason, '드라이브 반영 상태를 불러오지 못했습니다.'))
      }
      timer = window.setTimeout(() => void poll(), POLL_MS)
    }
    void poll()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [batchId, initiallyFinished])

  if (!batch) return <p className="drive-batch" role="status"><LoaderCircle className="drive-batch__spin" aria-hidden="true" /> 드라이브 반영 상태를 확인하는 중</p>
  const percent = batch.bytes_total > 0 ? Math.round(batch.bytes_done / batch.bytes_total * 100) : batch.files_total > 0 ? Math.round(batch.files_done / batch.files_total * 100) : (batch.state === 'DONE' ? 100 : 0)
  const tone = batch.state === 'DONE' ? 'ok' : batch.paused ? 'warn' : batch.finished ? 'bad' : 'busy'
  const Icon = batch.state === 'DONE' ? CheckCircle2 : batch.paused ? PauseCircle : batch.finished ? AlertTriangle : LoaderCircle
  return <div className={`drive-batch drive-batch--${tone}`} data-testid="drive-batch" data-state={batch.state} role="status">
    <p><Icon className={tone === 'busy' ? 'drive-batch__spin' : undefined} aria-hidden="true" /><strong>{DRIVE_BATCH_STATE_LABELS[batch.state] ?? batch.state}</strong>
      <span> · 파일 {batch.files_done}/{batch.files_total} · {bytes(batch.bytes_done)} / {bytes(batch.bytes_total)}</span></p>
    {!batch.finished ? <div className="drive-batch__bar" role="progressbar" aria-label="드라이브 반영 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><i style={{ width: `${percent}%` }} /></div> : null}
    {batch.current && !batch.finished ? <small>현재: <code>{batch.current}</code></small> : null}
    {batch.paused ? <small data-testid="drive-batch-paused">드라이브 인증이 필요해 대기열이 멈췄습니다. 관리자가 토큰을 다시 등록하면 이어서 진행합니다.</small> : null}
    {batch.next_retry_at && !batch.finished && !batch.paused ? <small>드라이브 응답이 없어 잠시 후 자동으로 다시 시도합니다.</small> : null}
    {batch.errors?.length ? <ul className="drive-batch__errors" data-testid="drive-batch-errors">{batch.errors.slice(0, 10).map((item) => <li key={item.id}>
      <code>{item.target.split('/').pop()}</code> {driveCodeMessage(item.code)}{item.message && item.message !== driveCodeMessage(item.code) ? ` (${item.message})` : ''}
    </li>)}</ul> : null}
    {batch.state === 'CONFLICT' || batch.state === 'PARTIAL' || batch.state === 'FAILED' ? <small>드라이브의 기존 파일은 그대로입니다(덮어쓰거나 지우지 않음). 관리자가 드라이브 관리 › 업로드 대기열에서 확인할 수 있습니다.</small> : null}
    {error ? <small className="drive-batch__error" role="alert">{error}</small> : null}
  </div>
}
