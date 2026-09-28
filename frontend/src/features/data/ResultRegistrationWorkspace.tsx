import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Check, ChevronDown, ChevronRight, ClipboardCheck, Database, FileImage, FileVideo, FolderOpen, LoaderCircle, RefreshCw, Upload, X } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../../api'
import type { AnalysisRequest, Project } from '../../types'
import { SearchableSelect } from '../../shared/components/selectionLabels'
import {
  resultRegistrationApi,
  sha256Hex,
  type ResultEnvironment,
  type ResultFileExclusion,
  type ResultFolderNode,
  type ResultFolderPreparation,
  type ResultInspection,
  type ResultInspectionIssue,
  type ResultRegistrationContext,
  type ResultRegistrationDraftRead,
  type ResultRegistrationPublished,
  type ResultRegistrationTarget,
} from '../../shared/api/resultRegistration'
import './DataWorkspace.css'

export type ResultRegistrationWorkspaceProps = {
  embedded?: boolean
  contextChanging?: boolean
  projects: Project[]
  initialProjectId: string
  initialRequestId?: string
  onContextChange?: (context: { projectId: string; requestId: string }) => void
  onDataChanged: () => Promise<void>
}

type PrepareInput = {
  project_id: string
  request_id: string
  environment: ResultEnvironment
  parent_relative_path?: string
  segments: Array<{ role_kind: string; name: string }>
}

type Completion = {
  published: ResultRegistrationPublished
  draftId: string
  mediaCount: number
  imageCount: number
  videoCount: number
  targetLabel: string
}


const roleLabels: Record<string, string> = {
  SIMULATION_CASE: '해석 Case', EVALUATION: '평가 항목', LOAD_CASE: '하중경우',
  EXECUTION_RUN: 'Run Case', RUN_OPTION: 'Run Option', SCENE: 'Scene', RESULTS: '결과 폴더',
  CONTAINER: '폴더',
}
const evaluationNames = ['Settle', 'Wobble', 'Horizontal_Force_Angle', 'Slope_Angle', 'Slope_Angle_360']
const draftResumeStorageKey = 'result-registration:last-draft'
const statusLabels: Record<string, string> = {
  PRESENT: '폴더 있음', MISSING: '하위 폴더 없음', UNAVAILABLE: '경로 접근 불가',
  READY: '확인 완료', OK: '확인 완료', UPLOADED: '업로드 완료', PENDING: '대기 중',
  INSPECTED: '검사 완료', REVIEW_REQUIRED: '검수 대기', APPROVED: '등록 대기',
  MIRROR_PENDING: 'DB 등록 완료 · 원본 저장 중', PUBLISHED: '등록 완료',
  MIRROR_CONFLICT: '원본 저장 경로 충돌', MIRROR_FAILED: '원본 폴더 저장 실패',
  BINDING_REQUIRED: 'SPDM 연결 필요', DUPLICATE_CANDIDATE: '중복 원본 확인 필요',
  INVALID_SOURCE_SELECTION: '원본 선택 필요', SOURCE_PARSE_ERROR: '원본을 읽을 수 없음',
  ERROR: '오류', VALUE_ERROR: '값 오류', INVALID_VALUE: '값 오류', INVALID_TYPE: '값 형식 오류',
  MISSING_SOURCE: '원본 없음', MISSING_VALUE: '값 누락', MISSING_KEY: '필수 값 없음', NULL: '값 없음',
  INVALID_MAPPING: '평가 기준 확인 필요', EXCLUDED: '제외', UNSUPPORTED: '지원하지 않는 형식',
  UNLINKED: '연결된 항목 없음', ABSENT: '선택된 항목 없음', UNRESOLVED: '선택 정보 확인 필요',
  CONFLICT: '저장 경로 충돌', FAILED: '저장 실패',
}
const issueCodeLabels: Record<string, string> = {
  MISSING_RESULT: '결과 항목의 원본 파일이 없습니다.', FILE_NOT_UPLOADED: '선언한 파일을 아직 업로드하지 않았습니다.',
  FILE_NOT_USED: '결과 항목이나 미디어에 연결되지 않은 파일입니다.', PARTIAL_CAPTURE: '일부 항목이 누락된 결과입니다.',
  SOURCE_PARSE_ERROR: '원본 파일을 읽지 못했습니다.', INVALID_TYPE: '원본 값의 형식을 확인할 수 없습니다.',
  INVALID_SOURCE_SELECTION: '선택한 원본 파일을 확인할 수 없습니다.', DUPLICATE_CANDIDATE: '같은 결과 항목의 원본 파일이 여러 개 있습니다.',
  UNRECOGNIZED_RESULT_FILE: '결과 파일의 이름이나 형식을 확인할 수 없습니다.',
  UNSUPPORTED_DISTRIBUTION_FORMAT: '지원하지 않는 결과 파일 형식입니다.', NO_PARSED_RESULTS: '읽을 수 있는 결과 수치가 없습니다.',
}
const severityLabels: Record<string, string> = { ERROR: '오류', WARNING: '주의', INFO: '안내' }
const distributionRoles = new Set(['SIMULATION_CASE', 'LOAD_CASE', 'EXECUTION_RUN', 'RUN_OPTION', 'SCENE'])

function emptyContext(): ResultRegistrationContext {
  return { simulation_case: null, evaluation: null, load_case: null, execution_run: null, run_option: null, scene: null }
}

function roleLabel(role?: string | null) { return role ? roleLabels[role] ?? '기타 폴더' : '분류 확인 필요' }
function statusLabel(status?: string | null) { return status ? statusLabels[status.toUpperCase()] ?? '확인 필요' : '상태 미확인' }
function stripTechnicalCode(value: string) { return value.replace(/^[A-Z][A-Z0-9_]{2,}:\s*/, '').trim() }
function errorMessage(reason: unknown, fallback: string) {
  if (!(reason instanceof Error)) return fallback
  return stripTechnicalCode(reason.message) || fallback
}
function pathParent(path: string) { const parts = path.replaceAll('\\', '/').split('/').filter(Boolean); return parts.slice(0, -1).join('/') || undefined }
function filePath(file: File) { return (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name }
function fileMediaType(file: File) {
  const extension = file.name.split('.').pop()?.toLowerCase()
  const canonicalType = ({ csv: 'text/csv', json: 'application/json', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', mp4: 'video/mp4', webm: 'video/webm' } as Record<string, string>)[extension ?? '']
  return (canonicalType ?? file.type) || 'application/octet-stream'
}
function serializeExclusions(values: Record<string, string>): ResultFileExclusion[] {
  return Object.entries(values).sort(([left], [right]) => left.localeCompare(right)).map(([relative_path, reason]) => ({ relative_path, reason: reason.trim() }))
}
function exclusionKey(values: Record<string, string>) { return JSON.stringify(serializeExclusions(values)) }
function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
function displayValue(value: unknown) {
  if (value == null || value === '') return '—'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  try { return JSON.stringify(value) } catch { return String(value) }
}
function issueText(issue: ResultInspectionIssue) {
  const message = stripTechnicalCode(issue.message || '') ||
    (issue.code ? issueCodeLabels[issue.code] : undefined) ||
    (issue.detail && !/^[A-Z][A-Z0-9_]{2,}$/.test(issue.detail) ? issue.detail : undefined) ||
    '검사 내용을 확인하세요.'
  return issue.source_path ? `${message} (${issue.source_path})` : message
}
function resultLink(published: ResultRegistrationPublished) {
  const query = new URLSearchParams({ project: published.project_id, request: published.request_id, view: 'case_results', result_environment: published.environment, case: published.case_id, capture: published.capture_id })
  return `/workspace/requests?${query.toString()}`
}
function safeIdempotencyKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `result-registration-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

type DraftResumeRecord = { project_id: string; request_id: string; environment: ResultEnvironment; draft_id: string; idempotency_key: string | null }
function readDraftResume(): DraftResumeRecord | null {
  try {
    const value = sessionStorage.getItem(draftResumeStorageKey)
    if (!value) return null
    const record = JSON.parse(value) as Partial<DraftResumeRecord>
    if (typeof record.project_id !== 'string' || typeof record.request_id !== 'string' || !['USAGE', 'DISTRIBUTION'].includes(record.environment ?? '') || typeof record.draft_id !== 'string') return null
    return { project_id: record.project_id, request_id: record.request_id, environment: record.environment as ResultEnvironment, draft_id: record.draft_id, idempotency_key: typeof record.idempotency_key === 'string' ? record.idempotency_key : null }
  } catch { return null }
}
function writeDraftResume(record: DraftResumeRecord) {
  try { sessionStorage.setItem(draftResumeStorageKey, JSON.stringify(record)) } catch { /* Restricted browser contexts may disable session storage. */ }
}
function clearDraftResume(draftId?: string) {
  try {
    const record = readDraftResume()
    if (!draftId || record?.draft_id === draftId) sessionStorage.removeItem(draftResumeStorageKey)
  } catch { /* Ignore unavailable session storage. */ }
}
function inspectionFromDraft(draft: ResultRegistrationDraftRead): ResultInspection | null {
  if (draft.inspection_revision == null) return null
  return {
    draft_id: draft.draft_id, status: draft.status, inspection_revision: draft.inspection_revision, source_revision: draft.source_revision,
    manifest: draft.manifest, exclusions: draft.exclusions ?? [], metrics: draft.inspection.metrics, media: draft.inspection.media, issues: draft.inspection.issues,
    missing_count: draft.inspection.missing_count, blocking_count: draft.inspection.blocking_count,
  }
}
function publishedFromDraft(draft: ResultRegistrationDraftRead): ResultRegistrationPublished | null {
  if (!draft.case_id || !draft.capture_id) return null
  return {
    draft_id: draft.draft_id, status: draft.status, case_id: draft.case_id, capture_id: draft.capture_id,
    environment: draft.environment, project_id: draft.project_id, request_id: draft.request_id, context: draft.context,
    asset_count: draft.asset_count, result_count: draft.result_count, media_count: draft.media_count,
    image_count: draft.image_count, video_count: draft.video_count, mirror_status: draft.mirror_status, error: draft.error,
  }
}

function breadcrumb(context: ResultRegistrationContext) {
  type TrailEntry = { role: string; label: string; relative_path: string }
  const entries: Array<TrailEntry | null> = [
    context.simulation_case && { role: 'SIMULATION_CASE', ...context.simulation_case },
    context.evaluation && { role: 'EVALUATION', ...context.evaluation },
    context.load_case && { role: 'LOAD_CASE', ...context.load_case },
    context.execution_run && { role: 'EXECUTION_RUN', ...context.execution_run },
    context.run_option?.status === 'PRESENT' && context.run_option.label && context.run_option.relative_path
      ? { role: 'RUN_OPTION', label: context.run_option.label, relative_path: context.run_option.relative_path }
      : null,
    context.scene && { role: 'SCENE', ...context.scene },
  ]
  return entries.filter((item): item is TrailEntry => Boolean(item))
}

export function ResultRegistrationWorkspace({ embedded = false, contextChanging = false, projects, initialProjectId, initialRequestId, onContextChange, onDataChanged }: ResultRegistrationWorkspaceProps) {
  const [environment, setEnvironment] = useState<ResultEnvironment>(() => new URLSearchParams(window.location.search).get('result_environment') === 'DISTRIBUTION' ? 'DISTRIBUTION' : 'USAGE')
  const [localProjectId, setLocalProjectId] = useState(initialProjectId)
  const [requests, setRequests] = useState<AnalysisRequest[]>([])
  const [localRequestId, setLocalRequestId] = useState(initialRequestId || '')
  const projectId = embedded ? initialProjectId : localProjectId
  const requestId = embedded ? initialRequestId || '' : localRequestId
  const [targets, setTargets] = useState<ResultRegistrationTarget[] | null>(null)
  const [targetsEnvironment, setTargetsEnvironment] = useState<ResultEnvironment | null>(null)
  const [targetError, setTargetError] = useState('')
  const [requestsError, setRequestsError] = useState('')
  const [foldersError, setFoldersError] = useState('')
  const [targetBusy, setTargetBusy] = useState(false)
  const [folderBusy, setFolderBusy] = useState(false)
  const [targetCasePath, setTargetCasePath] = useState('')
  const [activeContextNode, setActiveContextNode] = useState<ResultFolderNode | null>(null)
  const [folderStack, setFolderStack] = useState<string[]>([])
  const [folderNodes, setFolderNodes] = useState<ResultFolderNode[]>([])
  const [selectedResultPath, setSelectedResultPath] = useState('')
  const [selectedResultState, setSelectedResultState] = useState<'PRESENT' | 'MISSING' | 'UNAVAILABLE' | ''>('')
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [draftId, setDraftId] = useState('')
  const [inspection, setInspection] = useState<ResultInspection | null>(null)
  const [fileExclusions, setFileExclusions] = useState<Record<string, string>>({})
  const [inspectedExclusionKey, setInspectedExclusionKey] = useState('[]')
  const [acknowledgePartial, setAcknowledgePartial] = useState(false)
  const [approved, setApproved] = useState(false)
  const [busyAction, setBusyAction] = useState('')
  const [formError, setFormError] = useState('')
  const [notice, setNotice] = useState('')
  const [prepareInput, setPrepareInput] = useState<PrepareInput | null>(null)
  const [preparePreview, setPreparePreview] = useState<ResultFolderPreparation | null>(null)
  const [preparedFolder, setPreparedFolder] = useState<ResultFolderPreparation | null>(null)
  const [confirmCreateFolder, setConfirmCreateFolder] = useState(false)
  const [builderCaseName, setBuilderCaseName] = useState('')
  const [builderEvaluation, setBuilderEvaluation] = useState(evaluationNames[0])
  const [builderLoadName, setBuilderLoadName] = useState('')
  const [builderRunName, setBuilderRunName] = useState('')
  const [builderOptionName, setBuilderOptionName] = useState('')
  const [builderIncludeOption, setBuilderIncludeOption] = useState(false)
  const [builderSceneName, setBuilderSceneName] = useState('')
  const [completion, setCompletion] = useState<Completion | null>(null)
  const [restoringDraft, setRestoringDraft] = useState(false)
  const actionLock = useRef(false)
  const draftIdRef = useRef('')
  const targetKeyRef = useRef('')
  const selectionKeyRef = useRef('')
  const folderRequestId = useRef(0)
  const folderAbort = useRef<AbortController | null>(null)
  const targetAbort = useRef<AbortController | null>(null)
  const idempotencyByDraft = useRef(new Map<string, string>())
  const restoreAttemptKey = useRef('')
  const targetKey = JSON.stringify([environment, projectId, requestId])
  targetKeyRef.current = targetKey
  draftIdRef.current = draftId
  const currentFolderPath = folderStack[folderStack.length - 1]
  const activeTarget = targetsEnvironment === environment ? targets?.find((item) => item.project_id === projectId && item.request_id === requestId) : undefined
  const selectedTargetCase = activeTarget?.cases.find((item) => item.relative_path === targetCasePath)
  const activeContext = preparedFolder?.context ?? activeContextNode?.context ?? selectedTargetCase?.context ?? null
  const activeCasePath = preparedFolder?.case_relative_path ?? activeContext?.simulation_case?.relative_path ?? selectedTargetCase?.relative_path ?? ''
  const suggestedPath = activeContextNode?.suggested_relative_path || ''
  const suggestedState = activeContextNode?.result_state ?? ''
  const selectedTargetLabel = activeTarget ? `${activeTarget.project_name} · ${activeTarget.request_name}` : ''
  const effectiveContext = activeContext ?? emptyContext()
  const isContextReady = Boolean(activeTarget && activeTarget.status && activeCasePath && effectiveContext.simulation_case?.id)
  const selectedFilesBytes = selectedFiles.reduce((sum, file) => sum + file.size, 0)
  const isActionBusy = Boolean(busyAction)
  const selectionKey = JSON.stringify([targetKey, targetCasePath, activeContextNode?.relative_path ?? '', selectedResultPath])
  selectionKeyRef.current = selectionKey
  const pathPreviews = useMemo(() => selectedFiles.map((file) => ({ file, relativePath: filePath(file) })), [selectedFiles])
  const [previewUrls, setPreviewUrls] = useState<Record<string, string>>({})

  const clearReview = useCallback(() => {
    const currentDraftId = draftIdRef.current
    if (currentDraftId) clearDraftResume(currentDraftId)
    setDraftId(''); setInspection(null); setAcknowledgePartial(false); setApproved(false)
    setFileExclusions({}); setInspectedExclusionKey('[]')
    idempotencyByDraft.current.clear()
  }, [])
  const clearPathState = useCallback(() => {
    setActiveContextNode(null); setFolderStack([]); setFolderNodes([]); setTargetCasePath('')
    setSelectedResultPath(''); setSelectedResultState(''); setSelectedFiles([])
    setPrepareInput(null); setPreparePreview(null); setPreparedFolder(null); setConfirmCreateFolder(false)
    setFoldersError(''); setFormError(''); setNotice(''); setCompletion(null); clearReview()
  }, [clearReview])

  useEffect(() => {
    if (!embedded) {
      setLocalProjectId((current) => current && projects.some((item) => item.id === current) ? current : initialProjectId || projects[0]?.id || '')
    }
  }, [embedded, initialProjectId, projects])

  useEffect(() => {
    let active = true
    if (!projectId) { setRequests([]); setLocalRequestId(''); setRequestsError(''); return () => { active = false } }
    setRequestsError('')
    api.requests(projectId).then((items) => {
      if (!active) return
      setRequests(items)
      setLocalRequestId((current) => items.some((item) => item.id === (initialRequestId || current)) ? initialRequestId || current : items[0]?.id || '')
    }).catch((reason) => { if (active) setRequestsError(errorMessage(reason, '의뢰 목록을 불러오지 못했습니다.')) })
    return () => { active = false }
  }, [initialRequestId, projectId])

  useEffect(() => {
    if (!embedded && projectId && requestId) onContextChange?.({ projectId, requestId })
  }, [embedded, onContextChange, projectId, requestId])

  useEffect(() => {
    targetAbort.current?.abort()
    const controller = new AbortController(); targetAbort.current = controller
    setTargets(null); setTargetsEnvironment(null); setTargetError(''); setTargetBusy(true); clearPathState()
    if (!projectId || !requestId) { setTargetBusy(false); return () => controller.abort() }
    resultRegistrationApi.targets(environment, controller.signal).then((response) => {
      if (controller.signal.aborted || targetKeyRef.current !== targetKey) return
      setTargets(response.targets)
      setTargetsEnvironment(response.environment)
    }).catch((reason) => {
      if (!controller.signal.aborted && targetKeyRef.current === targetKey) setTargetError(errorMessage(reason, 'SPDM 등록 대상을 불러오지 못했습니다.'))
    }).finally(() => { if (!controller.signal.aborted && targetKeyRef.current === targetKey) setTargetBusy(false) })
    return () => controller.abort()
  }, [clearPathState, environment, projectId, requestId, targetKey])

  useEffect(() => {
    if (!activeTarget || !projectId || !requestId) return
    const controller = new AbortController()
    const request = ++folderRequestId.current
    setFolderBusy(true); setFoldersError(''); setFolderStack([])
    resultRegistrationApi.folders({ project_id: projectId, request_id: requestId, environment }, controller.signal).then((response) => {
      if (controller.signal.aborted || request !== folderRequestId.current || targetKeyRef.current !== targetKey) return
      setFolderNodes(response.nodes)
    }).catch((reason) => {
      if (!controller.signal.aborted && request === folderRequestId.current && targetKeyRef.current === targetKey) setFoldersError(errorMessage(reason, '연결된 폴더를 불러오지 못했습니다.'))
    }).finally(() => { if (!controller.signal.aborted && request === folderRequestId.current) setFolderBusy(false) })
    return () => controller.abort()
  }, [activeTarget, environment, projectId, requestId, targetKey])

  useEffect(() => () => {
    folderAbort.current?.abort(); targetAbort.current?.abort()
  }, [])

  useEffect(() => { restoreAttemptKey.current = '' }, [targetKey])

  useEffect(() => {
    if (!activeTarget || !projectId || !requestId || contextChanging) return
    const saved = readDraftResume()
    if (!saved || saved.project_id !== projectId || saved.request_id !== requestId || saved.environment !== environment) return
    const attemptKey = `${targetKey}:${saved.draft_id}`
    if (restoreAttemptKey.current === attemptKey) return
    restoreAttemptKey.current = attemptKey
    let active = true
    setRestoringDraft(true)
    const restore = async () => {
      try {
        const draft = await resultRegistrationApi.getDraft(saved.draft_id)
        if (!active || targetKeyRef.current !== targetKey) return
        if (draft.project_id !== projectId || draft.request_id !== requestId || draft.environment !== environment || !draft.case_relative_path || !draft.result_relative_path) throw new Error('저장된 검수 초안이 현재 선택한 업무와 다릅니다.')
        const caseContext = draft.context.simulation_case
        if (!caseContext || caseContext.relative_path !== draft.case_relative_path) throw new Error('저장된 검수 초안의 Case 문맥을 확인할 수 없습니다.')
        const restoredFolder: ResultFolderPreparation = {
          status: 'RESTORED', case_relative_path: draft.case_relative_path, result_relative_path: draft.result_relative_path,
          created: false, context: draft.context,
        }
        const caseNode: ResultFolderNode = {
          relative_path: draft.case_relative_path, name: caseContext.label, role_kind: 'SIMULATION_CASE', context: draft.context,
          result_state: 'PRESENT', can_prepare: false, suggested_relative_path: draft.result_relative_path, children_available: false,
        }
        setTargetCasePath(draft.case_relative_path); setFolderStack([draft.case_relative_path]); setActiveContextNode(caseNode)
        setPreparedFolder(restoredFolder); setSelectedResultPath(draft.result_relative_path); setSelectedResultState('PRESENT')
        setDraftId(draft.draft_id)
        const restoredExclusions = draft.exclusions ?? []
        const restoredExclusionValues = Object.fromEntries(restoredExclusions.map(({ relative_path, reason }) => [relative_path, reason]))
        setFileExclusions(restoredExclusionValues)
        setInspectedExclusionKey(exclusionKey(restoredExclusionValues))
        const idempotencyKey = saved.idempotency_key ?? draft.idempotency_key
        if (idempotencyKey) idempotencyByDraft.current.set(draft.draft_id, idempotencyKey)
        const inspectionResult = inspectionFromDraft(draft)
        const publishedResult = publishedFromDraft(draft)
        if (publishedResult && ['MIRROR_PENDING', 'MIRROR_CONFLICT', 'MIRROR_FAILED', 'PUBLISHED'].includes(draft.status)) {
          setCompletion({
            published: publishedResult, draftId: draft.draft_id, mediaCount: draft.media_count,
            imageCount: draft.image_count, videoCount: draft.video_count,
            targetLabel: `${activeTarget.project_name} · ${activeTarget.request_name}`,
          })
          setApproved(true)
        } else if (inspectionResult) {
          setInspection(inspectionResult)
          const wasApproved = ['APPROVED', 'PUBLISH_FAILED', 'MIRROR_PENDING', 'MIRROR_CONFLICT', 'MIRROR_FAILED'].includes(draft.status)
          setApproved(wasApproved)
          setAcknowledgePartial(wasApproved && needsPartialAcknowledgement(inspectionResult))
          setNotice('이전에 저장한 검수 초안을 복원했습니다. 내용을 확인한 뒤 DB 등록을 진행하세요.')
        } else if (draft.status === 'DRAFT' || draft.status === 'UPLOAD_PENDING') {
          const inspected = await resultRegistrationApi.inspect(draft.draft_id, restoredExclusions.length ? { exclusions: restoredExclusions } : undefined)
          if (!active || targetKeyRef.current !== targetKey) return
          const appliedExclusions = inspected.exclusions ?? restoredExclusions
          const appliedExclusionValues = Object.fromEntries(appliedExclusions.map(({ relative_path, reason }) => [relative_path, reason]))
          setFileExclusions(appliedExclusionValues); setInspectedExclusionKey(exclusionKey(appliedExclusionValues))
          setInspection(inspected); setApproved(false)
          setNotice('저장한 초안의 자동 검사를 이어서 완료했습니다. 원문과 단위를 확인하세요.')
        } else {
          throw new Error('저장된 초안에 복원할 검수 정보가 없습니다. 새 업로드로 다시 시작하세요.')
        }
        writeDraftResume({ ...saved, idempotency_key: idempotencyKey ?? null })
      } catch (reason) {
        if (!active || targetKeyRef.current !== targetKey) return
        clearDraftResume(saved.draft_id)
        setNotice('이전 초안을 확인할 수 없어 현재 업무의 검수 화면은 비웠습니다. 파일을 다시 선택해 시작하세요.')
        setFormError(errorMessage(reason, '이전 초안을 복원하지 못했습니다.'))
      } finally { if (active) setRestoringDraft(false) }
    }
    void restore()
    return () => { active = false }
  }, [activeTarget, contextChanging, environment, projectId, requestId, targetKey])

  useEffect(() => {
    const next: Record<string, string> = {}
    for (const file of selectedFiles) {
      const type = file.type || fileMediaType(file)
      if (type.startsWith('image/') || type.startsWith('video/')) next[filePath(file)] = URL.createObjectURL(file)
    }
    setPreviewUrls(next)
    return () => Object.values(next).forEach((url) => URL.revokeObjectURL(url))
  }, [selectedFiles])

  const fetchFolderChildren = async (path: string | undefined, options: { setStack?: string[]; keepContext?: boolean } = {}) => {
    if (!activeTarget || !projectId || !requestId) return
    folderAbort.current?.abort()
    const controller = new AbortController(); folderAbort.current = controller
    const request = ++folderRequestId.current
    const expectedTargetKey = targetKey
    setFolderBusy(true); setFoldersError('')
    try {
      const response = await resultRegistrationApi.folders({ project_id: projectId, request_id: requestId, environment, parent_relative_path: path }, controller.signal)
      if (controller.signal.aborted || request !== folderRequestId.current || targetKeyRef.current !== expectedTargetKey) return
      setFolderNodes(response.nodes)
      setFolderStack(options.setStack ?? (path ? [...folderStack, path] : []))
      if (!options.keepContext && !path) setActiveContextNode(null)
    } catch (reason) {
      if (!controller.signal.aborted && request === folderRequestId.current && targetKeyRef.current === expectedTargetKey) setFoldersError(errorMessage(reason, '폴더 목록을 불러오지 못했습니다.'))
    } finally { if (!controller.signal.aborted && request === folderRequestId.current) setFolderBusy(false) }
  }

  const loadCaseContext = async (path: string, caseItem?: ResultRegistrationTarget['cases'][number]) => {
    if (!activeTarget || isActionBusy) return
    folderAbort.current?.abort()
    const controller = new AbortController(); folderAbort.current = controller
    const request = ++folderRequestId.current
    const expectedTargetKey = targetKey
    setTargetCasePath(path); setActiveContextNode(null); setFolderNodes([]); setFolderStack([])
    setSelectedResultPath(''); setSelectedResultState(''); setPreparedFolder(null); setPreparePreview(null); setPrepareInput(null); setSelectedFiles([]); clearReview()
    setFoldersError(''); setFolderBusy(true)
    try {
      const parent = pathParent(path)
      const [siblings, children] = await Promise.all([
        resultRegistrationApi.folders({ project_id: projectId, request_id: requestId, environment, parent_relative_path: parent }, controller.signal),
        resultRegistrationApi.folders({ project_id: projectId, request_id: requestId, environment, parent_relative_path: path }, controller.signal),
      ])
      if (controller.signal.aborted || request !== folderRequestId.current || targetKeyRef.current !== expectedTargetKey) return
      const node = siblings.nodes.find((item) => item.relative_path === path && item.role_kind === 'SIMULATION_CASE')
      const contextNode = node ?? (caseItem ? {
        relative_path: caseItem.relative_path, name: caseItem.name, role_kind: 'SIMULATION_CASE', context: caseItem.context,
        result_state: caseItem.result_state, can_prepare: caseItem.can_prepare ?? false, suggested_relative_path: caseItem.suggested_relative_path ?? null,
        children_available: children.nodes.length > 0,
      } satisfies ResultFolderNode : null)
      if (!contextNode) throw new Error('선택한 Case의 서버 연결 정보를 확인할 수 없습니다. SPDM 폴더 연결을 확인해 주세요.')
      setActiveContextNode(contextNode); setFolderStack([path]); setFolderNodes(children.nodes)
      if (!caseItem) setTargetCasePath(contextNode.context.simulation_case?.relative_path ?? path)
    } catch (reason) {
      if (!controller.signal.aborted && request === folderRequestId.current && targetKeyRef.current === expectedTargetKey) setFoldersError(errorMessage(reason, '선택한 Case의 폴더 정보를 확인하지 못했습니다.'))
    } finally { if (!controller.signal.aborted && request === folderRequestId.current) setFolderBusy(false) }
  }

  const chooseCase = (path: string) => {
    const item = activeTarget?.cases.find((candidate) => candidate.relative_path === path)
    if (item) void loadCaseContext(item.relative_path, item)
  }

  const chooseContextNode = (node: ResultFolderNode) => {
    const allowed = environment === 'USAGE' ? ['SIMULATION_CASE', 'EVALUATION'] : [...distributionRoles]
    if (!node.role_kind || !allowed.includes(node.role_kind)) return
    if (node.role_kind === 'SIMULATION_CASE' && node.context.simulation_case) {
      void loadCaseContext(node.context.simulation_case.relative_path)
      return
    }
    if (!node.context.simulation_case?.id) {
      setFoldersError('서버가 이 폴더를 기존 Case 문맥에 연결하지 않았습니다. 임의로 연결하지 말고 관리자에게 폴더 연결을 요청하세요.')
      return
    }
    setActiveContextNode(node); setSelectedResultPath(''); setSelectedResultState(''); setPreparedFolder(null); setPreparePreview(null); setPrepareInput(null)
    setSelectedFiles([]); clearReview(); setNotice('')
    void fetchFolderChildren(node.relative_path, { setStack: [node.context.simulation_case.relative_path, node.relative_path], keepContext: true })
  }

  const openFolder = (node: ResultFolderNode) => {
    if (!node.children_available || isActionBusy) return
    void fetchFolderChildren(node.relative_path, { setStack: [...folderStack, node.relative_path], keepContext: true })
  }

  const goToParentFolder = () => {
    if (folderStack.length > 1) {
      const nextStack = folderStack.slice(0, -1)
      void fetchFolderChildren(nextStack[nextStack.length - 1], { setStack: nextStack, keepContext: true })
    } else if (folderStack.length === 1 && activeTarget) {
      setTargetCasePath(''); setActiveContextNode(null); setSelectedResultPath(''); setSelectedResultState(''); setPreparedFolder(null); setPreparePreview(null); setSelectedFiles([]); clearReview()
      void fetchFolderChildren(undefined, { setStack: [] })
    }
  }

  const selectResultFolder = (path: string, state: 'PRESENT' | 'MISSING' | 'UNAVAILABLE', node?: ResultFolderNode) => {
    setSelectedResultPath(path); setSelectedResultState(state); setPreparedFolder(null); setPreparePreview(null); setPrepareInput(null); setConfirmCreateFolder(false)
    setSelectedFiles([]); clearReview(); setFormError(''); setNotice('')
  }

  const beginAction = (label: string) => {
    if (actionLock.current) return false
    actionLock.current = true; setBusyAction(label); setFormError(''); setNotice('')
    return true
  }
  const endAction = () => { actionLock.current = false; setBusyAction('') }

  const missingPlan = useMemo(() => {
    const context = activeContextNode?.context ?? selectedTargetCase?.context ?? emptyContext()
    const segments: PrepareInput['segments'] = []
    const fieldKeys: string[] = []
    if (!context.simulation_case) { segments.push({ role_kind: 'SIMULATION_CASE', name: builderCaseName.trim() }); fieldKeys.push('case') }
    if (environment === 'USAGE') {
      if (!context.evaluation) { segments.push({ role_kind: 'EVALUATION', name: builderEvaluation }); fieldKeys.push('evaluation') }
    } else {
      if (!context.load_case) { segments.push({ role_kind: 'LOAD_CASE', name: builderLoadName.trim() }); fieldKeys.push('load') }
      if (!context.execution_run) { segments.push({ role_kind: 'EXECUTION_RUN', name: builderRunName.trim() }); fieldKeys.push('run') }
      if (context.run_option?.status !== 'PRESENT' && builderIncludeOption) { segments.push({ role_kind: 'RUN_OPTION', name: builderOptionName.trim() }); fieldKeys.push('option') }
      if (!context.scene) { segments.push({ role_kind: 'SCENE', name: builderSceneName.trim() }); fieldKeys.push('scene') }
    }
    segments.push({ role_kind: 'RESULTS', name: 'results' })
    const parent = activeContextNode?.relative_path ?? selectedTargetCase?.relative_path ?? activeTarget?.spdm_request_folder ?? undefined
    const casePathExists = Boolean(context.simulation_case)
    const requiredValues = fieldKeys.filter((key) => key !== 'evaluation').map((key) => ({
      case: builderCaseName, load: builderLoadName, run: builderRunName, option: builderOptionName, scene: builderSceneName,
    } as Record<string, string>)[key]?.trim())
    return { input: { project_id: projectId, request_id: requestId, environment, parent_relative_path: parent, segments }, fieldKeys, ready: Boolean(activeTarget && projectId && requestId && (!casePathExists ? !fieldKeys.includes('case') || Boolean(builderCaseName.trim()) : true) && requiredValues.every(Boolean)), context }
  }, [activeContextNode, activeTarget, builderCaseName, builderEvaluation, builderIncludeOption, builderLoadName, builderOptionName, builderRunName, builderSceneName, environment, projectId, requestId, selectedTargetCase])

  const previewPreparation = async (input: PrepareInput) => {
    if (!beginAction('결과 경로 확인 중')) return
    const token = selectionKeyRef.current
    try {
      const preview = await resultRegistrationApi.prepareFolder({ ...input, confirm_create: false })
      if (selectionKeyRef.current !== token) return
      setPrepareInput(input); setPreparePreview(preview); setPreparedFolder(null); setConfirmCreateFolder(false)
      setNotice('서버가 확인한 전체 경로를 검토한 뒤 명시적으로 생성할 수 있습니다.')
    } catch (reason) { setFormError(errorMessage(reason, '결과 경로를 미리 확인하지 못했습니다.')) }
    finally { endAction() }
  }

  const previewSuggestedPath = () => {
    if (!activeContextNode || !activeContextNode.can_prepare || !suggestedPath) return
    void previewPreparation({ project_id: projectId, request_id: requestId, environment, parent_relative_path: activeContextNode.relative_path, segments: [{ role_kind: 'RESULTS', name: 'results' }] })
  }

  const previewMissingHierarchy = () => {
    if (!missingPlan.ready) return
    void previewPreparation(missingPlan.input)
  }

  const confirmPreparation = async () => {
    if (!prepareInput || !preparePreview || !confirmCreateFolder || !beginAction('결과 폴더 준비 중')) return
    const token = selectionKeyRef.current
    try {
      const prepared = await resultRegistrationApi.prepareFolder({ ...prepareInput, confirm_create: true })
      if (selectionKeyRef.current !== token) return
      setPreparedFolder(prepared); setTargetCasePath(prepared.case_relative_path); setPreparePreview(null); setSelectedResultPath(prepared.result_relative_path); setSelectedResultState('PRESENT'); setNotice(`결과용 폴더를 준비했습니다: ${prepared.result_relative_path}`)
    } catch (reason) { setFormError(errorMessage(reason, '결과 폴더를 준비하지 못했습니다.')) }
    finally { endAction() }
  }

  const chooseFiles = (files: File[]) => {
    setFormError(''); setNotice(''); clearReview()
    const paths = files.map(filePath)
    if (new Set(paths).size !== paths.length) { setSelectedFiles([]); setFormError('같은 상대 경로를 가진 파일이 있습니다. 파일 선택을 확인해 주세요.'); return }
    if (files.some((file) => file.size > 32 * 1024 * 1024)) { setSelectedFiles([]); setFormError('파일 하나의 최대 크기는 32 MiB입니다.'); return }
    if (files.reduce((sum, file) => sum + file.size, 0) > 256 * 1024 * 1024) { setSelectedFiles([]); setFormError('한 번에 등록하는 파일 묶음은 256 MiB 이하여야 합니다.'); return }
    setSelectedFiles(files)
  }

  const uploadAndInspect = async () => {
    if (!activeTarget || !isContextReady || !selectedResultPath || !selectedFiles.length || isActionBusy || !beginAction('파일 업로드 준비 중')) return
    const token = selectionKeyRef.current
    const selectedCasePath = activeCasePath
    const selectedContext = preparedFolder?.context ?? activeContext
    const resultPath = preparedFolder?.result_relative_path ?? selectedResultPath
    if (!selectedCasePath || !selectedContext?.simulation_case?.id) { setFormError('Case 연결 정보가 없습니다. 기존 SPDM Case를 다시 선택하세요.'); endAction(); return }
    try {
      const manifest = await Promise.all(selectedFiles.map(async (file) => ({
        relative_path: filePath(file), size: file.size, sha256: await sha256Hex(file), media_type: fileMediaType(file),
      })))
      if (selectionKeyRef.current !== token) return
      setBusyAction('검수 대기 파일 격리 업로드 중')
      const draft = await resultRegistrationApi.createDraft({ project_id: projectId, request_id: requestId, environment, case_relative_path: selectedCasePath, result_relative_path: resultPath, context: selectedContext, files: manifest })
      if (selectionKeyRef.current !== token) return
      setDraftId(draft.draft_id)
      writeDraftResume({ project_id: projectId, request_id: requestId, environment, draft_id: draft.draft_id, idempotency_key: null })
      await resultRegistrationApi.uploadFiles(draft.draft_id, selectedFiles.map((file, index) => ({ file, relative_path: manifest[index].relative_path, sha256: manifest[index].sha256, media_type: manifest[index].media_type })))
      if (selectionKeyRef.current !== token) return
      setBusyAction('자동 검사 중')
      const result = await resultRegistrationApi.inspect(draft.draft_id)
      if (selectionKeyRef.current !== token) return
      const appliedExclusions = result.exclusions ?? []
      const appliedExclusionValues = Object.fromEntries(appliedExclusions.map(({ relative_path, reason }) => [relative_path, reason]))
      setFileExclusions(appliedExclusionValues); setInspectedExclusionKey(exclusionKey(appliedExclusionValues))
      setInspection(result); setAcknowledgePartial(false); setApproved(false)
      setNotice('자동 검사가 끝났습니다. 원본 값·출처·단위와 미디어를 검토한 뒤 DB 등록을 확정하세요.')
    } catch (reason) { if (selectionKeyRef.current === token) setFormError(errorMessage(reason, '업로드 또는 자동 검사에 실패했습니다.')) }
    finally { endAction() }
  }

  const reinspectWithExclusions = async () => {
    const exclusions = serializeExclusions(fileExclusions)
    if (!draftId || !inspection || approved || isActionBusy || exclusions.some(({ reason }) => !reason) || !beginAction('제외 파일을 반영해 다시 검사 중')) return
    const token = selectionKeyRef.current
    const inspectionDraftId = draftId
    try {
      const result = await resultRegistrationApi.inspect(inspectionDraftId, { exclusions })
      if (selectionKeyRef.current !== token || draftIdRef.current !== inspectionDraftId) return
      const appliedExclusions = result.exclusions ?? exclusions
      const appliedExclusionValues = Object.fromEntries(appliedExclusions.map(({ relative_path, reason }) => [relative_path, reason]))
      setFileExclusions(appliedExclusionValues); setInspectedExclusionKey(exclusionKey(appliedExclusionValues))
      setInspection(result); setAcknowledgePartial(false)
      setNotice('제외 사유를 반영해 다시 검사했습니다. 제외된 파일 상태와 남은 결과값을 확인하세요.')
      setFormError('')
    } catch (reason) {
      if (selectionKeyRef.current === token && draftIdRef.current === inspectionDraftId) setFormError(errorMessage(reason, '파일 제외를 반영해 다시 검사하지 못했습니다.'))
    } finally { endAction() }
  }

  const startFreshUpload = () => {
    if (!draftId || approved || isActionBusy) return
    const previousDraftId = draftId
    clearDraftResume(previousDraftId)
    setDraftId(''); setInspection(null); setAcknowledgePartial(false); setApproved(false)
    setFileExclusions({}); setInspectedExclusionKey('[]'); setFormError('')
    idempotencyByDraft.current.delete(previousDraftId)
    if (fileInputRef.current) fileInputRef.current.value = ''
    setNotice('현재 초안은 게시하지 않고, 선택 파일을 수정해 새 검수를 시작할 수 있습니다.')
  }

  const approveAndPublish = async () => {
    const exclusions = serializeExclusions(fileExclusions)
    if (!draftId || !inspection || inspection.blocking_count > 0 || exclusionKey(fileExclusions) !== inspectedExclusionKey || exclusions.some(({ reason }) => !reason) || (needsPartialAcknowledgement(inspection) && !acknowledgePartial) || isActionBusy || !beginAction('DB 등록 중')) return
    const token = selectionKeyRef.current
    const originalTargetLabel = selectedTargetLabel || `${projectId} · ${requestId}`
    try {
      if (!approved) {
        await resultRegistrationApi.approve(draftId, { inspection_revision: inspection.inspection_revision, acknowledge_partial: acknowledgePartial, exclusions })
        if (selectionKeyRef.current !== token) return
        setApproved(true)
      }
      if (selectionKeyRef.current !== token) return
      setBusyAction('승인한 파일을 DB에 등록 중')
      const idempotencyKey = idempotencyByDraft.current.get(draftId) ?? safeIdempotencyKey()
      idempotencyByDraft.current.set(draftId, idempotencyKey)
      writeDraftResume({ project_id: projectId, request_id: requestId, environment, draft_id: draftId, idempotency_key: idempotencyKey })
      const result = await resultRegistrationApi.publish(draftId, { inspection_revision: inspection.inspection_revision, idempotency_key: idempotencyKey })
      const fallbackMedia = inspection.media.filter((item) => item.status === 'READY').length
      const images = result.image_count ?? inspection.media.filter((item) => item.kind === 'IMAGE' && item.status === 'READY').length
      const videos = result.video_count ?? inspection.media.filter((item) => item.kind === 'VIDEO' && item.status === 'READY').length
      setCompletion({ published: result, draftId, mediaCount: result.media_count ?? fallbackMedia, imageCount: images, videoCount: videos, targetLabel: originalTargetLabel })
      setNotice('DB 등록이 완료되었습니다. 수집 버전과 결과를 확인할 수 있습니다.')
      try { await onDataChanged() } catch { setNotice('DB 등록은 완료됐지만 화면의 업무 목록을 새로고침하지 못했습니다. Case 결과는 아래 링크에서 확인할 수 있습니다.') }
    } catch (reason) { if (selectionKeyRef.current === token) setFormError(errorMessage(reason, '검수 승인 또는 DB 등록에 실패했습니다. 같은 검수 초안에서 안전하게 다시 시도하세요.')) }
    finally { endAction() }
  }

  const retryMirror = async (completionItem: Completion) => {
    if (isActionBusy || !beginAction('DB 등록된 결과 폴더 반영을 복구 중')) return
    try {
      const result = await resultRegistrationApi.retryMirror(completionItem.draftId)
      setCompletion((current) => current?.draftId === completionItem.draftId ? { ...current, published: result } : current)
      setNotice(result.status === 'PUBLISHED' ? 'DB 등록된 결과를 저장 폴더에도 반영했습니다.' : 'DB 등록은 유지되며 저장 폴더 반영을 다시 확인해야 합니다.')
    } catch (reason) { setFormError(errorMessage(reason, '저장 폴더 반영을 다시 시도하지 못했습니다. Case 결과 DB 등록은 유지됩니다.')) }
    finally { endAction() }
  }

  const selectProject = (next: string) => { setLocalProjectId(next); setLocalRequestId(''); clearPathState() }
  const selectRequest = (next: string) => { setLocalRequestId(next); clearPathState() }
  const changeEnvironment = (next: ResultEnvironment) => {
    if (next === environment || isActionBusy) return
    setEnvironment(next); clearPathState()
    const query = new URLSearchParams(window.location.search); query.set('result_environment', next)
    window.history.replaceState(window.history.state, '', `${window.location.pathname}?${query.toString()}`)
  }

  const contextTrail = activeContext ? breadcrumb(activeContext) : []
  const currentSuggestedDestinationSelected = selectedResultPath && selectedResultPath === suggestedPath
  const partialRequired = inspection ? needsPartialAcknowledgement(inspection) : false
  const exclusions = serializeExclusions(fileExclusions)
  const exclusionsChanged = Boolean(inspection && exclusionKey(fileExclusions) !== inspectedExclusionKey)
  const hasInvalidExclusionReason = exclusions.some(({ reason }) => !reason)
  const canReinspectExclusions = Boolean(draftId && inspection && exclusionsChanged && !approved && !isActionBusy && !hasInvalidExclusionReason)
  const setFileExcluded = (relativePath: string, checked: boolean) => setFileExclusions((current) => {
    const next = { ...current }
    if (checked) next[relativePath] = ''
    else delete next[relativePath]
    return next
  })
  const setFileExclusionReason = (relativePath: string, reason: string) => setFileExclusions((current) => ({ ...current, [relativePath]: reason }))
  const canInspect = Boolean(isContextReady && selectedResultPath && (selectedResultState === 'PRESENT' || preparedFolder) && selectedFiles.length && !draftId && !isActionBusy)
  const activeProject = projects.find((item) => item.id === projectId)
  const activeRequest = requests.find((item) => item.id === requestId)

  return <section className="data-workspace result-registration-workspace" data-ui-density="v1" data-testid="result-registration-workspace">
    {!embedded ? <header className="data-workspace-head"><div><span>SPDM / RESULT REGISTRATION</span><h1>해석 결과 등록</h1><p>기존 SPDM 업무를 선택해 결과를 검수한 뒤 DB에 등록합니다.</p></div><div className="data-count"><strong>{projects.length}</strong><span>PROJECTS</span></div></header> : <header className="result-registration-heading"><span>SPDM / RESULT REGISTRATION</span><h2>결과 등록</h2><p>기존 업무의 저장 위치와 검수 내용을 확인한 뒤 DB에 등록합니다.</p></header>}

    {!embedded ? <div className="data-hierarchy-bar result-registration-hierarchy">
      <label><span>기존 프로젝트</span><SearchableSelect ariaLabel="등록 프로젝트 선택" items={projects} kind="project" value={projectId} onChange={selectProject} placeholder="프로젝트 선택" /></label><i>›</i>
      <label><span>기존 의뢰</span><SearchableSelect ariaLabel="등록 의뢰 선택" items={requests} kind="request" value={requestId} onChange={selectRequest} disabled={!requests.length} placeholder={requests.length ? '의뢰 선택' : '의뢰가 없습니다'} /></label>
    </div> : <div className="result-registration-current-context"><span>기존 SPDM 업무</span><strong>{activeTarget?.project_name ?? activeProject?.name ?? (projectId || '프로젝트를 선택하세요')} · {activeTarget?.request_name ?? activeRequest?.title ?? (requestId || '의뢰를 선택하세요')}</strong></div>}

    <section className="result-registration-card result-registration-scope" aria-labelledby="registration-scope-title">
      <header><span>01 · 대상 선택</span><h3 id="registration-scope-title">등록 환경과 기존 Case 선택</h3><p>새 프로젝트·의뢰·하중경우를 만들지 않고, 연결된 SPDM 업무만 선택합니다.</p></header>
      <div className="result-registration-form-row">
        <label><span>환경</span><select aria-label="결과 등록 환경" value={environment} disabled={isActionBusy || contextChanging} onChange={(event) => changeEnvironment(event.target.value as ResultEnvironment)}><option value="USAGE">사용환경 · Case / 평가 항목</option><option value="DISTRIBUTION">유통환경 · Case / 하중 / Run / Option / Scene</option></select></label>
        <label><span>해석 Case</span><select aria-label="SPDM 해석 Case 선택" value={targetCasePath} disabled={!activeTarget || (!activeTarget.cases.length && !targetCasePath) || folderBusy || isActionBusy || contextChanging} onChange={(event) => chooseCase(event.target.value)}><option value="">{activeTarget?.cases.length ? '기존 Case 선택' : '연결된 Case 없음'}</option>{activeTarget?.cases.map((item) => <option key={item.relative_path} value={item.relative_path}>{item.name} · {item.relative_path}</option>)}{targetCasePath && !activeTarget?.cases.some((item) => item.relative_path === targetCasePath) ? <option value={targetCasePath}>{effectiveContext.simulation_case?.label ?? '복원된 Case'} · {targetCasePath}</option> : null}</select></label>
      </div>
      {targetBusy ? <p className="result-registration-state"><LoaderCircle className="result-registration-spin" /> SPDM 연결 대상 확인 중</p> : null}
      {restoringDraft ? <p className="result-registration-state" role="status"><LoaderCircle className="result-registration-spin" /> 저장된 검수 초안 확인 중</p> : null}
      {targetError ? <p className="result-registration-error" role="alert"><AlertTriangle />{targetError}</p> : null}
      {requestsError ? <p className="result-registration-error" role="alert"><AlertTriangle />{requestsError}</p> : null}
      {activeTarget ? <div className="result-registration-binding"><span>SPDM 경로 연결</span><code>{activeTarget.spdm_project_folder || '프로젝트 폴더 연결 없음'}{activeTarget.spdm_request_folder ? ` / ${activeTarget.spdm_request_folder}` : ''}</code><b>{statusLabel(activeTarget.status)}</b></div> : null}
      {!targetBusy && projectId && requestId && !targetError && !activeTarget ? <p className="result-registration-empty" role="status">선택한 프로젝트·의뢰에 이 환경의 확정된 SPDM 경로 연결이 없습니다. SPDM에서 업무를 먼저 등록하거나 관리자에게 폴더 연결을 요청하세요.</p> : null}
      {activeTarget && (!activeTarget.spdm_request_folder || ['CONFLICT', 'UNAVAILABLE', 'BINDING_REQUIRED'].includes(activeTarget.status)) ? <p className="result-registration-error" role="alert"><AlertTriangle />프로젝트·의뢰의 저장 위치를 확인할 수 없습니다. 이름으로 경로를 추정하지 말고 관리자에게 연결 상태를 확인해 주세요.</p> : null}
    </section>

    {activeTarget && activeTarget.spdm_request_folder && !['CONFLICT', 'UNAVAILABLE', 'BINDING_REQUIRED'].includes(activeTarget.status) ? <>
      <section className="result-registration-card result-registration-folders" aria-labelledby="registration-folder-title">
        <header><span>02 · 저장 위치</span><h3 id="registration-folder-title">Case 문맥과 결과 폴더</h3><p>{environment === 'USAGE' ? '사용환경은 Case와 평가 항목을 선택합니다.' : '유통환경은 Case와 하중경우, Run Case, 선택적 Run Option, Scene 문맥을 선택합니다.'} 경로는 SPDM 연결을 기준으로 서버가 반환합니다.</p></header>
        <div className="result-registration-explorer-tools">
          <div><span>현재 폴더</span><code>{currentFolderPath || activeTarget.spdm_request_folder}</code></div>
          <button type="button" className="ghost-button" disabled={folderBusy || isActionBusy || !folderStack.length} onClick={goToParentFolder}><ChevronDown className="result-registration-back-icon" /> 상위 폴더</button>
          <button type="button" className="ghost-button" disabled={folderBusy || isActionBusy} onClick={() => { setTargetCasePath(''); setActiveContextNode(null); setSelectedResultPath(''); setSelectedResultState(''); setPreparedFolder(null); setSelectedFiles([]); clearReview(); void fetchFolderChildren(undefined, { setStack: [] }) }}><RefreshCw /> Case 다시 선택</button>
        </div>
        {folderBusy ? <p className="result-registration-state"><LoaderCircle className="result-registration-spin" /> 연결된 하위 폴더 확인 중</p> : null}
        {foldersError ? <p className="result-registration-error" role="alert"><AlertTriangle />{foldersError}</p> : null}
        {activeContextNode ? <div className="result-registration-context-summary" aria-label="선택한 Case 문맥">
          <strong>선택 문맥</strong>{contextTrail.map((entry) => <span key={`${entry.role}:${entry.relative_path}`}><b>{roleLabel(entry.role)}</b> {entry.label}</span>)}
        </div> : null}
        {!folderBusy && folderNodes.length > 0 ? <div className="result-registration-node-list" aria-label="연결된 하위 폴더">
          {folderNodes.map((node) => {
            const contextRole = node.role_kind === 'SIMULATION_CASE' || node.role_kind === 'EVALUATION' || (environment === 'DISTRIBUTION' && node.role_kind && distributionRoles.has(node.role_kind))
            const isResultsNode = node.role_kind === 'RESULTS'
            return <article key={`${node.role_kind}:${node.relative_path}`} className={isResultsNode ? 'result-registration-node result-registration-node--result' : 'result-registration-node'}>
              <div><strong>{node.name}</strong><span>{roleLabel(node.role_kind)} · {node.relative_path}</span></div>
              <small>{statusLabel(node.result_state)}</small>
              {contextRole ? <button type="button" disabled={isActionBusy || node.selectable === false} onClick={() => chooseContextNode(node)}>{activeContextNode?.relative_path === node.relative_path ? '현재 문맥' : '문맥 선택'}</button> : null}
              {isResultsNode && node.result_state === 'PRESENT' ? <button type="button" disabled={isActionBusy} onClick={() => selectResultFolder(node.relative_path, 'PRESENT', node)}>기존 결과 폴더 사용</button> : null}
              {!contextRole && !isResultsNode && node.children_available ? <button type="button" disabled={folderBusy || isActionBusy} onClick={() => openFolder(node)}><FolderOpen /> 하위 폴더 보기</button> : null}
            </article>
          })}
        </div> : null}
        {!folderBusy && !folderNodes.length ? <p className="result-registration-empty">현재 폴더에 연결된 하위 항목이 없습니다. 기존 Case를 선택하거나 아래에서 필요한 결과 폴더 계층을 확인하세요.</p> : null}

        {activeContextNode?.suggested_relative_path ? <div className="result-registration-destination">
          <div><span>선택 문맥의 결과 위치</span><code>{suggestedPath}</code><small>{statusLabel(suggestedState)} · 경로 읽기 전용</small></div>
          {suggestedState === 'PRESENT' ? <button type="button" disabled={isActionBusy} className={currentSuggestedDestinationSelected ? 'result-registration-selected' : ''} onClick={() => selectResultFolder(suggestedPath, 'PRESENT')}>{currentSuggestedDestinationSelected ? <><Check /> 선택됨</> : '이 기존 폴더 선택'}</button> : null}
          {suggestedState === 'MISSING' && activeContextNode.can_prepare ? <button type="button" disabled={isActionBusy} onClick={previewSuggestedPath}>하위 결과 폴더 준비</button> : null}
          {suggestedState === 'UNAVAILABLE' ? <p role="alert">이 경로에 접근할 수 없습니다. 관리자에게 저장소 접근 권한을 요청하세요.</p> : null}
        </div> : null}

        {selectedResultPath ? <div className={`result-registration-selected-path ${selectedResultState === 'UNAVAILABLE' ? 'is-error' : ''}`}><Check /><div><span>결과 저장 위치</span><code>{selectedResultPath}</code></div><b>{preparedFolder ? '준비 완료' : statusLabel(selectedResultState)}</b></div> : null}
        {preparePreview ? <div className="result-registration-prepare-preview" aria-label="결과 폴더 생성 경로 미리보기"><div><strong>생성 전 전체 경로 확인</strong><span>SPDM의 프로젝트·의뢰 폴더와 DB 업무 항목은 생성하지 않습니다.</span></div><code>{preparePreview.result_relative_path}</code><ul>{(preparePreview.proposed_paths ?? [{ relative_path: preparePreview.result_relative_path, role_kind: 'RESULTS', name: 'results', exists: false }]).map((path) => <li key={`${path.role_kind}:${path.relative_path}`}><code>{path.relative_path}</code><span>{roleLabel(path.role_kind)} · {path.exists ? '기존 폴더' : '새 폴더 생성'}</span></li>)}</ul><label className="result-registration-confirm"><input type="checkbox" checked={confirmCreateFolder} onChange={(event) => setConfirmCreateFolder(event.target.checked)} /><span>표시된 결과용 하위 폴더만 생성하도록 확인했습니다.</span></label><button type="button" className="data-submit" disabled={!confirmCreateFolder || isActionBusy} onClick={() => void confirmPreparation()}><Check /> 경로 확인 후 폴더 생성</button></div> : null}

        <details className="result-registration-missing-builder">
          <summary>필요한 Case·환경별 하위 폴더가 없을 때</summary>
          <p>서버가 확인한 기존 SPDM 의뢰 폴더 아래에 환경별 폴더 경로만 준비합니다. 새 업무·DB Case·하중경우는 만들지 않습니다.</p>
          {missingPlan.fieldKeys.includes('case') ? <label><span>Case 폴더 이름</span><input value={builderCaseName} disabled={Boolean(draftId) || isActionBusy} onChange={(event) => { setBuilderCaseName(event.target.value); setPreparePreview(null) }} placeholder="예: Assy_RES_001" /></label> : null}
          {environment === 'USAGE' && missingPlan.fieldKeys.includes('evaluation') ? <label><span>평가 항목</span><select value={builderEvaluation} disabled={Boolean(draftId) || isActionBusy} onChange={(event) => { setBuilderEvaluation(event.target.value); setPreparePreview(null) }}>{evaluationNames.map((name) => <option key={name} value={name}>{name}</option>)}</select></label> : null}
          {environment === 'DISTRIBUTION' && missingPlan.fieldKeys.includes('load') ? <label><span>하중경우 폴더 이름</span><input value={builderLoadName} disabled={Boolean(draftId) || isActionBusy} onChange={(event) => { setBuilderLoadName(event.target.value); setPreparePreview(null) }} placeholder="예: Drop_800mm" /></label> : null}
          {environment === 'DISTRIBUTION' && missingPlan.fieldKeys.includes('run') ? <label><span>Run Case 폴더 이름</span><input value={builderRunName} disabled={Boolean(draftId) || isActionBusy} onChange={(event) => { setBuilderRunName(event.target.value); setPreparePreview(null) }} placeholder="예: Run_01" /></label> : null}
          {environment === 'DISTRIBUTION' && effectiveContext.run_option?.status !== 'PRESENT' && activeContextNode?.context.execution_run ? <label className="result-registration-option-toggle"><input type="checkbox" checked={builderIncludeOption} disabled={Boolean(draftId) || isActionBusy} onChange={(event) => { setBuilderIncludeOption(event.target.checked); setPreparePreview(null) }} /><span>Run Option 결과 폴더 추가</span></label> : null}
          {environment === 'DISTRIBUTION' && (missingPlan.fieldKeys.includes('option') || builderIncludeOption) ? <label><span>Run Option 이름</span><input value={builderOptionName} disabled={!builderIncludeOption || Boolean(draftId) || isActionBusy} onChange={(event) => { setBuilderOptionName(event.target.value); setPreparePreview(null) }} placeholder="예: Option_A" /></label> : null}
          {environment === 'DISTRIBUTION' && missingPlan.fieldKeys.includes('scene') ? <label><span>Scene 폴더 이름</span><input value={builderSceneName} disabled={Boolean(draftId) || isActionBusy} onChange={(event) => { setBuilderSceneName(event.target.value); setPreparePreview(null) }} placeholder="예: Bottom" /></label> : null}
          <button type="button" className="ghost-button" disabled={!missingPlan.ready || Boolean(draftId) || isActionBusy} onClick={previewMissingHierarchy}><FolderOpen /> 전체 경로 미리보기</button>
        </details>
      </section>

      <section className="result-registration-card result-registration-upload" aria-labelledby="registration-upload-title">
        <header><span>03 · 격리 업로드와 자동 검사</span><h3 id="registration-upload-title">파일 업로드</h3><p>업로드 직후 자동 검사하며, 검수 전에는 정식 Case 결과로 게시되지 않습니다.</p></header>
        <div className="result-registration-upload-controls">
          <label className={`result-registration-file-picker${selectedResultPath && (selectedResultState === 'PRESENT' || preparedFolder) ? '' : ' is-disabled'}`} htmlFor="registration-files"><Upload /><strong>결과 파일과 영상·이미지 선택</strong><span>CSV / JSON / JPG / PNG / MP4 / WebM · 파일당 32 MiB · 묶음당 256 MiB</span></label>
          <input ref={fileInputRef} id="registration-files" aria-label="결과 파일과 영상·이미지 선택" type="file" multiple accept=".csv,.json,.jpg,.jpeg,.png,.mp4,.webm" disabled={!selectedResultPath || (selectedResultState !== 'PRESENT' && !preparedFolder) || isActionBusy || Boolean(draftId)} onChange={(event) => chooseFiles(Array.from(event.target.files ?? []))} />
          {selectedFiles.length ? <div className="result-registration-file-list" aria-label="선택한 파일">{pathPreviews.map(({ file, relativePath }) => <div key={relativePath}><span>{fileMediaType(file).startsWith('video/') ? <FileVideo /> : fileMediaType(file).startsWith('image/') ? <FileImage /> : <Database />}</span><div><strong>{relativePath}</strong><small>{fileMediaType(file)} · {formatBytes(file.size)}</small></div>{!draftId ? <button type="button" aria-label={`${relativePath} 제외`} disabled={isActionBusy} onClick={() => chooseFiles(selectedFiles.filter((item) => filePath(item) !== relativePath))}><X /></button> : null}{previewUrls[relativePath] && fileMediaType(file).startsWith('image/') ? <img src={previewUrls[relativePath]} alt={`${relativePath} 미리보기`} /> : null}{previewUrls[relativePath] && fileMediaType(file).startsWith('video/') ? <video src={previewUrls[relativePath]} controls preload="metadata" aria-label={`${relativePath} 미리보기`} /> : null}</div>)}</div> : null}
          <div className="result-registration-upload-footer"><span>{selectedFiles.length}개 파일 · {formatBytes(selectedFilesBytes)}</span><button type="button" className="data-submit" disabled={!canInspect} onClick={() => void uploadAndInspect()}>{isActionBusy ? <LoaderCircle className="result-registration-spin" /> : <Upload />} 업로드하고 자동 검사</button></div>
          {draftId && !approved && !completion ? <div className="result-registration-correction"><p>업로드나 자동 검사에 문제가 있으면 현재 초안을 게시하지 않고 파일을 다시 선택해 새 검수를 시작할 수 있습니다.</p><button type="button" className="ghost-button" disabled={isActionBusy} onClick={startFreshUpload}>파일 수정 후 새 검수 시작</button></div> : null}
        </div>
      </section>
    </> : null}

    {formError ? <p className="result-registration-error" role="alert"><AlertTriangle />{formError}</p> : null}
    {notice ? <p className="result-registration-notice" role="status"><Check />{notice}</p> : null}
    {busyAction ? <p className="result-registration-state" role="status"><LoaderCircle className="result-registration-spin" />{busyAction}</p> : null}

    {inspection ? <ReviewInspection inspection={inspection} previewUrls={previewUrls} selectedFiles={selectedFiles} draftId={draftId} exclusions={fileExclusions} exclusionsChanged={exclusionsChanged} canReinspectExclusions={canReinspectExclusions} hasInvalidExclusionReason={hasInvalidExclusionReason} isActionBusy={isActionBusy} readOnly={approved} onExcludeChange={setFileExcluded} onExclusionReasonChange={setFileExclusionReason} onReinspectExclusions={() => void reinspectWithExclusions()} /> : null}
    {inspection && draftId && !completion ? <section className="result-registration-card result-registration-approval" aria-label="검수 승인과 DB 등록">
      <header><span>04 · 사용자 검수</span><h3>검수 완료 후 DB 등록</h3><p>자동 검사는 파일 형식과 데이터 정합성만 확인합니다. 공학적 타당성 판정은 대신하지 않습니다.</p></header>
      {inspection.blocking_count > 0 ? <p className="result-registration-error" role="alert"><AlertTriangle />필수 오류 {inspection.blocking_count}건이 있어 DB 등록을 진행할 수 없습니다. 파일을 수정한 뒤 새 검수로 다시 올려 주세요.</p> : null}
      {exclusionsChanged ? <p className="result-registration-notice" role="status">제외 대상 또는 사유가 변경되었습니다. 제외를 반영해 다시 검사한 뒤 승인할 수 있습니다.</p> : null}
      {partialRequired ? <label className="result-registration-partial-ack"><input type="checkbox" checked={acknowledgePartial} disabled={isActionBusy || approved || exclusionsChanged} onChange={(event) => setAcknowledgePartial(event.target.checked)} /><span>누락·제외 항목과 이번 업로드 파일만으로 새 수집 버전을 만드는 점을 확인했습니다.</span></label> : null}
      <button type="button" className="data-submit result-registration-publish" disabled={inspection.blocking_count > 0 || exclusionsChanged || hasInvalidExclusionReason || (partialRequired && !acknowledgePartial) || isActionBusy} onClick={() => void approveAndPublish()}><ClipboardCheck />{approved ? 'DB 등록 다시 시도' : '검수 완료·DB 등록'}</button>
    </section> : null}

    {completion ? <CompletionPanel completion={completion} onRetryMirror={() => void retryMirror(completion)} retrying={isActionBusy} /> : null}
  </section>
}

function needsPartialAcknowledgement(inspection: ResultInspection) {
  return inspection.missing_count > 0 || inspection.manifest.some((item) => item.inspection_status === 'EXCLUDED') || inspection.issues.some((issue) => issue.severity?.toUpperCase() !== 'ERROR')
}

function ReviewInspection({ inspection, previewUrls, selectedFiles, draftId, exclusions, exclusionsChanged, canReinspectExclusions, hasInvalidExclusionReason, isActionBusy, readOnly, onExcludeChange, onExclusionReasonChange, onReinspectExclusions }: {
  inspection: ResultInspection
  previewUrls: Record<string, string>
  selectedFiles: File[]
  draftId: string
  exclusions: Record<string, string>
  exclusionsChanged: boolean
  canReinspectExclusions: boolean
  hasInvalidExclusionReason: boolean
  isActionBusy: boolean
  readOnly: boolean
  onExcludeChange: (relativePath: string, checked: boolean) => void
  onExclusionReasonChange: (relativePath: string, reason: string) => void
  onReinspectExclusions: () => void
}) {
  const filesByPath = useMemo(() => new Map(selectedFiles.map((file) => [filePath(file), file])), [selectedFiles])
  return <section className="result-registration-card result-registration-inspection" aria-labelledby="registration-inspection-title" data-testid="result-registration-inspection">
    <header><span>자동 검사 · 검수 대기</span><h3 id="registration-inspection-title">읽은 값과 원본을 확인하세요</h3><p>자동 검사 결과를 확인하고 오류 파일은 아래에서 제외 사유를 입력해 다시 검사하거나, 파일을 수정해 새 검수를 시작할 수 있습니다.</p><div className="result-registration-inspection-counts"><span>결과값 {inspection.metrics.length}</span><span>미디어 {inspection.media.length}</span><span>누락 {inspection.missing_count}</span><strong className={inspection.blocking_count ? 'is-error' : ''}>게시 차단 {inspection.blocking_count}</strong></div></header>
    <div className="result-registration-review-block"><h4>업로드 파일 검사</h4><div className="result-registration-review-table"><table><thead><tr><th>파일</th><th>업로드</th><th>검사</th><th>형식</th><th>이번 등록 제외</th></tr></thead><tbody>{inspection.manifest.map((item) => {
      const excluded = Object.hasOwn(exclusions, item.relative_path)
      return <tr key={item.relative_path}><td>{item.relative_path}</td><td>{statusLabel(item.upload_status)}</td><td>{statusLabel(item.inspection_status)}</td><td>{item.media_type} · {formatBytes(item.size)}</td><td><label className="result-registration-exclusion"><input type="checkbox" aria-label={`이번 등록에서 제외: ${item.relative_path}`} checked={excluded} disabled={isActionBusy || readOnly} onChange={(event) => onExcludeChange(item.relative_path, event.target.checked)} /><span>제외</span></label>{excluded ? <input className="result-registration-exclusion-reason" type="text" aria-label={`파일 제외 사유: ${item.relative_path}`} placeholder="제외 사유를 입력하세요" value={exclusions[item.relative_path] ?? ''} disabled={isActionBusy || readOnly} onChange={(event) => onExclusionReasonChange(item.relative_path, event.target.value)} /> : null}</td></tr>
    })}</tbody></table></div>
      {exclusionsChanged ? <div className="result-registration-exclusion-action" role="status"><p>제외할 파일이나 사유가 바뀌었습니다. 다시 검사한 결과를 확인한 뒤 등록하세요.</p>{hasInvalidExclusionReason ? <small role="alert">선택한 각 파일에 제외 이유가 필요합니다.</small> : null}<button type="button" className="ghost-button" disabled={!canReinspectExclusions} onClick={onReinspectExclusions}>{isActionBusy ? <LoaderCircle className="result-registration-spin" /> : <RefreshCw />} 제외 반영해 다시 검사</button></div> : null}
    </div>
    <div className="result-registration-review-block"><h4>검사한 결과값</h4>{inspection.metrics.length ? <div className="result-registration-review-table"><table><thead><tr><th>평가·항목</th><th>원문 값</th><th>단위</th><th>원본 필드</th><th>검사 상태</th></tr></thead><tbody>{inspection.metrics.map((metric, index) => <tr key={`${metric.source_path}:${metric.key}:${index}`}><td>{[metric.evaluation, metric.metric].filter(Boolean).join(' · ') || metric.key || '평가 항목'}</td><td>{displayValue(metric.value)}</td><td>{metric.unit || '—'}</td><td><span>{metric.source_path || '—'}</span>{metric.key ? <small>{metric.key}</small> : null}</td><td>{statusLabel(metric.status)}</td></tr>)}</tbody></table></div> : <p className="result-registration-empty">검사 결과값이 없습니다. 누락·제외 상태를 확인하세요.</p>}</div>
    <div className="result-registration-review-block"><h4>연결된 영상·이미지</h4>{inspection.media.length ? <div className="result-registration-media-list">{inspection.media.map((media) => {
      const file = filesByPath.get(media.relative_path)
      const url = previewUrls[media.relative_path] || (media.status === 'READY' ? resultRegistrationApi.mediaUrl(draftId, media.relative_path) : '')
      return <article key={`${media.relative_path}:${media.sha256}`}><div><strong>{media.title || media.relative_path}</strong><span>{media.kind === 'VIDEO' ? '영상' : media.kind === 'IMAGE' ? '이미지' : '미디어'} · {media.media_type} · {statusLabel(media.status)}</span><small>{media.relative_path} · {formatBytes(media.size)}</small></div>{url && media.kind === 'IMAGE' ? <img src={url} alt={`${media.relative_path} 검수 미리보기`} /> : null}{url && media.kind === 'VIDEO' ? <video src={url} controls preload="metadata" aria-label={`${media.relative_path} 검수 미리보기`} /> : null}{!file && media.status !== 'READY' ? <small>미디어 원본을 미리 볼 수 없습니다.</small> : null}</article>
    })}</div> : <p className="result-registration-empty">연결된 영상·이미지가 없습니다.</p>}</div>
    {inspection.issues.length ? <div className="result-registration-review-block"><h4>검사 메모</h4><ul className="result-registration-issues">{inspection.issues.map((issue, index) => <li key={`${issue.code}:${issue.source_path}:${index}`} className={issue.severity?.toUpperCase() === 'ERROR' ? 'is-error' : ''}><strong>{severityLabels[issue.severity?.toUpperCase() ?? ''] ?? '확인'}</strong><span>{issueText(issue)}</span></li>)}</ul></div> : null}
  </section>
}

function CompletionPanel({ completion, onRetryMirror, retrying }: { completion: Completion; onRetryMirror: () => void; retrying: boolean }) {
  const { published } = completion
  const mirrorNeedsRetry = ['MIRROR_PENDING', 'MIRROR_CONFLICT', 'MIRROR_FAILED'].includes(published.status)
  const mirrorConflict = published.status === 'MIRROR_CONFLICT'
  return <section className="result-registration-card result-registration-completion" aria-label="DB 등록 완료" data-testid="result-registration-completion">
    <header><span>{mirrorConflict ? 'DB 등록 완료 · 폴더 반영 충돌' : 'DB 등록 완료'}</span><h3>결과값과 영상이 Case 수집 버전에 저장되었습니다.</h3><p>{completion.targetLabel} · {published.environment === 'USAGE' ? '사용환경' : '유통환경'}</p></header>
    <div className="result-registration-completion-stats"><div><span>수집 버전 ID</span><strong>{published.capture_id}</strong></div><div><span>결과값</span><strong>{published.result_count}</strong></div><div><span>미디어</span><strong>{published.media_count ?? completion.mediaCount}</strong></div><div><span>이미지 / 영상</span><strong>{published.image_count ?? completion.imageCount} / {published.video_count ?? completion.videoCount}</strong></div></div>
    {mirrorNeedsRetry ? <div className="result-registration-mirror-warning" role="status"><p><AlertTriangle />{mirrorConflict ? '기존 저장 폴더의 파일과 충돌해 원본 폴더에는 반영하지 않았습니다. 기존 파일은 덮어쓰지 않았습니다.' : published.error?.message || 'DB 등록은 완료됐지만 선택한 저장 폴더 반영이 끝나지 않았습니다.'} 수집 버전은 보존되어 있으며 다시 게시되지 않습니다.</p><button type="button" className="ghost-button" onClick={onRetryMirror} disabled={retrying}><RefreshCw /> 저장 폴더 반영만 재시도</button></div> : null}
    <Link className="data-submit result-registration-case-link" to={resultLink(published)}><Database /> Case 결과 보기</Link>
  </section>
}
