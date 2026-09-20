import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api'
import type { AnalysisRequest, Project } from '../../types'
import { folderEnvironmentApi as service, type FolderAssignment, type FolderEnvironment, type FolderEnvironmentNode, type FolderEnvironmentPreview, type FolderEnvironmentProfile, type FolderEnvironmentProfileRule, type FolderEnvironmentRegistration, type FolderEnvironmentScan } from '../../shared/api/folderEnvironment'
import { folderDiscoveryApi, type FolderDiscoveryBrowse } from '../../shared/api/folderDiscovery'
import { environmentRoles, FolderProfileEditor, FolderRegistrationResults, roleLabel } from './FolderEnvironmentPanels'
import './FolderEnvironmentWorkspace.css'

type Props = { selectedProjectId?: string; selectedRequestId?: string; onComplete?: () => void }
type Tab = 'connect' | 'profiles' | 'history'
function preferredProfile(environment: FolderEnvironment) { try { return localStorage.getItem(`folder-environment-profile:${environment}`) ?? '' } catch { return '' } }
function rememberProfile(environment: FolderEnvironment, id: string) { try { localStorage.setItem(`folder-environment-profile:${environment}`, id) } catch { /* Storage may be disabled; the current session remains usable. */ } }

export function FolderEnvironmentWorkspace({ selectedProjectId = '', selectedRequestId = '', onComplete }: Props) {
  const [tab, setTab] = useState<Tab>('connect')
  const [environment, setEnvironment] = useState<FolderEnvironment>(() => new URLSearchParams(window.location.search).get('result_environment') === 'DISTRIBUTION' ? 'DISTRIBUTION' : 'USAGE')
  const [profiles, setProfiles] = useState<FolderEnvironmentProfile[]>([])
  const [profileId, setProfileId] = useState('')
  const [path, setPath] = useState('')
  const [projects, setProjects] = useState<Project[]>([])
  const [requests, setRequests] = useState<AnalysisRequest[]>([])
  const [project, setProject] = useState(selectedProjectId)
  const [request, setRequest] = useState(selectedRequestId)
  const [useExisting, setUseExisting] = useState(Boolean(selectedRequestId))
  const [scan, setScan] = useState<FolderEnvironmentScan | null>(null)
  const [assignments, setAssignments] = useState<Record<string, FolderAssignment>>({})
  const [preview, setPreview] = useState<FolderEnvironmentPreview | null>(null)
  const [registration, setRegistration] = useState<FolderEnvironmentRegistration | null>(null)
  const [history, setHistory] = useState<{ items: FolderEnvironmentRegistration[]; total: number }>({ items: [], total: 0 })
  const [historyPage, setHistoryPage] = useState(0)
  const [browse, setBrowse] = useState<FolderDiscoveryBrowse | null>(null)
  const [step, setStep] = useState(1)
  const [nodeId, setNodeId] = useState('')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [onlyIssues, setOnlyIssues] = useState(false)
  const [treePage, setTreePage] = useState(0)
  const [mobilePane, setMobilePane] = useState<'tree' | 'detail'>('tree')
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')
  const epoch = useRef(0)
  const nodes = scan?.nodes ?? []
  const current = nodes.find((node) => node.id === nodeId)
  const effectiveRole = (node: FolderEnvironmentNode) => assignments[node.id]?.role_kind ?? node.role_kind ?? ''
  const needsReview = (node: FolderEnvironmentNode) => !assignments[node.id]?.confirm && ['UNRESOLVED', 'ERROR'].includes(node.status)
  const envProfiles = profiles.filter((profile) => profile.environment === environment)
  const invalidate = () => { epoch.current += 1; setScan(null); setPreview(null); setRegistration(null); setAssignments({}); setStep(1); setNotice(''); setTreePage(0) }
  async function execute<T>(name: string, operation: () => Promise<T>, accept: (value: T) => void) {
    const intent = epoch.current; setBusy(name); setNotice('')
    try { const value = await operation(); if (intent === epoch.current) accept(value) }
    catch (error) { if (intent === epoch.current) setNotice(error instanceof Error ? error.message : '처리하지 못했습니다.') }
    finally { if (intent === epoch.current) setBusy('') }
  }
  useEffect(() => { let active = true; Promise.all([service.profiles(), api.projects()]).then(([items, all]) => { if (active) { setProfiles(items); setProjects(all) } }).catch((error) => { if (active) setNotice(String(error)) }); return () => { active = false; epoch.current += 1 } }, [])
  useEffect(() => { let active = true; setRequests([]); if (project) api.requests(project).then((items) => { if (active) { setRequests(items); setRequest((value) => items.some((item) => item.id === value) ? value : '') } }).catch((error) => { if (active) setNotice(String(error)) }); return () => { active = false } }, [project])
  useEffect(() => { if (tab !== 'history') return; let active = true; service.history(historyPage * 50).then((value) => { if (active) setHistory(value) }).catch((error) => { if (active) setNotice(String(error)) }); return () => { active = false } }, [tab, historyPage])
  useEffect(() => { const preferred = preferredProfile(environment); setProfileId(profiles.some((item) => item.environment === environment && item.id === preferred) ? preferred : '') }, [environment, profiles])
  const survey = () => execute('조사 중…', () => service.scan({ environment, relative_path: path.trim(), profile_id: profileId || undefined, project_id: useExisting ? project || undefined : undefined, request_id: useExisting ? request || undefined : undefined }), (value) => { setScan(value); setAssignments({}); setPreview(null); setRegistration(null); setNodeId(value.nodes.find((node) => node.role_kind === 'SIMULATION_CASE')?.id ?? value.nodes[0]?.id ?? ''); setStep(2); setTreePage(0); setCollapsed(new Set()); setMobilePane('tree'); setNotice(value.status === 'COMPLETE' ? `${value.nodes.length}개 폴더를 조사했습니다.` : '불완전 조사입니다. 표시된 문제를 확인하고 다시 조사하세요.') })
  const assign = (patch: Partial<FolderAssignment>) => { if (!current) return; setAssignments((values) => ({ ...values, [current.id]: { ...(values[current.id] ?? { node_id: current.id, role_kind: effectiveRole(current), target_mode: 'CREATE' as const }), ...patch, confirm: true } })); if (effectiveRole(current) === 'PROJECT' && patch.target_id) { setProject(patch.target_id); setRequest('') }; setPreview(null); setRegistration(null) }
  const visibleNodes = useMemo(() => nodes.filter((node) => (!onlyIssues || needsReview(node)) && ![...collapsed].some((parent) => node.relative_path !== parent && node.relative_path.startsWith(`${parent}/`))), [scan, onlyIssues, collapsed, assignments])
  const seedRules = useMemo<FolderEnvironmentProfileRule[]>(() => nodes.filter((node) => effectiveRole(node) && !['CONTAINER', 'EXCLUDE'].includes(effectiveRole(node))).map((node) => {
    let parent = nodes.find((item) => item.relative_path === node.parent_path)
    while (parent && (!effectiveRole(parent) || effectiveRole(parent) === 'CONTAINER')) parent = nodes.find((item) => item.relative_path === parent!.parent_path)
    return { role_kind: effectiveRole(node), parent_role: parent ? effectiveRole(parent) : undefined, pattern: node.name.replace(/[?*\[]/g, (c) => `[${c}]`), match_mode: 'glob' }
  }), [scan, assignments])
  const showRegistration = (value: FolderEnvironmentRegistration) => { setRegistration(value); setTab('connect'); setStep(3); onComplete?.() }
  const jobPanel = registration && <FolderRegistrationResults value={registration} busy={Boolean(busy)} onRefresh={() => void execute('상태 조회 중…', () => service.registration(registration.registration_id), setRegistration)} onRetry={() => void execute('결과 읽는 중…', () => service.retryCapture(registration.registration_id), setRegistration)} />
  return <section className="folder-environment-workspace" data-ui-density="v1">
    <header className="folder-environment-heading"><div><h1>폴더 연결·규칙</h1><p>폴더 구조를 확인하고 등록한 Case의 결과를 바로 엽니다.</p></div><label>환경<select aria-label="폴더 환경" value={environment} disabled={Boolean(busy)} onChange={(event) => { invalidate(); setEnvironment(event.target.value as FolderEnvironment); setProfileId('') }}><option value="USAGE">사용환경</option><option value="DISTRIBUTION">유통환경</option></select></label></header>
    <nav className="saved-work-tabs" aria-label="폴더 규칙 작업">{([['connect', '폴더 연결'], ['profiles', '저장된 규칙'], ['history', '등록 이력']] as const).map(([key, title]) => <button type="button" key={key} disabled={Boolean(busy)} className={tab === key ? 'active' : ''} aria-current={tab === key ? 'page' : undefined} onClick={() => setTab(key)}>{title}</button>)}</nav>
    {notice && <div className="folder-environment-notice info" role="status">{notice}</div>}
    {busy && <p role="status">{busy}</p>}
    {tab === 'profiles' && <FolderProfileEditor environment={environment} profiles={envProfiles} seedRules={seedRules} onNotice={setNotice} onSaved={(item) => { setProfiles((items) => [...items.filter((p) => p.id !== item.id), item]); setProfileId(item.id); rememberProfile(item.environment, item.id); invalidate() }} />}
    {tab === 'history' && <article className="folder-environment-card"><h2>등록 이력</h2>{!history.items.length && <p>현재 저장소의 등록 이력이 없습니다.</p>}{history.items.map((item) => <button type="button" className="folder-history-row ghost-button" key={item.registration_id} onClick={() => void execute('이력 조회 중…', () => service.registration(item.registration_id), showRegistration)}>{new Date(item.created_at).toLocaleString('ko-KR')} · {item.environment === 'USAGE' ? '사용환경' : '유통환경'} · {item.relative_path || '저장소 전체'} · Case {item.capture_jobs.length}개</button>)}<div className="folder-environment-footer"><button type="button" disabled={!historyPage} onClick={() => setHistoryPage(historyPage - 1)}>이전</button><span>{history.total}건 · {historyPage + 1}페이지</span><button type="button" disabled={(historyPage + 1) * 50 >= history.total} onClick={() => setHistoryPage(historyPage + 1)}>다음</button></div></article>}
    {tab === 'connect' && <>
      <nav className="folder-steps" aria-label="폴더 연결 단계">{['폴더 선택', '구조 확인', '등록·결과 확인'].map((title, i) => <button type="button" key={title} disabled={Boolean(busy) || (i === 1 && !scan) || (i === 2 && !preview && !registration)} aria-current={step === i + 1 ? 'step' : undefined} className={step === i + 1 ? 'active' : ''} onClick={() => setStep(i + 1)}><b>{i + 1}</b>{title}</button>)}</nav>
      {step === 1 && <article className="folder-environment-card"><div className="folder-environment-form">
        <label>저장 규칙<select value={profileId} disabled={Boolean(busy)} onChange={(event) => { invalidate(); setProfileId(event.target.value); rememberProfile(environment, event.target.value) }}><option value="">기본 규칙으로 시작</option>{envProfiles.map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.revision}</option>)}</select></label>
        <label>업무 연결<select value={useExisting ? 'existing' : 'tree'} disabled={Boolean(busy)} onChange={(event) => { invalidate(); setUseExisting(event.target.value === 'existing') }}><option value="tree">폴더에서 프로젝트·의뢰 찾기</option><option value="existing">기존 프로젝트·의뢰에 연결</option></select></label>
        {useExisting && <><label>프로젝트<select aria-label="연결 프로젝트" value={project} disabled={Boolean(busy)} onChange={(event) => { invalidate(); setProject(event.target.value); setRequest('') }}><option value="">선택</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label><label>의뢰<select aria-label="연결 의뢰" value={request} disabled={Boolean(busy) || !project} onChange={(event) => { invalidate(); setRequest(event.target.value) }}><option value="">선택</option>{requests.map((r) => <option key={r.id} value={r.id}>{r.title}</option>)}</select></label></>}
        <label className="wide">저장소 안 조사 경로<div className="path-entry"><input value={path} disabled={Boolean(busy)} placeholder="비우면 저장소 전체" onChange={(event) => { invalidate(); setPath(event.target.value) }} /><button type="button" className="ghost-button" disabled={Boolean(busy)} onClick={() => void execute('폴더 목록 조회 중…', () => folderDiscoveryApi.browse(path), setBrowse)}>폴더 찾아보기</button></div></label>
        {browse && <div className="folder-browser wide"><strong>{browse.relative_path || '저장소 전체'}</strong>{!browse.configured ? <p>관리자 저장소 설정에서 서버 경로를 먼저 연결하세요.</p> : <><div className="folder-environment-footer"><button type="button" onClick={() => { invalidate(); setPath(browse.relative_path); setBrowse(null) }}>이 폴더 선택</button><button type="button" disabled={Boolean(busy) || !browse.relative_path} onClick={() => void execute('폴더 목록 조회 중…', () => folderDiscoveryApi.browse(browse.relative_path.split('/').slice(0, -1).join('/')), setBrowse)}>상위 폴더</button></div>{browse.entries.filter((item) => item.is_directory).map((item) => <button type="button" key={item.relative_path} disabled={Boolean(busy)} onClick={() => void execute('폴더 목록 조회 중…', () => folderDiscoveryApi.browse(item.relative_path), setBrowse)}>{item.name} ›</button>)}</>}</div>}
      </div><footer className="folder-environment-footer"><span>서버 저장소에서 읽기 전용으로 조사합니다.</span><button type="button" className="primary-button" disabled={Boolean(busy) || (useExisting && (!project || !request))} onClick={() => void survey()}>조사 시작</button></footer></article>}
      {step === 2 && scan && <article className="folder-environment-card"><div className="folder-environment-card-head"><h2>구조 확인</h2><span>{scan.relative_path || '저장소 전체'} · {nodes.length}개 폴더</span></div>
        {scan.issues.length > 0 && <div role="alert">{scan.issues.map((issue, i) => <p key={i}>{issue.message}</p>)}</div>}
        <div className="folder-environment-footer"><label><input type="checkbox" checked={onlyIssues} onChange={(event) => { setOnlyIssues(event.target.checked); setTreePage(0) }} /> 확인 필요만 보기</label><span>{nodes.filter(needsReview).length}개 확인 필요</span></div>
        <div className="folder-mobile-panes"><button type="button" aria-pressed={mobilePane === 'tree'} onClick={() => setMobilePane('tree')}>폴더 트리</button><button type="button" disabled={!current} aria-pressed={mobilePane === 'detail'} onClick={() => setMobilePane('detail')}>선택 폴더 설정</button></div><div className={`folder-tree-layout mobile-${mobilePane}`}><div className="folder-tree" aria-label="조사한 폴더">{visibleNodes.slice(treePage * 100, (treePage + 1) * 100).map((node) => <div className={`folder-tree-row ${node.id === nodeId ? 'selected' : ''}`} key={node.id} style={{ paddingLeft: 8 + Math.min(node.depth, 6) * 12 }}>
          <button type="button" className="folder-fold" aria-label={`${node.name} ${collapsed.has(node.relative_path) ? '펼치기' : '접기'}`} onClick={() => { setCollapsed((value) => { const next = new Set(value); if (next.has(node.relative_path)) next.delete(node.relative_path); else next.add(node.relative_path); return next }); setTreePage(0) }}>{collapsed.has(node.relative_path) ? '›' : '⌄'}</button>
          <button type="button" className="folder-node-button" title={node.relative_path} aria-pressed={node.id === nodeId} onClick={() => { setNodeId(node.id); setMobilePane('detail') }}><span>{node.name || '저장소 전체'}</span><em>{roleLabel(effectiveRole(node))}</em></button></div>)}<div className="folder-environment-footer"><button type="button" disabled={!treePage} onClick={() => setTreePage(treePage - 1)}>이전</button><span>{treePage + 1}페이지</span><button type="button" disabled={(treePage + 1) * 100 >= visibleNodes.length} onClick={() => setTreePage(treePage + 1)}>다음</button></div></div>
          <div className="folder-node-detail">{current && <><h3>{current.name || '저장소 전체'}</h3><code>{current.relative_path || '/'}</code><label>폴더 역할<select aria-label="선택 폴더 역할" value={effectiveRole(current)} disabled={Boolean(busy)} onChange={(event) => assign({ role_kind: event.target.value, target_mode: 'CREATE', target_id: null })}><option value="">확인 필요</option>{[...environmentRoles(environment), 'EXCLUDE'].map((role) => <option key={role} value={role}>{roleLabel(role)}</option>)}</select></label>
          {['PROJECT', 'REQUEST'].includes(effectiveRole(current)) && <><label>연결 방식<select value={assignments[current.id]?.target_mode || 'CREATE'} onChange={(event) => assign({ target_mode: event.target.value as 'LINK' | 'CREATE', target_id: null })}><option value="CREATE">폴더 이름으로 등록·기존 연결 유지</option><option value="LINK">기존 업무 선택</option></select></label>{assignments[current.id]?.target_mode === 'LINK' && <label>기존 업무<select value={assignments[current.id]?.target_id || ''} onChange={(event) => assign({ target_id: event.target.value })}><option value="">선택</option>{(effectiveRole(current) === 'PROJECT' ? projects.map((p) => ({ id: p.id, name: p.name })) : requests.map((r) => ({ id: r.id, name: r.title }))).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>{effectiveRole(current) === 'REQUEST' && !requests.length && <small>1단계에서 연결할 프로젝트를 선택하세요.</small>}</label>}</>}
          <p>{current.message}</p><p>{effectiveRole(current) === 'RUN_OPTION' ? '이 폴더 이름을 Run Option 원문 값으로 표시합니다.' : '역할 변경은 미리보기에서 하위 구조와 함께 다시 검증합니다.'}</p></>}</div></div>
        <footer className="folder-environment-footer"><button type="button" className="ghost-button" disabled={Boolean(busy)} onClick={() => setStep(1)}>이전</button><button type="button" className="ghost-button" disabled={Boolean(busy)} onClick={() => setTab('profiles')}>구조를 규칙으로 저장</button><button type="button" className="primary-button" disabled={Boolean(busy) || scan.status !== 'COMPLETE'} onClick={() => void execute('미리보기 만드는 중…', () => service.preview({ scan_id: scan.id, assignments: Object.values(assignments) }), (value) => { setPreview(value); setRegistration(null); setStep(3) })}>등록 내용 확인</button></footer>
      </article>}
      {step === 3 && <article className="folder-environment-card"><h2>등록·결과 확인</h2>{preview && !registration && <><p>신규 {preview.summary.new} · 기존 {preview.summary.existing} · 확인 필요 {preview.unresolved_count}</p><div className="folder-preview-table">{preview.rows.map((row) => <div className="folder-preview-row" key={row.id}><span>{row.relative_path}</span><b>{roleLabel(row.role_kind)}</b><span>{row.name}</span><em>{row.status}</em>{row.message && <small>{row.message}</small>}</div>)}</div>{!preview.can_apply && <p role="alert">{preview.message || '구조 확인으로 돌아가 미분류·충돌을 해결하세요.'}</p>}<footer className="folder-environment-footer"><button type="button" className="ghost-button" disabled={Boolean(busy)} onClick={() => setStep(2)}>구조 수정</button><button type="button" className="primary-button" disabled={Boolean(busy) || !preview.can_apply} onClick={() => void execute('등록하고 결과 읽는 중…', () => service.register({ preview_id: preview.id, idempotency_key: `folder-${preview.id}`, capture: true }), showRegistration)}>등록하고 결과 읽기</button></footer></>}{jobPanel}</article>}
    </>}
  </section>
}
