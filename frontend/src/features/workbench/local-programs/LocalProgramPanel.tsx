import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronRight, CircleAlert, FolderOpen, Link2, Monitor, Plus, RefreshCw, Search, Settings2, Trash2, WandSparkles, X } from 'lucide-react'
import type { WorkflowStep } from '../../../types'
import { localRunnerApi, newIdempotencyKey, type LocalBatch, type LocalProgram, type LocalProgramCandidate, type LocalRun, type LocalRunItem } from '../../../shared/api/localRunner'
import { localExecutionApi, type CentralRun, type LocalIdentity } from '../../../shared/api/localExecution'
import { useManagedLocalConnection } from './useManagedLocalConnection'
import './local-programs.css'

type Mode = 'DIRECT' | 'BATCH'
type Props = {
  currentUserId: string
  currentUserName?: string
  requestId: string
  selectedWorkItem: WorkflowStep
  taskName: string
  canExecute: boolean
  isAdmin: boolean
  isCurrent: boolean
}

const EMPTY_ITEM: LocalRunItem = { input_path: '', working_directory: '' }
const statusLabels: Record<string, string> = { QUEUED: '대기', RUNNING: '실행 중', AWAITING_COMPLETION: '사용자 완료 필요', SUCCEEDED: '프로세스 종료', FAILED: '실패', COMPLETED: '완료', INTERRUPTED: '중단됨' }
type HistoryRun = LocalRun & { binding_id?: string | null; device_id?: string | null; host_name?: string | null; actor_user_id?: string | null; synced_at?: string | null; sync_pending?: boolean }

function friendlyError(reason: unknown) {
  return reason instanceof Error ? reason.message : '로컬 실행 요청을 처리하지 못했습니다.'
}

function defaultKey(userId: string, hostId: string, taskType: string) {
  return `local-runner:default:${userId}:${hostId}:${taskType}`
}

function displayPath(value: string) {
  if (!value) return '선택하지 않음 · 도우미 기본값 사용'
  return value.length > 58 ? `…${value.slice(-55)}` : value
}

function mergeHistory(localRuns: LocalRun[], centralRuns: CentralRun[], bindingId: string | undefined, identity: LocalIdentity | null, currentUserId: string): HistoryRun[] {
  const centralById = new Map(centralRuns.map((run) => [run.id, run]))
  const merged: HistoryRun[] = localRuns.map((run) => {
    const central = centralById.get(run.id)
    if (central) return { ...central, ...run, binding_id: central.binding_id ?? bindingId, device_id: central.device_id ?? identity?.device_id, host_name: central.host_name ?? identity?.host_name, actor_user_id: central.actor_user_id ?? currentUserId, sync_pending: central.status !== run.status } as HistoryRun
    return { ...run, binding_id: bindingId, device_id: identity?.device_id, host_name: identity?.host_name, actor_user_id: currentUserId, sync_pending: true } as HistoryRun
  })
  const ownIds = new Set(localRuns.map((run) => run.id))
  for (const run of centralRuns) if (!ownIds.has(run.id)) merged.push(run as HistoryRun)
  return merged.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
}

export function LocalProgramPanel({ currentUserId, currentUserName, requestId, selectedWorkItem, taskName, canExecute, isAdmin, isCurrent }: Props) {
  const contextKey = `${currentUserId}:${requestId}:${selectedWorkItem.id}`
  const contextRef = useRef(contextKey)
  const requestSequence = useRef(0)
  const abortRef = useRef<AbortController | null>(null)
  const connection = useManagedLocalConnection(currentUserId)
  const { token, session, identity, health, clearBinding, connect } = connection
  const connectionState = connection.status
  const connectionError = connection.error
  const [operationBusy, setBusy] = useState(false)
  const busy = operationBusy || connectionState === 'CHECKING' || connectionState === 'PAIRING'
  const [mode, setMode] = useState<Mode>('DIRECT')
  const [query, setQuery] = useState('')
  const [programs, setPrograms] = useState<LocalProgram[]>([])
  const [selectedProgramId, setSelectedProgramId] = useState('')
  const [rememberDefault, setRememberDefault] = useState(false)
  const [discoveries, setDiscoveries] = useState<LocalProgramCandidate[]>([])
  const [items, setItems] = useState<LocalRunItem[]>([{ ...EMPTY_ITEM }])
  const [runs, setRuns] = useState<HistoryRun[]>([])
  const [batches, setBatches] = useState<LocalBatch[]>([])
  const [batchName, setBatchName] = useState(`${taskName} 일괄 실행`)
  const [noteDraft, setNoteDraft] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [showRegister, setShowRegister] = useState(false)
  const [editingProgramId, setEditingProgramId] = useState('')
  const [registerDraft, setRegisterDraft] = useState({ name: '', version: '', keywords: '', executable_path: '', arguments: '[]' })
  const retryKeysRef = useRef<Record<string, string>>({})


  const selectedProgram = useMemo(() => programs.find((program) => program.id === selectedProgramId) ?? null, [programs, selectedProgramId])
  const selectedAllowed = Boolean(selectedProgram?.available)
  const passesInput = Boolean(selectedProgram?.arguments.some((argument) => argument.includes('{input}')))
  const canOperate = Boolean(canExecute && selectedWorkItem.owner_user_id && (isAdmin || selectedWorkItem.owner_user_id === currentUserId))
  const canLaunch = Boolean(token && health && selectedProgram && selectedAllowed && (mode === 'DIRECT' || passesInput) && canOperate && isCurrent && selectedWorkItem.status === 'IN_PROGRESS' && !busy)
  const canRetry = Boolean(token && health && canOperate && isCurrent && selectedWorkItem.status === 'IN_PROGRESS' && !busy)
  const eligibilityMessage = !canExecute
    ? '실행 권한이 없어 로컬 프로그램을 시작할 수 없습니다.'
    : !selectedWorkItem.owner_user_id
      ? '담당자가 지정된 작업에서만 로컬 프로그램을 실행할 수 있습니다.'
      : !isAdmin && selectedWorkItem.owner_user_id !== currentUserId
      ? `작업 담당자(${selectedWorkItem.owner})만 실행할 수 있습니다.`
      : !isCurrent
        ? '현재 순서의 작업만 로컬 실행을 시작할 수 있습니다.'
        : selectedWorkItem.status !== 'IN_PROGRESS'
          ? '작업을 먼저 시작하면 로컬 프로그램 실행이 열립니다.'
          : selectedProgram && !selectedProgram.available
            ? '등록된 실행 파일 경로를 찾을 수 없어 실행할 수 없습니다.'
            : '현재 작업 문맥으로 실행 이력에 저장됩니다. 실행 후 업무 완료는 직접 표시하세요.'

  const context = useMemo(() => ({ request_id: requestId, work_item_id: selectedWorkItem.id, task_name: taskName, actor: currentUserId }), [requestId, selectedWorkItem.id, taskName, currentUserId])

  const stopPending = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  const runRequest = useCallback(async <T,>(work: (signal: AbortSignal) => Promise<T>, onSuccess: (value: T) => void) => {
    const sequence = ++requestSequence.current
    stopPending()
    const controller = new AbortController()
    abortRef.current = controller
    setBusy(true); setError(''); setNotice('')
    try {
      const value = await work(controller.signal)
      if (contextRef.current === contextKey && sequence === requestSequence.current) onSuccess(value)
    } catch (reason) {
      if (!controller.signal.aborted && contextRef.current === contextKey && sequence === requestSequence.current) setError(friendlyError(reason))
    } finally {
      if (sequence === requestSequence.current) { setBusy(false); abortRef.current = null }
    }
  }, [contextKey, stopPending])

  // Work selection and connection lifecycle are independent. A session refresh
  // must preserve the user's program choice and batch inputs.
  useEffect(() => {
    contextRef.current = contextKey
    requestSequence.current += 1
    stopPending()
    setBusy(false); setRuns([]); setError(''); setNotice(''); setSelectedProgramId(''); setDiscoveries([])
    setItems([{ ...EMPTY_ITEM }]); setBatchName(`${taskName} 일괄 실행`); setRememberDefault(false)
    setNoteDraft({}); retryKeysRef.current = {}
  }, [contextKey, stopPending, taskName])

  useEffect(() => {
    if (!token) { setPrograms([]); setBatches([]); setSelectedProgramId(''); return }
    const controller = new AbortController()
    void localRunnerApi.programs(token, '', controller.signal)
      .then((value) => { if (!controller.signal.aborted) setPrograms(value) })
      .catch((reason) => { if (!controller.signal.aborted) setError(friendlyError(reason)) })
    return () => controller.abort()
  }, [token])

  useEffect(() => {
    if (!token || !health) return
    const remembered = (() => { try { return localStorage.getItem(defaultKey(currentUserId, health.host_id, selectedWorkItem.task_type_id ?? taskName)) ?? '' } catch { return '' } })()
    if (remembered && programs.some((program) => program.id === remembered)) { setSelectedProgramId(remembered); setRememberDefault(true) }
  }, [currentUserId, health, programs, selectedWorkItem.task_type_id, taskName, token])

  useEffect(() => {
    const expired = () => { stopPending(); requestSequence.current += 1; historySequence.current += 1; setRuns([]); setPrograms([]); setBatches([]); setBusy(false) }
    window.addEventListener('analysis-auth-expired', expired)
    return () => { stopPending(); window.removeEventListener('analysis-auth-expired', expired) }
  }, [stopPending])

  const loadPrograms = async (currentToken = token, search = query) => {
    if (!currentToken) return
    const sequence = ++requestSequence.current
    stopPending(); const controller = new AbortController(); abortRef.current = controller; setBusy(true); setError('')
    try {
      const value = await localRunnerApi.programs(currentToken, search.trim(), controller.signal)
      if (contextRef.current === contextKey && sequence === requestSequence.current) setPrograms(value)
    } catch (reason) {
      if (!controller.signal.aborted && contextRef.current === contextKey && sequence === requestSequence.current) {
        if (reason instanceof Error && 'status' in reason && [401, 403].includes((reason as { status?: number }).status ?? 0)) clearBinding('DISCONNECTED')
        setError(friendlyError(reason))
      }
    } finally { if (sequence === requestSequence.current) { setBusy(false); abortRef.current = null } }
  }

  const pick = async (kind: 'program' | 'files' | 'directory', rowIndex?: number) => {
    if (!token) return
    await runRequest((signal) => localRunnerApi.pick(token, kind, signal), ({ paths }) => {
      if (!paths.length) return
      if (kind === 'program') setRegisterDraft((draft) => ({ ...draft, executable_path: paths[0] ?? '' }))
      else if (kind === 'directory') setItems((current) => current.map((item, index) => index === (rowIndex ?? 0) ? { ...item, working_directory: paths[0] ?? '' } : item))
      else {
        if (mode === 'DIRECT' && paths.length > 1) { setError('여러 파일은 일괄 실행에서 선택하세요.'); return }
        if (mode === 'BATCH' && items.length - (rowIndex == null ? 0 : 1) + paths.length > 100) { setError('일괄 실행에는 최대 100개 파일을 선택할 수 있습니다.'); return }
        setItems((current) => {
          if (mode === 'DIRECT') return [{ input_path: paths[0], working_directory: current[0]?.working_directory ?? '' }]
          const insertion = rowIndex ?? current.length
          const additions = paths.map((input_path) => ({ input_path, working_directory: current[insertion]?.working_directory ?? current[0]?.working_directory ?? '' }))
          return [...current.slice(0, insertion), ...additions, ...current.slice(insertion + (rowIndex == null ? 0 : 1))]
        })
      }
    })
  }

  const chooseProgram = (program: LocalProgram) => {
    setSelectedProgramId(program.id)
    if (health && rememberDefault) try { localStorage.setItem(defaultKey(currentUserId, health.host_id, selectedWorkItem.task_type_id ?? taskName), program.id) } catch { /* storage is optional */ }
  }

  const changeRememberDefault = (checked: boolean) => {
    setRememberDefault(checked)
    if (health) try {
      const key = defaultKey(currentUserId, health.host_id, selectedWorkItem.task_type_id ?? taskName)
      if (checked && selectedProgram) localStorage.setItem(key, selectedProgram.id)
      else localStorage.removeItem(key)
    } catch { /* storage is optional */ }
  }

  const discover = () => {
    if (!token) return
    void runRequest((signal) => localRunnerApi.discover(token, signal), (value) => { setDiscoveries(value); setNotice(value.length ? `${value.length}개 발견 후보를 확인했습니다. 등록 후 실행할 수 있습니다.` : '발견 후보가 없습니다. 실행 파일을 선택해 직접 등록하세요.') })
  }

  const register = (input = registerDraft, forceCreate = false) => {
    if (!token) return
    let args: string[]
    try {
      const parsed: unknown = JSON.parse(input.arguments || '[]')
      if (!Array.isArray(parsed) || parsed.some((item) => typeof item !== 'string')) throw new Error('인자는 문자열 배열이어야 합니다.')
      args = parsed
    } catch (reason) { setError(reason instanceof Error ? reason.message : '인자 token JSON을 확인하세요.'); return }
    const payload = { name: input.name.trim(), version: input.version.trim(), keywords: input.keywords.split(/[,\s]+/).map((item) => item.trim()).filter(Boolean), executable_path: input.executable_path.trim(), arguments: args }
    if (!payload.name || !payload.version || !payload.executable_path) { setError('프로그램 이름, 버전, 실행 파일 경로를 입력하세요.'); return }
    const activeEditId = forceCreate ? '' : editingProgramId
    void runRequest((signal) => activeEditId ? localRunnerApi.updateProgram(token, activeEditId, payload, signal) : localRunnerApi.registerProgram(token, payload, signal), (program) => { setPrograms((current) => [program, ...current.filter((item) => item.id !== program.id)]); chooseProgram(program); setShowRegister(false); setEditingProgramId(''); setNotice(`${program.name} v${program.version}을 ${activeEditId ? '수정' : '등록'}했습니다.`) })
  }

  const updateItem = (index: number, key: keyof LocalRunItem, value: string) => setItems((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: value } : item))
  const idempotencyRef = useRef<{ fingerprint: string; key: string } | null>(null)
  const execute = () => {
    if (!canLaunch || !selectedProgram) return
    const submitItems = mode === 'DIRECT' ? [items[0] ?? EMPTY_ITEM] : items.filter((item) => item.input_path || item.working_directory)
    if (!submitItems.length) { setError('일괄 실행 항목을 하나 이상 추가하세요.'); return }
    const fingerprint = JSON.stringify({ program_id: selectedProgram.id, mode, items: submitItems, context })
    if (!idempotencyRef.current || idempotencyRef.current.fingerprint !== fingerprint) idempotencyRef.current = { fingerprint, key: newIdempotencyKey() }
    void runRequest((signal) => localRunnerApi.createRuns(token, { program_id: selectedProgram.id, mode, items: submitItems, context, idempotency_key: idempotencyRef.current?.key ?? newIdempotencyKey() }, signal), (value) => { setRuns((current) => [...value, ...current.filter((item) => !value.some((next) => next.id === item.id))]); idempotencyRef.current = null; setNotice(mode === 'DIRECT' ? '직접 실행을 시작했습니다. 프로세스 종료 후 업무 완료를 직접 표시하세요.' : `${value.length}개 일괄 실행 항목을 제출했습니다.`) })
  }

  const historySequence = useRef(0)
  const loadHistory = useCallback(async (signal: AbortSignal) => {
    const sequence = ++historySequence.current
    const [central, local] = await Promise.allSettled([
      localExecutionApi.centralRuns(requestId, selectedWorkItem.id, signal),
      token ? localRunnerApi.runs(token, context, signal) : Promise.resolve([] as LocalRun[]),
    ])
    if (signal.aborted || sequence !== historySequence.current || contextRef.current !== contextKey) return
    if (central.status === 'rejected') {
      // A lost project permission must also remove previously displayed rows.
      const reason = central.reason as { status?: number }
      if (reason.status === 401 || reason.status === 403) setRuns([])
      setError(friendlyError(central.reason))
      return
    }
    if (local.status === 'rejected' && token) {
      const status = (local.reason as { status?: number }).status
      clearBinding(status === 401 || status === 403 ? 'DISCONNECTED' : 'HELPER_REQUIRED')
      setError(friendlyError(local.reason))
    }
    setRuns(mergeHistory(local.status === 'fulfilled' ? local.value : [], central.value, session?.binding_id, identity, currentUserId))
  }, [requestId, selectedWorkItem.id, token, context, contextKey, session?.binding_id, identity, currentUserId, clearBinding])
  const refreshHistory = () => { if (busy) return; void runRequest((signal) => loadHistory(signal), () => undefined) }
  const loadBatches = () => { if (!token) return; void runRequest((signal) => localRunnerApi.batches(token, signal), setBatches) }
  const saveBatch = () => { if (!token || !selectedProgram) return; void runRequest((signal) => localRunnerApi.saveBatch(token, { name: batchName.trim() || `${taskName} 일괄 실행`, program_id: selectedProgram.id, items }, signal), (value) => { setBatches((current) => [value, ...current.filter((item) => item.id !== value.id)]); setNotice('일괄 실행 목록을 저장했습니다.') }) }
  const canControlRun = (run: HistoryRun) => Boolean(token && session?.binding_id && identity?.device_id && run.actor_user_id === currentUserId && run.binding_id === session.binding_id && run.device_id === identity.device_id)
  const completeRun = (run: HistoryRun) => { if (!token || !canControlRun(run)) return; void runRequest((signal) => localRunnerApi.complete(token, run.id, noteDraft[run.id] ?? '', signal), (value) => { setRuns((current) => current.map((item) => item.id === value.id ? { ...item, ...value, sync_pending: true } : item)); setNotice('직접 실행을 업무 완료로 표시했습니다.') }) }
  const retryRun = (run: HistoryRun) => { if (!token || !canRetry || !canControlRun(run)) return; const key = retryKeysRef.current[run.id] ?? newIdempotencyKey(); retryKeysRef.current[run.id] = key; void runRequest((signal) => localRunnerApi.retryRun(token, run.id, key, signal), (value) => { setRuns((current) => [...value.map((item) => ({ ...item, binding_id: session?.binding_id, device_id: identity?.device_id, host_name: identity?.host_name, actor_user_id: currentUserId, sync_pending: true } as HistoryRun)), ...current]); delete retryKeysRef.current[run.id]; setNotice(`${run.program_name} v${run.program_version}로 실패 항목을 새 실행 기록으로 재제출했습니다.`) }) }

  useEffect(() => {
    if (busy) return
    const controller = new AbortController()
    let timer: number | undefined
    const poll = async () => {
      await loadHistory(controller.signal)
      if (!controller.signal.aborted) timer = window.setTimeout(() => void poll(), 4000)
    }
    void poll()
    return () => { controller.abort(); if (timer !== undefined) window.clearTimeout(timer) }
  }, [loadHistory, busy])

  const beginEdit = (program: LocalProgram) => { setEditingProgramId(program.id); setRegisterDraft({ name: program.name, version: program.version, keywords: program.keywords.join(', '), executable_path: program.executable_path, arguments: JSON.stringify(program.arguments) }); setShowRegister(true) }

  const inputRows = mode === 'DIRECT' ? items.slice(0, 1) : items

  return <section className="local-program-panel" data-testid="local-program-panel" aria-busy={busy}>
    <header className="local-program-header"><div><span className="local-program-eyebrow"><Link2 aria-hidden="true" /> MANAGED LOCAL RUNNER</span><h3>내 PC 프로그램으로 작업 실행</h3><p>회사 계정으로 승인된 PC에서 실행하고 중앙 실행 이력을 함께 확인합니다.</p></div>{health ? <div className="local-host-status"><span className="status-dot" /> <strong>{health.host_name}</strong><small>{currentUserId} · {health.platform}</small></div> : <span className="local-planned-label">중앙 실행 이력 사용 가능</span>}</header>
    <div className="local-connection-row managed-connection-row"><div className="managed-identity"><Monitor aria-hidden="true" /><span><strong>{identity?.host_name ?? '이 PC 확인 중'}</strong><small>{currentUserName || '로그인한 계정'} · {identity ? '내 PC' : '도우미를 실행하면 자동 확인됩니다.'}</small></span></div><div className={`managed-state managed-state-${connectionState.toLowerCase()}`} role="status">{connectionState === 'CONNECTED' ? '연결됨' : connectionState === 'PAIRING' ? '최초 연결 승인 대기' : connectionState === 'HELPER_REQUIRED' ? '도우미 필요' : connectionState === 'NEEDS_PAIRING' ? '최초 연결 필요' : connectionState === 'CHECKING' ? '확인 중' : connectionState === 'ERROR' ? '연결 오류' : '연결 해제'}</div>{(connectionState === 'NEEDS_PAIRING' || connectionState === 'ERROR' || connectionState === 'DISCONNECTED' || connectionState === 'HELPER_REQUIRED') && <button type="button" onClick={connect} disabled={busy}><Link2 aria-hidden="true" /> {connectionState === 'NEEDS_PAIRING' ? '이 PC 연결' : connectionState === 'HELPER_REQUIRED' ? '도우미 다시 확인' : '다시 연결'}</button>}{health && <button type="button" className="local-quiet-button" onClick={() => { stopPending(); requestSequence.current += 1; setBusy(false); void connection.disconnect() }} disabled={busy}>연결 해제</button>}</div>
    {connectionError && <p className="local-inline-error" role="alert">{connectionError}</p>}
    {!health ? <div className="local-disconnected"><CircleAlert aria-hidden="true" /><strong>{connectionState === 'NEEDS_PAIRING' ? '이 PC를 한 번 연결하면 이후 자동으로 복원됩니다.' : connectionState === 'PAIRING' ? '도우미의 네이티브 승인 창에서 계정과 서버를 확인하세요.' : connectionState === 'HELPER_REQUIRED' ? '로컬 실행 도우미를 시작하면 이 PC를 확인할 수 있습니다.' : '로컬 도우미와 연결 상태를 확인하는 중입니다.'}</strong><p>{connectionError || '중앙 실행 이력은 도우미가 잠시 꺼져도 계속 조회할 수 있습니다.'}</p></div> : <>
      <div className="local-program-toolbar"><label className="local-search"><Search aria-hidden="true" /><span className="sr-only">프로그램 검색</span><input aria-label="프로그램 검색" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void loadPrograms() }} placeholder="이름·키워드·버전 검색" /><button type="button" aria-label="프로그램 검색 실행" onClick={() => void loadPrograms()} disabled={busy}><Search aria-hidden="true" /></button></label><button type="button" className="local-quiet-button" onClick={discover} disabled={busy}><WandSparkles aria-hidden="true" /> 설치 후보 발견</button><button type="button" className="local-quiet-button" onClick={() => setShowRegister((value) => !value)} disabled={busy}><Settings2 aria-hidden="true" /> 프로그램 등록</button></div>
      {programs.length ? <div className="local-program-grid" role="listbox" aria-label="등록 프로그램 목록">{programs.map((program) => <button type="button" role="option" aria-selected={selectedProgramId === program.id} key={program.id} className={`local-program-option ${selectedProgramId === program.id ? 'selected' : ''}`} onClick={() => chooseProgram(program)} disabled={busy}><span className="program-option-check">{selectedProgramId === program.id ? <Check aria-hidden="true" /> : null}</span><span><strong>{program.name} <em>v{program.version}</em></strong><small>{program.keywords.length ? program.keywords.join(' · ') : '키워드 없음'}</small><small className="program-path">{displayPath(program.executable_path)}</small></span><b className={program.available ? 'available' : 'unavailable'}>{program.available ? '사용 가능' : '경로 없음'}</b></button>)}</div> : <p className="local-empty">{query.trim() ? `“${query.trim()}”에 맞는 등록 프로그램이 없습니다.` : '등록된 프로그램이 없습니다. 설치 후보를 발견하거나 실행 파일을 선택해 등록하세요.'}</p>}
      {discoveries.length > 0 && <details className="local-discovery" open><summary><span><strong>발견 후보</strong><small>후보는 등록을 눌러야 프로그램 목록에 추가됩니다.</small></span><ChevronRight aria-hidden="true" /></summary><div>{discoveries.map((candidate) => <article key={`${candidate.name}-${candidate.version}-${candidate.executable_path}`}><div><strong>{candidate.name} · v{candidate.version}</strong><small>{displayPath(candidate.executable_path)}</small></div><button type="button" onClick={() => register({ ...candidate, keywords: candidate.keywords.join(', '), arguments: JSON.stringify(candidate.arguments) }, true)} disabled={busy}>등록</button></article>)}</div></details>}
      {showRegister && <details className="local-register" open><summary><span><strong>프로그램 등록 · 고급 설정</strong><small>이름·버전별로 이 PC의 실행 파일과 인자를 저장합니다.</small></span><ChevronRight aria-hidden="true" /></summary><div className="local-register-grid"><label><span>프로그램 이름</span><input value={registerDraft.name} onChange={(event) => setRegisterDraft({ ...registerDraft, name: event.target.value })} /></label><label><span>버전</span><input value={registerDraft.version} onChange={(event) => setRegisterDraft({ ...registerDraft, version: event.target.value })} /></label><label><span>키워드</span><input value={registerDraft.keywords} onChange={(event) => setRegisterDraft({ ...registerDraft, keywords: event.target.value })} placeholder="메시, HyperMesh, 2024.1" /></label><label className="wide"><span>실행 파일 경로</span><div className="picker-field"><input value={registerDraft.executable_path} onChange={(event) => setRegisterDraft({ ...registerDraft, executable_path: event.target.value })} /><button type="button" onClick={() => void pick('program')} disabled={busy}><FolderOpen aria-hidden="true" /> 선택</button></div></label><label className="wide"><span>실행 인자 (JSON 배열)</span><textarea value={registerDraft.arguments} onChange={(event) => setRegisterDraft({ ...registerDraft, arguments: event.target.value })} rows={2} spellCheck={false} placeholder='["-i", "{input}"]' /><small>{'{input}'}만 치환됩니다. 빈 배열이면 파일 인자를 자동으로 추가하지 않습니다.</small></label><button type="button" className="local-primary-action" onClick={() => register()} disabled={busy}>{editingProgramId ? '수정 저장' : '등록 저장'}</button></div></details>}
      <div className="local-mode-tabs" role="tablist"><button type="button" role="tab" aria-selected={mode === 'DIRECT'} className={mode === 'DIRECT' ? 'active' : ''} onClick={() => setMode('DIRECT')} disabled={busy}>직접 작업</button><button type="button" role="tab" aria-selected={mode === 'BATCH'} className={mode === 'BATCH' ? 'active' : ''} onClick={() => setMode('BATCH')} disabled={busy}>일괄 실행</button></div>
      <div className="local-execution-card"><div className="local-selected-program"><span>선택 프로그램</span>{selectedProgram ? <><div className="selected-program-line"><strong>{selectedProgram.name} · v{selectedProgram.version}</strong><button type="button" className="local-quiet-button" onClick={() => beginEdit(selectedProgram)} disabled={busy}><Settings2 aria-hidden="true" /> 설정 수정</button></div><small>{displayPath(selectedProgram.executable_path)} · {health.host_name}</small><label className="remember-default"><input type="checkbox" checked={rememberDefault} onChange={(event) => changeRememberDefault(event.target.checked)} disabled={busy} /> 이 작업 유형의 기본 프로그램으로 기억</label></> : <p>프로그램을 검색하고 하나를 명시적으로 선택하세요.</p>}</div><p className="local-eligibility" role="status">{eligibilityMessage}</p>{selectedProgram && !selectedProgram.available && <div className="local-blocked" role="alert">경로 없음 · 프로그램 등록에서 실행 파일을 다시 선택하세요.</div>}
        {selectedProgram && !passesInput && <p className="local-input-help">{mode === 'BATCH' ? '파일별 일괄 실행을 하려면 설정 수정에서 {input}을 포함한 실행 인자를 지정하세요.' : '이 설정은 프로그램만 엽니다. 선택한 파일을 함께 열려면 설정 수정에서 파일 전달 인자를 지정하세요.'}</p>}
        <div className="local-input-rows">{inputRows.map((item, index) => <div className="local-input-row" key={index}><span className="row-number">{mode === 'BATCH' ? index + 1 : '입력'}</span><label><span>입력 파일</span><div className="picker-field"><input aria-label="입력 파일" value={item.input_path} onChange={(event) => updateItem(index, 'input_path', event.target.value)} placeholder="선택 사항 · PC 경로" disabled={busy} /><button type="button" onClick={() => void pick('files', index)} disabled={busy}><FolderOpen aria-hidden="true" /> 파일</button></div></label><label><span>작업 폴더</span><div className="picker-field"><input aria-label="작업 폴더" value={item.working_directory} onChange={(event) => updateItem(index, 'working_directory', event.target.value)} placeholder="비우면 도우미 기본 폴더" disabled={busy} /><button type="button" onClick={() => void pick('directory', index)} disabled={busy}><FolderOpen aria-hidden="true" /> 폴더</button></div></label>{mode === 'BATCH' && items.length > 1 && <button type="button" className="local-icon-button" aria-label={`일괄 항목 ${index + 1} 삭제`} onClick={() => setItems((current) => current.filter((_, itemIndex) => itemIndex !== index))} disabled={busy}><Trash2 aria-hidden="true" /></button>}</div>)}</div>
        {mode === 'BATCH' && <div className="local-batch-actions"><button type="button" className="local-quiet-button" onClick={() => setItems((current) => [...current, { ...EMPTY_ITEM }])} disabled={busy || items.length >= 100}><Plus aria-hidden="true" /> 항목 추가</button><label><span>목록 이름</span><input value={batchName} onChange={(event) => setBatchName(event.target.value)} disabled={busy} /></label><button type="button" className="local-quiet-button" onClick={saveBatch} disabled={busy || !selectedProgram}><Check aria-hidden="true" /> 목록 저장</button><button type="button" className="local-quiet-button" onClick={loadBatches} disabled={busy}><RefreshCw aria-hidden="true" /> 불러오기</button></div>}
        {mode === 'BATCH' && batches.length > 0 && <div className="local-saved-batches">{batches.map((batch) => <button type="button" key={batch.id} onClick={() => { setItems(batch.items.length ? batch.items : [{ ...EMPTY_ITEM }]); setBatchName(batch.name); setSelectedProgramId(batch.program_id) }} disabled={busy}><span>{batch.name}</span><small>{batch.items.length}개 항목</small></button>)}</div>}
        <button type="button" className="local-primary-action local-launch-action" onClick={execute} disabled={!canLaunch}><ChevronRight aria-hidden="true" /> {mode === 'BATCH' ? `선택한 ${inputRows.length}개 실행` : '프로그램 열고 작업 시작'}</button>
      </div>
    </>}
      <details className="local-history" open><summary><span><strong>현재 작업 실행 이력</strong><small>실행 당시 프로그램·인자·경로 상세</small></span><ChevronRight aria-hidden="true" /></summary><div className="local-history-head"><p>프로세스 종료와 업무 완료는 별도입니다. 중앙 의뢰 상태는 자동 변경하지 않습니다.</p><button type="button" className="local-icon-button" aria-label="로컬 실행 이력 새로고침" onClick={refreshHistory} disabled={busy}><RefreshCw className={busy ? 'spin' : ''} aria-hidden="true" /></button></div>{runs.length === 0 ? <p className="local-empty">이 작업 문맥의 로컬 실행 이력이 없습니다.</p> : <div className="local-run-list">{runs.map((run) => <article key={run.id} className={`local-run status-${run.status.toLowerCase()}`}><header><div><strong>{run.program_name} · v{run.program_version}</strong><small>{new Date(run.created_at).toLocaleString('ko-KR')} · {run.mode}</small><small className="run-actor">실행자 {run.context.actor || run.actor_user_id || '기록 없음'} · PC {run.host_name ?? '중앙 기록'}</small></div><b>{statusLabels[run.status] ?? run.status}{run.sync_pending ? ' · 동기화 대기' : ''}</b></header><p>{run.input_path ? displayPath(run.input_path) : '입력 파일 없음'} · {run.working_directory ? displayPath(run.working_directory) : '기본 작업 폴더'}</p><details className="local-run-snapshot"><summary>실행 상세 보기</summary><div><code>{run.program_snapshot.executable_path}</code><code>{JSON.stringify(run.program_snapshot.arguments ?? [])}</code>{run.note && <p>완료 메모: {run.note}</p>}</div></details>{run.error && <small className="local-run-error">{run.error}</small>}{run.status === 'AWAITING_COMPLETION' && run.mode === 'DIRECT' && canControlRun(run) && <div className="complete-run"><input value={noteDraft[run.id] ?? ''} onChange={(event) => setNoteDraft((current) => ({ ...current, [run.id]: event.target.value }))} placeholder="작업 완료 메모 (선택)" disabled={busy} /><button type="button" onClick={() => completeRun(run)} disabled={busy || !canControlRun(run)}>업무 완료 표시</button></div>}{(run.status === 'FAILED' || run.status === 'INTERRUPTED') && canControlRun(run) && <button type="button" className="retry-button" onClick={() => retryRun(run)} disabled={!canRetry || !canControlRun(run)}>실패 항목 새로 실행</button>}</article>)}</div>}</details>
    {(error || notice) && <div className={error ? 'local-toast error' : 'local-toast'} role={error ? 'alert' : 'status'}>{error ? <X aria-hidden="true" /> : <Check aria-hidden="true" />}<span>{error || notice}</span><button type="button" aria-label="메시지 닫기" onClick={() => { setError(''); setNotice('') }}><X aria-hidden="true" /></button></div>}
  </section>
}
