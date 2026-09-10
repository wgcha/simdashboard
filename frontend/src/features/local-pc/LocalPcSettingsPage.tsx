import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Check, CircleAlert, FolderOpen, Link2, LoaderCircle, Monitor, Pencil, RefreshCw, Search, Settings2, Trash2, Unplug, WandSparkles, X } from 'lucide-react'
import { api } from '../../api'
import { localExecutionApi, type ManagedDevice } from '../../shared/api/localExecution'
import { localRunnerApi, type LocalProgram, type LocalProgramCandidate, type LocalProgramInput } from '../../shared/api/localRunner'
import { useManagedLocalConnection } from '../../shared/local-execution/useManagedLocalConnection'
import { LocalHelperInstallCard } from './LocalHelperInstallCard'
import './local-pc-settings.css'

type Props = { currentUserId: string; currentUserName?: string; passwordSettings?: ReactNode }
type Draft = { name: string; version: string; keywords: string; executable_path: string; arguments: string }
type Action = 'programs' | 'discover' | 'save' | 'remove' | 'pick' | 'devices'

const EMPTY_DRAFT: Draft = { name: '', version: '', keywords: '', executable_path: '', arguments: '[]' }
const statusText: Record<string, string> = {
  CHECKING: 'PC 상태를 확인하는 중', PAIRING: '연결을 승인하는 중', CONNECTED: '연결됨',
  NEEDS_PAIRING: '연결 필요', HELPER_REQUIRED: '도우미 설치 필요', DISCONNECTED: '연결 해제됨', ERROR: '확인 필요',
}

function friendlyError(reason: unknown) {
  return reason instanceof Error ? reason.message : 'PC 설정 요청을 처리하지 못했습니다.'
}

function displayPath(value: string) {
  return value.length > 84 ? `…${value.slice(-81)}` : value
}

function normalizeKeywords(value: string) {
  return Array.from(new Set(value.split(/[,\n]/).map((item) => item.trim()).filter(Boolean))).slice(0, 32)
}

function parseArguments(value: string): { value?: string[]; error?: string } {
  let parsed: unknown
  try { parsed = JSON.parse(value) } catch { return { error: '인수는 JSON 배열로 입력하세요. 예: ["--input", "{input}"]' } }
  if (!Array.isArray(parsed) || parsed.length > 64 || parsed.some((item) => typeof item !== 'string')) return { error: '인수는 문자열만 담은 JSON 배열이어야 합니다(최대 64개).' }
  const values = parsed as string[]
  if (values.some((item) => item.length > 2000 || /[\u0000-\u0008\u000B\u000C\u000E-\u001F]/.test(item))) return { error: '인수에 허용되지 않는 제어 문자 또는 너무 긴 값이 있습니다.' }
  return { value: values }
}

function validateDraft(draft: Draft): { input?: LocalProgramInput; error?: string } {
  const name = draft.name.trim()
  const version = draft.version.trim() || '기본'
  const executablePath = draft.executable_path.trim()
  if (!name || name.length > 120) return { error: '프로그램 이름을 1~120자로 입력하세요.' }
  if (version.length > 80) return { error: '버전은 80자 이내로 입력하세요.' }
  if (!executablePath || executablePath.length > 2048 || /[\u0000-\u001F]/.test(executablePath)) return { error: '실행 파일 경로를 안전한 값으로 입력하세요.' }
  const args = parseArguments(draft.arguments.trim() || '[]')
  if (args.error) return { error: args.error }
  return { input: { name, version, keywords: normalizeKeywords(draft.keywords), executable_path: executablePath, arguments: args.value ?? [] } }
}

function candidateDraft(candidate: LocalProgramCandidate): Draft {
  return { name: candidate.name, version: candidate.version, keywords: candidate.keywords.join(', '), executable_path: candidate.executable_path, arguments: JSON.stringify(candidate.arguments) }
}

function LoggedOutLocalPcSettings() {
  return <section className="local-pc-settings" data-testid="local-pc-settings"><header className="local-pc-page-header"><span className="local-pc-eyebrow"><Monitor aria-hidden="true" /> PERSONAL PC</span><h1>내 PC 설정</h1><p>로그인한 계정에 연결된 PC와 로컬 프로그램을 관리합니다.</p></header><section className="local-pc-login-required" role="status"><CircleAlert aria-hidden="true" /><div><strong>로그인이 필요합니다.</strong><p>내 PC 연결과 프로그램 목록은 로그인한 계정별로 안전하게 관리됩니다.</p></div></section></section>
}

function AuthenticatedLocalPcSettings({ currentUserId, currentUserName, passwordSettings, authMode }: Props & { authMode: 'password' | 'oidc' }) {
  const connection = useManagedLocalConnection(currentUserId)
  const { status, error: connectionError, identity, device, session, health, token, connect, disconnect } = connection
  const [programs, setPrograms] = useState<LocalProgram[]>([])
  const [discoveries, setDiscoveries] = useState<LocalProgramCandidate[]>([])
  const [query, setQuery] = useState('')
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT)
  const [editingId, setEditingId] = useState('')
  const [showEditor, setShowEditor] = useState(false)
  const [devices, setDevices] = useState<ManagedDevice[]>([])
  const [busy, setBusy] = useState<Action | ''>('')
  const [loadingDevices, setLoadingDevices] = useState(false)
  const [loadingPrograms, setLoadingPrograms] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const epoch = useRef(0)
  const deviceEpoch = useRef(0)
  const programEpoch = useRef(0)
  const deviceAbort = useRef<AbortController | null>(null)
  const programAbort = useRef<AbortController | null>(null)
  const actionAbort = useRef<AbortController | null>(null)

  const cancelProgramRead = useCallback(() => {
    programAbort.current?.abort(); programAbort.current = null; programEpoch.current += 1; setLoadingPrograms(false)
  }, [])
  const cancelDeviceRead = useCallback(() => {
    deviceAbort.current?.abort(); deviceAbort.current = null; deviceEpoch.current += 1; setLoadingDevices(false)
  }, [])
  const cancelReads = useCallback(() => { cancelDeviceRead(); cancelProgramRead() }, [cancelDeviceRead, cancelProgramRead])
  const invalidate = useCallback(() => { epoch.current += 1; actionAbort.current?.abort(); actionAbort.current = null }, [])
  const startAction = useCallback((kind: Action) => {
    invalidate()
    cancelProgramRead()
    const controller = new AbortController(); actionAbort.current = controller
    const generation = epoch.current
    setBusy(kind); setError(''); setNotice('')
    return { controller, generation, current: () => !controller.signal.aborted && epoch.current === generation }
  }, [cancelProgramRead, invalidate])
  const finishAction = useCallback((generation: number) => { if (epoch.current === generation) { setBusy(''); actionAbort.current = null } }, [])

  const loadDevices = useCallback(async () => {
    deviceAbort.current?.abort()
    const controller = new AbortController(); deviceAbort.current = controller
    const generation = ++deviceEpoch.current
    setLoadingDevices(true)
    try { const value = await localExecutionApi.devices(controller.signal); if (!controller.signal.aborted && deviceEpoch.current === generation) setDevices(value.filter((item) => item.user_id === currentUserId && !item.revoked_at)) }
    catch (reason) { if (!controller.signal.aborted && deviceEpoch.current === generation) setError(friendlyError(reason)) }
    finally { if (deviceEpoch.current === generation) { deviceAbort.current = null; setLoadingDevices(false) } }
  }, [currentUserId])

  const loadPrograms = useCallback(async (search: string) => {
    if (!token) return
    programAbort.current?.abort()
    const controller = new AbortController(); programAbort.current = controller
    const generation = ++programEpoch.current
    setLoadingPrograms(true)
    try { const value = await localRunnerApi.programs(token, search.trim(), controller.signal); if (!controller.signal.aborted && programEpoch.current === generation) setPrograms(value) }
    catch (reason) { if (!controller.signal.aborted && programEpoch.current === generation) setError(friendlyError(reason)) }
    finally { if (programEpoch.current === generation) { programAbort.current = null; setLoadingPrograms(false) } }
  }, [token])

  useEffect(() => {
    invalidate(); cancelReads(); setPrograms([]); setDiscoveries([]); setDevices([]); setDraft(EMPTY_DRAFT); setEditingId(''); setShowEditor(false); setError(''); setNotice('')
    void loadDevices()
    return () => { invalidate(); cancelReads() }
  }, [cancelReads, currentUserId, loadDevices, invalidate]) // account changes cancel every page-owned request before starting a new one

  useEffect(() => {
    if (status === 'CONNECTED' && token) { void loadDevices(); void loadPrograms('') }
    else { invalidate(); cancelProgramRead(); setBusy(''); setPrograms([]); setDiscoveries([]) }
  }, [cancelProgramRead, invalidate, loadDevices, loadPrograms, status, token])

  const runDiscover = async () => {
    if (!token) return
    const action = startAction('discover')
    try { const value = await localRunnerApi.discover(token, action.controller.signal); if (action.current()) { setDiscoveries(value); setNotice(value.length ? `${value.length}개 설치 후보를 찾았습니다. 등록할 후보를 선택하세요.` : '발견 후보가 없습니다. 실행 파일을 선택해 직접 등록하세요.') } }
    catch (reason) { if (action.current() && !action.controller.signal.aborted) setError(friendlyError(reason)) }
    finally { finishAction(action.generation) }
  }

  const pickExecutable = async () => {
    if (!token) return
    const action = startAction('pick')
    try { const result = await localRunnerApi.pick(token, 'program', action.controller.signal); if (action.current() && result.paths[0]) setDraft((old) => ({ ...old, executable_path: result.paths[0] })) }
    catch (reason) { if (action.current() && !action.controller.signal.aborted) setError(friendlyError(reason)) }
    finally { finishAction(action.generation) }
  }

  const saveProgram = async () => {
    if (!token) return
    const checked = validateDraft(draft)
    if (checked.error || !checked.input) { setError(checked.error ?? '프로그램 정보를 확인하세요.'); return }
    const action = startAction('save')
    try {
      const value = editingId ? await localRunnerApi.updateProgram(token, editingId, checked.input, action.controller.signal) : await localRunnerApi.registerProgram(token, checked.input, action.controller.signal)
      if (action.current()) { setPrograms((old) => editingId ? old.map((item) => item.id === value.id ? value : item) : [...old, value]); setDraft(EMPTY_DRAFT); setEditingId(''); setShowEditor(false); setNotice(`${value.name} 프로그램을 ${editingId ? '수정' : '등록'}했습니다.`) }
    } catch (reason) { if (action.current() && !action.controller.signal.aborted) setError(friendlyError(reason)) }
    finally { finishAction(action.generation) }
  }

  const removeProgram = async (program: LocalProgram) => {
    if (!token || !window.confirm(`'${program.name}' 프로그램을 삭제할까요?`)) return
    const action = startAction('remove')
    try { await localRunnerApi.removeProgram(token, program.id, action.controller.signal); if (action.current()) { setPrograms((old) => old.filter((item) => item.id !== program.id)); setNotice(`${program.name} 프로그램을 삭제했습니다.`) } }
    catch (reason) { if (action.current() && !action.controller.signal.aborted) setError(friendlyError(reason)) }
    finally { finishAction(action.generation) }
  }

  const revokeOther = async (item: ManagedDevice) => {
    const action = startAction('devices')
    try { await localExecutionApi.revoke(item.id, action.controller.signal); if (action.current()) { setDevices((old) => old.filter((value) => value.id !== item.id)); setNotice(`${item.host_name} 연결을 해제했습니다.`) } }
    catch (reason) { if (action.current() && !action.controller.signal.aborted) setError(friendlyError(reason)) }
    finally { finishAction(action.generation) }
  }

  const connected = status === 'CONNECTED' && Boolean(session && health)
  const currentDeviceId = device?.id
  const stateClass = `local-pc-state local-pc-state-${status.toLowerCase()}`
  const busyUi = Boolean(busy || loadingDevices || loadingPrograms)
  const disconnectCurrent = async () => {
    invalidate(); cancelProgramRead(); setPrograms([]); setDiscoveries([])
    await disconnect()
    await loadDevices()
  }

  return <section className="local-pc-settings" data-testid="local-pc-settings">
    <header className="local-pc-page-header"><div><span className="local-pc-eyebrow"><Monitor aria-hidden="true" /> PERSONAL PC</span><h1>내 PC 설정</h1><p>로그인한 계정에 연결된 PC와 로컬 프로그램을 관리합니다. 도우미는 선택 기능이며 대시보드 조회에는 필요하지 않습니다.</p></div><span className={stateClass}>{statusText[status] ?? status}</span></header>
    <section className="local-pc-account-card" aria-label="내 계정과 PC 상태"><div className="local-pc-account"><span className="local-pc-eyebrow">ACCOUNT</span><strong>{currentUserName || '내 계정'}</strong></div><div className="local-pc-host"><span className="local-pc-eyebrow">THIS PC</span><strong>{health?.host_name || identity?.host_name || (status === 'HELPER_REQUIRED' ? '도우미 설치 후 확인' : '이 PC를 확인하는 중')}</strong></div><div className="local-pc-account-actions">{status === 'CONNECTED' ? <button type="button" className="local-pc-secondary" onClick={() => void disconnectCurrent()} disabled={busyUi}><Unplug aria-hidden="true" /> 이 PC 연결 해제</button> : status === 'NEEDS_PAIRING' ? <button type="button" className="local-pc-primary" onClick={() => connect()} disabled={busyUi}><Link2 aria-hidden="true" /> 이 PC 연결</button> : status === 'DISCONNECTED' ? <><button type="button" className="local-pc-primary" onClick={() => connect()} disabled={busyUi}><Link2 aria-hidden="true" /> 이 PC 연결</button><button type="button" className="local-pc-secondary" onClick={() => connection.refresh()} disabled={busyUi}><RefreshCw aria-hidden="true" /> 다시 확인</button></> : status === 'HELPER_REQUIRED' || status === 'ERROR' ? <button type="button" className="local-pc-secondary" onClick={() => connection.refresh()} disabled={busyUi}><RefreshCw aria-hidden="true" /> 다시 확인</button> : null}</div></section>
    {connectionError && status !== 'HELPER_REQUIRED' && <div className="local-pc-alert" role="alert"><CircleAlert aria-hidden="true" /><span>{connectionError}</span></div>}
    {(status === 'HELPER_REQUIRED' || (status === 'ERROR' && !identity?.managed)) && <LocalHelperInstallCard busy={Boolean(busy)} />}
    {status === 'NEEDS_PAIRING' && <section className="local-pc-pairing-note" role="status"><Link2 aria-hidden="true" /><div><strong>도우미를 찾았습니다.</strong><p>이 PC 연결을 누르고 승인 창을 확인하세요.</p></div></section>}
    {connected && <section className="local-pc-programs-card" aria-labelledby="local-pc-programs-heading">
      <header className="local-pc-section-header"><div><span className="local-pc-eyebrow"><Settings2 aria-hidden="true" /> PROGRAM LIBRARY</span><h2 id="local-pc-programs-heading">이 PC의 프로그램</h2><p>작업 선택 없이 프로그램을 검색하고, 설치 후보를 확인해 등록합니다.</p></div><button type="button" className="local-pc-secondary" onClick={() => void loadPrograms(query)} disabled={busyUi}><RefreshCw aria-hidden="true" /> 새로고침</button></header>
      <div className="local-pc-toolbar"><label className="local-pc-search"><Search aria-hidden="true" /><span className="local-pc-sr-only">프로그램 검색</span><input aria-label="프로그램 검색" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void loadPrograms(query) }} placeholder="이름·키워드·버전 검색" /><button type="button" aria-label="프로그램 검색 실행" onClick={() => void loadPrograms(query)} disabled={busyUi}><Search aria-hidden="true" /></button></label><button type="button" className="local-pc-secondary" onClick={() => void runDiscover()} disabled={busyUi}><WandSparkles aria-hidden="true" /> 자동검색</button><button type="button" className="local-pc-secondary" onClick={() => { setDraft(EMPTY_DRAFT); setEditingId(''); setShowEditor(true) }} disabled={busyUi}><Settings2 aria-hidden="true" /> 직접 등록</button></div>
      {discoveries.length > 0 && <details className="local-pc-discovery" open><summary><span><strong>설치 후보 {discoveries.length}개</strong><small>후보를 선택하면 등록 폼에 채워집니다.</small></span><WandSparkles aria-hidden="true" /></summary><div>{discoveries.map((candidate) => <article key={`${candidate.name}-${candidate.version}-${candidate.executable_path}`}><div><strong>{candidate.name} <em>v{candidate.version}</em></strong><small>{displayPath(candidate.executable_path)}</small></div><button type="button" onClick={() => { setDraft(candidateDraft(candidate)); setEditingId(''); setShowEditor(true) }}>선택하여 등록</button></article>)}</div></details>}
      {showEditor && <form className="local-pc-editor" onSubmit={(event) => { event.preventDefault(); void saveProgram() }}><header><div><span className="local-pc-eyebrow">{editingId ? 'EDIT PROGRAM' : 'REGISTER PROGRAM'}</span><h3>{editingId ? '프로그램 수정' : '프로그램 등록'}</h3></div><button type="button" className="local-pc-icon-button" aria-label="프로그램 등록 닫기" onClick={() => setShowEditor(false)}><X aria-hidden="true" /></button></header><div className="local-pc-form-grid"><label><span>이름 *</span><input required maxLength={120} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label><label><span>버전</span><input maxLength={80} value={draft.version} onChange={(event) => setDraft({ ...draft, version: event.target.value })} /></label><label className="wide"><span>키워드</span><input value={draft.keywords} onChange={(event) => setDraft({ ...draft, keywords: event.target.value })} placeholder="쉼표로 구분 · 예: 전처리, 메시" /></label><label className="wide"><span>실행 파일 *</span><div className="local-pc-picker"><input aria-label="실행 파일" required value={draft.executable_path} onChange={(event) => setDraft({ ...draft, executable_path: event.target.value })} placeholder="도우미가 접근할 절대 경로" /><button type="button" onClick={() => void pickExecutable()} disabled={busyUi}><FolderOpen aria-hidden="true" /> 파일 선택</button></div></label><details className="local-pc-advanced wide"><summary>고급 설정 · 인수 JSON 배열</summary><label><span>인수 JSON 배열</span><textarea value={draft.arguments} onChange={(event) => setDraft({ ...draft, arguments: event.target.value })} spellCheck={false} aria-describedby="local-pc-arguments-help" /><small id="local-pc-arguments-help">문자열 배열만 허용합니다. 입력 파일 자리표시자는 {`{input}`}을 사용할 수 있습니다.</small></label></details></div><footer><button type="button" className="local-pc-secondary" onClick={() => setShowEditor(false)}>취소</button><button type="submit" className="local-pc-primary" disabled={busyUi}>{busy === 'save' ? <LoaderCircle className="local-pc-spin" aria-hidden="true" /> : <Check aria-hidden="true" />} {editingId ? '수정 저장' : '프로그램 등록'}</button></footer></form>}
      {programs.length ? <div className="local-pc-program-grid" role="list" aria-label="등록 프로그램 목록">{programs.map((program) => <article className="local-pc-program" key={program.id}><div className="local-pc-program-main"><div><strong>{program.name} <em>v{program.version}</em></strong><small>{program.keywords.length ? program.keywords.join(' · ') : '키워드 없음'}</small><code>{displayPath(program.executable_path)}</code></div><b className={program.available ? 'available' : 'unavailable'}>{program.available ? '사용 가능' : '경로 없음'}</b></div><footer><button type="button" onClick={() => { setDraft({ name: program.name, version: program.version, keywords: program.keywords.join(', '), executable_path: program.executable_path, arguments: JSON.stringify(program.arguments) }); setEditingId(program.id); setShowEditor(true) }} disabled={busyUi}><Pencil aria-hidden="true" /> 수정</button><button type="button" className="danger" onClick={() => void removeProgram(program)} disabled={busyUi}><Trash2 aria-hidden="true" /> 삭제</button></footer></article>)}</div> : <p className="local-pc-empty">{query.trim() ? `“${query.trim()}”에 맞는 등록 프로그램이 없습니다.` : '등록된 프로그램이 없습니다. 자동검색 후보를 선택하거나 직접 등록하세요.'}</p>}
    </section>}
    <section className="local-pc-devices-card" aria-labelledby="local-pc-devices-heading"><header className="local-pc-section-header"><div><span className="local-pc-eyebrow"><Monitor aria-hidden="true" /> ACCOUNT DEVICES</span><h2 id="local-pc-devices-heading">내 계정의 연결 PC</h2><p>현재 로그인한 계정의 연결만 표시됩니다. 다른 계정의 장치는 조회하지 않습니다.</p></div><button type="button" className="local-pc-icon-button" aria-label="연결 PC 새로고침" onClick={() => void loadDevices()} disabled={busyUi}><RefreshCw aria-hidden="true" /></button></header><div className="local-pc-device-list">{devices.length ? devices.map((item) => <article key={item.id} className={item.id === currentDeviceId ? 'current' : ''}><div><strong>{item.host_name}</strong><small>{item.id === currentDeviceId ? '이 PC · 현재 연결' : `연결됨 · ${new Date(item.created_at).toLocaleString('ko-KR')}`}</small></div>{item.id !== currentDeviceId && <button type="button" className="danger" onClick={() => void revokeOther(item)} disabled={busyUi}><Unplug aria-hidden="true" /> 연결 해제</button>}</article>) : <p className="local-pc-empty">연결된 PC가 없습니다.</p>}</div></section>
    {authMode === 'password' && passwordSettings}
    {(error || notice) && <div className={`local-pc-toast ${error ? 'error' : ''}`} role={error ? 'alert' : 'status'} aria-live="polite">{error ? <CircleAlert aria-hidden="true" /> : <Check aria-hidden="true" />}<span>{error || notice}</span><button type="button" aria-label="알림 닫기" onClick={() => { setError(''); setNotice('') }}><X aria-hidden="true" /></button></div>}
  </section>
}

export function LocalPcSettingsPage(props: Props) {
  const [authMode, setAuthMode] = useState<'disabled' | 'password' | 'oidc' | null>(null)
  const [authError, setAuthError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let disposed = false
    setAuthMode(null); setAuthError('')
    if (!props.currentUserId.trim()) return
    void api.authStatus().then((value) => { if (!disposed) setAuthMode(value.mode) })
      .catch((reason: unknown) => { if (!disposed) setAuthError(friendlyError(reason)) })
    return () => { disposed = true }
  }, [props.currentUserId, revision])
  if (!props.currentUserId.trim()) return <LoggedOutLocalPcSettings />
  if (authMode === 'password' || authMode === 'oidc') return <AuthenticatedLocalPcSettings key={props.currentUserId} {...props} authMode={authMode} />
  return <section className="local-pc-settings" data-testid="local-pc-settings"><header className="local-pc-page-header"><h1>내 PC 설정</h1></header><section className="local-pc-login-required" role="status"><CircleAlert aria-hidden="true" /><div><strong>{authMode === 'disabled' ? '서버 로그인 설정이 필요합니다.' : authError ? '로그인 설정을 확인하지 못했습니다.' : '로그인 설정을 확인하고 있습니다.'}</strong><p>{authMode === 'disabled' ? '개인별 PC 연결을 사용하려면 서버 관리자가 아이디·비밀번호 로그인 또는 회사 SSO를 활성화해야 합니다. 설정 후 개인 계정으로 로그인해 주세요.' : authError}</p>{(authMode === 'disabled' || authError) && <button type="button" className="local-pc-secondary" onClick={() => setRevision((value) => value + 1)}><RefreshCw aria-hidden="true" /> 로그인 설정 다시 확인</button>}</div></section></section>
}
