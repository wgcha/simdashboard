import { FolderOpen, LoaderCircle, Play, RefreshCw, Save, Search, WandSparkles } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { folderDiscoveryApi, type FolderDiscoveryBrowse, type FolderDiscoveryConnection, type FolderDiscoveryHistory, type FolderDiscoveryPreview, type FolderDiscoveryRule, type FolderDiscoverySavedRule, type FolderDiscoveryScan, type FolderResultConfigPreview, type FolderResultRefresh, type FolderRole } from '../../shared/api/folderDiscovery'
import type { FolderDiscoveryCatalog, FolderRoleKind, FolderRoleOption } from '../../shared/api/folderDiscovery'
import { FolderDiscoveryCatalogEditor } from './FolderDiscoveryCatalogEditor'
import { saveStorageConfig } from '../../shared/api/storage'
import { semanticMappingApi, type SemanticCatalog } from '../../shared/api/semanticMapping'
import { SemanticWidgetGrid } from '../../shared/components/semanticResults'
import type { PreviewWidget } from '../../shared/api/semanticMapping'
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

export function FolderDiscoveryWorkspace({ onComplete, onOpenFolder, canImportResults = true, canImportForProject }: { onComplete?: () => void; onOpenFolder?: (target?: FolderDiscoveryTarget) => void; canImportResults?: boolean; canImportForProject?: (projectId: string) => boolean }) {
  const [browse, setBrowse] = useState<FolderDiscoveryBrowse | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerPath, setPickerPath] = useState('')
  const [selectedPath, setSelectedPath] = useState('')
  const [rootPath, setRootPath] = useState('')
  const [scan, setScan] = useState<FolderDiscoveryScan | null>(null)
  const [rules, setRules] = useState<FolderDiscoveryRule[]>(DEFAULT_RULES)
  const [catalog, setCatalog] = useState<FolderDiscoveryCatalog | null>(null)
  const [semanticCatalog, setSemanticCatalog] = useState<SemanticCatalog | null>(null)
  const [catalogError, setCatalogError] = useState('')
  const [rulesRevision, setRulesRevision] = useState<number | null>(null)
  const [rulesLoaded, setRulesLoaded] = useState(false)
  const [preview, setPreview] = useState<FolderDiscoveryPreview | null>(null)
  const [desiredExcludedPaths, setDesiredExcludedPaths] = useState<string[]>([])
  const [previewOutdated, setPreviewOutdated] = useState(false)
  const [appliedPreviewId, setAppliedPreviewId] = useState('')
  const [savedRules, setSavedRules] = useState<FolderDiscoverySavedRule[]>([])
  const [history, setHistory] = useState<FolderDiscoveryHistory[]>([])
  const [connections, setConnections] = useState<FolderDiscoveryConnection[]>([])
  const [connectionRefresh, setConnectionRefresh] = useState<Record<string, { status: 'loading' | 'done' | 'error'; text: string; result?: FolderResultRefresh }>>({})
  const [connectionWidgets, setConnectionWidgets] = useState<Record<string, PreviewWidget[]>>({})
  const [bulkConnectionIds, setBulkConnectionIds] = useState<string[]>([])
  const [bulkRecipeIds, setBulkRecipeIds] = useState<string[]>([])
  const [bulkTemplateId, setBulkTemplateId] = useState('')
  const [bulkPreview, setBulkPreview] = useState<{ response: FolderResultConfigPreview; items: Array<{ registry_id: string; expected_binding_revision?: number; result_config: { recipe_ids: string[]; template_id?: string | null } }> } | null>(null)
  const [bulkPreviewMessage, setBulkPreviewMessage] = useState('')
  const [bulkBusy, setBulkBusy] = useState(false)
  const bulkPreviewGeneration = useRef(0)
  const [savedWorkError, setSavedWorkError] = useState('')
  const [workPages, setWorkPages] = useState({ rules: 0, history: 0, connections: 0 })
  const [workTotals, setWorkTotals] = useState({ rules: 0, history: 0, connections: 0 })
  const [workLoading, setWorkLoading] = useState(false)
  const workGeneration = useRef(0)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [busy, setBusy] = useState<'browse' | 'root' | 'scan' | 'preview' | 'apply' | 'rules' | 'catalog' | ''>('')
  const requestGeneration = useRef(0)
  const rulesGeneration = useRef(0)
  const skipNextRuleLoad = useRef(false)
  const connectionRefreshGeneration = useRef<Record<string, number>>({})

  const loadCatalog = useCallback(async () => {
    setCatalogError('')
    try { setCatalog(await folderDiscoveryApi.catalog()) } catch (reason) { setCatalog(null); setCatalogError(errorText(reason, '폴더 역할·해석 종류 카탈로그를 불러오지 못했습니다.')) }
  }, [])
  const loadSemanticCatalog = useCallback(async () => {
    try { setSemanticCatalog(await semanticMappingApi.catalog()) } catch { setSemanticCatalog(null) }
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
      if (generation !== rulesGeneration.current) return false
      setRules(result.rules.length ? result.rules.map((rule) => ({ ...rule, keyword: rule.keyword ?? rule.prefix ?? '', prefix: undefined })) : DEFAULT_RULES); setRulesRevision(result.revision); setRulesLoaded(true)
      return true
    } catch { if (generation === rulesGeneration.current) { setRules(DEFAULT_RULES); setRulesRevision(null); setRulesLoaded(false); setNotice({ kind: 'error', text: '규칙을 불러오지 못했습니다. 폴더를 다시 선택하거나 새로고침하세요.' }) } return false }
  }, [])
  const loadSavedWork = useCallback(async (offsets = { rules: 0, history: 0, connections: 0 }) => {
    const generation = ++workGeneration.current
    setSavedWorkError(''); setWorkLoading(true)
    try {
      const [stored, applied, linked] = await Promise.all([folderDiscoveryApi.savedRules(offsets.rules), folderDiscoveryApi.history(offsets.history), folderDiscoveryApi.connections(offsets.connections)])
      if (generation !== workGeneration.current) return
      setSavedRules(stored.items); setHistory(applied.items); setConnections(linked.items)
      setWorkPages(offsets); setWorkTotals({ rules: stored.total, history: applied.total, connections: linked.total })
    } catch (reason) { if (generation === workGeneration.current) setSavedWorkError(errorText(reason, '저장된 규칙과 업무 생성 기록을 불러오지 못했습니다.')) }
    finally { if (generation === workGeneration.current) setWorkLoading(false) }
  }, [])
  useEffect(() => { void loadBrowse(); void loadCatalog(); void loadSemanticCatalog() }, [loadBrowse, loadCatalog, loadSemanticCatalog])
  useEffect(() => () => { connectionRefreshGeneration.current = {} }, [])
  useEffect(() => { bulkPreviewGeneration.current += 1; setBulkBusy(false); setBulkPreview(null); setBulkPreviewMessage('') }, [bulkConnectionIds, bulkRecipeIds, bulkTemplateId, connections])
  useEffect(() => { if (browse?.configured) { void loadSavedWork(); if (skipNextRuleLoad.current) { skipNextRuleLoad.current = false } else void loadRules(selectedPath) } }, [loadRules, loadSavedWork, selectedPath, browse?.configured])

  const selectFolder = (path: string) => { setSelectedPath(path); setScan(null); invalidatePreview() }
  const loadSavedRulesAt = async (path: string) => {
    skipNextRuleLoad.current = path !== selectedPath; selectFolder(path)
    if (await loadRules(path)) setNotice({ kind: 'info', text: `${path || '/'}에 저장한 규칙을 불러왔습니다. 전체 트리 조사 후 미리보기를 만드세요.` })
  }
  const loadAppliedRules = async (previewId: string) => {
    const generation = ++rulesGeneration.current
    setRulesLoaded(false); setRulesRevision(null); setScan(null); invalidatePreview()
    try {
      const result = await folderDiscoveryApi.historyRules(previewId)
      if (generation !== rulesGeneration.current) return
      skipNextRuleLoad.current = result.relative_path !== selectedPath; setSelectedPath(result.relative_path)
      setRules(result.rules.map((rule) => ({ ...rule, keyword: rule.keyword ?? rule.prefix ?? '', prefix: undefined })))
      setRulesRevision(result.current_rules_revision); setRulesLoaded(true)
      setNotice({ kind: 'info', text: `적용 당시 규칙 v${result.rules_revision}을 불러왔습니다. 현재 저장 개정 v${result.current_rules_revision}으로 다시 저장할 수 있습니다.` })
    } catch (reason) { if (generation === rulesGeneration.current) setNotice({ kind: 'error', text: errorText(reason, '적용 당시 규칙을 불러오지 못했습니다.') }) }
  }
  const openPicker = () => { setPickerPath(selectedPath); setPickerOpen(true); void loadBrowse(selectedPath) }
  const saveRoot = async () => {
    if (!rootPath.trim()) return
    setBusy('root')
    try { await saveStorageConfig(rootPath.trim()); setScan(null); setPreview(null); setSelectedPath(''); setNotice({ kind: 'success', text: '저장 폴더를 설정했습니다. 조사할 최상위 폴더를 선택하세요.' }); await loadBrowse(''); await loadRules(''); await loadSavedWork() } catch (reason) { setNotice({ kind: 'error', text: errorText(reason, '저장 폴더를 설정하지 못했습니다.') }) } finally { setBusy('') }
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
    try { const result = await folderDiscoveryApi.saveRules(selectedPath, rules, rulesRevision); setRules(result.rules); setRulesRevision(result.revision); invalidatePreview(); void loadSavedWork(); setNotice({ kind: 'success', text: `이 폴더의 규칙 v${result.revision}을 저장했습니다.` }) } catch (reason) { setNotice({ kind: 'error', text: errorText(reason, '규칙 저장에 실패했습니다. 최신 규칙을 다시 불러오세요.') }) } finally { setBusy('') }
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
      onComplete?.(); window.dispatchEvent(new CustomEvent('folder-discovery-applied')); void loadSavedWork()
    } catch (reason) { setNotice({ kind: 'error', text: errorText(reason, '업무 생성 적용에 실패했습니다. 미리보기를 새로 만드세요.') }) } finally { setBusy('') }
  }
  const refreshConnectionResults = async (connection: FolderDiscoveryConnection) => {
    const loadCaseId = connection.load_case_id
    if (!loadCaseId || connection.role_kind !== 'RESULTS' || !connection.binding?.id || ['CONFIG_REQUIRED', 'ADVANCED_MANAGEMENT_REQUIRED'].includes(connection.binding.status ?? '')) return
    const key = connection.binding.id
    const generation = (connectionRefreshGeneration.current[key] ?? 0) + 1
    connectionRefreshGeneration.current[key] = generation
    const current = () => connectionRefreshGeneration.current[key] === generation
    setConnectionWidgets((items) => ({ ...items, [key]: [] }))
    setConnectionRefresh((items) => ({ ...items, [key]: { status: 'loading', text: '파일 확인 중…' } }))
    try { const result = await folderDiscoveryApi.refreshResults(loadCaseId); if (!current()) return; const successful = result.results.filter((item) => ['IMPORTED', 'SKIPPED', 'SUCCESS', 'READY'].includes(item.status)).length; if (result.display_run_id) { const widgets = await semanticMappingApi.results({ load_case_id: loadCaseId, run_id: result.display_run_id }); if (current()) setConnectionWidgets((items) => ({ ...items, [key]: widgets.widgets })) } else setConnectionWidgets((items) => ({ ...items, [key]: [] })); if (current()) setConnectionRefresh((items) => ({ ...items, [key]: { status: 'done', text: result.display_run_id ? `${successful}개 파일 · Run ${result.display_run_id}` : successful ? `${successful}개 파일 처리 · 표시 가능한 Run 없음` : '처리된 결과 파일 없음', result } })) } catch (reason) { if (current()) { setConnectionWidgets((items) => ({ ...items, [key]: [] })); setConnectionRefresh((items) => ({ ...items, [key]: { status: 'error', text: errorText(reason, '결과 파일을 조회하지 못했습니다.') } })) } }
  }
  const bulkItems = () => connections.filter((item) => bulkConnectionIds.includes(item.id)).map((item) => ({ registry_id: item.id, ...(item.binding?.revision != null ? { expected_binding_revision: item.binding.revision } : {}), result_config: { recipe_ids: bulkRecipeIds, template_id: bulkTemplateId || null } }))
  const bulkPermissionAllowed = () => bulkConnectionIds.length > 0 && connections.filter((item) => bulkConnectionIds.includes(item.id)).every((item) => canImportForProject ? canImportForProject(item.project_id ?? '') : canImportResults)
  const previewBulkResultConfig = async () => { const items = bulkItems(); if (!items.length || !bulkRecipeIds.length) { setNotice({ kind: 'error', text: '일괄 적용할 결과 폴더와 활성 레시피를 하나 이상 선택하세요.' }); return }; const generation = ++bulkPreviewGeneration.current; setBulkBusy(true); try { const response = await folderDiscoveryApi.resultConfigPreview(items); if (generation !== bulkPreviewGeneration.current) return; setBulkPreview(response.can_apply === true ? { response, items } : null); setBulkPreviewMessage(response.items.map((item) => `${item.registry_id}: ${item.status}${item.current_revision != null ? ` · 현재 개정 ${item.current_revision}` : ''}`).join(' · ')); setNotice({ kind: response.can_apply === true ? 'info' : 'error', text: response.can_apply === true ? '결과 설정 영향 미리보기를 확인한 뒤 일괄 적용하세요.' : '충돌 또는 현재 개정 불일치가 있어 일괄 적용할 수 없습니다.' }) } catch (reason) { if (generation === bulkPreviewGeneration.current) setNotice({ kind: 'error', text: errorText(reason, '결과 설정 미리보기를 만들지 못했습니다.') }) } finally { if (generation === bulkPreviewGeneration.current) setBulkBusy(false) } }
  const applyBulkResultConfig = async () => { if (!bulkPreview || bulkPreview.response.can_apply !== true) return; setBulkBusy(true); try { await folderDiscoveryApi.resultConfigApply(bulkPreview.items); setBulkPreview(null); setNotice({ kind: 'success', text: `${bulkPreview.items.length}개 결과 폴더의 읽기 설정을 업데이트했습니다. 선택하지 않은 연결은 유지됩니다.` }); await loadSavedWork() } catch (reason) { setNotice({ kind: 'error', text: errorText(reason, '결과 설정 일괄 적용에 실패했습니다. 현재 개정을 확인한 뒤 다시 시도하세요.') }) } finally { setBulkBusy(false) } }
  const workPager = (key: keyof typeof workPages, label: string) => <div className="folder-discovery-actions" aria-label={`${label} 페이지`}><button className="ghost-button" disabled={workLoading || workPages[key] === 0} onClick={() => void loadSavedWork({ ...workPages, [key]: Math.max(0, workPages[key] - 100) })}>이전</button><span>{workTotals[key]}개 · {workPages[key] + 1}–{Math.min(workPages[key] + 100, workTotals[key])}</span><button className="ghost-button" disabled={workLoading || workPages[key] + 100 >= workTotals[key]} onClick={() => void loadSavedWork({ ...workPages, [key]: workPages[key] + 100 })}>다음</button></div>
  const nodes = useMemo(() => scan?.nodes ?? [], [scan])
  const applyDisabled = busy !== '' || previewOutdated || scan?.status !== 'COMPLETE' || !preview?.can_apply
  const catalogResolved = Boolean(catalog) && !catalogError
  const roleOptions = catalog?.roles.filter((item) => item.active) ?? DEFAULT_ROLE_OPTIONS
  const analysisTypes = catalog?.analysis_types.filter((item) => item.active) ?? DEFAULT_ANALYSIS_TYPES
  const folderTarget = (row: FolderDiscoveryPreview['rows'][number]): FolderDiscoveryTarget | undefined => row.status !== 'EXCLUDED' && (row.role_kind === 'RESULTS' || row.role_kind === 'INPUT') ? { relative_path: row.relative_path, project_id: row.project_id, request_id: row.request_id, load_case_id: row.load_case_id, role: row.role_kind } : undefined

  if (!browse) return <section className="folder-discovery" data-ui-density="v1" data-testid="folder-discovery-workspace"><p role="status">저장소를 확인하고 있습니다.</p>{notice ? <NoticeView notice={notice} /> : null}<button onClick={() => void loadBrowse()} disabled={busy !== ''}>다시 시도</button></section>

  if (!browse.configured) return <section className="folder-discovery" data-ui-density="v1" data-testid="folder-discovery-workspace"><header><span>FOLDER DISCOVERY</span><h2>폴더 조사·업무 생성</h2><p>기존 예제나 프로젝트를 고르지 않고, 실제 저장 폴더에서 업무 구조를 조사합니다.</p></header><div className="folder-discovery-card"><h3>저장 폴더 설정</h3><p>먼저 실제 폴더를 보관하는 SPDM root를 설정하세요.</p><div className="folder-discovery-root"><input aria-label="저장 폴더 경로" value={rootPath} onChange={(event) => setRootPath(event.target.value)} placeholder="예: D:\\Simulation" /><button className="primary-button" onClick={() => void saveRoot()} disabled={busy !== ''}><Save /> 저장</button></div></div>{notice ? <NoticeView notice={notice} /> : null}</section>

  return <section className="folder-discovery" data-ui-density="v1" data-testid="folder-discovery-workspace">
    {Object.entries(connectionWidgets).map(([key, widgets]) => widgets.length ? <section key={key} data-testid="result-widget-display"><h3>연결된 결과 위젯 · {connectionRefresh[key]?.result?.display_run_id ?? 'Run'}</h3><SemanticWidgetGrid widgets={widgets} /></section> : null)}
    {Object.entries(connectionRefresh).map(([key, state]) => state.result?.results?.length ? <section key={`${key}-files`} className="folder-discovery-card folder-result-file-status" data-testid="folder-result-file-status"><h3>결과 파일 처리 상태</h3><table><thead><tr><th>파일</th><th>상태</th><th>Run</th><th>상세·검토</th></tr></thead><tbody>{state.result!.results.map((file, index) => { const sourcePath = file.source_relative_path ?? file.relative_path ?? file.filename ?? '알 수 없는 파일'; const reviewable = Boolean(file.run_id && state.result!.project_id && state.result!.request_id && state.result!.load_case_id && ['IMPORTED', 'SKIPPED'].includes(file.status) && file.review_available === true); const reviewHref = reviewable ? `/workspace/requests?${new URLSearchParams({ project: state.result!.project_id!, request: state.result!.request_id!, loadCase: state.result!.load_case_id!, run: file.run_id!, view: 'custom' }).toString()}` : ''; const candidateDetail = file.candidate_errors?.length ? `후보 오류: ${file.candidate_errors.map((candidate) => typeof candidate === 'string' ? candidate : JSON.stringify(candidate)).join(' · ')}` : ''; const detail = file.message || candidateDetail || (file.detail ? JSON.stringify(file.detail) : file.status === 'IMPORTED' || file.status === 'SKIPPED' ? '' : '이 파일은 결과 Run에 포함되지 않았습니다.'); return <tr key={`${sourcePath}-${index}`}><td>{sourcePath}</td><td>{file.status}</td><td>{file.run_id ? <span>{file.run_no != null ? `#${file.run_no} · ` : ''}{file.run_id}</span> : 'Run 없음'}</td><td>{detail || '상세 정보 없음'}{reviewable ? <Link className="secondary-button" to={reviewHref}>결과 검토</Link> : null}</td></tr> })}</tbody></table></section> : null)}
    {bulkPreviewMessage ? <div className="folder-discovery-notice" data-testid="folder-result-bulk-preview" role={bulkPreview?.response.can_apply ? 'status' : 'alert'}>{bulkPreviewMessage}</div> : null}
    {!canImportResults && connections.some((item) => item.role_kind === 'RESULTS') ? <div className="folder-discovery-notice" role="status">결과 파일 처리·연결 설정은 결과 등록 권한이 필요합니다. 저장된 결과 조회는 결과 검토 화면에서 사용할 수 있습니다.</div> : null}
    {appliedPreviewId && preview && onOpenFolder ? <button type="button" className="ghost-button" data-testid="folder-discovery-advanced-link" onClick={() => { const row = preview.rows.find((item) => (item.role_kind === 'RESULTS' || item.role_kind === 'INPUT') && item.status !== 'EXCLUDED'); onOpenFolder(row ? folderTarget(row) : undefined) }}>이 폴더 연결</button> : null}
    {connections.some((item) => item.role_kind === 'RESULTS') && semanticCatalog ? <div className="folder-discovery-card" data-testid="folder-result-bulk-config"><div className="folder-discovery-heading"><div><h3>기존 결과 연결 일괄 설정</h3><p>이미 조사·생성된 결과 폴더를 다시 고르지 않고 레시피와 템플릿을 적용합니다. 적용 전 영향 미리보기를 확인하세요.</p></div></div><fieldset><legend>대상 결과 폴더</legend>{connections.filter((item) => item.role_kind === 'RESULTS').map((item) => { const projectAllowed = canImportForProject ? canImportForProject(item.project_id ?? '') : canImportResults; return <label key={item.id}><input type="checkbox" aria-label={`${item.relative_path} 일괄 설정 대상`} checked={bulkConnectionIds.includes(item.id)} disabled={!projectAllowed || bulkBusy} onChange={(event) => setBulkConnectionIds((current) => event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id))} />{item.relative_path} · {item.name}{!projectAllowed ? ' · 권한 없음' : ''}</label> })}</fieldset><fieldset><legend>적용 레시피</legend>{semanticCatalog.recipes.filter((item) => item.active_version).map((recipe) => <label key={recipe.id}><input type="checkbox" aria-label={`일괄 결과 레시피 ${recipe.name ?? recipe.id}`} checked={bulkRecipeIds.includes(recipe.id)} disabled={bulkBusy} onChange={(event) => setBulkRecipeIds((current) => event.target.checked ? [...current, recipe.id] : current.filter((id) => id !== recipe.id))} />{recipe.name ?? recipe.id} · v{recipe.active_version}</label>)}</fieldset><label>표시 템플릿<select aria-label="일괄 결과 템플릿" disabled={!bulkRecipeIds.length || bulkBusy} value={bulkTemplateId} onChange={(event) => setBulkTemplateId(event.target.value)}><option value="">레시피 저장 템플릿</option>{semanticCatalog.templates.filter((item) => item.active_version).map((template) => <option key={template.id} value={template.id}>{template.name ?? template.id} · v{template.active_version}</option>)}</select></label><div className="folder-discovery-actions"><button type="button" className="ghost-button" onClick={() => void previewBulkResultConfig()} disabled={bulkBusy || !bulkPermissionAllowed()}>설정 영향 미리보기</button><button type="button" className="primary-button" onClick={() => void applyBulkResultConfig()} disabled={bulkBusy || !bulkPermissionAllowed() || bulkPreview?.response.can_apply !== true}>일괄 적용</button>{bulkPreview ? <small role="status">미리보기가 준비되었습니다. 정책을 확인한 뒤 적용하세요.</small> : null}</div></div> : null}
    {connections.some((item) => item.role_kind === 'RESULTS') ? <div className="folder-discovery-card" data-testid="folder-result-connections"><div className="folder-discovery-heading"><div><h3>저장된 결과 연결</h3><p>확정된 하중 경우의 결과 폴더를 경로 재입력 없이 확인합니다.</p></div></div><ul>{connections.filter((item) => item.role_kind === 'RESULTS').map((item) => { const key = item.binding?.id ?? item.id; const status = connectionRefresh[key]; const requiresConfig = ['CONFIG_REQUIRED', 'ADVANCED_MANAGEMENT_REQUIRED'].includes(item.binding?.status ?? ''); const projectAllowed = canImportForProject ? canImportForProject(item.project_id ?? '') : canImportResults; const canQuery = projectAllowed && Boolean(item.load_case_id && item.binding?.id) && !requiresConfig; return <li data-testid="folder-connection-row" data-path={item.relative_path} key={item.id}><strong>{item.relative_path}</strong><small>{item.name}{item.binding?.status ? ` · ${item.binding.status}` : ''}</small><button type="button" className="ghost-button" aria-label={`${item.relative_path} 결과 조회`} onClick={() => void refreshConnectionResults(item)} disabled={!canQuery || status?.status === 'loading'}>{!projectAllowed ? '결과 등록 권한 필요' : !canQuery ? '결과 설정 필요' : status?.status === 'loading' ? '조회 중…' : '결과 조회'}</button>{status ? <span role={status.status === 'error' ? 'alert' : 'status'}>{status.text}</span> : !canQuery ? <span role="status">{!projectAllowed ? '이 프로젝트의 결과 등록 권한이 없습니다.' : '하중 경우 연결과 결과 설정을 확인하세요.'}</span> : null}</li> })}</ul></div> : null}
    <header><span>FOLDER DISCOVERY</span><h2>폴더 조사·업무 생성</h2><p>선택한 최상위 폴더의 전체 하위 트리를 조사하고, 확정 전 미리보기로 프로젝트·의뢰·하중 경우를 검토합니다.</p></header>
    {notice ? <NoticeView notice={notice} /> : null}
    {catalog ? <FolderDiscoveryCatalogEditor catalog={catalog} saving={busy === 'catalog'} resultCatalog={semanticCatalog} onSave={saveCatalog} /> : catalogError ? <div className="folder-discovery-notice error" role="alert">{catalogError} 새로고침으로 다시 시도하세요.</div> : <div className="folder-discovery-notice" role="status">폴더 역할·해석 종류 카탈로그를 불러오는 중입니다.</div>}
    <div className="folder-discovery-card"><div className="folder-discovery-heading"><div><h3>1. 최상위 폴더 선택</h3><p>{browse?.root_path ?? '설정된 저장 root'}</p></div><button className="ghost-button" onClick={() => { void loadBrowse(selectedPath); void loadRules(selectedPath); void loadCatalog(); void loadSavedWork() }} disabled={busy !== ''}><RefreshCw /> 새로고침</button></div><div className="folder-discovery-browser"><strong>{selectedPath || '/'}</strong><button className="ghost-button" onClick={openPicker} disabled={busy !== ''}><FolderOpen />최상위 폴더 선택</button></div><div className="folder-discovery-actions"><button className="primary-button" onClick={() => void startScan()} disabled={busy !== ''}>{busy === 'scan' ? <LoaderCircle className="spin" /> : <Search />}{busy === 'scan' ? '조사 중…' : '전체 트리 조사'}</button></div></div>
    <div className="folder-discovery-card folder-discovery-saved"><div className="folder-discovery-heading"><div><h3>저장된 규칙·업무 연결</h3><p>규칙 저장 위치와 실제로 적용된 업무 생성 기록을 다시 불러올 수 있습니다.</p></div></div>{savedWorkError ? <div className="folder-discovery-notice error" role="alert">{savedWorkError}</div> : null}<div className="folder-discovery-saved-grid"><div><h4>저장된 규칙</h4>{savedRules.length ? <ul>{savedRules.map((item) => <li key={item.relative_path}><button type="button" className="ghost-button" onClick={() => void loadSavedRulesAt(item.relative_path)} disabled={busy !== ''}>{item.relative_path || '/'} · v{item.revision}</button></li>)}</ul> : <p>저장된 규칙이 없습니다.</p>}{workPager('rules', '저장 규칙')}</div><div><h4>적용 기록</h4>{history.length ? <ul>{history.map((item) => <li key={item.id}><button type="button" className="ghost-button" onClick={() => void loadAppliedRules(item.id)} disabled={busy !== ''}>{item.relative_path || '/'} · 프로젝트 {item.outcome.created.projects}, 의뢰 {item.outcome.created.requests}, 하중 경우 {item.outcome.created.load_cases}</button></li>)}</ul> : <p>적용 기록이 없습니다.</p>}{workPager('history', '적용 기록')}</div><div><h4>연결된 폴더</h4>{connections.length ? <ul>{connections.map((item) => <li key={`${item.relative_path}/${item.role}`}><code>{item.role_kind}</code> {item.relative_path} · {item.name}</li>)}</ul> : <p>연결된 폴더가 없습니다.</p>}{workPager('connections', '연결 폴더')}</div></div></div>
    {scan ? <div className="folder-discovery-card"><div className="folder-discovery-heading"><div><h3>2. 조사 결과</h3><p>{scan.relative_path || '/'} · 폴더 {scan.folder_count} · 파일 {scan.file_count} · {scan.status}</p></div>{busy === 'scan' ? <LoaderCircle className="spin" /> : null}</div>{scan.issues.length ? <ul className="folder-discovery-issues">{scan.issues.map((issue, index) => <li key={index}><IssueDetail issue={issue} /></li>)}</ul> : null}<div className="folder-discovery-tree">{nodes.map((node) => <div key={node.relative_path} style={{ paddingInlineStart: `${Math.max(0, node.depth) * 22}px` }}><FolderOpen /><span>{node.name}</span><small>파일 {node.file_count}{node.extensions.length ? ` · ${node.extensions.join(', ')}` : ''}</small></div>)}</div></div> : null}
    <div className="folder-discovery-card"><div className="folder-discovery-heading"><div><h3>3. 폴더 역할 규칙</h3><p>포함할 단어가 폴더 이름 어디에든 있으면 규칙을 적용합니다. 같은 깊이·역할 규칙은 서로 다른 폴더에 적용될 수 있으며, 한 폴더에서 같은 업무를 똑같이 가리킬 때만 하나로 합칩니다. 구분자가 비어 있으면 폴더 이름 전체를 이름으로 쓰고 코드는 비워 둡니다. 구분자를 지정하면 폴더 이름에 실제로 포함된 경우에만 규칙을 적용합니다.</p></div><button className="ghost-button" onClick={() => void saveRules()} disabled={busy !== '' || !rulesLoaded || rulesRevision == null || !catalogResolved}><Save /> 규칙 저장</button></div><fieldset className="folder-discovery-rules" disabled={busy !== '' || !rulesLoaded || !catalogResolved}>{rules.map((rule, index) => <RuleEditor key={`${rule.role}-${index}`} rule={rule} roleOptions={roleOptions} analysisTypes={analysisTypes} catalog={catalog} resultCatalog={semanticCatalog} onChange={(patch) => updateRule(index, patch)} onDelete={() => removeRule(index)} />)}</fieldset><div className="folder-discovery-actions"><button className="ghost-button" onClick={addRule} disabled={busy !== '' || !rulesLoaded || !catalogResolved || rules.length >= 30}>규칙 추가</button><button className="primary-button" onClick={() => void runPreview()} disabled={busy !== '' || !rulesLoaded || !catalogResolved || scan?.status !== 'COMPLETE'}><WandSparkles />{busy === 'preview' ? '미리보기 생성 중…' : '업무 생성 미리보기'}</button>{!scan ? <small>전체 트리를 조사한 뒤 미리보기를 만들 수 있습니다.</small> : null}</div></div>
    {preview ? <div className="folder-discovery-card"><div className="folder-discovery-heading"><div><h3>4. 생성 미리보기</h3><p>프로젝트 {preview.summary.projects} · 의뢰 {preview.summary.requests} · 하중 경우 {preview.summary.load_cases} · 충돌 {preview.summary.conflicts} · 제외 {preview.summary.excluded ?? preview.rows.filter((row) => row.status === 'EXCLUDED').length} · 미분류 {preview.unmatched_count}</p><small>이미 생성된 업무와 결과는 삭제되지 않습니다. 결과 폴더는 파일 처리 설정과 준비 상태를 함께 확인하세요.</small></div></div><div className="folder-discovery-preview"><table><thead><tr><th>경로</th><th>역할</th><th>코드</th><th>이름</th><th>결과 설정</th><th>결과</th><th>상세</th></tr></thead><tbody>{preview.rows.map((row, index) => { const isExcluded = row.status === 'EXCLUDED'; const isExplicitExclusion = isExcluded && row.excluded_by === row.relative_path; const hasExcludedBy = row.excluded_by !== null && row.excluded_by !== undefined; const detail = isExcluded && hasExcludedBy && row.excluded_by !== row.relative_path ? `${row.message ?? '상위 폴더가 제외되었습니다.'} · 상위 제외: ${row.excluded_by || '(root)'}` : row.message ?? (isExcluded ? '이 폴더가 제외되었습니다.' : ''); const config = row.result_config; const configLabel = row.role_kind === 'RESULTS' ? (config?.recipe_ids?.length ? `${config.recipe_ids.length}개 레시피${config.template_id ? ' · 템플릿 지정' : ''}` : '읽기 설정 필요') : '—'; return <tr data-testid="folder-discovery-preview-row" data-path={row.relative_path} key={`${row.relative_path}/${row.role}/${index}`} className={row.status.toLowerCase()}><td>{row.relative_path}</td><td>{row.role_label ?? roleName(row.role, catalog)}{row.role_kind && row.role_kind !== row.role ? <small> · {row.role_kind}</small> : null}</td><td>{row.code}</td><td>{row.name}</td><td>{configLabel}{row.result_config_source ? <small> · {row.result_config_source === 'ANALYSIS_TYPE_DEFAULT' ? '해석 종류 기본값' : row.result_config_source === 'RULE' ? '규칙 지정' : '없음'}</small> : null}{row.binding_status ? <small> · {row.binding_status}</small> : null}</td><td>{row.status}</td><td>{detail}{isExcluded ? (isExplicitExclusion ? <button type="button" className="ghost-button folder-discovery-row-action" onClick={() => void updateExclusion(row.relative_path, true)} disabled={busy !== ''}>제외 취소</button> : null) : <button type="button" className="ghost-button folder-discovery-row-action" onClick={() => void updateExclusion(row.relative_path, false)} disabled={busy !== ''}>이번 적용에서 제외</button>}{onOpenFolder && !previewOutdated && folderTarget(row) && row.status !== 'CONFLICT' && (row.status === 'KEEP' || appliedPreviewId === preview.id) ? <><button type="button" className="ghost-button folder-discovery-row-action" onClick={() => onOpenFolder(folderTarget(row))}>이 폴더 연결</button><small>고급 연결 관리</small></> : null}</td></tr> })}</tbody></table></div><div className="folder-discovery-actions"><button className="primary-button" onClick={() => void apply()} disabled={applyDisabled}><Play />{busy === 'apply' ? '생성 중…' : '검토한 업무 생성 적용'}</button>{preview && notice?.kind === 'success' && notice.text.startsWith('완료:') ? <button className="ghost-button" onClick={() => { const row = preview.rows.find((item) => (item.role_kind === 'RESULTS' || item.role_kind === 'INPUT') && item.status !== 'EXCLUDED'); onOpenFolder?.(row ? folderTarget(row) : undefined) }} disabled={busy !== '' || previewOutdated}>결과파일 연결하기</button> : null}{scan?.status !== 'COMPLETE' ? <small>조사가 완료되어야 적용할 수 있습니다.</small> : null}</div></div> : null}
    {pickerOpen ? <dialog className="folder-discovery-picker" ref={(node) => { if (node && !node.open) node.showModal() }} aria-label="최상위 폴더 선택" onCancel={() => setPickerOpen(false)}><div className="folder-discovery-heading"><div><h3>조사할 최상위 폴더 선택</h3><p>선택한 폴더 아래의 모든 하위 트리를 조사합니다.</p></div><button className="ghost-button" onClick={() => setPickerOpen(false)}>닫기</button></div><div className="folder-discovery-browser"><button onClick={() => { setPickerPath(''); void loadBrowse('') }} disabled={busy !== ''}>root</button>{browse?.relative_path ? <button onClick={() => { const parent = browse.relative_path.split('/').slice(0, -1).join('/'); setPickerPath(parent); void loadBrowse(parent) }} disabled={busy !== ''}>상위 폴더</button> : null}<strong>{browse?.relative_path || '/'}</strong></div><div className="folder-discovery-entries">{browse?.entries.filter((entry) => entry.is_directory).map((entry) => <button key={entry.relative_path} className={pickerPath === entry.relative_path ? 'selected' : ''} onClick={() => { setPickerPath(entry.relative_path); void loadBrowse(entry.relative_path) }} disabled={busy !== ''}><FolderOpen />{entry.name}</button>)}</div><div className="folder-discovery-actions"><button className="ghost-button" disabled={busy !== ''} onClick={() => setPickerPath(browse?.relative_path ?? '')}>이 위치 선택</button><button className="primary-button" disabled={busy !== '' || pickerPath !== browse?.relative_path} onClick={() => { selectFolder(pickerPath); setPickerOpen(false); void loadRules(pickerPath) }}>선택 완료</button></div></dialog> : null}
  </section>
}

function RuleEditor({ rule, roleOptions, analysisTypes, catalog, resultCatalog, onChange, onDelete }: { rule: FolderDiscoveryRule; roleOptions: FolderRoleOption[]; analysisTypes: { key: string; label: string }[]; catalog: FolderDiscoveryCatalog | null; resultCatalog: SemanticCatalog | null; onChange: (patch: Partial<FolderDiscoveryRule>) => void; onDelete: () => void }) {
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
    {(selectedRole?.kind ?? legacyRole?.kind) === 'RESULTS' ? <ResultRuleConfigEditor rule={rule} analysisTypes={catalog?.analysis_types ?? []} resultCatalog={resultCatalog} onChange={onChange} /> : null}
    <button type="button" className="ghost-button" onClick={onDelete}>삭제</button>
  </div>
}

function ResultRuleConfigEditor({ rule, analysisTypes, resultCatalog, onChange }: { rule: FolderDiscoveryRule; analysisTypes: Array<{ key: string; default_result_config?: { recipe_ids: string[]; template_id?: string | null } | null }>; resultCatalog: SemanticCatalog | null; onChange: (patch: Partial<FolderDiscoveryRule>) => void }) {
  const config = rule.result_config ?? { recipe_ids: [], template_id: null }
  const recipes = resultCatalog?.recipes.filter((item) => item.active_version) ?? []
  const templates = resultCatalog?.templates.filter((item) => item.active_version) ?? []
  const defaultConfig = analysisTypes.find((item) => item.key === rule.analysis_type)?.default_result_config
  const toggleRecipe = (id: string, checked: boolean) => {
    const recipe_ids = checked ? [...new Set([...config.recipe_ids, id])] : config.recipe_ids.filter((value) => value !== id)
    onChange({ result_config: recipe_ids.length ? { recipe_ids, template_id: config.template_id || null } : undefined })
  }
  return <div className="folder-result-rule-config"><strong>결과 읽기 설정</strong><fieldset><legend>활성 레시피</legend>{recipes.length ? recipes.map((recipe) => <label key={recipe.id}><input type="checkbox" aria-label={`결과 레시피 ${recipe.name ?? recipe.id}`} checked={config.recipe_ids.includes(recipe.id)} onChange={(event) => toggleRecipe(recipe.id, event.target.checked)} />{recipe.name ?? recipe.id} · v{recipe.active_version}</label>) : <small>활성 레시피를 불러오지 못했습니다. 저장 후 결과 설정을 다시 확인하세요.</small>}</fieldset><label>표시 템플릿<select aria-label="결과 규칙 표시 템플릿" disabled={!config.recipe_ids.length} value={config.template_id ?? ''} onChange={(event) => { if (config.recipe_ids.length) onChange({ result_config: { recipe_ids: config.recipe_ids, template_id: event.target.value || null } }) }}><option value="">레시피에 저장된 템플릿</option>{templates.map((template) => <option key={template.id} value={template.id}>{template.name ?? template.id} · v{template.active_version}</option>)}</select></label>{defaultConfig ? <small>해석 종류 기본값: 레시피 {defaultConfig.recipe_ids.length}개{defaultConfig.template_id ? ' · 템플릿 지정' : ''} (규칙에 직접 지정하면 우선합니다)</small> : <small>설정하지 않아도 업무 생성은 가능하지만 미리보기 상태가 읽기 설정 필요로 표시됩니다.</small>}</div>
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
