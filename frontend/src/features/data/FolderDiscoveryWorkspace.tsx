import { FolderOpen, LoaderCircle, Play, RefreshCw, Save, Search, WandSparkles } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { folderDiscoveryApi, type FolderDiscoveryBrowse, type FolderDiscoveryPreview, type FolderDiscoveryRule, type FolderDiscoveryScan, type FolderRole } from '../../shared/api/folderDiscovery'
import { saveStorageConfig } from '../../shared/api/storage'
import './FolderDiscoveryWorkspace.css'

type Notice = { kind: 'success' | 'error' | 'info'; text: string }
const DEFAULT_RULES: FolderDiscoveryRule[] = [
  { depth: 1, role: 'PROJECT', delimiter: '_', code_token: 1, name_from_token: 2 },
  { depth: 2, role: 'REQUEST', delimiter: '_', code_token: 1, name_from_token: 2 },
  { depth: 3, role: 'LOAD_CASE', delimiter: '_', code_token: 1, name_from_token: 2, analysis_type: 'SPDM_CMS' },
]
const ANALYSIS_TYPES = ['DROP', 'SIDE_CLAMP', 'SPDM_CMS', 'SPDM_MODAL', 'SPDM_DEFLECTION', 'SPDM_STIFFNESS', 'SPDM_VIBRATION']

function errorText(reason: unknown, fallback: string) { return reason instanceof Error ? reason.message : fallback }
function roleName(role: FolderRole) { return role === 'PROJECT' ? '프로젝트' : role === 'REQUEST' ? '의뢰' : '하중 경우' }

export function FolderDiscoveryWorkspace({ onComplete, onOpenFolder }: { onComplete?: () => void; onOpenFolder?: () => void }) {
  const [browse, setBrowse] = useState<FolderDiscoveryBrowse | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerPath, setPickerPath] = useState('')
  const [selectedPath, setSelectedPath] = useState('')
  const [rootPath, setRootPath] = useState('')
  const [scan, setScan] = useState<FolderDiscoveryScan | null>(null)
  const [rules, setRules] = useState<FolderDiscoveryRule[]>(DEFAULT_RULES)
  const [rulesRevision, setRulesRevision] = useState<number | null>(null)
  const [rulesLoaded, setRulesLoaded] = useState(false)
  const [preview, setPreview] = useState<FolderDiscoveryPreview | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [busy, setBusy] = useState<'browse' | 'root' | 'scan' | 'preview' | 'apply' | 'rules' | ''>('')
  const requestGeneration = useRef(0)
  const rulesGeneration = useRef(0)

  const invalidatePreview = useCallback(() => setPreview(null), [])
  const loadBrowse = useCallback(async (path = '') => {
    const generation = ++requestGeneration.current
    setBusy('browse')
    try {
      const result = await folderDiscoveryApi.browse(path)
      if (generation !== requestGeneration.current) return
      setBrowse(result)
    } catch (reason) { if (generation === requestGeneration.current) setNotice({ kind: 'error', text: errorText(reason, '폴더 목록을 불러오지 못했습니다.') }) } finally { if (generation === requestGeneration.current) setBusy('') }
  }, [])
  const loadRules = useCallback(async (path: string) => {
    const generation = ++rulesGeneration.current
    setRulesLoaded(false); setRulesRevision(null); setPreview(null)
    try {
      const result = await folderDiscoveryApi.rules(path)
      if (generation !== rulesGeneration.current) return
      setRules(result.rules.length ? result.rules : DEFAULT_RULES); setRulesRevision(result.revision); setRulesLoaded(true)
    } catch { if (generation === rulesGeneration.current) { setRules(DEFAULT_RULES); setRulesRevision(null); setRulesLoaded(false); setNotice({ kind: 'error', text: '규칙을 불러오지 못했습니다. 폴더를 다시 선택하거나 새로고침하세요.' }) } }
  }, [])
  useEffect(() => { void loadBrowse() }, [loadBrowse])
  useEffect(() => { if (browse?.configured) void loadRules(selectedPath) }, [loadRules, selectedPath, browse?.configured])

  const selectFolder = (path: string) => { setSelectedPath(path); setScan(null); invalidatePreview() }
  const openPicker = () => { setPickerPath(selectedPath); setPickerOpen(true); void loadBrowse(selectedPath) }
  const saveRoot = async () => {
    if (!rootPath.trim()) return
    setBusy('root')
    try { await saveStorageConfig(rootPath.trim()); setScan(null); setPreview(null); setSelectedPath(''); setNotice({ kind: 'success', text: '저장 폴더를 설정했습니다. 조사할 최상위 폴더를 선택하세요.' }); await loadBrowse(''); await loadRules('') } catch (reason) { setNotice({ kind: 'error', text: errorText(reason, '저장 폴더를 설정하지 못했습니다.') }) } finally { setBusy('') }
  }
  const startScan = async () => {
    setBusy('scan'); setScan(null); invalidatePreview(); setNotice(null)
    try { const result = await folderDiscoveryApi.scan(selectedPath); setScan(result); setNotice({ kind: result.status === 'COMPLETE' ? 'success' : 'error', text: result.status === 'COMPLETE' ? `전체 트리 ${result.folder_count}개 폴더를 조사했습니다.` : '조사가 완료되지 않았습니다. 문제를 해결한 뒤 다시 조사하세요.' }) } catch (reason) { setNotice({ kind: 'error', text: errorText(reason, '폴더 조사를 시작하지 못했습니다.') }) } finally { setBusy('') }
  }
  const updateRule = (index: number, patch: Partial<FolderDiscoveryRule>) => { setRules((current) => current.map((rule, row) => row === index ? { ...rule, ...patch } : rule)); invalidatePreview() }
  const addRule = () => { setRules((current) => current.length >= 30 ? current : [...current, { depth: Math.min(64, current.length + 1), role: 'LOAD_CASE', delimiter: '_', code_token: 1, name_from_token: 2, analysis_type: 'SPDM_CMS' }]); invalidatePreview() }
  const removeRule = (index: number) => { setRules((current) => current.length > 1 ? current.filter((_, row) => row !== index) : current); invalidatePreview() }
  const runPreview = async () => {
    if (!scan || scan.status !== 'COMPLETE') return
    setBusy('preview'); setNotice(null)
    try { const result = await folderDiscoveryApi.preview(scan.id, rules); setPreview(result); setNotice({ kind: result.can_apply ? 'success' : 'error', text: result.can_apply ? '업무 생성 미리보기를 확인하세요.' : '충돌 또는 미분류 항목을 해결해야 적용할 수 있습니다.' }) } catch (reason) { setNotice({ kind: 'error', text: errorText(reason, '미리보기를 생성하지 못했습니다.') }) } finally { setBusy('') }
  }
  const saveRules = async () => {
    if (rulesRevision == null) return
    setBusy('rules')
    try { const result = await folderDiscoveryApi.saveRules(selectedPath, rules, rulesRevision); setRules(result.rules); setRulesRevision(result.revision); invalidatePreview(); setNotice({ kind: 'success', text: `이 폴더의 규칙 v${result.revision}을 저장했습니다.` }) } catch (reason) { setNotice({ kind: 'error', text: errorText(reason, '규칙 저장에 실패했습니다. 최신 규칙을 다시 불러오세요.') }) } finally { setBusy('') }
  }
  const apply = async () => {
    if (!preview?.can_apply || !scan || scan.status !== 'COMPLETE') return
    setBusy('apply')
    try {
      const result = await folderDiscoveryApi.apply(preview.id)
      setNotice({ kind: 'success', text: `완료: 프로젝트 ${result.created.projects}, 의뢰 ${result.created.requests}, 하중 경우 ${result.created.load_cases}개를 만들고 ${result.kept_count}개를 유지했습니다.` })
      onComplete?.(); window.dispatchEvent(new CustomEvent('folder-discovery-applied'))
    } catch (reason) { setNotice({ kind: 'error', text: errorText(reason, '업무 생성 적용에 실패했습니다. 미리보기를 새로 만드세요.') }) } finally { setBusy('') }
  }
  const nodes = useMemo(() => scan?.nodes ?? [], [scan])
  const applyDisabled = busy !== '' || scan?.status !== 'COMPLETE' || !preview?.can_apply

  if (!browse) return <section className="folder-discovery" data-testid="folder-discovery-workspace"><p role="status">저장소를 확인하고 있습니다.</p>{notice ? <NoticeView notice={notice} /> : null}<button onClick={() => void loadBrowse()} disabled={busy !== ''}>다시 시도</button></section>

  if (!browse.configured) return <section className="folder-discovery" data-testid="folder-discovery-workspace"><header><span>FOLDER DISCOVERY</span><h2>폴더 조사·업무 생성</h2><p>기존 예제나 프로젝트를 고르지 않고, 실제 저장 폴더에서 업무 구조를 조사합니다.</p></header><div className="folder-discovery-card"><h3>저장 폴더 설정</h3><p>먼저 실제 폴더를 보관하는 SPDM root를 설정하세요.</p><div className="folder-discovery-root"><input aria-label="저장 폴더 경로" value={rootPath} onChange={(event) => setRootPath(event.target.value)} placeholder="예: D:\\Simulation" /><button className="primary-button" onClick={() => void saveRoot()} disabled={busy !== ''}><Save /> 저장</button></div></div>{notice ? <NoticeView notice={notice} /> : null}</section>

  return <section className="folder-discovery" data-testid="folder-discovery-workspace">
    <header><span>FOLDER DISCOVERY</span><h2>폴더 조사·업무 생성</h2><p>선택한 최상위 폴더의 전체 하위 트리를 조사하고, 확정 전 미리보기로 프로젝트·의뢰·하중 경우를 검토합니다.</p></header>
    {notice ? <NoticeView notice={notice} /> : null}
    <div className="folder-discovery-card"><div className="folder-discovery-heading"><div><h3>1. 최상위 폴더 선택</h3><p>{browse?.root_path ?? '설정된 저장 root'}</p></div><button className="ghost-button" onClick={() => { void loadBrowse(selectedPath); void loadRules(selectedPath) }} disabled={busy !== ''}><RefreshCw /> 새로고침</button></div><div className="folder-discovery-browser"><strong>{selectedPath || '/'}</strong><button className="ghost-button" onClick={openPicker} disabled={busy !== ''}><FolderOpen />최상위 폴더 선택</button></div><div className="folder-discovery-actions"><button className="primary-button" onClick={() => void startScan()} disabled={busy !== ''}>{busy === 'scan' ? <LoaderCircle className="spin" /> : <Search />}{busy === 'scan' ? '조사 중…' : '전체 트리 조사'}</button></div></div>
    {scan ? <div className="folder-discovery-card"><div className="folder-discovery-heading"><div><h3>2. 조사 결과</h3><p>{scan.relative_path || '/'} · 폴더 {scan.folder_count} · 파일 {scan.file_count} · {scan.status}</p></div>{busy === 'scan' ? <LoaderCircle className="spin" /> : null}</div>{scan.issues.length ? <ul className="folder-discovery-issues">{scan.issues.map((issue, index) => <li key={index}><IssueDetail issue={issue} /></li>)}</ul> : null}<div className="folder-discovery-tree">{nodes.map((node) => <div key={node.relative_path} style={{ paddingInlineStart: `${Math.max(0, node.depth) * 22}px` }}><FolderOpen /><span>{node.name}</span><small>파일 {node.file_count}{node.extensions.length ? ` · ${node.extensions.join(', ')}` : ''}</small></div>)}</div></div> : null}
    {scan ? <div className="folder-discovery-card"><div className="folder-discovery-heading"><div><h3>3. 폴더 역할 규칙</h3><p>밑줄 토큰으로 코드와 이름을 추출합니다. 코드·이름 토큰 0은 폴더 전체 이름을 사용합니다.</p></div><button className="ghost-button" onClick={() => void saveRules()} disabled={busy !== '' || !rulesLoaded || rulesRevision == null}><Save /> 규칙 저장</button></div><fieldset className="folder-discovery-rules" disabled={busy !== '' || !rulesLoaded}>{rules.map((rule, index) => <RuleEditor key={`${rule.role}-${index}`} rule={rule} onChange={(patch) => updateRule(index, patch)} onDelete={() => removeRule(index)} />)}</fieldset><div className="folder-discovery-actions"><button className="ghost-button" onClick={addRule} disabled={busy !== '' || !rulesLoaded || rules.length >= 30}>규칙 추가</button><button className="primary-button" onClick={() => void runPreview()} disabled={busy !== '' || !rulesLoaded || scan.status !== 'COMPLETE'}><WandSparkles />{busy === 'preview' ? '미리보기 생성 중…' : '업무 생성 미리보기'}</button></div></div> : null}
    {preview ? <div className="folder-discovery-card"><div className="folder-discovery-heading"><div><h3>4. 생성 미리보기</h3><p>프로젝트 {preview.summary.projects} · 의뢰 {preview.summary.requests} · 하중 경우 {preview.summary.load_cases} · 충돌 {preview.summary.conflicts} · 미분류 {preview.unmatched_count}</p></div></div><div className="folder-discovery-preview"><table><thead><tr><th>경로</th><th>역할</th><th>코드</th><th>이름</th><th>결과</th><th>상세</th></tr></thead><tbody>{preview.rows.map((row) => <tr key={`${row.relative_path}/${row.role}`} className={row.status.toLowerCase()}><td>{row.relative_path}</td><td>{roleName(row.role)}</td><td>{row.code}</td><td>{row.name}</td><td>{row.status}</td><td>{row.message ?? ''}</td></tr>)}</tbody></table></div><div className="folder-discovery-actions"><button className="primary-button" onClick={() => void apply()} disabled={applyDisabled}><Play />{busy === 'apply' ? '생성 중…' : '검토한 업무 생성 적용'}</button>{preview && notice?.kind === 'success' && notice.text.startsWith('완료:') ? <button className="ghost-button" onClick={onOpenFolder}>결과파일 연결하기</button> : null}{scan?.status !== 'COMPLETE' ? <small>조사가 완료되어야 적용할 수 있습니다.</small> : null}</div></div> : null}
    {pickerOpen ? <dialog className="folder-discovery-picker" ref={(node) => { if (node && !node.open) node.showModal() }} aria-label="최상위 폴더 선택" onCancel={() => setPickerOpen(false)}><div className="folder-discovery-heading"><div><h3>조사할 최상위 폴더 선택</h3><p>선택한 폴더 아래의 모든 하위 트리를 조사합니다.</p></div><button className="ghost-button" onClick={() => setPickerOpen(false)}>닫기</button></div><div className="folder-discovery-browser"><button onClick={() => { setPickerPath(''); void loadBrowse('') }} disabled={busy !== ''}>root</button>{browse?.relative_path ? <button onClick={() => { const parent = browse.relative_path.split('/').slice(0, -1).join('/'); setPickerPath(parent); void loadBrowse(parent) }} disabled={busy !== ''}>상위 폴더</button> : null}<strong>{browse?.relative_path || '/'}</strong></div><div className="folder-discovery-entries">{browse?.entries.filter((entry) => entry.is_directory).map((entry) => <button key={entry.relative_path} className={pickerPath === entry.relative_path ? 'selected' : ''} onClick={() => { setPickerPath(entry.relative_path); void loadBrowse(entry.relative_path) }} disabled={busy !== ''}><FolderOpen />{entry.name}</button>)}</div><div className="folder-discovery-actions"><button className="ghost-button" disabled={busy !== ''} onClick={() => setPickerPath(browse?.relative_path ?? '')}>이 위치 선택</button><button className="primary-button" disabled={busy !== '' || pickerPath !== browse?.relative_path} onClick={() => { selectFolder(pickerPath); setPickerOpen(false); void loadRules(pickerPath) }}>선택 완료</button></div></dialog> : null}
  </section>
}

function RuleEditor({ rule, onChange, onDelete }: { rule: FolderDiscoveryRule; onChange: (patch: Partial<FolderDiscoveryRule>) => void; onDelete: () => void }) {
  return <div className="folder-discovery-rule">
    <label>깊이<input aria-label={`${roleName(rule.role)} 깊이`} type="number" min="0" max="64" value={rule.depth} onChange={(event) => onChange({ depth: Math.max(0, Math.min(64, Number(event.target.value) || 0)) })} /></label>
    <label>역할<select aria-label={`${roleName(rule.role)} 역할`} value={rule.role} onChange={(event) => onChange({ role: event.target.value as FolderRole })}><option value="PROJECT">프로젝트</option><option value="REQUEST">의뢰</option><option value="LOAD_CASE">하중 경우</option></select></label>
    <label>prefix<input aria-label={`${roleName(rule.role)} prefix`} value={rule.prefix ?? ''} placeholder="예: P_" onChange={(event) => onChange({ prefix: event.target.value || undefined })} /></label>
    <label>구분자<input aria-label={`${roleName(rule.role)} 구분자`} value={rule.delimiter ?? '_'} maxLength={8} onChange={(event) => onChange({ delimiter: event.target.value || '_' })} /></label>
    <label>코드 토큰<input aria-label={`${roleName(rule.role)} 코드 토큰`} type="number" min="0" max="100" value={rule.code_token ?? 1} onChange={(event) => onChange({ code_token: Math.max(0, Math.min(100, Number(event.target.value) || 0)) })} /></label>
    <label>이름 시작 토큰<input aria-label={`${roleName(rule.role)} 이름 토큰`} type="number" min="0" max="100" value={rule.name_from_token ?? 2} onChange={(event) => onChange({ name_from_token: Math.max(0, Math.min(100, Number(event.target.value) || 0)) })} /></label>
    {rule.role === 'LOAD_CASE' ? <label>해석 종류<select aria-label="하중 경우 해석 종류" value={rule.analysis_type ?? 'SPDM_CMS'} onChange={(event) => onChange({ analysis_type: event.target.value })}>{ANALYSIS_TYPES.map((item) => <option key={item}>{item}</option>)}</select></label> : <span />}
    <button type="button" className="ghost-button" onClick={onDelete}>삭제</button>
  </div>
}

function NoticeView({ notice }: { notice: Notice }) { return <div className={`folder-discovery-notice ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.text}</div> }

function IssueDetail({ issue }: { issue: unknown }) {
  if (typeof issue === 'string') return <>{issue}</>
  if (issue && typeof issue === 'object') {
    const value = issue as Record<string, unknown>
    const path = typeof value.relative_path === 'string' ? value.relative_path : typeof value.path === 'string' ? value.path : ''
    const reason = typeof value.message === 'string' ? value.message : typeof value.reason === 'string' ? value.reason : typeof value.code === 'string' ? value.code : JSON.stringify(value)
    return <>{path ? `${path}: ` : ''}{reason}</>
  }
  return <>{String(issue)}</>
}
