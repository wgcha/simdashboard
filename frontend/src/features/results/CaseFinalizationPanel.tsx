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
import { CaseReportMetaFields, TemplateNotAppliedNotice } from './caseReport/CaseReportFields'
import { defaultReportLayout, initialReportMeta, rememberReportLayout, usesUploadedTemplate, type CaseReportMeta } from './caseReport/reportPreferences'
import { reportApi } from '../../shared/api/reportLayouts'
import { DRIVE_READ_ONLY_NOTICE } from '../../shared/api/drive'
import { useDriveWriteStatus } from '../../shared/hooks/useDriveWriteStatus'
import type { ReportLayout } from '../../types'
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
  /** W5: each new value opens the Final dialog for this Case (e.g. "이 Case를 Final 지정" in Case 비교). */
  openRequest?: number
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
  const { projectId, requestId, environment, caseId, captureId, hasCapturedCase, canFinalize, reportScope, openRequest = 0 } = props
  // SCX drive mode (plan D2) is read-only: Final designation writes to the SPDM root.
  const driveReadOnly = !useDriveWriteStatus().writesAvailable
  const [status, setStatus] = useState<CaseFinalizationStatus | null>(null)
  const [preview, setPreview] = useState<CaseFinalizationPreview | null>(null)
  const [scopeAtOpen, setScopeAtOpen] = useState<CaseReportFinalScope | null>(null)
  const [formats, setFormats] = useState<Record<CaseFinalizationReportFormat, boolean>>({ pptx: true, html: false })
  // W3: re-designating while another Case is the current Final needs an explicit acknowledgement.
  const [replaceAcknowledged, setReplaceAcknowledged] = useState(false)
  // W7: Final reports use the chosen layout (default: last used in the 보고서 dialog) and report information.
  const [layouts, setLayouts] = useState<ReportLayout[]>([])
  const [layoutId, setLayoutId] = useState<string | null>(null)
  const [meta, setMeta] = useState<CaseReportMeta>(initialReportMeta)
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

  // Declared after the reset above so an open request on mount survives it.
  useEffect(() => {
    if (openRequest && canFinalize && caseId && captureId) void makePreview()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openRequest])

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
    setBusy(true); setError(''); setNotice(''); setDialogError(''); setResult(null); setSkippedVideos([]); setSkippedImages([]); setReplaceAcknowledged(false)
    try {
      const next = await caseFinalizationApi.preview(input)
      if (token !== generation.current) return
      built.current = null
      const storedLayouts = await reportApi.layouts().catch(() => [] as ReportLayout[])
      if (token !== generation.current) return
      setLayouts(storedLayouts)
      setLayoutId(defaultReportLayout(storedLayouts, requestId)?.id ?? null)
      setMeta((current) => current.author ? current : initialReportMeta())
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
      const key = `${preview.operation_id}|${chosen.join(',')}|${formats.html && includeVideos ? 'video' : ''}|${layoutId ?? ''}|${JSON.stringify(meta)}`
      if (built.current?.key !== key) {
        setPhase('building')
        built.current = { key, reports: await buildFinalReports(scopeAtOpen, { formats: chosen, includeVideos: formats.html && includeVideos, layoutId, meta }) }
        if (formats.pptx && layoutId) rememberReportLayout(requestId, layoutId)
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

  const repairSummary = async (override = false) => {
    if (override && !window.confirm('관리자 강제 갱신: 앱이 만들지 않은 요약 파일을 바꾸거나, 사라진 현재 Final 대신 남아 있는 가장 최근 Final을 현재로 지정합니다. 계속할까요?')) return
    const token = generation.current
    setBusy(true); setError('')
    try {
      const next = await caseFinalizationApi.repairSummary({ project_id: projectId, request_id: requestId, environment, case_id: caseId, override })
      if (token === generation.current) { setStatus(next); setNotice('Final 요약 파일을 갱신했습니다.') }
    } catch (cause) {
      if (token === generation.current) setError(message(cause, 'Final 요약 파일을 갱신하지 못했습니다.'))
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
  const isPrevious = Boolean(current && status?.current_final && status.current_final.operation_id !== current.operation_id)
  const badgeText = copying ? jobLabel(copying) : failed ? 'Final 복사 실패' : current ? (isPrevious ? '이전 Final' : currentCaptureMismatch ? '이전 결과로 확정' : '확정 완료') : !hasCapturedCase ? '결과 없음' : status ? '미확정' : error ? '확인 필요' : '확인 중'
  const copyTitle = copying ? `Final ${copying.operation_id.slice(0, 8)} · 파일 ${copying.files_done}/${copying.files_total} · ${formatBytes(copying.bytes_done)} / ${formatBytes(copying.bytes_total)}${copying.current_file ? `\n현재: ${copying.current_file}` : ''}` : failed ? `Final ${failed.operation_id.slice(0, 8)} 실패: ${failed.error?.message ?? ''}` : ''
  const badgeTitle = [copyTitle, current ? `확정 ${current.operation_id.slice(0, 8)} · ${current.files.length}개 파일${current.reports?.length ? ` · 보고서 ${current.reports.length}개` : ''} · ${formatDate(current.confirmed_at)}` : '', missing.join(' · '), requestLatest, unfinished, unverified].filter(Boolean).join('\n') || undefined

  const sourceCount = preview ? new Set(preview.scene_sources.map((item) => item.source_capture_id)).size : 0
  const reportRange = !scopeAtOpen ? '' : scopeAtOpen.source.kind === 'case_usage' ? '사용환경 Case 전체 · 다섯 평가 종합 · 결과 이미지·영상' : 'Case 전체 · 모든 Run Case · Run Option (결과가 없는 항목은 결과 없음으로 표시)'
  const sceneDocs = preview?.counts.scene_reports ?? 0
  const excludedScenes = preview?.excluded_scenes ?? []
  const dialogCopying = isRunning(dialogJob) ? dialogJob : null
  const dialogFailed = dialogJob?.state === 'FAILED' ? dialogJob : null
  const currentFinal = status?.current_final ?? null
  const history = status?.final_history ?? []
  const replacesOtherCase = Boolean(currentFinal && currentFinal.case_id !== caseId)
  const summaryNeedsRepair = Boolean(currentFinal && (status?.summary?.state === 'MISSING' || status?.summary?.state === 'STALE'))
  const confirmDisabled = !canFinalize || busy || Boolean(dialogCopying) || !preview?.can_confirm || !scopeAtOpen || !chosen.length || (replacesOtherCase && !replaceAcknowledged && !result)
  const listedFiles = preview ? preview.files.slice(0, LISTED_FILES) : []

  return <>
    <div className="case-finalization" role="group" aria-label="Case 최종확정">
      <span className={`case-finalization__status${current && !copying && !failed ? ' case-finalization__status--complete' : ''}${copying ? ' case-finalization__status--copying' : ''}${failed ? ' case-finalization__status--failed' : ''}${!copying && !failed && (currentCaptureMismatch || missing.length || unverified) ? ' case-finalization__status--mismatch' : ''}`} title={badgeTitle} data-testid="case-final-status" role={copying ? 'status' : undefined}>
        {copying ? <LoaderCircle size={14} className="case-finalization__spinner" aria-hidden="true" /> : failed ? <AlertTriangle size={13} aria-hidden="true" /> : current ? <CheckCircle2 size={14} aria-hidden="true" /> : null}{badgeText}{!copying && !failed && (missing.length || unverified) ? <AlertTriangle size={13} aria-label="확인 필요 항목 있음" /> : null}
      </span>
      {current?.verification === 'SIZE' && !copying && !failed ? <span className="case-finalization__missing-inline" data-testid="case-final-size-only" title="Final 파일이 커서 이번 조회에서는 존재와 크기만 확인했습니다. 완료 뒤 파일이 바뀌어 내용(해시) 확인 기록과 맞지 않습니다.">크기만 확인</span> : null}
      {current?.verification === 'STAT_SINCE_COMPLETION' && !copying && !failed ? <span className="case-finalization__missing-inline" data-testid="case-final-stat-only" title="Final 파일이 커서 이번 조회에서는 내용(해시)을 다시 읽지 않았습니다. 완료 때 해시를 확인한 뒤 크기·수정 시각이 바뀌지 않았음만 확인했으며, 내용이 같다는 증명은 아닙니다.">완료 후 변경 없음(크기·시각 확인)</span> : null}
      {status?.current_final ? <span className="case-finalization__current" data-testid="case-final-current" title={`현재 Final ${status.current_final.operation_id}\n${status.current_final.case_path}`}>현재 Final · {status.current_final.case_label} · {status.current_final.designated_by ?? '지정자 없음'} · {formatDate(status.current_final.designated_at)}</span> : null}
      {currentFinal && currentFinal.verified === false ? <span className="case-finalization__message case-finalization__message--error" role="alert" data-testid="case-final-current-unverified">
        <AlertTriangle size={14} aria-hidden="true" />{currentFinal.missing ? '현재 Final의 기록이나 파일을 찾을 수 없습니다. 관리자에게 문의하세요.' : '현재 Final 파일이 완료 기록과 다릅니다(해시 불일치). 관리자에게 문의하세요.'}
      </span> : null}
      {status?.summary?.state === 'CONFLICT' ? <span className="case-finalization__missing-inline" data-testid="case-final-summary-conflict" title={`${status.summary.path}에 앱이 만들지 않은 파일(또는 바로가기)이 있어 덮어쓰지 않았습니다. 관리자가 확인한 뒤 정리하거나 갱신해야 합니다.`}>요약 파일 충돌(관리자 확인)</span> : null}
      {(status?.summary?.state === 'CONFLICT' || status?.summary?.state === 'CURRENT_MISSING') && status?.can_override_summary ? <button type="button" className="case-finalization__retry" data-testid="case-final-summary-override" disabled={busy || driveReadOnly} onClick={() => void repairSummary(true)}><RotateCcw size={13} aria-hidden="true" />강제 갱신(관리자)</button> : null}
      {summaryNeedsRepair ? <span className="case-finalization__missing-inline" data-testid="case-final-summary-repair" title={`${status?.summary?.path ?? 'Final/current.json'}이(가) 현재 Final을 가리키지 않습니다.`}>요약 파일 갱신 필요</span> : null}
      {summaryNeedsRepair && canFinalize ? <button type="button" className="case-finalization__retry" disabled={busy || driveReadOnly} onClick={() => void repairSummary()}><RotateCcw size={13} aria-hidden="true" />요약 파일 갱신</button> : null}
      {failed && !dialogOpen ? <button type="button" className="case-finalization__retry" disabled={driveReadOnly || !canFinalize || busy} title={failed.error?.message} onClick={() => void retryJob(failed)}><RotateCcw size={13} aria-hidden="true" />재시도</button> : null}
      {driveReadOnly ? <span className="case-finalization__missing-inline" data-testid="case-final-drive-read-only" title={DRIVE_READ_ONLY_NOTICE}>드라이브 읽기 전용</span> : null}
      <button type="button" className="case-finalization__trigger" title={driveReadOnly ? DRIVE_READ_ONLY_NOTICE : canFinalize ? undefined : 'Final 지정 권한이 있는 사용자만 실행할 수 있습니다.'} disabled={driveReadOnly || !canFinalize || busy || !captureId || !caseId} onClick={() => void makePreview()}>
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
        {currentFinal ? <section className="case-finalization__section" aria-label="현재 Final" data-testid="case-final-replace">
          {replacesOtherCase
            ? <label className="case-finalization__format"><input type="checkbox" checked={replaceAcknowledged} disabled={busy || Boolean(dialogCopying)} onChange={(event) => setReplaceAcknowledged(event.target.checked)} />현재 Final({currentFinal.case_label})을 이전 Final로 바꾸고 이 Case를 Final로 지정합니다</label>
            : <p className="case-finalization__hint">이 Case의 현재 Final({formatDate(currentFinal.designated_at)})은 이전 Final이 되고 새 Final이 현재 Final이 됩니다.</p>}
          <p className="case-finalization__hint">이전 Final 폴더와 파일은 그대로 남습니다.</p>
          {history.length ? <details className="case-finalization__file-list">
            <summary>Final 이력 {history.length}건</summary>
            <ul className="case-finalization__files" aria-label="Final 이력">
              {history.map((item) => <li key={item.operation_id}>
                <span className="case-finalization__source-basis">{item.role === 'CURRENT' ? '현재' : '이전'}</span>
                <span className="case-finalization__file-path" title={item.operation_id}>{item.case_label} · {item.designated_by ?? '-'}</span>
                <span className="case-finalization__file-size">{formatDate(item.designated_at)}</span>
              </li>)}
            </ul>
          </details> : null}
        </section> : null}
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
          {formats.pptx && layouts.length ? <div className="case-report__layout" data-testid="case-final-layout">
            <label><span>PPTX 레이아웃</span><select value={layoutId ?? ''} disabled={busy || Boolean(dialogCopying)} onChange={(event) => setLayoutId(event.target.value)}>{layouts.map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.version}</option>)}</select></label>
            {usesUploadedTemplate(layouts.find((item) => item.id === layoutId)?.definition) ? <TemplateNotAppliedNotice /> : null}
          </div> : null}
          <CaseReportMetaFields value={meta} disabled={busy || Boolean(dialogCopying) || !scopeAtOpen} onChange={setMeta} />
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
