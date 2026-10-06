import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, FileArchive, LoaderCircle, RotateCcw } from 'lucide-react'
import {
  caseFinalizationApi,
  type CaseFinalizationInput,
  type CaseFinalizationJob,
  type CaseFinalizationPreview,
  type CaseFinalizationRecord,
  type CaseFinalizationReportFormat,
  type CaseFinalizationExcludedScene,
  type CaseFinalizationStatus,
} from '../../shared/api/caseFinalization'
import type { DashboardEnvironment } from '../../shared/api/simulationDashboard'
import type { CaseReportFinalScope } from './caseReport/caseReport'
import { buildFinalReports, type FinalReportFiles } from './caseReport/finalReports'
import './CaseFinalizationPanel.css'

type Props = {
  projectId: string
  requestId: string
  environment: DashboardEnvironment
  caseId: string
  /** `latest:<dashboard_case_id>`: the merged latest result the screen shows. */
  captureId: string
  hasCapturedCase: boolean
  canFinalize: boolean
  /** Final report scope: the whole Case (usage Case, or every Run Case · Run Option); null without results. */
  reportScope: CaseReportFinalScope | null
}

type Phase = 'idle' | 'building' | 'uploading' | 'starting'

const FORMATS: CaseFinalizationReportFormat[] = ['pptx', 'html']
const PHASE_TEXT: Record<Exclude<Phase, 'idle'>, string> = { building: '보고서 만드는 중…', uploading: '보고서 올리는 중…', starting: 'Final 복사 시작 중…' }
/** Job progress polling interval (the server writes progress about once a second). */
const FINAL_JOB_POLL_MS = 1000
const MAX_POLL_FAILURES = 5
const LISTED_FILES = 500

const formatBytes = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

const isRunning = (job: CaseFinalizationJob | null | undefined): job is CaseFinalizationJob => job?.state === 'QUEUED' || job?.state === 'RUNNING'

/** One overall percentage: copy 0–60 %, read-back check 60–95 %, folder rename 95–100 %. */
const jobPercent = (job: CaseFinalizationJob) => {
  if (job.state === 'COMPLETE') return 100
  const ratio = job.bytes_total > 0 ? Math.min(1, job.bytes_done / job.bytes_total) : (job.files_total > 0 ? Math.min(1, job.files_done / job.files_total) : 0)
  if (job.phase === 'VERIFYING') return Math.floor(60 + ratio * 35)
  if (job.phase === 'PUBLISHING') return 95
  if (job.phase === 'COPYING') return Math.floor(ratio * 60)
  return 0
}

const JOB_PHASE_TEXT: Record<NonNullable<CaseFinalizationJob['phase']>, string> = { COPYING: '복사', VERIFYING: '검증', PUBLISHING: '공개' }
const jobLabel = (job: CaseFinalizationJob) => job.state === 'QUEUED' ? 'Final 복사 대기' : `Final ${job.phase ? JOB_PHASE_TEXT[job.phase] : '복사'} 중 ${jobPercent(job)}%`

const formatDate = (value?: string | null) => {
  if (!value) return '시간 정보 없음'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '시간 정보 없음' : date.toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' })
}

const lastSegment = (path: string) => path.split('/').filter(Boolean).pop() ?? path
const EXCLUDED_REASON: Record<CaseFinalizationExcludedScene['reason'], string> = {
  NO_CAPTURE: '수집된 결과 없음',
  CAPTURE_SCHEMA_MISSING: '최신 결과에 확정 위치 정보 없음 · 다시 수집 필요',
  CAPTURE_SCHEMA_INCOMPATIBLE: '최신 결과가 현재 Folder Schema와 다름 · 다시 수집 필요',
}
const message = (cause: unknown, fallback: string) => cause instanceof Error && cause.message ? cause.message : fallback

export function CaseFinalizationPanel(props: Props) {
  const { projectId, requestId, environment, caseId, captureId, hasCapturedCase, canFinalize, reportScope } = props
  const [status, setStatus] = useState<CaseFinalizationStatus | null>(null)
  const [preview, setPreview] = useState<CaseFinalizationPreview | null>(null)
  const [scopeAtOpen, setScopeAtOpen] = useState<CaseReportFinalScope | null>(null)
  const [formats, setFormats] = useState<Record<CaseFinalizationReportFormat, boolean>>({ pptx: true, html: false })
  const [includeVideos, setIncludeVideos] = useState(false)
  const [phase, setPhase] = useState<Phase>('idle')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [dialogError, setDialogError] = useState('')
  const [notice, setNotice] = useState('')
  const [result, setResult] = useState<CaseFinalizationRecord | null>(null)
  const [skippedVideos, setSkippedVideos] = useState<string[]>([])
  const [skippedImages, setSkippedImages] = useState<string[]>([])
  const [dialogOpen, setDialogOpen] = useState(false)
  const [job, setJob] = useState<CaseFinalizationJob | null>(null)
  const pollFailures = useRef(0)
  const generation = useRef(0)
  const dialogRef = useRef<HTMLDialogElement>(null)
  // Reports are built once per operation and choice, so a retry uploads the same bytes.
  const built = useRef<{ key: string; reports: FinalReportFiles } | null>(null)
  const input = useMemo<CaseFinalizationInput>(() => ({ project_id: projectId, request_id: requestId, environment, case_id: caseId, capture_id: captureId }), [projectId, requestId, environment, caseId, captureId])

  const refreshStatus = useCallback(async (token = generation.current) => {
    if (!projectId || !requestId || !caseId || !hasCapturedCase) {
      if (token === generation.current) setStatus(null)
      return
    }
    try {
      const next = await caseFinalizationApi.status({ project_id: projectId, request_id: requestId, environment, case_id: caseId })
      if (token !== generation.current) return
      setStatus(next)
      // A copy job started earlier (or in another tab) keeps reporting progress after a reload.
      const active = next.active_operations?.[0] ?? null
      setJob((current) => active ?? (isRunning(current) ? current : null))
    } catch (cause) {
      if (token === generation.current) setError(message(cause, '최종확정 상태를 불러오지 못했습니다.'))
    }
  }, [projectId, requestId, environment, caseId, hasCapturedCase])

  useEffect(() => {
    const token = ++generation.current
    setStatus(null); setPreview(null); setResult(null); setError(''); setDialogError(''); setNotice('')
    setDialogOpen(false); setBusy(false); setPhase('idle'); setJob(null)
    void refreshStatus(token)
    return () => {
      if (generation.current === token) generation.current += 1
    }
  }, [refreshStatus, captureId])

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (dialogOpen && !dialog.open) dialog.showModal()
    else if (!dialogOpen && dialog.open) dialog.close()
  }, [dialogOpen])

  // Background copy (W2): poll the job until it completes or fails; closing the dialog does not stop it.
  useEffect(() => {
    if (!isRunning(job) || !projectId || !requestId || !caseId) return
    const token = generation.current
    let stopped = false
    const timer = window.setTimeout(async () => {
      try {
        const next = await caseFinalizationApi.job({ project_id: projectId, request_id: requestId, environment, case_id: caseId, operation_id: job.operation_id })
        if (stopped || token !== generation.current) return
        pollFailures.current = 0
        setJob(next)
        if (next.state === 'COMPLETE' && next.record) {
          const record = next.record
          setResult((current) => current ?? (preview?.operation_id === record.operation_id ? record : null))
          setNotice(`Final 지정 완료 · CAE ${record.files.length}개 · 보고서 ${record.reports.length}개`)
          void refreshStatus(token)
        } else if (next.state === 'FAILED') {
          setDialogError(next.error?.message || 'Final 복사를 완료하지 못했습니다. 같은 Final ID로 다시 시도할 수 있습니다.')
        }
      } catch (cause) {
        if (stopped || token !== generation.current) return
        pollFailures.current += 1
        if (pollFailures.current >= MAX_POLL_FAILURES) setError(message(cause, 'Final 복사 진행 상황을 불러오지 못했습니다.'))
        else setJob((current) => current ? { ...current } : current)
      }
    }, FINAL_JOB_POLL_MS)
    return () => { stopped = true; window.clearTimeout(timer) }
  }, [job, projectId, requestId, environment, caseId, preview?.operation_id, refreshStatus])

  const startedJob = (next: CaseFinalizationJob) => {
    pollFailures.current = 0
    setJob(next)
    if (next.state === 'COMPLETE' && next.record) {
      setResult(next.record)
      setNotice(`Final 지정 완료 · CAE ${next.record.files.length}개 · 보고서 ${next.record.reports.length}개`)
    }
  }

  const closeDialog = () => {
    if (busy) return
    setDialogOpen(false)
    if (result) { setPreview(null); setResult(null) }
  }

  const makePreview = async () => {
    const token = generation.current
    setBusy(true); setError(''); setNotice(''); setDialogError(''); setResult(null); setSkippedVideos([]); setSkippedImages([])
    try {
      const next = await caseFinalizationApi.preview(input)
      if (token !== generation.current) return
      built.current = null
      setPreview(next)
      setScopeAtOpen(reportScope ? structuredClone(reportScope) : null)
      setDialogOpen(true)
    } catch (cause) {
      if (token === generation.current) setError(message(cause, '최종확정 미리보기를 만들지 못했습니다.'))
    } finally {
      if (token === generation.current) setBusy(false)
    }
  }

  const chosen = FORMATS.filter((format) => formats[format])

  const confirmFinal = async () => {
    if (!preview || !scopeAtOpen || !chosen.length) return
    const token = generation.current
    const operation = { ...input, operation_id: preview.operation_id }
    setBusy(true); setDialogError(''); setNotice('')
    try {
      const key = `${preview.operation_id}|${chosen.join(',')}|${formats.html && includeVideos ? 'video' : ''}`
      if (built.current?.key !== key) {
        setPhase('building')
        built.current = { key, reports: await buildFinalReports(scopeAtOpen, { formats: chosen, includeVideos: formats.html && includeVideos }) }
      }
      if (token !== generation.current) return
      const reports = built.current.reports
      setSkippedVideos(reports.skippedVideos)
      setSkippedImages(reports.skippedImages)
      setPhase('uploading')
      for (const format of chosen) {
        const blob = reports.files[format]
        if (!blob) throw new Error(`${format.toUpperCase()} 보고서를 만들지 못했습니다.`)
        await caseFinalizationApi.uploadReport(operation, format, blob)
        if (token !== generation.current) return
      }
      setPhase('starting')
      const started = await caseFinalizationApi.confirm({ ...operation, report_formats: chosen })
      if (token !== generation.current) return
      startedJob(started)
      if (started.state === 'COMPLETE') await refreshStatus(token)
    } catch (cause) {
      if (token === generation.current) setDialogError(message(cause, 'Final 지정을 완료하지 못했습니다. 같은 요청으로 다시 시도할 수 있습니다.'))
    } finally {
      if (token === generation.current) { setBusy(false); setPhase('idle') }
    }
  }

  /** Retry a failed copy job with the same Final ID and its already uploaded reports. */
  const retryJob = async (target: CaseFinalizationJob) => {
    const token = generation.current
    setBusy(true); setError(''); setDialogError(''); setNotice('')
    try {
      const started = await caseFinalizationApi.confirm({ ...input, capture_id: target.capture_id || captureId, operation_id: target.operation_id, report_formats: target.reports.map((report) => report.format) })
      if (token !== generation.current) return
      startedJob(started)
      if (started.state === 'COMPLETE') await refreshStatus(token)
    } catch (cause) {
      if (token === generation.current) setError(message(cause, 'Final 복사를 다시 시작하지 못했습니다.'))
    } finally {
      if (token === generation.current) setBusy(false)
    }
  }

  const current = status?.selected_case_latest
  const currentCaptureMismatch = Boolean(current && captureId && current.capture_id !== captureId)
  const unverified = status && status.unverified_records > 0 ? `확정 이력 ${status.unverified_records}건의 무결성을 확인하지 못했습니다. 기존 파일은 보존되어 있습니다.` : ''
  const missing = [current?.missing.rad_decks ? 'RAD 없음' : '', current?.missing.inc_decks ? 'INC 없음' : ''].filter(Boolean)
  const requestLatest = status?.latest && status.latest.case_id !== caseId ? `의뢰 최근 확정: ${status.latest.case_label} · ${formatDate(status.latest.confirmed_at)}` : ''
  const unfinished = status?.retryable_operations?.length ? `완료되지 않은 Final 지정 ${status.retryable_operations.length}건 (다시 지정하면 새로 만듭니다)` : ''
  const copying = isRunning(job) ? job : null
  // A failure is worth showing until a newer Final of this Case completes.
  const failed = job?.state === 'FAILED' && (!current || String(job.queued_at ?? '') > String(current.queued_at ?? current.confirmed_at ?? '')) ? job : null
  const dialogJob = preview && job?.operation_id === preview.operation_id ? job : null
  const badgeText = copying ? jobLabel(copying) : failed ? 'Final 복사 실패' : current ? (currentCaptureMismatch ? '이전 결과로 확정' : '확정 완료') : !hasCapturedCase ? '결과 없음' : status ? '미확정' : error ? '확인 필요' : '확인 중'
  const copyTitle = copying ? `Final ${copying.operation_id.slice(0, 8)} · 파일 ${copying.files_done}/${copying.files_total} · ${formatBytes(copying.bytes_done)} / ${formatBytes(copying.bytes_total)}${copying.current_file ? `\n현재: ${copying.current_file}` : ''}` : failed ? `Final ${failed.operation_id.slice(0, 8)} 실패: ${failed.error?.message ?? ''}` : ''
  const badgeTitle = [copyTitle, current ? `확정 ${current.operation_id.slice(0, 8)} · ${current.files.length}개 파일${current.reports?.length ? ` · 보고서 ${current.reports.length}개` : ''} · ${formatDate(current.confirmed_at)}` : '', missing.join(' · '), requestLatest, unfinished, unverified].filter(Boolean).join('\n') || undefined

  const sourceCount = preview ? new Set(preview.scene_sources.map((item) => item.source_capture_id)).size : 0
  const reportRange = !scopeAtOpen ? '' : scopeAtOpen.source.kind === 'case_usage' ? '사용환경 Case 전체 · 다섯 평가 종합 · 결과 이미지·영상' : 'Case 전체 · 모든 Run Case · Run Option (결과가 없는 항목은 결과 없음으로 표시)'
  const sceneDocs = preview?.counts.scene_reports ?? 0
  const excludedScenes = preview?.excluded_scenes ?? []
  const dialogCopying = isRunning(dialogJob) ? dialogJob : null
  const dialogFailed = dialogJob?.state === 'FAILED' ? dialogJob : null
  const confirmDisabled = !canFinalize || busy || Boolean(dialogCopying) || !preview?.can_confirm || !scopeAtOpen || !chosen.length
  const listedFiles = preview ? preview.files.slice(0, LISTED_FILES) : []

  return <>
    <div className="case-finalization" role="group" aria-label="Case 최종확정">
      <span className={`case-finalization__status${current && !copying && !failed ? ' case-finalization__status--complete' : ''}${copying ? ' case-finalization__status--copying' : ''}${failed ? ' case-finalization__status--failed' : ''}${!copying && !failed && (currentCaptureMismatch || missing.length || unverified) ? ' case-finalization__status--mismatch' : ''}`} title={badgeTitle} data-testid="case-final-status" role={copying ? 'status' : undefined}>
        {copying ? <LoaderCircle size={14} className="case-finalization__spinner" aria-hidden="true" /> : failed ? <AlertTriangle size={13} aria-hidden="true" /> : current ? <CheckCircle2 size={14} aria-hidden="true" /> : null}{badgeText}{!copying && !failed && (missing.length || unverified) ? <AlertTriangle size={13} aria-label="확인 필요 항목 있음" /> : null}
      </span>
      {current?.verification === 'SIZE' && !copying && !failed ? <span className="case-finalization__missing-inline" data-testid="case-final-size-only" title="Final 파일이 커서 이번 조회에서는 존재와 크기만 확인했습니다. 완료 뒤 파일이 바뀌어 내용(해시) 확인 기록과 맞지 않습니다.">크기만 확인</span> : null}
      {current?.verification === 'STAT_SINCE_COMPLETION' && !copying && !failed ? <span className="case-finalization__missing-inline" data-testid="case-final-stat-only" title="Final 파일이 커서 이번 조회에서는 내용(해시)을 다시 읽지 않았습니다. 완료 때 해시를 확인한 뒤 크기·수정 시각이 바뀌지 않았음만 확인했으며, 내용이 같다는 증명은 아닙니다.">완료 후 변경 없음(크기·시각 확인)</span> : null}
      {failed && !dialogOpen ? <button type="button" className="case-finalization__retry" disabled={!canFinalize || busy} title={failed.error?.message} onClick={() => void retryJob(failed)}><RotateCcw size={13} aria-hidden="true" />재시도</button> : null}
      <button type="button" className="case-finalization__trigger" title={canFinalize ? undefined : 'Final 지정 권한이 있는 사용자만 실행할 수 있습니다.'} disabled={!canFinalize || busy || !captureId || !caseId} onClick={() => void makePreview()}>
        {busy && !dialogOpen ? <LoaderCircle size={15} className="case-finalization__spinner" /> : <FileArchive size={15} aria-hidden="true" />}
        Final 지정
      </button>
      {error && <span className="case-finalization__message case-finalization__message--error" role="alert" title={error}><AlertTriangle size={14} />{error}</span>}
      {notice && <span className="case-finalization__message" role="status" title={notice}><CheckCircle2 size={14} />{notice}</span>}
    </div>
    {preview && <dialog ref={dialogRef} className="case-finalization__dialog" aria-labelledby="case-finalization-dialog-title" onCancel={(event) => { event.preventDefault(); closeDialog() }}>
      <header className="case-finalization__dialog-heading">
        <div><h3 id="case-finalization-dialog-title">{result ? 'Final 지정 완료' : 'Final 지정 확인'}</h3><p title={preview.case_path}>{preview.case_label}</p></div>
        <button type="button" className="case-finalization__close" aria-label="닫기" disabled={busy} onClick={closeDialog}>×</button>
      </header>
      {result ? <div className="case-finalization__dialog-body">
        <p className="case-finalization__done"><CheckCircle2 size={16} aria-hidden="true" />CAE {result.files.length}개 파일과 보고서 {result.reports.length}개를 저장했습니다.</p>
        <section className="case-finalization__section" aria-label="저장 위치">
          <h4>저장 위치</h4>
          <ul className="case-finalization__paths">
            <li><span className="case-finalization__file-kind case-finalization__file-kind--cae">CAE</span><code title={result.output_paths.CAE}>{result.output_paths.CAE}</code></li>
            {result.reports.map((report) => <li key={report.format}><span className="case-finalization__file-kind">{report.format.toUpperCase()}</span><code title={report.relative_path}>{report.relative_path}</code><span className="case-finalization__file-size">{formatBytes(report.size)}</span></li>)}
          </ul>
        </section>
        {skippedVideos.length > 0 && <p className="case-finalization__missing">HTML에 넣지 못한 영상 {skippedVideos.length}개(파일 이름으로 표시): {skippedVideos.join(', ')}</p>}
        {skippedImages.length > 0 && <p className="case-finalization__missing">보고서에 넣지 못한 이미지 {skippedImages.length}개: {skippedImages.join(', ')}</p>}
      </div> : <div className="case-finalization__dialog-body">
        <section className="case-finalization__section" aria-label="기준">
          <h4>기준 <span>최신 결과 · Scene {preview.scene_sources.length}개{sourceCount > 1 ? ` · 결과 버전 ${sourceCount}개` : ''}</span></h4>
          <ul className="case-finalization__scenes" aria-label="Scene 기준">
            {preview.scene_sources.map((item) => <li key={item.scene_path} title={item.scene_path}>{lastSegment(item.scene_path)}</li>)}
          </ul>
          {excludedScenes.length > 0 && <div className="case-finalization__excluded" data-testid="case-final-excluded-scenes">
            <p className="case-finalization__missing">제외되는 Scene {excludedScenes.length}개 (이전 결과로 대신하지 않습니다)</p>
            <ul className="case-finalization__scenes" aria-label="제외 Scene">
              {excludedScenes.map((item) => <li key={item.scene_path} title={`${item.scene_path}\n${EXCLUDED_REASON[item.reason] ?? item.reason}`}>{lastSegment(item.scene_path)} · {EXCLUDED_REASON[item.reason] ?? item.reason}</li>)}
            </ul>
          </div>}
        </section>
        <section className="case-finalization__section" aria-label="Final/CAE">
          <h4>Final/CAE <span>Scene 폴더 파일 {preview.files.length}개{preview.counts.total_bytes !== undefined ? ` · ${formatBytes(preview.counts.total_bytes)}` : ''}</span></h4>
          <div className="case-finalization__counts">
            <span>RAD {preview.counts.rad_decks}</span>
            <span>INC {preview.counts.inc_decks}</span>
            <span>결과 {preview.counts.results}</span>
            {sceneDocs ? <span>Scene 문서 {sceneDocs}</span> : null}
            {preview.counts.other_files ? <span>기타 {preview.counts.other_files}</span> : null}
          </div>
          <p className="case-finalization__hint">임시·잠금 파일(~$*, *.tmp)과 숨김·시스템 항목은 복사하지 않습니다.</p>
          {preview.disk && !preview.disk.sufficient && <p className="case-finalization__missing" role="note">Final 드라이브 남은 공간이 부족합니다(필요 {formatBytes(preview.disk.required_bytes + preview.disk.margin_bytes)}{preview.disk.free_bytes !== null ? ` · 남은 공간 ${formatBytes(preview.disk.free_bytes)}` : ''}).</p>}
          {preview.include_unchecked?.length ? <p className="case-finalization__hint" title={preview.include_unchecked.join('\n')}>크기가 커서 include 참조를 확인하지 않은 덱 {preview.include_unchecked.length}개 (파일은 복사합니다)</p> : null}
          {preview.missing.rad_decks && <p className="case-finalization__missing">.rad 입력 덱이 없습니다.</p>}
          {preview.missing.inc_decks && <p className="case-finalization__missing">.inc 입력 덱이 없습니다.</p>}
          {preview.excluded_capture_file_count > 0 && <p className="case-finalization__hint">현재 확정 범위 밖의 결과 파일 {preview.excluded_capture_file_count}개는 제외됩니다.</p>}
          {preview.files.length > 0 ? <details className="case-finalization__file-list">
            <summary>파일 {preview.files.length}개 보기</summary>
            <ul className="case-finalization__files">
              {listedFiles.map((file) => <li key={file.case_relative_path}>
                <span className="case-finalization__source-basis">{file.source_basis === 'CURRENT_CONFIRMED_SCENE' ? 'Scene 파일' : '결과'}</span>
                <span className="case-finalization__file-path" title={file.source_relative_path}>{file.case_relative_path}</span>
                <span className="case-finalization__file-size">{formatBytes(file.size)}</span>
              </li>)}
              {preview.files.length > listedFiles.length ? <li className="case-finalization__hint">외 {preview.files.length - listedFiles.length}개</li> : null}
            </ul>
          </details> : <p className="case-finalization__empty">확정할 파일이 없습니다.</p>}
        </section>
        <section className="case-finalization__section" aria-label="Final/Report">
          <h4>Final/Report <span>하나 이상 선택</span></h4>
          {scopeAtOpen ? <p className="case-finalization__hint" title={reportRange} data-testid="case-final-report-range">보고서 범위: {reportRange}</p> : <p className="case-finalization__missing">이 Case에는 보고서를 만들 결과가 없습니다.</p>}
          <fieldset className="case-finalization__formats" disabled={busy || Boolean(dialogCopying) || !scopeAtOpen}>
            <legend className="case-sr-only">보고서 형식</legend>
            <label className="case-finalization__format"><input type="checkbox" checked={formats.pptx} onChange={(event) => setFormats((value) => ({ ...value, pptx: event.target.checked }))} />PPTX<code title={preview.report_paths.pptx}>{preview.report_files.pptx}</code></label>
            <label className="case-finalization__format"><input type="checkbox" checked={formats.html} onChange={(event) => setFormats((value) => ({ ...value, html: event.target.checked }))} />HTML<code title={preview.report_paths.html}>{preview.report_files.html}</code></label>
            <label className="case-finalization__format case-finalization__format--sub" title="이미지 전체 300MB, 영상당 20MB·전체 200MB까지 넣습니다(원본 크기 기준). HTML은 base64로 약 1.33배 커집니다."><input type="checkbox" checked={includeVideos} disabled={!formats.html} onChange={(event) => setIncludeVideos(event.target.checked)} />영상 포함</label>
          </fieldset>
          {!chosen.length && <p className="case-finalization__missing" role="note">PPTX 또는 HTML을 하나 이상 고르세요.</p>}
        </section>
        {skippedVideos.length > 0 && <p className="case-finalization__missing">HTML에 넣지 못한 영상 {skippedVideos.length}개: {skippedVideos.join(', ')}</p>}
        {skippedImages.length > 0 && <p className="case-finalization__missing">보고서에 넣지 못한 이미지 {skippedImages.length}개: {skippedImages.join(', ')}</p>}
        {dialogJob && dialogJob.state !== 'COMPLETE' && <section className="case-finalization__section case-finalization__job" aria-label="Final 복사 진행" data-testid="case-final-job">
          <h4>{dialogFailed ? 'Final 복사 실패' : jobLabel(dialogJob)} <span>Final ID {dialogJob.operation_id.slice(0, 8)}</span></h4>
          <div className="case-finalization__bar" role="progressbar" aria-label="Final 복사 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={jobPercent(dialogJob)}><span style={{ width: `${jobPercent(dialogJob)}%` }} /></div>
          <p className="case-finalization__job-counts">파일 {dialogJob.files_done}/{dialogJob.files_total} · {formatBytes(dialogJob.bytes_done)} / {formatBytes(dialogJob.bytes_total)}</p>
          {dialogJob.current_file && dialogCopying ? <p className="case-finalization__job-file" title={dialogJob.current_file}>현재: {dialogJob.current_file}</p> : null}
          {dialogCopying ? <p className="case-finalization__hint">창을 닫아도 복사는 계속됩니다. 완료 전에는 Final 폴더에 나타나지 않습니다.</p> : null}
        </section>}
        {dialogError && <p className="case-finalization__dialog-error" role="alert"><AlertTriangle size={14} aria-hidden="true" />{dialogError}</p>}
      </div>}
      <footer className="case-finalization__dialog-actions">
        {phase !== 'idle' && <span className="case-finalization__progress" role="status"><LoaderCircle size={14} className="case-finalization__spinner" aria-hidden="true" />{PHASE_TEXT[phase]}</span>}
        {result ? <button type="button" className="case-finalization__confirm-button" onClick={closeDialog}>닫기</button> : dialogCopying ? <button type="button" className="case-finalization__cancel" onClick={closeDialog}>닫기 (복사 계속)</button> : <>
          <button type="button" className="case-finalization__cancel" disabled={busy} onClick={closeDialog}>취소</button>
          <button type="button" className="case-finalization__confirm-button" disabled={confirmDisabled} onClick={() => void confirmFinal()}>
            {busy ? <LoaderCircle size={15} className="case-finalization__spinner" /> : dialogError || dialogFailed ? <RotateCcw size={15} aria-hidden="true" /> : <FileArchive size={15} aria-hidden="true" />}
            {dialogError || dialogFailed ? '재시도' : 'Final 지정 확정'}
          </button>
        </>}
      </footer>
    </dialog>}
  </>
}
