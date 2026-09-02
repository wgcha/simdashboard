import { AlertTriangle, CheckCircle2, CircleDashed, Clock3, LoaderCircle, RefreshCw, RotateCcw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { fetchResultImportHistory, retryResultImport, type ResultImportHistoryItem, type ResultImportStatus } from '../../shared/api/resultImportHistory'
import './ResultImportHistory.css'

type Props = { loadCaseId: string; canRetryImports: boolean; refreshToken?: number }
const statusOptions: Array<{ value: '' | ResultImportStatus; label: string }> = [{ value: '', label: '전체 상태' }, { value: 'RUNNING', label: '진행 중' }, { value: 'COMPLETED', label: '완료' }, { value: 'FAILED', label: '실패' }, { value: 'REJECTED', label: '거부' }, { value: 'SKIPPED', label: '중복 제외' }]

function formatTime(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('ko-KR', { dateStyle: 'short', timeStyle: 'short' })
}

function statusIcon(status: string) {
  if (status === 'RUNNING') return <LoaderCircle className="result-import-history-spin" aria-hidden="true" />
  if (status === 'FAILED' || status === 'REJECTED') return <AlertTriangle aria-hidden="true" />
  if (status === 'COMPLETED') return <CheckCircle2 aria-hidden="true" />
  return <CircleDashed aria-hidden="true" />
}

function ImportHistoryRow({ item, canRetry, retrying, retryingAny, onRetry }: { item: ResultImportHistoryItem; canRetry: boolean; retrying: boolean; retryingAny: boolean; onRetry: (id: string) => void }) {
  const retryable = canRetry && item.retryable && (item.status === 'FAILED' || item.status === 'REJECTED')
  return <article className={`result-import-history-row ${item.status.toLowerCase()}`}>
    <div className="result-import-history-row-main">
      <i className="result-import-history-status-icon">{statusIcon(item.status)}</i>
      <div className="result-import-history-row-title"><strong>{item.source_folder || item.source_type || '결과 등록 작업'}</strong><small>{item.source_run_id ? `Producer Run ${item.source_run_id}` : `Job ${item.id}`}</small></div>
      <b className="result-import-history-status">{item.status}</b>
      {retryable ? <button type="button" className="result-import-history-retry" onClick={() => onRetry(item.id)} disabled={retrying || retryingAny} aria-label={`${item.id} 결과 등록 재시도`}>{retrying ? <LoaderCircle className="result-import-history-spin" aria-hidden="true" /> : <RotateCcw aria-hidden="true" />} 재시도</button> : null}
    </div>
    <div className="result-import-history-row-meta">
      <span><em>Operation</em>{item.operation || '—'}</span><span><em>사유</em>{item.outcome_reason || '—'}</span><span><em>Run</em>{item.analysis_run_id || item.replaced_analysis_run_id || '—'}</span><span><em>Revision</em>{item.source_revision ?? '—'}</span><span><em>등록</em>{formatTime(item.created_at)}</span><span><em>완료</em>{formatTime(item.completed_at)}</span>
    </div>
  </article>
}

export function ResultImportHistory({ loadCaseId, canRetryImports, refreshToken = 0 }: Props) {
  const [status, setStatus] = useState<'' | ResultImportStatus>('')
  const [items, setItems] = useState<ResultImportHistoryItem[]>([])
  const [total, setTotal] = useState(0)
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [retryingId, setRetryingId] = useState('')
  const [announcement, setAnnouncement] = useState('')
  const [reloadVersion, setReloadVersion] = useState(0)
  const contextVersion = useRef(0)
  const fetchVersion = useRef(0)
  const retryError = useRef<{ context: number; message: string } | null>(null)
  const retryInFlight = useRef<{ context: number; jobId: string } | null>(null)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  useEffect(() => {
    const version = ++contextVersion.current
    setRetryingId('')
    setAnnouncement('')
    setError('')
    retryError.current = null
    retryInFlight.current = null
    return () => {
      if (contextVersion.current === version) contextVersion.current += 1
    }
  }, [loadCaseId])

  useEffect(() => {
    const version = ++fetchVersion.current
    const context = contextVersion.current
    const preservedRetryError = retryError.current?.context === context ? retryError.current.message : null
    if (retryError.current && !preservedRetryError) retryError.current = null
    if (!loadCaseId) { setItems([]); setTotal(0); setCounts({}); setError(''); setLoading(false); return () => { if (fetchVersion.current === version) fetchVersion.current += 1 } }
    const controller = new AbortController()
    setLoading(true); if (!preservedRetryError) setError('')
    fetchResultImportHistory(loadCaseId, { status: status || undefined, signal: controller.signal }).then((result) => {
      if (version !== fetchVersion.current) return
      setItems(result.items); setTotal(result.total); setCounts(result.counts)
    }).catch((reason) => {
      if (controller.signal.aborted || version !== fetchVersion.current) return
      setError(reason instanceof Error ? reason.message : '결과 등록 이력을 불러오지 못했습니다.')
      setItems([]); setTotal(0); setCounts({})
    }).finally(() => {
      if (!controller.signal.aborted && version === fetchVersion.current) {
        setLoading(false)
        if (preservedRetryError && mounted.current && context === contextVersion.current) {
          setError(preservedRetryError)
          retryError.current = null
        }
      }
    })
    return () => { controller.abort(); if (fetchVersion.current === version) fetchVersion.current += 1 }
  }, [loadCaseId, refreshToken, reloadVersion, status])

  const retry = async (jobId: string) => {
    if (retryInFlight.current || !loadCaseId) return
    const version = contextVersion.current
    retryInFlight.current = { context: version, jobId }
    setRetryingId(jobId); setError(''); setAnnouncement('결과 등록 재시도 중입니다.')
    try {
      const result = await retryResultImport(jobId)
      if (!mounted.current || version !== contextVersion.current) return
      if (result.status === 'FAILED') {
        const message = result.message || result.reason_code || '결과 등록 재시도에 실패했습니다.'
        retryError.current = { context: version, message }
        setError(message)
        setAnnouncement('결과 등록 재시도가 실패했습니다.')
      } else if (result.status === 'IMPORTED') {
        retryError.current = null
        setAnnouncement('결과 등록 재시도가 완료되었습니다.')
      } else if (result.status === 'SKIPPED') {
        retryError.current = null
        setAnnouncement('동일한 결과가 이미 등록되어 중복 제외되었습니다.')
      } else {
        retryError.current = null
        setAnnouncement(`결과 등록 재시도 결과: ${result.status}`)
      }
      setReloadVersion((value) => value + 1)
    } catch (reason) {
      if (mounted.current && version === contextVersion.current) {
        const message = reason instanceof Error ? reason.message : '결과 등록 재시도에 실패했습니다.'
        retryError.current = { context: version, message }
        setError(message)
        setAnnouncement('결과 등록 재시도가 실패했습니다.')
        setReloadVersion((value) => value + 1)
      }
    } finally {
      if (mounted.current && version === contextVersion.current) setRetryingId('')
      if (retryInFlight.current?.context === version && retryInFlight.current.jobId === jobId) retryInFlight.current = null
    }
  }

  const countLabel = total ? `${total}건` : '기록 없음'
  return <section className="result-import-history" data-testid="result-import-history" aria-labelledby="result-import-history-heading">
    <header className="result-import-history-head">
      <div><span>IMPORT AUDIT TRAIL</span><h2 id="result-import-history-heading">결과 등록 이력</h2><p>선택한 하중 경우의 등록 상태와 재시도 가능 작업을 확인합니다.</p></div>
      <button type="button" className="result-import-history-refresh" data-testid="result-import-history-refresh" onClick={() => setReloadVersion((value) => value + 1)} disabled={!loadCaseId || loading} aria-label="결과 등록 이력 새로고침"><RefreshCw className={loading ? 'result-import-history-spin' : ''} aria-hidden="true" /> 새로고침</button>
    </header>
    <div className="result-import-history-toolbar"><label><span>상태 필터</span><select data-testid="result-import-history-status" aria-label="결과 등록 이력 상태 필터" value={status} onChange={(event) => setStatus(event.target.value as '' | ResultImportStatus)} disabled={!loadCaseId}>{statusOptions.map((option) => <option key={option.value} value={option.value}>{option.label}{option.value && counts[option.value] != null ? ` (${counts[option.value]})` : ''}</option>)}</select></label><strong>{loadCaseId ? countLabel : '하중 경우를 선택하세요'}</strong></div>
    <div className="result-import-history-live" aria-live="polite">{announcement || (loading ? '결과 등록 이력을 불러오는 중입니다.' : error ? `오류: ${error}` : `${countLabel} 결과 등록 이력`)}</div>
    {error ? <div className="result-import-history-error" role="alert"><AlertTriangle aria-hidden="true" />{error}</div> : null}
    {!loadCaseId ? <div className="result-import-history-empty"><Clock3 aria-hidden="true" /><strong>하중 경우를 선택하세요.</strong><span>선택한 하중 경우의 결과 등록 이력이 표시됩니다.</span></div> : loading && !items.length ? <div className="result-import-history-empty"><LoaderCircle className="result-import-history-spin" aria-hidden="true" /><strong>이력을 불러오는 중입니다.</strong></div> : error ? null : !items.length ? <div className="result-import-history-empty"><Clock3 aria-hidden="true" /><strong>표시할 등록 이력이 없습니다.</strong><span>새 결과를 등록하면 작업 상태가 여기에 쌓입니다.</span></div> : <div className="result-import-history-list">{items.map((item) => <ImportHistoryRow key={item.id} item={item} canRetry={canRetryImports} retrying={retryingId === item.id} retryingAny={Boolean(retryingId)} onRetry={(id) => void retry(id)} />)}</div>}
  </section>
}
