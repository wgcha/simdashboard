import { FolderOpen, LoaderCircle, Play, RefreshCw, Save, Search, WandSparkles } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { folderDiscoveryApi, type FolderDiscoveryBrowse, type FolderDiscoveryPreview, type FolderDiscoveryRule, type FolderDiscoveryScan, type FolderRole } from '../../shared/api/folderDiscovery'
import type { FolderDiscoveryCatalog, FolderRoleKind, FolderRoleOption } from '../../shared/api/folderDiscovery'
import { FolderDiscoveryCatalogEditor } from './FolderDiscoveryCatalogEditor'
import { saveStorageConfig } from '../../shared/api/storage'
import './FolderDiscoveryWorkspace.css'

type Notice = { kind: 'success' | 'error' | 'info'; text: string }
const DEFAULT_RULES: FolderDiscoveryRule[] = [
  { depth: 1, role: 'PROJECT', delimiter: '_', code_token: 1, name_from_token: 2 },
  { depth: 2, role: 'REQUEST', delimiter: '_', code_token: 1, name_from_token: 2 },
  { depth: 3, role: 'LOAD_CASE', delimiter: '_', code_token: 1, name_from_token: 2, analysis_type: 'SPDM_CMS' },
]
const DEFAULT_ROLE_OPTIONS = [
  { key: 'PROJECT', label: '프로젝트', kind: 'PROJECT' as FolderRoleKind, active: true },
  { key: 'REQUEST', label: '의뢰', kind: 'REQUEST' as FolderRoleKind, active: true },
  { key: 'LOAD_CASE', label: '하중 경우', kind: 'LOAD_CASE' as FolderRoleKind, active: true },
]
const DEFAULT_ANALYSIS_TYPES = ['DROP', 'SIDE_CLAMP', 'SPDM_CMS', 'SPDM_MODAL', 'SPDM_DEFLECTION', 'SPDM_STIFFNESS', 'SPDM_VIBRATION'].map((key) => ({ key, label: key, active: true }))

function errorText(reason: unknown, fallback: string) { return reason instanceof Error ? reason.message : fallback }
function roleName(role: FolderRole, catalog?: FolderDiscoveryCatalog | null) { return catalog?.roles.find((item) => item.key === role)?.label ?? (role === 'PROJECT' ? '프로젝트' : role === 'REQUEST' ? '의뢰' : role === 'LOAD_CASE' ? '하중 경우' : role) }

export type FolderDiscoveryTarget = { relative_path: string; project_id?: string | null; request_id?: string | null; load_case_id?: string | null; role: 'RESULTS' | 'INPUT' }

export function FolderDiscoveryWorkspace({ onComplete, onOpenFolder }: { onComplete?: () => void; onOpenFolder?: (target?: FolderDiscoveryTarget) => void }) {
  const [browse, setBrowse] = useState<FolderDiscoveryBrowse | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerPath, setPickerPath] = useState('')
  const [selectedPath, setSelectedPath] = useState('')
  const [rootPath, setRootPath] = useState('')
  const [scan, setScan] = useState<FolderDiscoveryScan | null>(null)
  const [rules, setRules] = useState<FolderDiscoveryRule[]>(DEFAULT_RULES)
  const [catalog, setCatalog] = useState<FolderDiscoveryCatalog | null>(null)
  const [catalogError, setCatalogError] = useState('')
  const [rulesRevision, setRulesRevision] = useState<number | null>(null)
  const [rulesLoaded, setRulesLoaded] = useState(false)
  const [preview, setPreview] = useState<FolderDiscoveryPreview | null>(null)
  const [desiredExcludedPaths, setDesiredExcludedPaths] = useState<string[]>([])
  const [previewOutdated, setPreviewOutdated] = useState(false)
  const [appliedPreviewId, setAppliedPreviewId] = useState('')
  const [notice, setNotice] = useState<Notice | null>(null)
  const [busy, setBusy] = useState<'browse' | 'root' | 'scan' | 'preview' | 'apply' | 'rules' | 'catalog' | ''>('')
  const requestGeneration = useRef(0)
  const rulesGeneration = useRef(0)

  const loadCatalog = useCallback(async () => {
    setCatalogError('')
    try { setCatalog(await folderDiscoveryApi.catalog()) } catch (reason) { setCatalog(null); setCatalogError(errorText(reason, '폴더 역할·해석 종류 카탈로그를 불러오지 못했습니다.')) }
  }, [])

  const invalidatePreview = useCallback(() => { setPreview(null); setAppliedPreviewId(''); setDesiredExcludedPaths([]); setPreviewOutdated(false) }, [])
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
    setRulesLoaded(false); setRulesRevision(null); setPreview(null); setDesiredExcludedPaths([]); setPreviewOutdated(false)
    try {
      const result = await folderDiscoveryApi.rules(path)
      if (generation !== rulesGeneration.current) return
      setRules(result.rules.length ? result.rules.map((rule) => ({ ...rule, keyword: rule.keyword ?? rule.prefix ?? '', prefix: undefined })) : DEFAULT_RULES); setRulesRevision(result.revision); setRulesLoaded(true)
    } catch { if (generation === rulesGeneration.current) { setRules(DEFAULT_RULES); setRulesRevision(null); setRulesLoaded(false); setNotice({ kind: 'error', text: '규칙을 불러오지 못했습니다. 폴더를 다시 선택하거나 새로고침하세요.' }) } }
  }, [])
  useEffect(() => { void loadBrowse(); void loadCatalog() }, [loadBrowse, loadCatalog])
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
  const runPreview = async (nextExcludedPaths?: string[]) => {
    if (!scan || scan.status !== 'COMPLETE') return
    const paths = nextExcludedPaths ?? desiredExcludedPaths
    setBusy('preview'); setNotice(null)
    setPreviewOutdated(true)
    try {
      const result = await folderDiscoveryApi.preview(scan.id, rules, undefined, paths)
      setPreview(result)
      setDesiredExcludedPaths(result.excluded_paths ?? paths)
      setPreviewOutdated(false)
      const hasActionableRows = result.rows.some((row) => row.status !== 'EXCLUDED')
      setNotice({ kind: result.can_apply ? 'success' : 'error', text: result.can_apply ? '업무 생성 미리보기를 확인하세요.' : hasActionableRows ? '충돌 또는 미분류 항목을 해결해야 적용할 수 있습니다.' : '적용할 항목이 없습니다. 제외한 폴더를 복원한 뒤 다시 시도하세요.' })
    } catch (reason) {
      const prefix = preview ? '미리보기 갱신에 실패했습니다. 기존 미리보기는 최신 제외 설정과 달라 적용할 수 없습니다.' : '미리보기를 생성하지 못했습니다.'
      setNotice({ kind: 'error', text: `${prefix} ${errorText(reason, '')}`.trim() })
    } finally { setBusy('') }
  }
  const updateExclusion = async (relativePath: string, excluded: boolean) => {
    const current = desiredExcludedPaths
    const next = excluded ? current.filter((path) => path !== relativePath) : current.includes(relativePath) ? current : [...current, relativePath]
    setDesiredExcludedPaths(next)
    await runPreview(next)
  }
  const saveRules = async () => {
    if (rulesRevision == null) return
    setBusy('rules')
    try { const result = await folderDiscoveryApi.saveRules(selectedPath, rules, rulesRevision); setRules(result.rules); setRulesRevision(result.revision); invalidatePreview(); setNotice({ kind: 'success', text: `이 폴더의 규칙 v${result.revision}을 저장했습니다.` }) } catch (reason) { setNotice({ kind: 'error', text: errorText(reason, '규칙 저장에 실패했습니다. 최신 규칙을 다시 불러오세요.') }) } finally { setBusy('') }
  }
  const saveCatalog = async (next: Omit<FolderDiscoveryCatalog, 'revision'>, expectedRevision: number) => {
    setBusy('catalog')
    try { const result = await folderDiscoveryApi.saveCatalog(next, expectedRevision); setCatalog(result); invalidatePreview(); setNotice({ kind: 'success', text: '폴더 역할·해석 종류 카탈로그를 저장했습니다. 규칙 선택지를 갱신했습니다.' }) }
    catch (reason) { throw new Error(errorText(reason, '카탈로그 저장에 실패했습니다. 최신 카탈로그를 다시 불러오세요.')) }
    finally { setBusy('') }
  }
  const apply = async () => {
    if (!preview?.can_apply || previewOutdated || !scan || scan.status !== 'COMPLETE') return
    setBusy('apply')
    try {
      const result = await folderDiscoveryApi.apply(preview.id)
      setAppliedPreviewId(preview.id)
      setNotice({ kind: 'success', text: `완료: 프로젝트 ${result.created.projects}, 의뢰 ${result.created.requests}, 하중 경우 ${result.created.load_cases}개를 만들고 ${result.kept_count}개를 유지했습니다. 이번 적용에서 제외 ${result.excluded_count ?? preview.summary.excluded}개.` })
      onComplete?.(); window.dispatchEvent(new CustomEvent('folder-discovery-applied'))
    } catch (reason) { setNotice({ kind: 'error', text: errorText(reason, '업무 생성 적용에 실패했습니다. 미리보기를 새로 만드세요.') }) } finally { setBusy('') }
  }
  const nodes = useMemo(() => scan?.nodes ?? [], [scan])
  const applyDisabled = busy !== '' || previewOutdated || scan?.status !== 'COMPLETE' || !preview?.can_apply
  const catalogResolved = Boolean(catalog) && !catalogError
  const roleOptions = catalog?.roles.filter((item) => item.active) ?? DEFAULT_ROLE_OPTIONS
  const analysisTypes = catalog?.analysis_types.filter((item) => item.active) ?? DEFAULT_ANALYSIS_TYPES
  const folderTarget = (row: FolderDiscoveryPreview['rows'][number]): FolderDiscoveryTarget | undefined => row.status !== 'EXCLUDED' && (row.role_kind === 'RESULTS' || row.role_kind === 'INPUT') ? { relative_path: row.relative_path, project_id: row.project_id, request_id: row.request_id, load_case_id: row.load_case_id, role: row.role_kind } : undefined

  if (!browse) return <section className="folder-discovery" data-testid="folder-discovery-workspace"><p role="status">저장소를 확인하고 있습니다.</p>{notice ? <NoticeView notice={notice} /> : null}<button onClick={() => void loadBrowse()} disabled={busy !== ''}>다시 시도</button></section>

  if (!browse.configured) return <section className="folder-discovery" data-testid="folder-discovery-workspace"><header><span>FOLDER DISCOVERY</span><h2>폴더 조사·업무 생성</h2><p>기존 예제나 프로젝트를 고르지 않고, 실제 저장 폴더에서 업무 구조를 조사합니다.</p></header><div className="folder-discovery-card"><h3>저장 폴더 설정</h3><p>먼저 실제 폴더를 보관하는 SPDM root를 설정하세요.</p><div className="folder-discovery-root"><input aria-label="저장 폴더 경로" value={rootPath} onChange={(event) => setRootPath(event.target.value)} placeholder="예: D:\\Simulation" /><button className="primary-button" onClick={() => void saveRoot()} disabled={busy !== ''}><Save /> 저장</button></div></div>{notice ? <NoticeView notice={notice} /> : null}</section>

  return <section className="folder-discovery" data-testid="folder-discovery-workspace">
    <header><span>FOLDER DISCOVERY</span><h2>폴더 조사·업무 생성</h2><p>선택한 최상위 폴더의 전체 하위 트리를 조사하고, 확정 전 미리보기로 프로젝트·의뢰·하중 경우를 검토합니다.</p></header>
    {notice ? <NoticeView notice={notice} /> : null}
    {catalog ? <FolderDiscoveryCatalogEditor catalog={catalog} saving={busy === 'catalog'} onSave={saveCatalog} /> : catalogError ? <div className="folder-discovery-notice error" role="alert">{catalogError} 새로고침으로 다시 시도하세요.</div> : <div className="folder-discovery-notice" role="status">폴더 역할·해석 종류 카탈로그를 불러오는 중입니다.</div>}
    <div className="folder-discovery-card"><div className="folder-discovery-heading"><div><h3>1. 최상위 폴더 선택</h3><p>{browse?.root_path ?? '설정된 저장 root'}</p></div><button className="ghost-button" onClick={() => { void loadBrowse(selectedPath); void loadRules(selectedPath); void loadCatalog() }} disabled={busy !== ''}><RefreshCw /> 새로고침</button></div><div className="folder-discovery-browser"><strong>{selectedPath || '/'}</strong><button className="ghost-button" onClick={openPicker} disabled={busy !== ''}><FolderOpen />최상위 폴더 선택</button></div><div className="folder-discovery-actions"><button className="primary-button" onClick={() => void startScan()} disabled={busy !== ''}>{busy === 'scan' ? <LoaderCircle className="spin" /> : <Search />}{busy === 'scan' ? '조사 중…' : '전체 트리 조사'}</button></div></div>
    {scan ? <div className="folder-discovery-card"><div className="folder-discovery-heading"><div><h3>2. 조사 결과</h3><p>{scan.relative_path || '/'} · 폴더 {scan.folder_count} · 파일 {scan.file_count} · {scan.status}</p></div>{busy === 'scan' ? <LoaderCircle className="spin" /> : null}</div>{scan.issues.length ? <ul className="folder-discovery-issues">{scan.issues.map((issue, index) => <li key={index}><IssueDetail issue={issue} /></li>)}</ul> : null}<div className="folder-discovery-tree">{nodes.map((node) => <div key={node.relative_path} style={{ paddingInlineStart: `${Math.max(0, node.depth) * 22}px` }}><FolderOpen /><span>{node.name}</span><small>파일 {node.file_count}{node.extensions.length ? ` · ${node.extensions.join(', ')}` : ''}</small></div>)}</div></div> : null}
    {scan ? <div className="folder-discovery-card"><div className="folder-discovery-heading"><div><h3>3. 폴더 역할 규칙</h3><p>포함할 단어가 폴더 이름 어디에든 있으면 규칙을 적용합니다. 같은 깊이에서 같은 역할을 여러 폴더에 적용해도 경로로 구분합니다. 구분자가 비어 있으면 폴더 이름 전체를 이름으로 쓰고 코드는 비워 둡니다. 구분자를 지정하면 폴더 이름에 실제로 포함된 경우에만 규칙을 적용합니다.</p></div><button className="ghost-button" onClick={() => void saveRules()} disabled={busy !== '' || !rulesLoaded || rulesRevision == null || !catalogResolved}><Save /> 규칙 저장</button></div><fieldset className="folder-discovery-rules" disabled={busy !== '' || !rulesLoaded || !catalogResolved}>{rules.map((rule, index) => <RuleEditor key={`${rule.role}-${index}`} rule={rule} roleOptions={roleOptions} analysisTypes={analysisTypes} catalog={catalog} onChange={(patch) => updateRule(index, patch)} onDelete={() => removeRule(index)} />)}</fieldset><div className="folder-discovery-actions"><button className="ghost-button" onClick={addRule} disabled={busy !== '' || !rulesLoaded || !catalogResolved || rules.length >= 30}>규칙 추가</button><button className="primary-button" onClick={() => void runPreview()} disabled={busy !== '' || !rulesLoaded || !catalogResolved || scan.status !== 'COMPLETE'}><WandSparkles />{busy === 'preview' ? '미리보기 생성 중…' : '업무 생성 미리보기'}</button></div></div> : null}
    {preview ? <div className="folder-discovery-card"><div className="folder-discovery-heading"><div><h3>4. 생성 미리보기</h3><p>프로젝트 {preview.summary.projects} · 의뢰 {preview.summary.requests} · 하중 경우 {preview.summary.load_cases} · 충돌 {preview.summary.conflicts} · 제외 {preview.summary.excluded ?? preview.rows.filter((row) => row.status === 'EXCLUDED').length} · 미분류 {preview.unmatched_count}</p><small>이미 생성된 업무와 결과는 삭제되지 않습니다.</small></div></div><div className="folder-discovery-preview"><table><thead><tr><th>경로</th><th>역할</th><th>코드</th><th>이름</th><th>결과</th><th>상세</th></tr></thead><tbody>{preview.rows.map((row, index) => { const isExcluded = row.status === 'EXCLUDED'; const isExplicitExclusion = isExcluded && row.excluded_by === row.relative_path; const hasExcludedBy = row.excluded_by !== null && row.excluded_by !== undefined; const detail = isExcluded && hasExcludedBy && row.excluded_by !== row.relative_path ? `${row.message ?? '상위 폴더가 제외되었습니다.'} · 상위 제외: ${row.excluded_by || '(root)'}` : row.message ?? (isExcluded ? '이 폴더가 제외되었습니다.' : ''); return <tr key={`${row.relative_path}/${row.role}/${index}`} className={row.status.toLowerCase()}><td>{row.relative_path}</td><td>{row.role_label ?? roleName(row.role, catalog)}{row.role_kind && row.role_kind !== row.role ? <small> · {row.role_kind}</small> : null}</td><td>{row.code}</td><td>{row.name}</td><td>{row.status}</td><td>{detail}{isExcluded ? (isExplicitExclusion ? <button type="button" className="ghost-button folder-discovery-row-action" onClick={() => void updateExclusion(row.relative_path, true)} disabled={busy !== ''}>제외 취소</button> : null) : <button type="button" className="ghost-button folder-discovery-row-action" onClick={() => void updateExclusion(row.relative_path, false)} disabled={busy !== ''}>이번 적용에서 제외</button>}{onOpenFolder && !previewOutdated && folderTarget(row) && row.status !== 'CONFLICT' && (row.status === 'KEEP' || appliedPreviewId === preview.id) ? <button type="button" className="ghost-button folder-discovery-row-action" onClick={() => onOpenFolder(folderTarget(row))} disabled={busy !== ''}>이 폴더 연결</button> : null}</td></tr> })}</tbody></table></div><div className="folder-discovery-actions"><button className="primary-button" onClick={() => void apply()} disabled={applyDisabled}><Play />{busy === 'apply' ? '생성 중…' : '검토한 업무 생성 적용'}</button>{preview && notice?.kind === 'success' && notice.text.startsWith('완료:') ? <button className="ghost-button" onClick={() => { const row = preview.rows.find((item) => (item.role_kind === 'RESULTS' || item.role_kind === 'INPUT') && item.status !== 'EXCLUDED'); onOpenFolder?.(row ? folderTarget(row) : undefined) }} disabled={busy !== '' || previewOutdated}>결과파일 연결하기</button> : null}{scan?.status !== 'COMPLETE' ? <small>조사가 완료되어야 적용할 수 있습니다.</small> : null}</div></div> : null}
    {pickerOpen ? <dialog className="folder-discovery-picker" ref={(node) => { if (node && !node.open) node.showModal() }} aria-label="최상위 폴더 선택" onCancel={() => setPickerOpen(false)}><div className="folder-discovery-heading"><div><h3>조사할 최상위 폴더 선택</h3><p>선택한 폴더 아래의 모든 하위 트리를 조사합니다.</p></div><button className="ghost-button" onClick={() => setPickerOpen(false)}>닫기</button></div><div className="folder-discovery-browser"><button onClick={() => { setPickerPath(''); void loadBrowse('') }} disabled={busy !== ''}>root</button>{browse?.relative_path ? <button onClick={() => { const parent = browse.relative_path.split('/').slice(0, -1).join('/'); setPickerPath(parent); void loadBrowse(parent) }} disabled={busy !== ''}>상위 폴더</button> : null}<strong>{browse?.relative_path || '/'}</strong></div><div className="folder-discovery-entries">{browse?.entries.filter((entry) => entry.is_directory).map((entry) => <button key={entry.relative_path} className={pickerPath === entry.relative_path ? 'selected' : ''} onClick={() => { setPickerPath(entry.relative_path); void loadBrowse(entry.relative_path) }} disabled={busy !== ''}><FolderOpen />{entry.name}</button>)}</div><div className="folder-discovery-actions"><button className="ghost-button" disabled={busy !== ''} onClick={() => setPickerPath(browse?.relative_path ?? '')}>이 위치 선택</button><button className="primary-button" disabled={busy !== '' || pickerPath !== browse?.relative_path} onClick={() => { selectFolder(pickerPath); setPickerOpen(false); void loadRules(pickerPath) }}>선택 완료</button></div></dialog> : null}
  </section>
}

function RuleEditor({ rule, roleOptions, analysisTypes, catalog, onChange, onDelete }: { rule: FolderDiscoveryRule; roleOptions: FolderRoleOption[]; analysisTypes: { key: string; label: string }[]; catalog: FolderDiscoveryCatalog | null; onChange: (patch: Partial<FolderDiscoveryRule>) => void; onDelete: () => void }) {
  const selectedRole = catalog?.roles.find((item) => item.key === rule.role)
  const legacyRole = !selectedRole && !roleOptions.some((item) => item.key === rule.role) ? { key: rule.role, label: rule.role, kind: (rule.role === 'LOAD_CASE' ? 'LOAD_CASE' : 'PROJECT') as FolderRoleKind, active: false } : null
  const options = selectedRole && !selectedRole.active ? [...roleOptions, selectedRole] : legacyRole ? [...roleOptions, legacyRole] : roleOptions
  const noDelimiter = !(rule.delimiter ?? '')
  const analysis = analysisTypes.map((item) => <option key={item.key} value={item.key}>{item.label} · {item.key}</option>)
  return <div className="folder-discovery-rule">
    <label>깊이<input aria-label={`${roleName(rule.role, catalog)} 깊이`} type="number" min="0" max="64" value={rule.depth} onChange={(event) => onChange({ depth: Math.max(0, Math.min(64, Number(event.target.value) || 0)) })} /></label>
    <label>역할<select aria-label={`${roleName(rule.role, catalog)} 역할`} value={rule.role} onChange={(event) => onChange({ role: event.target.value as FolderRole })}>{options.map((item) => <option key={item.key} value={item.key}>{item.label} · {item.key}{item.active ? '' : ' (비활성)'}</option>)}</select></label>
    <label>포함할 단어<input aria-label={`${roleName(rule.role, catalog)} 포함할 단어`} value={rule.keyword ?? rule.prefix ?? ''} placeholder="예: project (어디에든 포함)" onChange={(event) => onChange({ keyword: event.target.value, prefix: undefined })} /></label>
    <label>구분자<input aria-label={`${roleName(rule.role, catalog)} 구분자`} value={rule.delimiter ?? ''} maxLength={8} placeholder="없음" onChange={(event) => onChange({ delimiter: event.target.value })} /><small>지정 시 폴더 이름에 포함되어야 함 · 비우면 전체 이름·빈 코드</small></label>
    <label>코드 토큰<input aria-label={`${roleName(rule.role, catalog)} 코드 토큰`} disabled={noDelimiter} type="number" min="0" max="100" value={rule.code_token ?? 1} onChange={(event) => onChange({ code_token: Math.max(0, Math.min(100, Number(event.target.value) || 0)) })} /></label>
    <label>이름 시작 토큰<input aria-label={`${roleName(rule.role, catalog)} 이름 토큰`} disabled={noDelimiter} type="number" min="0" max="100" value={rule.name_from_token ?? 2} onChange={(event) => onChange({ name_from_token: Math.max(0, Math.min(100, Number(event.target.value) || 0)) })} /></label>
    {(selectedRole?.kind ?? legacyRole?.kind) === 'LOAD_CASE' ? <label>해석 종류<select aria-label="하중 경우 해석 종류" value={rule.analysis_type ?? ''} onChange={(event) => onChange({ analysis_type: event.target.value })}><option value="">선택하세요</option>{analysis}</select></label> : <span />}
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
