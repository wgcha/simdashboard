import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, FileArchive, LoaderCircle } from 'lucide-react'
import {
  caseFinalizationApi,
  type CaseFinalizationInput,
  type CaseFinalizationPreview,
  type CaseFinalizationRecord,
  type CaseFinalizationReportFormat,
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

type Phase = 'idle' | 'building' | 'uploading' | 'copying'

const FORMATS: CaseFinalizationReportFormat[] = ['pptx', 'html']
const PHASE_TEXT: Record<Exclude<Phase, 'idle'>, string> = { building: '보고서 만드는 중…', uploading: '보고서 올리는 중…', copying: '파일 복사 중…' }

const formatBytes = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const formatDate = (value?: string | null) => {
  if (!value) return '시간 정보 없음'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '시간 정보 없음' : date.toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' })
}

const lastSegment = (path: string) => path.split('/').filter(Boolean).pop() ?? path
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
      if (token === generation.current) setStatus(next)
    } catch (cause) {
      if (token === generation.current) setError(message(cause, '최종확정 상태를 불러오지 못했습니다.'))
    }
  }, [projectId, requestId, environment, caseId, hasCapturedCase])

  useEffect(() => {
    const token = ++generation.current
    setStatus(null); setPreview(null); setResult(null); setError(''); setDialogError(''); setNotice('')
    setDialogOpen(false); setBusy(false); setPhase('idle')
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
      setPhase('copying')
      const record = await caseFinalizationApi.confirm({ ...operation, report_formats: chosen })
      if (token !== generation.current) return
      setResult(record)
      setNotice(`Final 지정 완료 · CAE ${record.files.length}개 · 보고서 ${record.reports.length}개`)
      await refreshStatus(token)
    } catch (cause) {
      if (token === generation.current) setDialogError(message(cause, 'Final 지정을 완료하지 못했습니다. 같은 요청으로 다시 시도할 수 있습니다.'))
    } finally {
      if (token === generation.current) { setBusy(false); setPhase('idle') }
    }
  }

  const current = status?.selected_case_latest
  const currentCaptureMismatch = Boolean(current && captureId && current.capture_id !== captureId)
  const unverified = status && status.unverified_records > 0 ? `확정 이력 ${status.unverified_records}건의 무결성을 확인하지 못했습니다. 기존 파일은 보존되어 있습니다.` : ''
  const missing = [current?.missing.rad_decks ? 'RAD 없음' : '', current?.missing.inc_decks ? 'INC 없음' : ''].filter(Boolean)
  const requestLatest = status?.latest && status.latest.case_id !== caseId ? `의뢰 최근 확정: ${status.latest.case_label} · ${formatDate(status.latest.confirmed_at)}` : ''
  const unfinished = status?.retryable_operations?.length ? `완료되지 않은 Final 지정 ${status.retryable_operations.length}건 (다시 지정하면 새로 만듭니다)` : ''
  const badgeText = current ? (currentCaptureMismatch ? '이전 결과로 확정' : '확정 완료') : !hasCapturedCase ? '결과 없음' : status ? '미확정' : error ? '확인 필요' : '확인 중'
  const badgeTitle = [current ? `확정 ${current.operation_id.slice(0, 8)} · ${current.files.length}개 파일${current.reports?.length ? ` · 보고서 ${current.reports.length}개` : ''} · ${formatDate(current.confirmed_at)}` : '', missing.join(' · '), requestLatest, unfinished, unverified].filter(Boolean).join('\n') || undefined

  const sourceCount = preview ? new Set(preview.scene_sources.map((item) => item.source_capture_id)).size : 0
  const reportRange = !scopeAtOpen ? '' : scopeAtOpen.source.kind === 'case_usage' ? '사용환경 Case 전체 · 다섯 평가 종합 · 결과 이미지·영상' : 'Case 전체 · 모든 Run Case · Run Option (결과가 없는 항목은 결과 없음으로 표시)'
  const sceneDocs = preview?.counts.scene_reports ?? 0
  const confirmDisabled = !canFinalize || busy || !preview?.can_confirm || !scopeAtOpen || !chosen.length

  return <>
    <div className="case-finalization" role="group" aria-label="Case 최종확정">
      <span className={`case-finalization__status${current ? ' case-finalization__status--complete' : ''}${currentCaptureMismatch || missing.length || unverified ? ' case-finalization__status--mismatch' : ''}`} title={badgeTitle}>
        {current ? <CheckCircle2 size={14} aria-hidden="true" /> : null}{badgeText}{missing.length || unverified ? <AlertTriangle size={13} aria-label="확인 필요 항목 있음" /> : null}
      </span>
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
        </section>
        <section className="case-finalization__section" aria-label="Final/CAE">
          <h4>Final/CAE <span>입력·결과 {preview.files.length}개</span></h4>
          <div className="case-finalization__counts">
            <span>RAD {preview.counts.rad_decks}</span>
            <span>INC {preview.counts.inc_decks}</span>
            <span>결과 {preview.counts.results}</span>
            {sceneDocs ? <span>Scene 문서 {sceneDocs}</span> : null}
          </div>
          {preview.missing.rad_decks && <p className="case-finalization__missing">.rad 입력 덱이 없습니다.</p>}
          {preview.missing.inc_decks && <p className="case-finalization__missing">.inc 입력 덱이 없습니다.</p>}
          {preview.excluded_capture_file_count > 0 && <p className="case-finalization__hint">현재 확정 범위 밖의 결과 파일 {preview.excluded_capture_file_count}개는 제외됩니다.</p>}
          {preview.files.length > 0 ? <details className="case-finalization__file-list">
            <summary>파일 {preview.files.length}개 보기</summary>
            <ul className="case-finalization__files">
              {preview.files.map((file) => <li key={file.case_relative_path}>
                <span className="case-finalization__source-basis">{file.source_basis === 'CURRENT_CONFIRMED_SCENE' ? 'Scene 파일' : '결과'}</span>
                <span className="case-finalization__file-path" title={file.source_relative_path}>{file.case_relative_path}</span>
                <span className="case-finalization__file-size">{formatBytes(file.size)}</span>
              </li>)}
            </ul>
          </details> : <p className="case-finalization__empty">확정할 파일이 없습니다.</p>}
        </section>
        <section className="case-finalization__section" aria-label="Final/Reports">
          <h4>Final/Reports <span>하나 이상 선택</span></h4>
          {scopeAtOpen ? <p className="case-finalization__hint" title={reportRange} data-testid="case-final-report-range">보고서 범위: {reportRange}</p> : <p className="case-finalization__missing">이 Case에는 보고서를 만들 결과가 없습니다.</p>}
          <fieldset className="case-finalization__formats" disabled={busy || !scopeAtOpen}>
            <legend className="case-sr-only">보고서 형식</legend>
            <label className="case-finalization__format"><input type="checkbox" checked={formats.pptx} onChange={(event) => setFormats((value) => ({ ...value, pptx: event.target.checked }))} />PPTX<code title={preview.report_paths.pptx}>{preview.report_files.pptx}</code></label>
            <label className="case-finalization__format"><input type="checkbox" checked={formats.html} onChange={(event) => setFormats((value) => ({ ...value, html: event.target.checked }))} />HTML<code title={preview.report_paths.html}>{preview.report_files.html}</code></label>
            <label className="case-finalization__format case-finalization__format--sub" title="이미지 전체 300MB, 영상당 20MB·전체 200MB까지 넣습니다(원본 크기 기준). HTML은 base64로 약 1.33배 커집니다."><input type="checkbox" checked={includeVideos} disabled={!formats.html} onChange={(event) => setIncludeVideos(event.target.checked)} />영상 포함</label>
          </fieldset>
          {!chosen.length && <p className="case-finalization__missing" role="note">PPTX 또는 HTML을 하나 이상 고르세요.</p>}
        </section>
        {skippedVideos.length > 0 && <p className="case-finalization__missing">HTML에 넣지 못한 영상 {skippedVideos.length}개: {skippedVideos.join(', ')}</p>}
        {skippedImages.length > 0 && <p className="case-finalization__missing">보고서에 넣지 못한 이미지 {skippedImages.length}개: {skippedImages.join(', ')}</p>}
        {dialogError && <p className="case-finalization__dialog-error" role="alert"><AlertTriangle size={14} aria-hidden="true" />{dialogError}</p>}
      </div>}
      <footer className="case-finalization__dialog-actions">
        {phase !== 'idle' && <span className="case-finalization__progress" role="status"><LoaderCircle size={14} className="case-finalization__spinner" aria-hidden="true" />{PHASE_TEXT[phase]}</span>}
        {result ? <button type="button" className="case-finalization__confirm-button" onClick={closeDialog}>닫기</button> : <>
          <button type="button" className="case-finalization__cancel" disabled={busy} onClick={closeDialog}>취소</button>
          <button type="button" className="case-finalization__confirm-button" disabled={confirmDisabled} onClick={() => void confirmFinal()}>
            {busy ? <LoaderCircle size={15} className="case-finalization__spinner" /> : <FileArchive size={15} aria-hidden="true" />}
            {dialogError ? '다시 시도' : 'Final 지정 확정'}
          </button>
        </>}
      </footer>
    </dialog>}
  </>
}
