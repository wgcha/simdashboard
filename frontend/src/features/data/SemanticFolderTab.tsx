import { useEffect, useRef, useState } from 'react'
import { FolderOpen, RefreshCw, Save, Upload } from 'lucide-react'
import { semanticContextApi, semanticMappingApi, type ContextLoadCase, type ContextProject, type ContextRequest, type FolderResponse, type SemanticBinding, type SemanticCatalog } from '../../shared/api/semanticMapping'
import { AliasSuggestion } from './AliasSuggestion'
import { SemanticReviewQueue } from './SemanticReviewQueue'
import type { SemanticVocabularyEntry } from '../../shared/api/semanticVocabulary'

type Message = { kind: 'success' | 'error' | 'info'; text: string }
type Target = { project_id: string; request_id: string; load_case_id: string; role: string; recipe_ids: string[]; template_id: string }
export type FolderConnectionPrefill = { relative_path: string; project_id?: string | null; request_id?: string | null; load_case_id?: string | null; role: 'RESULTS' | 'INPUT' }
type RefreshResult = { partial: boolean; results: Array<{ relative_path?: string; status: string; run_id?: string; code?: string; detail?: unknown }> }
const emptyTarget = (): Target => ({ project_id: '', request_id: '', load_case_id: '', role: 'PROJECT', recipe_ids: [], template_id: '' })

export function FolderTab({ catalog, onMessage, busy, setBusy, onCatalog, scopeProjectId = '', onOpenDiscovery, initialTarget }: {
  catalog: SemanticCatalog; onMessage: (message: Message) => void; busy: string; setBusy: (value: string) => void; onCatalog: (catalog: SemanticCatalog) => void; scopeProjectId?: string; onOpenDiscovery?: () => void; initialTarget?: FolderConnectionPrefill | null
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
  const [reviewBinding, setReviewBinding] = useState<SemanticBinding | null>(null)
  const appliedPrefill = useRef('')
  const error = (reason: unknown) => onMessage({ kind: 'error', text: reason instanceof Error ? reason.message : '요청을 처리하지 못했습니다.' })

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
    } catch (reason) { error(reason) } finally { setBusy('') }
  }
  const edit = (binding: SemanticBinding) => {
    setEditing(binding); setPath(binding.relative_path); setFolder(null)
    setTarget({ project_id: binding.project_id, request_id: binding.request_id ?? '', load_case_id: binding.load_case_id ?? '', role: binding.role, recipe_ids: binding.recipe_ids, template_id: binding.template_id ?? '' })
  }
  const refresh = async (id: string) => {
    setBusy(`refresh-${id}`)
    try {
      const result = await semanticMappingApi.refreshBinding(id) as RefreshResult
      setRefreshResult(result)
      onMessage({ kind: result.partial ? 'info' : 'success', text: `${result.results.length}개 파일 처리 · ${result.partial ? '보류/오류 항목을 확인하세요.' : '완료'}` })
    } catch (reason) { error(reason) } finally { setBusy('') }
  }
  const upload = async () => {
    if (!file || target.recipe_ids.length !== 1 || !target.load_case_id) {
      onMessage({ kind: 'error', text: '단일 파일 등록에는 파일·하중 경우·레시피 하나를 선택하세요.' }); return
    }
    setBusy('import')
    try {
      const result = await semanticMappingApi.importFile(file, target.recipe_ids[0], target.load_case_id, target.template_id || undefined)
      onMessage({ kind: 'success', text: `${result.status} · run ${result.run_id} · 레시피 v${result.recipe_version}` })
    } catch (reason) { error(reason) } finally { setBusy('') }
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
        <label>프로젝트<select aria-label="프로젝트" value={target.project_id} onChange={(event) => setTarget({ ...target, project_id: event.target.value, request_id: '', load_case_id: '', role: 'PROJECT' })}><option value="">프로젝트 선택</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
        <label>의뢰<select aria-label="의뢰" disabled={!target.project_id} value={target.request_id} onChange={(event) => setTarget({ ...target, request_id: event.target.value, load_case_id: '', role: event.target.value ? 'REQUEST' : 'PROJECT' })}><option value="">선택 안 함</option>{requests.map((request) => <option key={request.id} value={request.id}>{request.title}</option>)}</select></label>
        <label>하중 경우<select aria-label="하중 경우" disabled={!target.request_id} value={target.load_case_id} onChange={(event) => setTarget({ ...target, load_case_id: event.target.value, role: event.target.value ? 'RESULTS' : 'REQUEST' })}><option value="">선택 안 함</option>{cases.map((loadCase) => <option key={loadCase.id} value={loadCase.id}>{loadCase.name}</option>)}</select></label>
        <label>연결 역할<select aria-label="연결 역할" value={target.role} onChange={(event) => setTarget({ ...target, role: event.target.value })}><option value="PROJECT">프로젝트 폴더</option><option value="REQUEST">의뢰 폴더</option><option value="LOAD_CASE">하중 경우 폴더</option><option value="RESULTS">결과 폴더</option><option value="INPUT">입력 폴더</option></select></label>
      </div>
      <fieldset className="recipe-checklist"><legend>적용 레시피</legend>{recipes.map((recipe) => <label key={recipe.id}><input type="checkbox" checked={target.recipe_ids.includes(recipe.id)} onChange={(event) => setTarget({ ...target, recipe_ids: event.target.checked ? [...target.recipe_ids, recipe.id] : target.recipe_ids.filter((id) => id !== recipe.id) })} />{recipe.name ?? recipe.id} · 활성 v{recipe.active_version}</label>)}</fieldset>
      <label>표시 템플릿<select aria-label="표시 템플릿" value={target.template_id} onChange={(event) => setTarget({ ...target, template_id: event.target.value })}><option value="">템플릿 없음</option>{templates.map((template) => <option key={template.id} value={template.id}>{template.name ?? template.id} · 활성 v{template.active_version}</option>)}</select></label>
      <button className="primary-button" onClick={() => void save()} disabled={!!busy}><Save />{editing ? '재연결 저장' : '연결 저장'}</button>
    </div>
    <div className="semantic-card">
      <h2>다음 파일 처리</h2><p>단일 파일 등록은 위에서 선택한 대상·레시피·템플릿을 사용합니다.</p>
      <div className="folder-import-row"><input aria-label="등록할 결과 파일" type="file" accept=".csv,.json" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><button className="primary-button" onClick={() => void upload()} disabled={!!busy}><Upload />단일 파일 가져오기</button></div>
      <div className="binding-list">{catalog.bindings.map((binding) => <article key={binding.id}><div><strong>{binding.relative_path}</strong><small>{projects.find((project) => project.id === binding.project_id)?.name ?? binding.project_id} · {binding.role} · 개정 {binding.revision}</small></div><button onClick={() => edit(binding)}>재연결·수정</button>{binding.load_case_id && <button onClick={() => void refresh(binding.id)} disabled={!!busy}><RefreshCw />새로고침</button>}{binding.load_case_id && <button onClick={() => setReviewBinding((current) => current?.id === binding.id ? null : binding)}>{reviewBinding?.id === binding.id ? '검토함 닫기' : '검토함'}</button>}</article>)}</div>
      {refreshResult && <table className="semantic-result-table"><thead><tr><th>파일 / 실행</th><th>처리 상태</th><th>상세</th></tr></thead><tbody>{refreshResult.results.map((result, index) => <tr key={index}><td>{result.relative_path ?? result.run_id}</td><td>{result.status}</td><td>{result.code ?? (result.detail ? JSON.stringify(result.detail) : '')}</td></tr>)}</tbody></table>}
      {reviewBinding ? <SemanticReviewQueue binding={reviewBinding} catalog={catalog} canReview onMessage={onMessage} /> : null}
    </div>
  </div>
}
