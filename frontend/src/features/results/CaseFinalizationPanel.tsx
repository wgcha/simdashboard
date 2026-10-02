import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, Copy, FileArchive, LoaderCircle, RotateCcw } from 'lucide-react'
import {
  caseFinalizationApi,
  type CaseFinalizationInput,
  type CaseFinalizationPreview,
  type CaseFinalizationStatus,
} from '../../shared/api/caseFinalization'
import type { DashboardEnvironment } from '../../shared/api/simulationDashboard'
import './CaseFinalizationPanel.css'

type Props = {
  projectId: string
  requestId: string
  environment: DashboardEnvironment
  caseId: string
  captureId: string
  hasCapturedCase: boolean
  canFinalize: boolean
}

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

function finalizationInput(props: Props): CaseFinalizationInput {
  return {
    project_id: props.projectId,
    request_id: props.requestId,
    environment: props.environment,
    case_id: props.caseId,
    capture_id: props.captureId,
  }
}

export function CaseFinalizationPanel(props: Props) {
  const { projectId, requestId, environment, caseId, captureId, hasCapturedCase, canFinalize } = props
  const [status, setStatus] = useState<CaseFinalizationStatus | null>(null)
  const [preview, setPreview] = useState<CaseFinalizationPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [dialogOpen, setDialogOpen] = useState(false)
  const generation = useRef(0)
  const dialogRef = useRef<HTMLDialogElement>(null)
  const input = useMemo(() => finalizationInput(props), [projectId, requestId, environment, caseId, captureId])

  const refreshStatus = useCallback(async (token = generation.current) => {
    if (!projectId || !requestId || !caseId || !hasCapturedCase) {
      if (token === generation.current) setStatus(null)
      return
    }
    try {
      const next = await caseFinalizationApi.status({ project_id: projectId, request_id: requestId, environment, case_id: caseId })
      if (token === generation.current) setStatus(next)
    } catch (cause) {
      if (token === generation.current) setError(cause instanceof Error ? cause.message : '최종확정 상태를 불러오지 못했습니다.')
    }
  }, [projectId, requestId, environment, caseId, hasCapturedCase])

  useEffect(() => {
    const token = ++generation.current
    setStatus(null)
    setPreview(null)
    setError('')
    setNotice('')
    setDialogOpen(false)
    setBusy(false)
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

  const makePreview = async () => {
    const token = generation.current
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const next = await caseFinalizationApi.preview(input)
      if (token !== generation.current) return
      setPreview(next)
      setDialogOpen(true)
      if (!next.can_confirm) setNotice('확정할 입력 덱 또는 결과 파일이 없습니다.')
    } catch (cause) {
      if (token === generation.current) setError(cause instanceof Error ? cause.message : '최종확정 미리보기를 만들지 못했습니다.')
    } finally {
      if (token === generation.current) setBusy(false)
    }
  }

  const confirmOperation = async (operationId: string) => {
    const token = generation.current
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const result = await caseFinalizationApi.confirm({ ...input, operation_id: operationId })
      if (token !== generation.current) return
      setPreview(null)
      setDialogOpen(false)
      setNotice(`Final 지정 완료 · ${result.files.length}개 파일`)
      await refreshStatus(token)
    } catch (cause) {
      if (token === generation.current) setError(cause instanceof Error ? cause.message : '최종확정을 완료하지 못했습니다. 같은 요청으로 다시 시도할 수 있습니다.')
    } finally {
      if (token === generation.current) setBusy(false)
    }
  }

  const current = status?.selected_case_latest
  const currentCaptureMismatch = Boolean(current && captureId && current.capture_id !== captureId)
  const retryable = captureId ? status?.retryable_operations?.find((item) => item.capture_id === captureId) : undefined

  const unverified = status && status.unverified_records > 0 ? `확정 이력 ${status.unverified_records}건의 무결성을 확인하지 못했습니다. 기존 파일은 보존되어 있습니다.` : ''
  const missing = [current?.missing.rad_decks ? 'RAD 없음' : '', current?.missing.inc_decks ? 'INC 없음' : '', current?.missing.reports ? '보고서 없음' : ''].filter(Boolean)
  const requestLatest = status?.latest && status.latest.case_id !== caseId ? `의뢰 최근 확정: ${status.latest.case_label} · ${formatDate(status.latest.confirmed_at)}` : ''
  const badgeText = current ? (currentCaptureMismatch ? '이전 결과로 확정' : '확정 완료') : !hasCapturedCase ? '결과 없음' : status ? '미확정' : error ? '확인 필요' : '확인 중'
  const badgeTitle = [current ? `확정 ${current.operation_id.slice(0, 8)} · ${current.files.length}개 파일 · ${formatDate(current.confirmed_at)}` : '', missing.join(' · '), requestLatest, unverified].filter(Boolean).join('\n') || undefined

  return <>
    <div className="case-finalization" role="group" aria-label="Case 최종확정">
      <span className={`case-finalization__status${current ? ' case-finalization__status--complete' : ''}${currentCaptureMismatch || missing.length || unverified ? ' case-finalization__status--mismatch' : ''}`} title={badgeTitle}>
        {current ? <CheckCircle2 size={14} aria-hidden="true" /> : null}{badgeText}{missing.length || unverified ? <AlertTriangle size={13} aria-label="확인 필요 항목 있음" /> : null}
      </span>
      {retryable && <button type="button" className="case-finalization__retry" disabled={!canFinalize || busy} onClick={() => void confirmOperation(retryable.operation_id)}>
        <RotateCcw size={13} aria-hidden="true" /> 재시도
      </button>}
      <button type="button" className="case-finalization__trigger" title={canFinalize ? undefined : 'Final 지정 권한이 있는 사용자만 실행할 수 있습니다.'} disabled={!canFinalize || busy || !captureId || !caseId} onClick={() => void makePreview()}>
        {busy ? <LoaderCircle size={15} className="case-finalization__spinner" /> : <FileArchive size={15} aria-hidden="true" />}
        Final 지정
      </button>
      {error && <span className="case-finalization__message case-finalization__message--error" role="alert" title={error}><AlertTriangle size={14} />{error}</span>}
      {notice && <span className="case-finalization__message" role="status" title={notice}><CheckCircle2 size={14} />{notice}</span>}
    </div>
    {preview && <dialog ref={dialogRef} className="case-finalization__dialog" aria-labelledby="case-finalization-dialog-title" onCancel={(event) => { event.preventDefault(); setDialogOpen(false) }}>
        <header className="case-finalization__dialog-heading">
          <div><h3 id="case-finalization-dialog-title">Final 지정 파일 확인</h3><p>{preview.case_label} · {preview.files.length}개 파일</p></div>
          <button type="button" className="case-finalization__close" aria-label="닫기" onClick={() => setDialogOpen(false)}>×</button>
        </header>
        <div className="case-finalization__dialog-body">
          <div className="case-finalization__counts">
            <span>RAD {preview.counts.rad_decks}개</span>
            <span>INC {preview.counts.inc_decks}개</span>
            <span>결과 {preview.counts.results}개</span>
            <span>보고서 {preview.counts.reports}개</span>
          </div>
          {preview.missing.rad_decks && <p className="case-finalization__missing">확정 Scene에서 .rad 입력 덱을 찾지 못했습니다.</p>}
          {preview.missing.inc_decks && <p className="case-finalization__missing">확정 Scene에서 .inc 입력 덱을 찾지 못했습니다.</p>}
          {preview.missing.reports && <p className="case-finalization__missing">확정 Scene에서 PDF/PPT 보고서를 찾지 못했습니다.</p>}
          {preview.excluded_capture_file_count > 0 && <p className="case-finalization__hint">현재 확정 범위 밖의 캡처 파일 {preview.excluded_capture_file_count}개는 제외됩니다.</p>}
          {preview.files.length > 0 ? <ul className="case-finalization__files">
            {preview.files.map((file) => <li key={`${file.category}:${file.case_relative_path}`}>
              <span className={`case-finalization__file-kind case-finalization__file-kind--${file.category.toLowerCase()}`}>{file.category === 'CAE' ? 'CAE' : /\.(pdf|pptx?|xlsx)$/i.test(file.source_relative_path) ? '보고서' : '결과'}</span>
              <span className="case-finalization__source-basis">{file.source_basis === 'SELECTED_CAPTURE' ? '선택 수집본' : '현재 Scene'}</span>
              <span className="case-finalization__file-path" title={file.source_relative_path}>{file.case_relative_path}</span>
              <span className="case-finalization__file-size">{formatBytes(file.size)}</span>
            </li>)}
          </ul> : <p className="case-finalization__empty">확정할 파일이 없습니다.</p>}
          <p className="case-finalization__destination">저장: Final/CAE/{preview.case_label}/… 및 Final/Reports/{preview.case_label}/…</p>
        </div>
        <footer className="case-finalization__dialog-actions">
          <button type="button" className="case-finalization__cancel" disabled={busy} onClick={() => setDialogOpen(false)}>취소</button>
          <button type="button" className="case-finalization__confirm-button" disabled={!canFinalize || busy || !preview.can_confirm} onClick={() => void confirmOperation(preview.operation_id)}>
            {busy ? <LoaderCircle size={15} className="case-finalization__spinner" /> : <Copy size={15} />}
            확정하고 파일 복사
          </button>
        </footer>
    </dialog>}
  </>
}
