import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Check, Pencil, Plus, RefreshCw, Save, Tags, X } from 'lucide-react'
import { semanticContextApi, semanticMappingApi, type ContextLoadCase, type ContextProject, type ContextRequest, type SemanticCatalog } from '../../shared/api/semanticMapping'
import { semanticVocabularyApi, type SemanticVocabularyEntry, type SemanticVocabularyEntryInput, type VocabularyTargetKind } from '../../shared/api/semanticVocabulary'
import './SemanticVocabularyTab.css'

const targetKinds: VocabularyTargetKind[] = ['FOLDER_ROLE', 'PROJECT', 'REQUEST', 'LOAD_CASE', 'RESULT_ITEM']
const targetKindLabels: Record<VocabularyTargetKind, string> = { FOLDER_ROLE: '폴더 역할', PROJECT: '프로젝트', REQUEST: '의뢰', LOAD_CASE: '하중 경우', RESULT_ITEM: '결과 항목' }
const folderRoles = ['PROJECT', 'REQUEST', 'LOAD_CASE', 'INPUT', 'RESULTS']
type Status = { kind: 'success' | 'error' | 'info'; text: string }
type Draft = SemanticVocabularyEntryInput

const emptyDraft = (): Draft => ({ key: '', label: '', description: '', target_kind: 'RESULT_ITEM', target_id: '', scope_project_id: null, aliases: [], enabled: true })

export function SemanticVocabularyTab({ isAdmin, selectedProjectId = '' }: { isAdmin: boolean; selectedProjectId?: string }) {
  const [entries, setEntries] = useState<SemanticVocabularyEntry[]>([])
  const [catalog, setCatalog] = useState<SemanticCatalog>({ items: [], recipes: [], templates: [], bindings: [] })
  const [projects, setProjects] = useState<ContextProject[]>([])
  const [requests, setRequests] = useState<ContextRequest[]>([])
  const [loadCases, setLoadCases] = useState<ContextLoadCase[]>([])
  const [targetProjectId, setTargetProjectId] = useState(selectedProjectId)
  const [targetRequestId, setTargetRequestId] = useState('')
  const [search, setSearch] = useState('')
  const [kind, setKind] = useState<'ALL' | VocabularyTargetKind>('ALL')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [aliasesText, setAliasesText] = useState('')
  const [editing, setEditing] = useState<SemanticVocabularyEntry | null>(null)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<Status | null>(null)
  const loadGeneration = useRef(0)

  const load = async () => {
    const generation = ++loadGeneration.current
    setBusy(true)
    try {
      const [vocabulary, context, semanticCatalog] = await Promise.all([semanticVocabularyApi.list(), semanticContextApi.projects(), semanticMappingApi.catalog()])
      if (generation !== loadGeneration.current) return
      setEntries(vocabulary.entries); setProjects(context); setCatalog(semanticCatalog)
    } catch (reason) { if (generation === loadGeneration.current) setStatus({ kind: 'error', text: reason instanceof Error ? reason.message : '기준 정의를 불러오지 못했습니다.' }) } finally { if (generation === loadGeneration.current) setBusy(false) }
  }
  useEffect(() => { void load() }, [])
  useEffect(() => { setTargetProjectId(selectedProjectId) }, [selectedProjectId])
  useEffect(() => {
    let active = true
    setRequests([]); setTargetRequestId(''); setLoadCases([])
    if ((draft?.target_kind === 'REQUEST' || draft?.target_kind === 'LOAD_CASE') && targetProjectId) semanticContextApi.requests(targetProjectId).then((value) => { if (active) setRequests(value) }).catch(() => undefined)
    return () => { active = false }
  }, [draft?.target_kind, targetProjectId])
  useEffect(() => {
    let active = true
    setLoadCases([])
    if (draft?.target_kind === 'LOAD_CASE' && targetRequestId) semanticContextApi.loadCases(targetRequestId).then((value) => { if (active) setLoadCases(value) }).catch(() => undefined)
    return () => { active = false }
  }, [draft?.target_kind, targetRequestId])

  const filtered = useMemo(() => {
    const query = search.normalize('NFKC').toLocaleLowerCase().trim()
    return entries.filter((entry) => (kind === 'ALL' || entry.target_kind === kind) && (!query || [entry.key, entry.label, entry.description, entry.target_label, ...entry.aliases].join(' ').normalize('NFKC').toLocaleLowerCase().includes(query)))
  }, [entries, kind, search])
  const targetOptions = useMemo(() => {
    if (!draft) return []
    if (draft.target_kind === 'FOLDER_ROLE') return folderRoles.map((value) => ({ id: value, label: value }))
    if (draft.target_kind === 'PROJECT') return projects.filter((value) => !draft.scope_project_id || value.id === draft.scope_project_id).map((value) => ({ id: value.id, label: value.name }))
    if (draft.target_kind === 'REQUEST') return requests.map((value) => ({ id: value.id, label: value.title }))
    if (draft.target_kind === 'LOAD_CASE') return loadCases.map((value) => ({ id: value.id, label: value.name }))
    return catalog.items.map((value) => ({ id: value.id, label: `${value.definition.label} (${value.definition.key})` }))
  }, [catalog.items, draft, loadCases, projects, requests])

  const startCreate = () => { setEditing(null); setAliasesText(''); setTargetProjectId(selectedProjectId); setTargetRequestId(''); setDraft({ ...emptyDraft(), scope_project_id: selectedProjectId || null }) }
  const startEdit = (entry: SemanticVocabularyEntry) => { setEditing(entry); setAliasesText(entry.aliases.join('\n')); setTargetProjectId(entry.project_id ?? selectedProjectId); setTargetRequestId(entry.request_id ?? ''); setDraft({ key: entry.key, label: entry.label, description: entry.description, target_kind: entry.target_kind, target_id: entry.target_id, scope_project_id: entry.scope_project_id, aliases: entry.aliases, enabled: entry.enabled }) }
  const updateDraft = (patch: Partial<Draft>) => setDraft((current) => current ? { ...current, ...patch } : current)
  const selectTargetKind = (next: VocabularyTargetKind) => { setTargetProjectId(selectedProjectId); setTargetRequestId(''); updateDraft({ target_kind: next, target_id: '' }) }
  const save = async () => {
    if (!draft || !draft.key.trim() || !draft.label.trim() || !draft.target_id) { setStatus({ kind: 'error', text: '고정 key·label·실제 대상을 모두 지정하세요.' }); return }
    const payload = { ...draft, aliases: aliasesText.split(/\r?\n/).map((value) => value.trim()).filter(Boolean) }
    setBusy(true); setStatus(null)
    try {
      const saved = editing ? await semanticVocabularyApi.update(editing.id, payload, editing.revision) : await semanticVocabularyApi.create(payload)
      setEntries((current) => editing ? current.map((entry) => entry.id === saved.id ? saved : entry) : [saved, ...current])
      setDraft(null); setEditing(null); setStatus({ kind: 'success', text: editing ? `기준 정의를 v${saved.revision}으로 수정했습니다.` : '기준 정의를 저장했습니다.' })
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '저장하지 못했습니다.'
      setStatus({ kind: 'error', text: message.includes('409') || message.toLocaleLowerCase().includes('revision') ? '다른 관리자가 먼저 수정했습니다. 목록을 새로고침한 뒤 다시 편집하세요.' : message })
    } finally { setBusy(false) }
  }
  const targetLabel = (id: string) => targetOptions.find((option) => option.id === id)?.label ?? id

  return <section className="semantic-vocabulary">
    <header className="semantic-vocabulary-header"><div><span className="semantic-eyebrow">SEMANTIC VOCABULARY</span><h2>기준 정의·별칭</h2><p>폴더, 프로젝트, 의뢰, 하중 경우, 결과 항목의 고정 key와 사람이 입력하는 별칭을 관리합니다.</p></div><div className="semantic-vocabulary-actions"><button className="ghost-button" type="button" onClick={() => void load()} disabled={busy}><RefreshCw className={busy ? 'spin' : ''} /> 새로고침</button>{isAdmin ? <button className="primary-button" type="button" onClick={startCreate}><Plus /> 새 기준 정의</button> : <span className="vocabulary-readonly"><Tags /> 읽기 전용</span>}</div></header>
    <div className="semantic-vocabulary-toolbar"><label>검색<input aria-label="기준 정의 검색" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="고정키, 표시명, 별칭 검색" /></label><label>종류<select aria-label="기준 정의 종류" value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}><option value="ALL">전체 종류</option>{targetKinds.map((value) => <option key={value} value={value}>{targetKindLabels[value]}</option>)}</select></label><strong>{filtered.length}개 표시 / {entries.length}개</strong></div>
    {status ? <div className={`semantic-message ${status.kind}`} role={status.kind === 'error' ? 'alert' : 'status'}>{status.kind === 'error' ? <AlertTriangle /> : <Check />}{status.text}<button type="button" aria-label="메시지 닫기" onClick={() => setStatus(null)}><X /></button></div> : null}
    <div className="semantic-vocabulary-table-wrap"><table className="semantic-vocabulary-table"><thead><tr><th>고정키 / 표시명</th><th>종류·대상</th><th>범위</th><th>별칭</th><th>상태</th><th /></tr></thead><tbody>{filtered.map((entry) => <tr key={entry.id}><td><code>{entry.key}</code><strong>{entry.label}</strong><small>{entry.description || '설명 없음'}</small></td><td><b>{targetKindLabels[entry.target_kind]}</b><span className={entry.target_available ? '' : 'vocabulary-target-missing'}>{entry.target_available ? (entry.target_label || entry.target_id) : `대상 없음 · ${entry.target_id}`}</span></td><td>{entry.scope_project_id ? projects.find((project) => project.id === entry.scope_project_id)?.name ?? entry.scope_project_id : '전역'}</td><td><div className="vocabulary-aliases">{entry.aliases.length ? entry.aliases.map((alias) => <span key={alias}>{alias}</span>) : <em>없음</em>}</div></td><td><span className={entry.enabled ? 'vocabulary-enabled' : 'vocabulary-disabled'}>{entry.enabled ? '사용' : '중지'}</span><small>v{entry.revision}</small></td><td>{isAdmin ? <button type="button" className="icon-button" aria-label={`${entry.label} 편집`} onClick={() => startEdit(entry)}><Pencil /></button> : null}</td></tr>)}{!filtered.length ? <tr><td colSpan={6} className="vocabulary-empty">조건에 맞는 기준 정의가 없습니다.</td></tr> : null}</tbody></table></div>
    {draft ? <div className="semantic-dialog-backdrop" role="presentation"><form className="semantic-dialog vocabulary-dialog" role="dialog" aria-modal="true" aria-labelledby="vocabulary-dialog-title" onSubmit={(event) => { event.preventDefault(); void save() }}><div className="semantic-card-heading"><div><span>{editing ? `REVISION ${editing.revision}` : 'NEW VOCABULARY'}</span><h2 id="vocabulary-dialog-title">{editing ? '기준 정의 수정' : '새 기준 정의'}</h2></div><button type="button" className="icon-button" aria-label="닫기" onClick={() => { setDraft(null); setEditing(null) }}><X /></button></div><p className="dialog-help">고정키와 실제 대상은 최초 생성 시 결정됩니다. 수정에서는 표시명·설명·별칭·활성 상태만 바꿀 수 있습니다.</p><div className="vocabulary-form-grid"><label>고정키<input required aria-label="고정키" value={draft.key} disabled={Boolean(editing)} onChange={(event) => updateDraft({ key: event.target.value })} /></label><label>표시명<input required aria-label="표시명" value={draft.label} onChange={(event) => updateDraft({ label: event.target.value })} /></label></div><label>설명<textarea value={draft.description} onChange={(event) => updateDraft({ description: event.target.value })} /></label><div className="vocabulary-form-grid"><label>종류<select aria-label="정의 종류" value={draft.target_kind} disabled={Boolean(editing)} onChange={(event) => selectTargetKind(event.target.value as VocabularyTargetKind)}>{targetKinds.map((value) => <option key={value} value={value}>{targetKindLabels[value]}</option>)}</select></label>{draft.target_kind === 'REQUEST' || draft.target_kind === 'LOAD_CASE' ? <label>대상 프로젝트<select aria-label="대상 프로젝트" value={targetProjectId} disabled={Boolean(editing)} onChange={(event) => { setTargetProjectId(event.target.value); setTargetRequestId(''); updateDraft({ target_id: '' }) }}><option value="">프로젝트 선택</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label> : null}</div>{draft.target_kind === 'LOAD_CASE' ? <label>대상 의뢰<select aria-label="대상 의뢰" value={targetRequestId} disabled={Boolean(editing)} onChange={(event) => { setTargetRequestId(event.target.value); updateDraft({ target_id: '' }) }}><option value="">의뢰 선택</option>{requests.map((request) => <option key={request.id} value={request.id}>{request.title}</option>)}</select></label> : null}<label>실제 대상<select required aria-label="실제 대상" value={draft.target_id} disabled={Boolean(editing)} onChange={(event) => updateDraft({ target_id: event.target.value })}><option value="">대상 선택</option>{targetOptions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select>{editing ? <small>{targetLabel(draft.target_id)}</small> : null}</label><label>프로젝트 범위<select aria-label="프로젝트 범위" value={draft.scope_project_id ?? ''} disabled={Boolean(editing)} onChange={(event) => updateDraft({ scope_project_id: event.target.value || null })}><option value="">전역</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select><small>범위가 있으면 해당 프로젝트에서 먼저 매칭하고 전역 정의로 보완합니다.</small></label><label>별칭 <span className="field-help">한 줄에 하나씩</span><textarea aria-label="별칭" value={aliasesText} onChange={(event) => setAliasesText(event.target.value)} placeholder="예: 최대 응력\npeak stress" /></label><label className="vocabulary-enabled-toggle"><input type="checkbox" checked={draft.enabled} onChange={(event) => updateDraft({ enabled: event.target.checked })} /> 매칭에 사용</label><div className="dialog-actions"><button type="button" className="ghost-button" onClick={() => { setDraft(null); setEditing(null) }}>취소</button><button type="submit" className="primary-button" disabled={busy}><Save />{busy ? '저장 중' : editing ? '수정 저장' : '기준 정의 저장'}</button></div></form></div> : null}
  </section>
}
