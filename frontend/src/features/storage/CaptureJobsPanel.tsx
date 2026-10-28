import { Download, RotateCcw, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { OPEN_CAPTURE_JOBS_EVENT, type OpenCaptureJobsDetail } from '../../shared/events/captureJobs'
import { folderEnvironmentApi, type CaptureJobItem, type CaptureJobStatus, type CaptureJobsState } from '../../shared/api/folderEnvironment'

const POLL_MS = 4000
const STATUS_LABEL: Record<CaptureJobStatus, string> = { RUNNING: '받는 중', PENDING: '대기', COMPLETED: '완료', FAILED: '실패' }
const ENVIRONMENT_LABEL: Record<string, string> = { USAGE: '사용', DISTRIBUTION: '유통' }

function bytesText(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 / 1024).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(2)} GB`
}

function timeText(value: string | null): string {
  if (!value) return ''
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function progressText(item: CaptureJobItem): string {
  if (item.status === 'RUNNING' && item.progress) {
    const parts = [`파일 ${item.progress.files}개 · ${bytesText(item.progress.bytes)} 받음`]
    if (item.progress.reused_files) parts.push(`이전에 받은 ${item.progress.reused_files}개 재사용`)
    if (item.progress.current_file) parts.push(`지금 받는 파일: ${item.progress.current_file.split('/').pop()}${item.progress.current_size ? ` (${bytesText(item.progress.current_size)})` : ''}`)
    return parts.join(' · ')
  }
  if (item.status === 'PENDING') return '순서를 기다립니다. 백그라운드에서 이어서 받습니다.'
  if (item.status === 'FAILED') return item.error_message || `결과를 받지 못했습니다${item.error_code ? ` (${item.error_code})` : ''}.`
  return ''
}

/**
 * [결과 받기 상태] (global administrators, SCX drive, issue #55): the capture jobs that download and read the Cases'
 * result files in the background. Shows pending / running / completed / failed with the live progress of the job
 * that runs now, refreshes every few seconds while any job is waiting or running, and retries failed ones.
 */
export function CaptureJobsPanel({ refreshKey }: { refreshKey?: string }) {
  const [state, setState] = useState<CaptureJobsState | null>(null)
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  const [retrying, setRetrying] = useState<string | null>(null)
  const [filter, setFilter] = useState<'ALL' | CaptureJobStatus>('ALL')
  const mounted = useRef(true)
  const load = useCallback(async () => {
    try {
      const next = await folderEnvironmentApi.captureJobs()
      if (!mounted.current) return
      setState(next); setError('')
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : '결과 받기 상태를 불러오지 못했습니다.')
    }
  }, [])
  const active = Boolean(state && (state.totals.pending > 0 || state.totals.running > 0))
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  // The 결과 대시보드 cards (받는 중 / 받기 실패) open this panel with the matching filter.
  useEffect(() => {
    const show = (event: Event) => { setFilter((event as CustomEvent<OpenCaptureJobsDetail | undefined>).detail?.filter ?? 'ALL'); setOpen(true) }
    window.addEventListener(OPEN_CAPTURE_JOBS_EVENT, show)
    return () => window.removeEventListener(OPEN_CAPTURE_JOBS_EVENT, show)
  }, [])
  useEffect(() => { void load() }, [load, refreshKey, open])
  useEffect(() => {
    if (!active) return undefined
    const timer = window.setInterval(() => { void load() }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [active, load])
  const retry = async (item: CaptureJobItem) => {
    if (retrying) return
    setRetrying(item.job_id)
    try { await folderEnvironmentApi.retryCapture(item.registration_id, [item.job_id]); await load() }
    catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : '다시 시도를 요청하지 못했습니다.') }
    finally { if (mounted.current) setRetrying(null) }
  }
  if (!state || state.totals.total === 0) return null
  const totals = state.totals
  const waiting = totals.pending + totals.running
  const rows = state.items.filter((item) => filter === 'ALL' || item.status === filter)
  return <>
    <button type="button" className="ghost-button" data-testid="capture-jobs-toggle" aria-expanded={open} onClick={() => setOpen((value) => !value)}
      title="Case 결과 파일을 드라이브에서 받는 작업의 진행·성공·실패를 봅니다."><Download /> 결과 받기 상태{waiting ? ` ${waiting}건 진행` : ''}{totals.failed ? ` · 실패 ${totals.failed}` : ''}</button>
    {open && <section className="storage-rescan-report storage-capture-jobs" aria-label="결과 받기 상태" data-testid="capture-jobs-panel">
      <header><strong>결과 받기 상태</strong><button type="button" className="storage-rescan-close" aria-label="닫기" onClick={() => setOpen(false)}><X /></button></header>
      <p className="storage-capture-summary" role="status" data-testid="capture-jobs-totals">
        받는 중 {totals.running} · 대기 {totals.pending} · 완료 {totals.completed} · 실패 {totals.failed} (전체 {totals.total}건)
        {waiting > 0 ? ' · 오래 걸려도 서버가 백그라운드에서 이어서 받습니다. 이 창을 닫아도 됩니다.' : ''}
      </p>
      <div className="storage-capture-filters" role="group" aria-label="상태 필터">
        {(['ALL', 'RUNNING', 'PENDING', 'FAILED', 'COMPLETED'] as const).map((value) =>
          <button key={value} type="button" className={`ghost-button${filter === value ? ' is-active' : ''}`} aria-pressed={filter === value} onClick={() => setFilter(value)}>{value === 'ALL' ? '전체' : STATUS_LABEL[value]}</button>)}
      </div>
      {error && <p className="storage-rescan-error" role="alert">{error}</p>}
      {rows.length === 0 ? <p>표시할 작업이 없습니다.</p> : <ul>
        {rows.map((item) => <li key={item.job_id} data-testid="capture-job-row" data-status={item.status}>
          <strong><span className={`storage-capture-chip is-${item.status.toLowerCase()}`}>{STATUS_LABEL[item.status]}</span> {item.project_name ? `${item.project_name} / ` : ''}{item.request_name || item.request_id}{item.environment ? ` (${ENVIRONMENT_LABEL[item.environment] ?? item.environment})` : ''}</strong>
          <code>{item.case_relative_path || item.case_id}</code>
          {progressText(item) && <span className={item.status === 'FAILED' ? 'storage-rescan-error' : undefined}>{progressText(item)}</span>}
          <small>요청 {timeText(item.created_at)} · 최근 변경 {timeText(item.updated_at)}{item.status === 'FAILED' && item.error_code ? ` · ${item.error_code}` : ''}</small>
          {item.status === 'FAILED' && <button type="button" className="ghost-button storage-rescan-item-action" disabled={retrying !== null} onClick={() => void retry(item)}><RotateCcw /> 다시 시도</button>}
        </li>)}
      </ul>}
      {state.truncated && <small>목록이 길어 일부만 표시합니다(상태별 합계는 전체 기준).</small>}
    </section>}
  </>
}
