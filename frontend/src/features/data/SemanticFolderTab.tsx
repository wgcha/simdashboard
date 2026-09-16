import { useEffect, useRef, useState } from 'react'
import { FolderOpen, RefreshCw, Save, Upload } from 'lucide-react'
import { Link } from 'react-router-dom'
import { semanticContextApi, semanticMappingApi, type ContextLoadCase, type ContextProject, type ContextRequest, type FolderResponse, type PreviewWidget, type SemanticBinding, type SemanticCatalog } from '../../shared/api/semanticMapping'
import { AliasSuggestion } from './AliasSuggestion'
import { SemanticReviewQueue } from './SemanticReviewQueue'
import type { SemanticVocabularyEntry } from '../../shared/api/semanticVocabulary'
import { semanticFormatLabel, semanticStatusLabel } from '../../shared/components/semanticLabels'
import { SearchableSelect } from '../../shared/components/selectionLabels'
import { SemanticWidgetGrid } from '../../shared/components/semanticResults'

type Message = { kind: 'success' | 'error' | 'info'; text: string }
type Target = { project_id: string; request_id: string; load_case_id: string; role: string; recipe_ids: string[]; template_id: string }
export type FolderConnectionPrefill = { relative_path: string; project_id?: string | null; request_id?: string | null; load_case_id?: string | null; role: 'RESULTS' | 'INPUT' }
type RefreshResult = { partial: boolean; display_run_id?: string | null; results: Array<{ source_relative_path?: string; relative_path?: string; filename?: string; status: string; run_id?: string; run_no?: number; code?: string; message?: string; detail?: unknown; clear_reason?: string; candidate_errors?: unknown[]; review_available?: boolean; review_reason?: string }> }
type ImportResult = { status: string; run_id?: string; recipe_version?: number; review_available?: boolean; clear_reason?: string; candidate_errors?: unknown[] }
type ReviewTarget = { projectId: string; requestId: string; loadCaseId: string; runId: string }
const emptyTarget = (): Target => ({ project_id: '', request_id: '', load_case_id: '', role: 'PROJECT', recipe_ids: [], template_id: '' })
const canOpenReview = (status: string, runId?: string | null, reviewAvailable?: boolean) => Boolean(runId && ['IMPORTED', 'SKIPPED'].includes(status) && reviewAvailable !== false)
const readableReason = (value: string) => ({ NO_DISPLAY_TEMPLATE: '표시 템플릿이 없어 검토 화면을 열 수 없습니다.', TEMPLATE_HAS_NO_WIDGETS: '표시 템플릿에 위젯이 없어 검토 화면을 열 수 없습니다.', NO_RENDERABLE_WIDGETS: '표시할 수 있는 위젯이 없어 검토 화면을 열 수 없습니다.' }[value] ?? value)
const candidateErrorText = (value: unknown) => {
  if (typeof value === 'string') return value
  if (!value || typeof value !== 'object') return String(value)
  const item = value as Record<string, unknown>
  const recipe = item.recipe_name ?? item.recipe_id ?? '레시피'
  const version = item.recipe_version ? ` v${item.recipe_version}` : ''
  const message = item.message ?? item.detail ?? item.reason ?? item.code ?? '검증 실패'
  return `${recipe}${version}: ${typeof message === 'string' ? readableReason(message) : JSON.stringify(message)}`
}
const activeRecipeFormat = (recipe: SemanticCatalog['recipes'][number]) => recipe.active_format ?? (recipe.latest_version === recipe.active_version ? recipe.definition.format : undefined)
const reviewHref = ({ projectId, requestId, loadCaseId, runId }: ReviewTarget) => {
  const query = new URLSearchParams({ project: projectId, request: requestId, loadCase: loadCaseId, run: runId, view: 'custom' })
  return `/workspace/requests?${query.toString()}`
}

export function FolderTab({ catalog, onMessage, busy, setBusy, onCatalog, scopeProjectId = '', onOpenDiscovery, initialTarget, canImportResults = true, canImportForProject }: {
  catalog: SemanticCatalog; onMessage: (message: Message) => void; busy: string; setBusy: (value: string) => void; onCatalog: (catalog: SemanticCatalog) => void; scopeProjectId?: string; onOpenDiscovery?: () => void; initialTarget?: FolderConnectionPrefill | null; canImportResults?: boolean; canImportForProject?: (projectId: string) => boolean
}) {
  const [path, setPath] = useState('')
  const [folder, setFolder] = useState<FolderResponse | null>(null)
  const [target, setTarget] = useState<Target>(emptyTarget)
  const [editing, setEditing] = useState<SemanticBinding | null>(null)
  const [projects, setProjects] = useState<ContextProject[]>([])
  const [requests, setRequests] = useState<ContextRequest[]>([])
  const [cases, setCases] = useState<ContextLoadCase[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [refreshResult, setRefreshResult] = useState<RefreshResult | null>(null)
  const [displayWidgets, setDisplayWidgets] = useState<PreviewWidget[]>([])
  const [refreshOwnerContext, setRefreshOwnerContext] = useState<Omit<ReviewTarget, 'runId'> | null>(null)
  const [reviewBinding, setReviewBinding] = useState<SemanticBinding | null>(null)
  const [reviewTarget, setReviewTarget] = useState<ReviewTarget | null>(null)
  const appliedPrefill = useRef('')
  const refreshRequest = useRef(0)
  const importRequest = useRef(0)
  const error = (reason: unknown) => onMessage({ kind: 'error', text: reason instanceof Error ? reason.message : '요청을 처리하지 못했습니다.' })

  useEffect(() => () => { refreshRequest.current += 1; importRequest.current += 1 }, [])

  useEffect(() => { let active = true; semanticContextApi.projects().then((value) => { if (active) setProjects(value) }).catch(error); return () => { active = false } }, [])
  useEffect(() => {
    let active = true; setRequests([])
    if (target.project_id) semanticContextApi.requests(target.project_id).then((value) => { if (active) setRequests(value) }).catch(error)
    return () => { active = false }
  }, [target.project_id])
  useEffect(() => {
    let active = true; setCases([])
    if (target.request_id) semanticContextApi.loadCases(target.request_id).then((value) => { if (active) setCases(value) }).catch(error)
    return () => { active = false }
  }, [target.request_id])
  useEffect(() => {
    if (!initialTarget) return
    const prefillKey = JSON.stringify(initialTarget)
    if (appliedPrefill.current === prefillKey) return
    appliedPrefill.current = prefillKey
    setEditing(null)
    setPath(initialTarget.relative_path)
    setFolder(null)
    setTarget((current) => ({ ...current, project_id: initialTarget.project_id ?? '', request_id: initialTarget.request_id ?? '', load_case_id: initialTarget.load_case_id ?? '', role: initialTarget.role }))
    onMessage({ kind: 'info', text: '폴더 조사 결과를 연결 대상으로 미리 채웠습니다. 레시피를 선택한 뒤 연결 저장을 누르세요.' })
  }, [initialTarget])

  const browse = async (next = path) => {
    setBusy('folders')
    try { setFolder(await semanticMappingApi.folders(next)); setPath(next) } catch (reason) { error(reason) } finally { setBusy('') }
  }
  const save = async () => {
    if (!(canImportForProject ? canImportForProject(target.project_id) : canImportResults)) { onMessage({ kind: 'error', text: '폴더 연결 저장에는 결과 등록 권한이 필요합니다.' }); return }
    if (!path || !target.project_id || (target.load_case_id && !target.recipe_ids.length)) {
      onMessage({ kind: 'error', text: '폴더 경로·프로젝트를 지정하고 결과 폴더에는 레시피를 선택하세요.' }); return
    }
    setBusy('bind')
    const payload = { ...target, relative_path: path, request_id: target.request_id || null, load_case_id: target.load_case_id || null, template_id: target.template_id || null }
    try {
      const saved = editing ? await semanticMappingApi.reconnectBinding(editing.id, { ...payload, expected_revision: editing.revision ?? 1 }) : await semanticMappingApi.createBinding(payload)
      onCatalog(await semanticMappingApi.catalog()); setEditing(saved)
      if (reviewBinding?.id === saved.id) setReviewBinding(saved)
      onMessage({ kind: 'success', text: `폴더 연결을 저장했습니다 · 개정 ${saved.revision}. 이 폴더 바로 아래 파일을 처리합니다.` })
      if (saved.load_case_id && saved.recipe_ids.length) await refresh(saved.id, saved)
    } catch (reason) { error(reason) } finally { setBusy('') }
  }
  const edit = (binding: SemanticBinding) => {
    setEditing(binding); setPath(binding.relative_path); setFolder(null)
    setTarget({ project_id: binding.project_id, request_id: binding.request_id ?? '', load_case_id: binding.load_case_id ?? '', role: binding.role, recipe_ids: binding.recipe_ids, template_id: binding.template_id ?? '' })
  }
  async function refresh(id: string, knownBinding?: SemanticBinding) {
    const requestId = ++refreshRequest.current
    const binding = knownBinding ?? catalog.bindings.find((item) => item.id === id)
    const ownerContext = binding && binding.request_id && binding.load_case_id ? { projectId: binding.project_id, requestId: binding.request_id, loadCaseId: binding.load_case_id } : null
    setRefreshOwnerContext(ownerContext)
    setReviewTarget(null)
    setRefreshResult(null); setDisplayWidgets([])
    setBusy(`refresh-${id}`)
    try {
      const result = (binding?.load_case_id ? await semanticMappingApi.refreshLoadCaseResults(binding.load_case_id) : await semanticMappingApi.refreshBinding(id)) as RefreshResult
      if (requestId !== refreshRequest.current) return
      setRefreshResult(result)
      if (result.display_run_id && binding?.load_case_id) {
        const widgets = await semanticMappingApi.results({ load_case_id: binding.load_case_id, run_id: result.display_run_id })
        if (requestId === refreshRequest.current) setDisplayWidgets(widgets.widgets)
      }
      onMessage({ kind: result.partial ? 'info' : 'success', text: `${result.results.length}개 파일 처리 · ${result.partial ? '보류/오류 항목을 확인하세요.' : '완료'}` })
    } catch (reason) { if (requestId === refreshRequest.current) error(reason) } finally { if (requestId === refreshRequest.current) setBusy('') }
  }
  const upload = async () => {
    if (!(canImportForProject ? canImportForProject(target.project_id) : canImportResults)) { onMessage({ kind: 'error', text: '결과 파일 가져오기에는 결과 등록 권한이 필요합니다.' }); return }
    if (!file || target.recipe_ids.length !== 1 || !target.load_case_id) {
      onMessage({ kind: 'error', text: '단일 파일 등록에는 파일·하중 경우·레시피 하나를 선택하세요.' }); return
    }
    const requestId = ++importRequest.current
    const targetAtStart = { projectId: target.project_id, requestId: target.request_id, loadCaseId: target.load_case_id }
    setReviewTarget(null)
    setBusy('import')
    try {
      const result = await semanticMappingApi.importFile(file, target.recipe_ids[0], target.load_case_id, target.template_id || undefined) as ImportResult
      if (requestId !== importRequest.current) return
      if (canOpenReview(result.status, result.run_id, result.review_available) && targetAtStart.requestId) setReviewTarget({ ...targetAtStart, runId: result.run_id! })
      const reason = result.clear_reason ? ` · ${readableReason(result.clear_reason)}` : ''
      onMessage({ kind: 'success', text: `${semanticStatusLabel(result.status)}${result.run_id ? ` · Run ${result.run_id}` : ''} · 레시피 v${result.recipe_version ?? '—'}${reason}` })
    } catch (reason) { if (requestId === importRequest.current) error(reason) } finally { if (requestId === importRequest.current) setBusy('') }
  }
  const applyVocabularyTarget = (entry: SemanticVocabularyEntry) => {
    if (entry.target_kind === 'PROJECT') {
      setTarget((current) => ({ ...current, project_id: entry.target_id, request_id: '', load_case_id: '', role: 'PROJECT' }))
    } else if (entry.target_kind === 'REQUEST') {
      setTarget((current) => ({ ...current, project_id: entry.project_id ?? current.project_id, request_id: entry.target_id, load_case_id: '', role: 'REQUEST' }))
    } else if (entry.target_kind === 'LOAD_CASE') {
      setTarget((current) => ({ ...current, project_id: entry.project_id ?? current.project_id, request_id: entry.request_id ?? current.request_id, load_case_id: entry.target_id, role: 'LOAD_CASE' }))
    } else if (entry.target_kind === 'FOLDER_ROLE') {
      setTarget((current) => ({ ...current, role: entry.target_id }))
    }
    onMessage({ kind: 'info', text: `후보 ${entry.label}을 폴더 연결 대상에 적용했습니다. 저장 버튼을 눌러야 연결이 저장됩니다.` })
  }
  const currentPathName = path.split('/').filter(Boolean).at(-1) ?? ''
  const recipes = catalog.recipes.filter((recipe) => recipe.active_version)
  const templates = catalog.templates.filter((template) => template.active_version)

  return <div className="semantic-flow folder-flow">
    <div className="semantic-card folder-discovery-handoff"><div><strong>실제 폴더에서 새 업무를 만들려면</strong><p>예제 대상 선택 없이 전체 하위 폴더를 조사하고, 역할 규칙과 생성 결과를 미리 검토할 수 있습니다.</p></div><button className="primary-button" onClick={onOpenDiscovery}>폴더 조사·업무 생성으로 이동</button></div>
    <div className="semantic-card">
      <div className="semantic-card-heading"><h2>{editing ? '폴더 재연결·설정 수정' : '임의 폴더 탐색·대상 연결'}</h2><FolderOpen /></div>
      {editing && <p role="status">기존 연결 · 개정 {editing.revision} <button onClick={() => { setEditing(null); setTarget(emptyTarget()); setPath('') }}>새 연결 작성</button></p>}
      <div className="folder-browser-controls"><div className="folder-path-with-alias"><input aria-label="폴더 상대 경로" value={path} placeholder="예: Eagle_2026/results" onChange={(event) => setPath(event.target.value)} />{currentPathName ? <AliasSuggestion term={currentPathName} targetKinds={['PROJECT', 'REQUEST', 'LOAD_CASE', 'FOLDER_ROLE']} scopeProjectId={target.project_id || undefined} onSelect={applyVocabularyTarget} label="현재 폴더 별칭 찾기" /> : null}</div><button onClick={() => void browse()} disabled={!!busy}>탐색</button></div>
      {folder && <div className="folder-list"><div><button onClick={() => void browse(path.split('/').slice(0, -1).join('/'))}>상위 폴더</button>{folder.entries?.filter((entry) => entry.is_directory).map((entry) => <button key={entry.relative_path} onClick={() => void browse(entry.relative_path)}>{entry.name}</button>)}</div><div>{folder.entries?.filter((entry) => !entry.is_directory).map((entry) => <span key={entry.relative_path}>{entry.name}</span>)}</div></div>}
      <div className="binding-fields">
        <label>프로젝트<SearchableSelect ariaLabel="프로젝트" items={projects} kind="project" value={target.project_id} onChange={(value) => setTarget({ ...target, project_id: value, request_id: '', load_case_id: '', role: 'PROJECT' })} placeholder="프로젝트 선택" /></label>
        <label>의뢰<SearchableSelect ariaLabel="의뢰" items={requests} kind="request" disabled={!target.project_id} value={target.request_id} onChange={(value) => setTarget({ ...target, request_id: value, load_case_id: '', role: value ? 'REQUEST' : 'PROJECT' })} placeholder="선택 안 함" /></label>
        <label>하중 경우<SearchableSelect ariaLabel="하중 경우" items={cases} kind="load_case" disabled={!target.request_id} value={target.load_case_id} onChange={(value) => setTarget({ ...target, load_case_id: value, role: value ? 'RESULTS' : 'REQUEST' })} placeholder="선택 안 함" /></label>
        <label>연결 역할<select aria-label="연결 역할" value={target.role} onChange={(event) => setTarget({ ...target, role: event.target.value })}><option value="PROJECT">프로젝트 폴더</option><option value="REQUEST">의뢰 폴더</option><option value="LOAD_CASE">하중 경우 폴더</option><option value="RESULTS">결과 폴더</option><option value="INPUT">입력 폴더</option></select></label>
      </div>
      <fieldset className="recipe-checklist"><legend>적용 레시피</legend>{recipes.map((recipe) => { const format = activeRecipeFormat(recipe); return <label key={recipe.id}><input type="checkbox" checked={target.recipe_ids.includes(recipe.id)} onChange={(event) => setTarget({ ...target, recipe_ids: event.target.checked ? [...target.recipe_ids, recipe.id] : target.recipe_ids.filter((id) => id !== recipe.id) })} />{recipe.name ?? recipe.id} · {format ? semanticFormatLabel(format) : '활성 형식 확인 중'} · 활성 v{recipe.active_version}</label> })}</fieldset>
      <label>표시 템플릿<select aria-label="표시 템플릿" value={target.template_id} onChange={(event) => setTarget({ ...target, template_id: event.target.value })}><option value="">레시피에 저장된 템플릿 사용</option>{templates.map((template) => <option key={template.id} value={template.id}>{template.name ?? template.id} · 활성 v{template.active_version}</option>)}</select><small>선택한 레시피의 저장된 표시 템플릿과 버전을 기본으로 사용합니다.</small></label>
      <button className="primary-button" onClick={() => void save()} disabled={!(canImportForProject ? canImportForProject(target.project_id) : canImportResults) || !!busy}><Save />{!(canImportForProject ? canImportForProject(target.project_id) : canImportResults) ? '결과 등록 권한 필요' : editing ? '재연결 저장' : '연결 저장'}</button>
    </div>
    <div className="semantic-card">
      <h2>다음 파일 처리</h2><p>단일 파일 등록은 위에서 선택한 대상·레시피·템플릿을 사용합니다.</p>
      <div className="folder-import-row" aria-label="등록한 결과"><input aria-label="등록할 결과 파일" type="file" accept=".csv,.tsv,.json,.txt" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><button className="primary-button" onClick={() => void upload()} disabled={!(canImportForProject ? canImportForProject(target.project_id) : canImportResults) || !!busy}><Upload />{(canImportForProject ? canImportForProject(target.project_id) : canImportResults) ? '단일 파일 가져오기' : '결과 등록 권한 필요'}</button>{reviewTarget ? <Link className="secondary-button" to={reviewHref(reviewTarget)}>결과 검토</Link> : null}</div>
      <div className="binding-list">{catalog.bindings.map((binding) => <article key={binding.id}><div><strong>{binding.relative_path}</strong><small>{projects.find((project) => project.id === binding.project_id)?.name ?? binding.project_id} · {binding.role} · 개정 {binding.revision}</small></div><button onClick={() => edit(binding)}>재연결·수정</button>{binding.load_case_id && <button onClick={() => void refresh(binding.id)} disabled={!(canImportForProject ? canImportForProject(binding.project_id) : canImportResults) || !!busy}><RefreshCw />새로고침</button>}{binding.load_case_id && <button onClick={() => setReviewBinding((current) => current?.id === binding.id ? null : binding)}>{reviewBinding?.id === binding.id ? '검토함 닫기' : '검토함'}</button>}</article>)}</div>
      {refreshResult && <table className="semantic-result-table folder-refresh-results"><thead><tr><th>파일</th><th>처리 상태</th><th>Run</th><th>상세</th><th>검토</th></tr></thead><tbody>{refreshResult.results.map((result, index) => { const sourcePath = result.source_relative_path ?? result.relative_path; const ownerContext = refreshOwnerContext && result.run_id ? { ...refreshOwnerContext, runId: result.run_id } : null; const candidateErrors = result.candidate_errors?.length ? `후보 오류: ${result.candidate_errors.map(candidateErrorText).join(' · ')}` : ''; const detail = [candidateErrors, result.message, result.code, result.detail ? JSON.stringify(result.detail) : '', result.clear_reason ? readableReason(result.clear_reason) : ''].filter(Boolean).join(' · '); return <tr key={`${sourcePath ?? result.filename ?? 'result'}:${result.run_id ?? index}`}><td>{result.filename ?? sourcePath ?? '파일명 없음'}{sourcePath && result.filename && sourcePath !== result.filename ? <small>{sourcePath}</small> : null}</td><td>{semanticStatusLabel(result.status)}</td><td>{result.run_id ? <span>{result.run_no != null ? `#${result.run_no} · ` : ''}{result.run_id}</span> : 'Run 없음'}</td><td className="folder-refresh-detail">{detail || '상세 정보 없음'}</td><td>{ownerContext && canOpenReview(result.status, result.run_id, result.review_available) ? <Link className="secondary-button" to={reviewHref(ownerContext)}>결과 검토</Link> : result.review_reason ? <span>{readableReason(result.review_reason)}</span> : null}</td></tr> })}</tbody></table>}
      {displayWidgets.length ? <section data-testid="result-widget-display"><h3>표시 Run {refreshResult?.display_run_id}</h3><SemanticWidgetGrid widgets={displayWidgets} /></section> : refreshResult?.display_run_id ? <p role="status">표시할 위젯이 없습니다. 템플릿 설정을 확인하세요.</p> : null}
      {reviewBinding ? <SemanticReviewQueue binding={reviewBinding} catalog={catalog} canReview onMessage={onMessage} /> : null}
    </div>
  </div>
}
