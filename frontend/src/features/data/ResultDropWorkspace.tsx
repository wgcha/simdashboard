import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent } from 'react'
import { AlertTriangle, CheckCircle2, Copy, FilePlus2, FolderInput, FolderPlus, LoaderCircle, Square, UploadCloud } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../../api'
import type { AnalysisRequest, Project } from '../../types'
import { Button } from '../../shared/components/Button'
import { HierarchyPath } from '../../shared/components/HierarchyPath'
import { Select } from '../../shared/components/Select'
import { SearchableSelect } from '../../shared/components/selectionLabels'
import { ApiError } from '../../shared/api/errors'
import { requestResultEnvironments, resolvedResultEnvironment } from '../../shared/api/simulationDashboard'
import { chunkSha256, resultDropApi, type DropCompletion, type DropConflict, type DropEnvironment, type DropPlan, type DropTree, type OpenDropSession } from '../../shared/api/resultDrop'
import { ROLE_LABELS, SKIP_REASON_LABELS, chunkRanges, depthRules, dropEntries, dropGuide, formatBytes, pickedFromInput, walkEntries, type PickedItems } from '../../shared/api/resultDropModel'
import { LegacyDraftHistory } from './LegacyDraftHistory'
import './DataWorkspace.css'
import './ResultDropWorkspace.css'

export type ResultDropWorkspaceProps = {
  embedded?: boolean
  contextChanging?: boolean
  projects: Project[]
  initialProjectId: string
  initialRequestId?: string
  onContextChange?: (context: { projectId: string; requestId: string }) => void
  onDataChanged: () => Promise<void>
}

type Progress = { sessionId: string; fileCount: number; doneFiles: number; totalBytes: number; sentBytes: number; current: string }
const PLAN_ROW_LIMIT = 200
const RETRIES = 3

function messageOf(reason: unknown, fallback: string) {
  if (reason instanceof ApiError && reason.detail && typeof reason.detail === 'object' && 'message' in reason.detail && typeof reason.detail.message === 'string') return reason.detail.message
  if (reason instanceof Error) return reason.message.replace(/^[A-Z][A-Z0-9_]{2,}:\s*/, '') || fallback
  return fallback
}
function detailOf(reason: unknown): Record<string, unknown> {
  return reason instanceof ApiError && reason.detail && typeof reason.detail === 'object' ? reason.detail as Record<string, unknown> : {}
}
function caseResultsHref(projectId: string, requestId: string, environment: DropEnvironment, caseId?: string | null) {
  const query = new URLSearchParams({ project: projectId, request: requestId, view: 'case_results', result_environment: environment })
  if (caseId) query.set('case', caseId)
  return `/workspace/requests?${query.toString()}`
}
async function copyText(text: string) {
  try {
    if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return true }
  } catch { /* plain HTTP on a LAN host: fall back below */ }
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  let copied = false
  try { copied = document.execCommand('copy') } catch { copied = false }
  area.remove()
  return copied
}

export function ResultDropWorkspace({ embedded = false, contextChanging = false, projects, initialProjectId, initialRequestId, onContextChange, onDataChanged }: ResultDropWorkspaceProps) {
  const [localProjectId, setLocalProjectId] = useState(initialProjectId)
  const [localRequestId, setLocalRequestId] = useState(initialRequestId || '')
  const [requests, setRequests] = useState<AnalysisRequest[]>([])
  const projectId = embedded ? initialProjectId : localProjectId
  const requestId = embedded ? initialRequestId || '' : localRequestId
  const [environment, setEnvironment] = useState<DropEnvironment>(() => new URLSearchParams(window.location.search).get('result_environment') === 'USAGE' ? 'USAGE' : 'DISTRIBUTION')
  const [lockedEnvironment, setLockedEnvironment] = useState<DropEnvironment | null>(null)
  const [tree, setTree] = useState<DropTree | null>(null)
  const [treeError, setTreeError] = useState('')
  const [treeBusy, setTreeBusy] = useState(false)
  const [treeVersion, setTreeVersion] = useState(0)
  const [chosen, setChosen] = useState<string[]>([])
  const [notice, setNotice] = useState('')
  const [folderOpen, setFolderOpen] = useState(false)
  const [folderName, setFolderName] = useState('')
  const [folderWarning, setFolderWarning] = useState<string[]>([])
  const [folderError, setFolderError] = useState('')
  const [folderBusy, setFolderBusy] = useState(false)
  const [picked, setPicked] = useState<PickedItems | null>(null)
  const [plan, setPlan] = useState<DropPlan | null>(null)
  const [planBusy, setPlanBusy] = useState(false)
  const [error, setError] = useState('')
  const [dragging, setDragging] = useState(false)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [conflicts, setConflicts] = useState<DropConflict[]>([])
  const [completion, setCompletion] = useState<DropCompletion | null>(null)
  const stopRef = useRef(false)
  // Upload phase for unload handling: while chunks are sent the session is aborted on unload
  // (nothing published yet); while files are being published the page only warns.
  const phaseRef = useRef<{ sessionId: string; phase: 'uploading' | 'publishing' } | null>(null)
  const [openSessions, setOpenSessions] = useState<OpenDropSession[]>([])
  const [sessionsVersion, setSessionsVersion] = useState(0)
  const [partialBusy, setPartialBusy] = useState(false)
  const fileInput = useRef<HTMLInputElement | null>(null)
  const folderInput = useRef<HTMLInputElement | null>(null)
  const scopeKey = `${projectId}:${requestId}:${environment}`

  useEffect(() => {
    if (embedded) return
    setLocalProjectId((current) => current && projects.some((item) => item.id === current) ? current : initialProjectId || projects[0]?.id || '')
  }, [embedded, initialProjectId, projects])
  useEffect(() => {
    if (embedded || !projectId) return
    let active = true
    api.requests(projectId).then((items) => {
      if (!active) return
      setRequests(items)
      setLocalRequestId((current) => items.some((item) => item.id === (initialRequestId || current)) ? initialRequestId || current : items[0]?.id || '')
    }).catch(() => { if (active) setRequests([]) })
    return () => { active = false }
  }, [embedded, initialRequestId, projectId])
  useEffect(() => { if (!embedded && projectId && requestId) onContextChange?.({ projectId, requestId }) }, [embedded, onContextChange, projectId, requestId])

  // A request with Cases of one environment fixes it (E5); otherwise the user chooses.
  useEffect(() => {
    setLockedEnvironment(null)
    if (!projectId || !requestId) return
    const controller = new AbortController()
    requestResultEnvironments(projectId, requestId, controller.signal).then((value) => {
      if (controller.signal.aborted) return
      const resolved = resolvedResultEnvironment(value)
      setLockedEnvironment(resolved)
      if (resolved) setEnvironment(resolved)
    }).catch(() => undefined)
    return () => controller.abort()
  }, [projectId, requestId])

  const resetDrop = useCallback(() => { setPicked(null); setPlan(null); setError(''); setConflicts([]); setProgress(null) }, [])
  useEffect(() => { setChosen([]); resetDrop(); setCompletion(null); setFolderOpen(false); setNotice('') }, [resetDrop, scopeKey])
  useEffect(() => {
    setTree(null); setTreeError('')
    if (!projectId || !requestId || contextChanging) return
    const controller = new AbortController()
    setTreeBusy(true)
    resultDropApi.tree({ project_id: projectId, request_id: requestId, environment }, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setTree(value) })
      .catch((reason) => { if (!controller.signal.aborted) setTreeError(messageOf(reason, '의뢰 폴더를 불러오지 못했습니다.')) })
      .finally(() => { if (!controller.signal.aborted) setTreeBusy(false) })
    return () => controller.abort()
  }, [contextChanging, environment, projectId, requestId, treeVersion])

  useEffect(() => {
    // beforeunload only asks "leave page?"; the abort is sent when the page is really left
    // (pagehide), so cancelling the prompt keeps the upload running (re-review N2).
    const onBeforeUnload = (event: BeforeUnloadEvent) => { if (phaseRef.current) event.preventDefault() }
    const onPageHide = () => {
      const current = phaseRef.current
      if (current?.phase === 'uploading') resultDropApi.abortOnUnload(current.sessionId)
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    window.addEventListener('pagehide', onPageHide)
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload)
      window.removeEventListener('pagehide', onPageHide)
      stopRef.current = true // leaving the screen stops a running upload (its loop aborts the session)
    }
  }, [])
  useEffect(() => {
    setOpenSessions([])
    if (!projectId || !requestId || contextChanging) return
    const controller = new AbortController()
    resultDropApi.openSessions({ project_id: projectId, request_id: requestId }, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setOpenSessions(value.sessions) })
      .catch(() => undefined)
    return () => controller.abort()
  }, [contextChanging, projectId, requestId, sessionsVersion])

  const roles = useMemo(() => tree?.levels.map((level) => level.role) ?? [], [tree])
  const target = chosen.length ? chosen[chosen.length - 1] : tree?.working_relative_path ?? ''
  const targetNode = tree?.nodes.find((node) => node.relative_path === target)
  const targetRole = targetNode?.role ?? 'WORKING'
  const targetDisplay = targetNode?.display_path ?? tree?.working_display_path ?? ''
  const nextRole = roles[roles.indexOf(targetRole) + 1]
  const guide = dropGuide(targetRole, roles)
  const busy = planBusy || Boolean(progress) || folderBusy
  const uploading = Boolean(progress)

  const choose = (levelIndex: number, value: string) => {
    setChosen((current) => value ? [...current.slice(0, levelIndex), value] : current.slice(0, levelIndex))
    resetDrop(); setCompletion(null); setFolderOpen(false)
  }
  const copyPath = async () => {
    setNotice(await copyText(targetDisplay) ? '경로를 복사했습니다. 탐색기 주소창에 붙여 넣으세요.' : '복사하지 못했습니다. 경로를 선택해 Ctrl+C로 복사하세요.')
  }

  const createFolder = async (confirm: boolean) => {
    if (!tree || !folderName.trim()) return
    setFolderBusy(true); setFolderError('')
    try {
      const created = await resultDropApi.createFolder({ project_id: projectId, request_id: requestId, environment, parent_relative_path: target, name: folderName.trim(), confirm })
      setFolderOpen(false); setFolderName(''); setFolderWarning([])
      setNotice(`새 ${created.role_label} 폴더를 만들었습니다: ${created.display_path}`)
      setChosen((current) => [...current, created.relative_path])
      setTreeVersion((value) => value + 1)
    } catch (reason) {
      const detail = detailOf(reason)
      if (detail.code === 'RESULT_DROP_FOLDER_NAME_WARNING' && Array.isArray(detail.warnings)) setFolderWarning(detail.warnings as string[])
      else setFolderError(messageOf(reason, '폴더를 만들지 못했습니다.'))
    } finally { setFolderBusy(false) }
  }

  const planFor = async (items: PickedItems) => {
    setPicked(items); setPlan(null); setError(''); setConflicts([]); setCompletion(null)
    if (!items.files.length && !items.folders.length) { setError('올릴 파일이 없습니다.'); return }
    setPlanBusy(true)
    try {
      setPlan(await resultDropApi.plan({ project_id: projectId, request_id: requestId, environment, target_relative_path: target,
        files: items.files.map((item) => ({ relative_path: item.relativePath, size: item.file.size })), folders: items.folders }))
    } catch (reason) {
      const detail = detailOf(reason)
      const skipped = Array.isArray(detail.skipped) ? detail.skipped as Array<{ relative_path: string }> : []
      setError(messageOf(reason, '올릴 내용을 확인하지 못했습니다.') + (skipped.length ? ` (제외: ${skipped.map((item) => item.relative_path).join(', ')})` : ''))
    } finally { setPlanBusy(false) }
  }
  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault(); setDragging(false)
    if (busy || !tree) return
    const { entries, files } = dropEntries(event.dataTransfer)
    void (entries.length ? walkEntries(entries) : Promise.resolve(pickedFromInput(files)))
      .then(planFor)
      .catch((reason) => setError(messageOf(reason, '끌어 놓은 항목을 읽지 못했습니다.')))
  }
  const onPick = (event: ChangeEvent<HTMLInputElement>) => {
    const list = event.target.files
    if (list?.length) void planFor(pickedFromInput(list))
    event.target.value = ''
  }

  const upload = async () => {
    if (!plan?.can_upload || !picked) return
    stopRef.current = false; setError(''); setConflicts([])
    const input = { project_id: projectId, request_id: requestId, environment, target_relative_path: target,
      files: picked.files.map((item) => ({ relative_path: item.relativePath, size: item.file.size })), folders: picked.folders }
    let sessionId = ''
    try {
      const session = await resultDropApi.start(input)
      sessionId = session.session_id
      phaseRef.current = { sessionId, phase: 'uploading' }
      const state: Progress = { sessionId, fileCount: session.file_count, doneFiles: 0, totalBytes: session.total_bytes, sentBytes: 0, current: '' }
      setProgress({ ...state })
      for (const entry of session.files) {
        const source = picked.files[entry.client_index]?.file
        if (!source) throw new Error(`파일을 찾을 수 없습니다: ${entry.relative_path}`)
        state.current = entry.relative_path; setProgress({ ...state })
        let offset = 0
        for (let attempt = 0; ; attempt += 1) {
          try {
            for (const [start, end] of chunkRanges(entry.size, session.chunk_bytes, offset)) {
              if (stopRef.current) throw new Error('STOPPED')
              const buffer = await source.slice(start, end).arrayBuffer()
              const result = await resultDropApi.chunk(sessionId, entry.index, start, buffer, await chunkSha256(buffer))
              state.sentBytes += result.received - offset; offset = result.received; setProgress({ ...state })
            }
            break
          } catch (reason) {
            if (reason instanceof Error && reason.message === 'STOPPED') throw reason
            const expected = detailOf(reason).expected_offset
            if (attempt >= RETRIES || (reason instanceof ApiError && reason.status < 500 && typeof expected !== 'number')) throw reason
            // Resume from where the server is (offset mismatch, network or 5xx).
            const current = (await resultDropApi.session(sessionId)).files[entry.index]
            state.sentBytes += current.received - offset; offset = current.received
            if (current.complete) break
          }
        }
        state.doneFiles += 1; setProgress({ ...state })
      }
      phaseRef.current = { sessionId, phase: 'publishing' }
      const done = await resultDropApi.complete(sessionId)
      phaseRef.current = null
      setCompletion(done); setProgress(null); setPicked(null); setPlan(null); setConflicts(done.conflicts)
      setTreeVersion((value) => value + 1)
      setSessionsVersion((value) => value + 1)
      void onDataChanged().catch(() => undefined)
    } catch (reason) {
      phaseRef.current = null
      const stopped = reason instanceof Error && reason.message === 'STOPPED'
      const detail = detailOf(reason)
      if (Array.isArray(detail.conflicts)) setConflicts(detail.conflicts as DropConflict[])
      // Nothing was published: remove this session's staged files (the server never touches others).
      if (sessionId) await resultDropApi.abort(sessionId).catch(() => undefined)
      setProgress(null); setSessionsVersion((value) => value + 1)
      setError(stopped ? '업로드를 중지했습니다. 임시 파일은 지웠고 공유 폴더는 바뀌지 않았습니다.' : messageOf(reason, '업로드하지 못했습니다.'))
    }
  }

  // A partially published upload (files locked by another program): publish the rest, or stop it.
  const retryPartial = async () => {
    if (!completion) return
    setPartialBusy(true); setError('')
    try {
      const done = await resultDropApi.complete(completion.session_id)
      setCompletion({ ...done, cases: done.cases.length ? done.cases : completion.cases })
      setTreeVersion((value) => value + 1)
    } catch (reason) { setError(messageOf(reason, '다시 옮기지 못했습니다.')) } finally { setPartialBusy(false); setSessionsVersion((value) => value + 1) }
  }
  const stopSession = async (sessionId: string) => {
    setPartialBusy(true); setError('')
    try {
      await resultDropApi.abort(sessionId)
      if (completion?.session_id === sessionId) setCompletion({ ...completion, state: 'ABORTED' })
      setNotice('업로드를 중지했습니다. 옮기지 못한 임시 파일과 빈 새 폴더를 지웠습니다.')
    } catch (reason) { setError(messageOf(reason, '중지하지 못했습니다.')) } finally { setPartialBusy(false); setSessionsVersion((value) => value + 1) }
  }
  const otherOpen = openSessions.filter((item) => item.session_id !== completion?.session_id && item.session_id !== progress?.sessionId)

  const activeProject = projects.find((item) => item.id === projectId)
  const levelSelects = tree ? tree.levels.slice(1).map((level, index) => {
    const parent = index === 0 ? tree.working_relative_path : chosen[index - 1]
    const choices = parent ? tree.nodes.filter((node) => node.level === level.level && node.parent_path === parent) : []
    const disabled = busy || !parent || !choices.length
    return <label key={level.level} className="shared-hierarchy-choice" data-hierarchy-level={level.label}>
      <span>{level.label}</span>
      <Select controlSize="sm" aria-label={level.label} value={chosen[index] ?? ''} disabled={disabled} title={!parent ? `${tree.levels[index].label}을(를) 먼저 선택하세요.` : !choices.length ? '하위 폴더가 없습니다.' : undefined} onChange={(event) => choose(index, event.target.value)}>
        <option value="">{!parent ? '—' : choices.length ? '여기까지' : '없음'}</option>
        {choices.map((node) => <option key={node.relative_path} value={node.relative_path}>{node.name}</option>)}
      </Select>
    </label>
  }) : []

  return <section className="data-workspace result-drop" data-ui-density="v1" data-testid="result-drop-workspace">
    {!embedded ? <header className="data-workspace-head"><div><span>SPDM / RESULT REGISTRATION</span><h1>결과 등록</h1><p>결과 폴더를 SPDM 공유 폴더에 넣으면 Case 결과에 자동 반영됩니다.</p></div></header> : null}
    {!embedded ? <div className="data-hierarchy-bar">
      <label><span>프로젝트</span><SearchableSelect ariaLabel="등록 프로젝트 선택" items={projects} kind="project" value={projectId} onChange={(value) => { setLocalProjectId(value); setLocalRequestId('') }} placeholder="프로젝트 선택" /></label><i>›</i>
      <label><span>의뢰</span><SearchableSelect ariaLabel="등록 의뢰 선택" items={requests} kind="request" value={requestId} onChange={setLocalRequestId} disabled={!requests.length} placeholder={requests.length ? '의뢰 선택' : '의뢰가 없습니다'} /></label>
    </div> : null}

    <section className="result-drop__card" aria-labelledby="result-drop-location-title">
      <header className="result-drop__head">
        <div><span>01 · 위치</span><h3 id="result-drop-location-title">넣을 폴더 선택</h3></div>
        <label className="result-drop__env"><span>환경</span><Select controlSize="sm" aria-label="결과 등록 환경" value={environment} disabled={busy || Boolean(lockedEnvironment)} title={lockedEnvironment ? '의뢰에 등록된 Case의 환경으로 정했습니다.' : undefined} onChange={(event) => setEnvironment(event.target.value as DropEnvironment)}><option value="DISTRIBUTION">유통환경</option><option value="USAGE">사용환경</option></Select></label>
      </header>
      {treeBusy ? <p className="result-drop__state" role="status"><LoaderCircle className="result-drop__spin" aria-hidden="true" /> 의뢰 폴더를 확인하는 중</p> : null}
      {treeError ? <p className="result-drop__error" role="alert"><AlertTriangle aria-hidden="true" />{treeError}</p> : null}
      {!projectId || !requestId ? <p className="result-drop__state">{activeProject ? '의뢰를 선택하세요.' : '프로젝트와 의뢰를 선택하세요.'}</p> : null}
      {tree ? <>
        <HierarchyPath label="결과 등록 위치" className="result-drop__path">
          <div className="shared-hierarchy-choice shared-hierarchy-choice--fixed" data-hierarchy-level="Working"><span>Working</span><b title={tree.working_display_path}>Working</b></div>
          {levelSelects}
        </HierarchyPath>
        <div className="result-drop__target">
          <code data-testid="result-drop-target-path" title={targetDisplay}>{targetDisplay}</code>
          <Button size="sm" onClick={() => void copyPath()} disabled={!targetDisplay}><Copy aria-hidden="true" />경로 복사</Button>
          <Button size="sm" variant="ghost" onClick={() => { setFolderOpen((value) => !value); setFolderError(''); setFolderWarning([]) }} disabled={busy || !nextRole} title={nextRole ? undefined : 'Scene 폴더 안에는 결과 파일을 바로 넣습니다.'}><FolderPlus aria-hidden="true" />새 폴더 만들기</Button>
        </div>
        {notice ? <p className="result-drop__notice" role="status">{notice}</p> : null}
        {folderOpen && nextRole ? <form className="result-drop__new-folder" onSubmit={(event) => { event.preventDefault(); void createFolder(folderWarning.length > 0) }}>
          <label><span>새 {ROLE_LABELS[nextRole] ?? nextRole} 폴더 이름</span><input aria-label="새 폴더 이름" value={folderName} maxLength={120} onChange={(event) => { setFolderName(event.target.value); setFolderWarning([]); setFolderError('') }} /></label>
          <Button size="sm" variant="primary" type="submit" disabled={folderBusy || !folderName.trim()}>{folderWarning.length ? '그래도 만들기' : '만들기'}</Button>
          <Button size="sm" variant="ghost" onClick={() => { setFolderOpen(false); setFolderWarning([]) }}>취소</Button>
          {folderWarning.map((text) => <p key={text} className="result-drop__warning" role="status"><AlertTriangle aria-hidden="true" />{text}</p>)}
          {folderError ? <p className="result-drop__error" role="alert"><AlertTriangle aria-hidden="true" />{folderError}</p> : null}
        </form> : null}
      </> : null}
    </section>

    {tree ? <section className="result-drop__card result-drop__guide" aria-labelledby="result-drop-guide-title">
      <header className="result-drop__head"><div><span>02 · 안내</span><h3 id="result-drop-guide-title">어느 깊이에 넣나요?</h3></div></header>
      <div className="result-drop__guide-body">
        <ol className="result-drop__ladder" aria-label="폴더 깊이">
          {roles.map((role, index) => <li key={role} style={{ paddingInlineStart: `${index * 1.25}em` }} className={role === targetRole ? 'is-current' : roles.indexOf(targetRole) + 1 === index ? 'is-next' : undefined}>
            <b>{ROLE_LABELS[role] ?? role}</b>{role === targetRole ? <small>선택한 위치</small> : roles.indexOf(targetRole) + 1 === index ? <small>여기 넣을 폴더</small> : null}
          </li>)}
          <li style={{ paddingInlineStart: `${roles.length * 1.25}em` }} className={targetRole === 'SCENE' ? 'is-next' : undefined}><b>결과 파일</b>{targetRole === 'SCENE' ? <small>여기 넣을 파일</small> : null}</li>
        </ol>
        <div className="result-drop__rules">
          <p className="result-drop__put"><strong>{ROLE_LABELS[targetRole] ?? targetRole}</strong>에는 {guide.put}{guide.example ? <small> 예: {guide.example}</small> : null}</p>
          <ul>{depthRules(roles).slice(1).reverse().map((rule) => <li key={rule.role}>{rule.text}</li>)}<li>결과 파일만 복사하려면 Scene 폴더 안에</li></ul>
          <p className="result-drop__auto"><CheckCircle2 aria-hidden="true" />탐색기로 복사해도, 여기서 올려도 30초 안에 자동 반영됩니다.</p>
        </div>
      </div>
    </section> : null}

    {tree ? <section className="result-drop__card" aria-labelledby="result-drop-upload-title">
      <header className="result-drop__head"><div><span>03 · 올리기</span><h3 id="result-drop-upload-title">파일·폴더 끌어 놓기</h3></div></header>
      <div className={`result-drop__zone${dragging ? ' is-dragging' : ''}`} data-testid="result-drop-zone" role="group" aria-label="끌어서 올리기"
        onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; if (!dragging) setDragging(true) }}
        onDragLeave={() => setDragging(false)} onDrop={onDrop}>
        <UploadCloud aria-hidden="true" />
        <p><strong>{targetDisplay.split(/[\\/]/).pop()}</strong>({ROLE_LABELS[targetRole] ?? targetRole})에 파일이나 폴더를 끌어 놓으세요.</p>
        <small>실행 파일·스크립트({tree.blocked_extensions.slice(0, 6).join(' ')} …)는 올리지 않습니다. 같은 이름 파일은 덮어쓰지 않습니다.</small>
        <div className="result-drop__pick">
          <Button size="sm" onClick={() => fileInput.current?.click()} disabled={busy}><FilePlus2 aria-hidden="true" />파일 선택</Button>
          <Button size="sm" onClick={() => folderInput.current?.click()} disabled={busy}><FolderInput aria-hidden="true" />폴더 선택</Button>
          <input ref={fileInput} type="file" multiple hidden aria-label="올릴 파일 선택" onChange={onPick} />
          <input ref={folderInput} type="file" multiple hidden aria-label="올릴 폴더 선택" onChange={onPick} {...{ webkitdirectory: '' }} />
        </div>
      </div>
      {planBusy ? <p className="result-drop__state" role="status"><LoaderCircle className="result-drop__spin" aria-hidden="true" /> 올릴 내용을 확인하는 중</p> : null}
      {error ? <p className="result-drop__error" role="alert"><AlertTriangle aria-hidden="true" />{error}</p> : null}
      {conflicts.length ? <div className="result-drop__conflicts" role="alert" data-testid="result-drop-conflicts"><strong>같은 이름의 파일이 이미 있어 덮어쓰지 않았습니다</strong><ul>{conflicts.slice(0, 20).map((item) => <li key={item.destination_relative_path}><code>{item.relative_path}</code></li>)}</ul></div> : null}
      {plan && !uploading ? <PlanView plan={plan} onUpload={() => void upload()} onReset={resetDrop} /> : null}
      {progress ? <div className="result-drop__progress" data-testid="result-drop-progress">
        <div className="result-drop__bar" role="progressbar" aria-label="업로드 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress.totalBytes ? Math.round(progress.sentBytes / progress.totalBytes * 100) : 100}><i style={{ width: `${progress.totalBytes ? progress.sentBytes / progress.totalBytes * 100 : 100}%` }} /></div>
        <p><strong>{progress.doneFiles} / {progress.fileCount} 파일</strong> · {formatBytes(progress.sentBytes)} / {formatBytes(progress.totalBytes)}{progress.current ? <> · <code>{progress.current}</code></> : null}</p>
        <Button size="sm" variant="ghost" onClick={() => { stopRef.current = true }}><Square aria-hidden="true" />중지</Button>
      </div> : null}
      {completion ? <div className="result-drop__done" role="status" data-testid="result-drop-complete">
        <p><CheckCircle2 aria-hidden="true" /><strong>{completion.published_files}개 파일을 올렸습니다</strong>{completion.created_folders.length ? ` · 새 폴더 ${completion.created_folders.length}개` : ''} · {formatBytes(completion.published_bytes)}</p>
        <p className="result-drop__sync">{completion.sync.status === 'REFRESHED' || completion.sync.status === 'UNCHANGED' ? 'Case 결과에 반영했습니다.' : '폴더 확인이 늦어지고 있습니다. 30초 안에 자동으로 다시 확인합니다.'}{completion.state === 'PARTIAL' ? ' 일부 파일은 다른 프로그램이 사용 중이거나 같은 이름이 생겨 옮기지 못했습니다.' : ''}{completion.state === 'ABORTED' ? ' 나머지는 중지했습니다.' : ''}</p>
        {completion.state === 'PARTIAL' ? <div className="result-drop__actions" data-testid="result-drop-partial">
          <Button size="sm" variant="primary" onClick={() => void retryPartial()} disabled={partialBusy}>나머지 다시 옮기기</Button>
          <Button size="sm" variant="ghost" onClick={() => void stopSession(completion.session_id)} disabled={partialBusy}><Square aria-hidden="true" />나머지 중지</Button>
          {completion.busy.length ? <span className="result-drop__muted">사용 중: {completion.busy.slice(0, 5).join(', ')}</span> : null}
        </div> : null}
        <div className="result-drop__links">{completion.cases.map((item) => <Link key={item.case_relative_path} className="result-drop__link" to={caseResultsHref(projectId, requestId, environment, item.case_id)}>{item.case_name} Case 결과 열기</Link>)}
          {!completion.cases.length ? <Link className="result-drop__link" to={caseResultsHref(projectId, requestId, environment)}>Case 결과 열기</Link> : null}</div>
        {completion.skipped.length ? <p className="result-drop__muted">올리지 않은 파일 {completion.skipped.length}개: {completion.skipped.slice(0, 5).map((item) => item.relative_path).join(', ')}</p> : null}
      </div> : null}
    </section> : null}

    {otherOpen.length ? <section className="result-drop__card" aria-label="열린 업로드" data-testid="result-drop-open-sessions">
      <p className="result-drop__muted">끝나지 않은 업로드 {otherOpen.length}개 · 60분 동안 움직임이 없으면 자동으로 정리됩니다.</p>
      <ul className="result-drop__open">{otherOpen.map((item) => <li key={item.session_id}>
        <code>{item.target_relative_path}</code><span>{item.own ? '내 업로드' : `다른 사용자(${item.user_id})`} · {item.state === 'PARTIAL' ? `일부 완료 ${item.published_files}/${item.file_count}` : `파일 ${item.file_count}개`} · {formatBytes(item.total_bytes)}</span>
        <Button size="sm" variant="ghost" onClick={() => void stopSession(item.session_id)} disabled={partialBusy || uploading}>중지</Button>
      </li>)}</ul>
    </section> : null}

    {projectId && requestId ? <LegacyDraftHistory projectId={projectId} requestId={requestId} environment={environment} resultsHref={(caseId) => caseResultsHref(projectId, requestId, environment, caseId)} /> : null}
  </section>
}

function PlanView({ plan, onUpload, onReset }: { plan: DropPlan; onUpload: () => void; onReset: () => void }) {
  const errors = plan.issues.filter((item) => item.severity === 'error')
  const warnings = plan.issues.filter((item) => item.severity === 'warning')
  const roleOf = new Map(plan.folders_to_create.map((item) => [item.client_path, item.role_label]))
  return <div className="result-drop__plan" data-testid="result-drop-plan">
    <p className="result-drop__summary"><strong>파일 {plan.file_count}개</strong> · {formatBytes(plan.total_bytes)} · 새 폴더 {plan.folder_count}개 → <code>{plan.target_display_path}</code></p>
    {errors.length || warnings.length ? <ul className="result-drop__issues" aria-label="올리기 전 확인">
      {[...errors, ...warnings].map((item) => <li key={item.code} className={item.severity === 'error' ? 'is-error' : 'is-warning'} data-code={item.code}>
        <AlertTriangle aria-hidden="true" /><span>{item.message}{item.paths.length ? <small>{item.paths.slice(0, 5).join(', ')}{item.count > 5 ? ` 외 ${item.count - 5}개` : ''}</small> : null}</span>
      </li>)}
    </ul> : null}
    {plan.conflicts.length ? <div className="result-drop__conflicts" data-testid="result-drop-conflicts"><strong>이미 있는 파일 {plan.conflicts.length}개 (덮어쓰지 않음)</strong><ul>{plan.conflicts.slice(0, 20).map((item) => <li key={item.destination_relative_path}><code>{item.relative_path}</code></li>)}</ul></div> : null}
    {plan.folders_to_create.length ? <div className="result-drop__folders"><strong>새로 만드는 폴더와 깊이</strong><ul>{plan.folders_to_create.slice(0, 50).map((item) => <li key={item.relative_path}><code>{item.client_path}</code><b>{item.role_label}</b></li>)}</ul></div> : null}
    <div className="result-drop__table-wrap"><table className="result-drop__table" aria-label="올라갈 경로">
      <thead><tr><th>올라갈 경로</th><th>들어갈 폴더</th><th>크기</th></tr></thead>
      <tbody>{plan.files.slice(0, PLAN_ROW_LIMIT).map((item) => {
        const folder = item.relative_path.includes('/') ? item.relative_path.slice(0, item.relative_path.lastIndexOf('/')) : ''
        return <tr key={item.client_index}><td><code>{item.relative_path}</code></td><td>{roleOf.get(folder) ?? ROLE_LABELS[item.role] ?? item.role}</td><td>{formatBytes(item.size)}</td></tr>
      })}</tbody>
    </table>{plan.files.length > PLAN_ROW_LIMIT ? <p className="result-drop__muted">외 {plan.files.length - PLAN_ROW_LIMIT}개 파일</p> : null}</div>
    {plan.skipped.length ? <p className="result-drop__muted" data-testid="result-drop-skipped">올리지 않는 파일 {plan.skipped.length}개: {plan.skipped.slice(0, 10).map((item) => `${item.relative_path}(${SKIP_REASON_LABELS[item.reason] ?? item.reason})`).join(', ')}</p> : null}
    <div className="result-drop__actions">
      <Button variant="primary" onClick={onUpload} disabled={!plan.can_upload}><UploadCloud aria-hidden="true" />올리기</Button>
      <Button variant="ghost" onClick={onReset}>다시 선택</Button>
      {!plan.can_upload ? <span className="result-drop__muted">위 문제를 고친 뒤 다시 끌어 놓으세요.</span> : null}
    </div>
  </div>
}
