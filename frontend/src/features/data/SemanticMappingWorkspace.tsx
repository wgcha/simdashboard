import { FolderTab } from './SemanticFolderTab'
import { ResultsTab } from './SemanticResultsTab'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Check, ChevronRight, FileJson, FolderOpen, Gauge, LineChart as LineChartIcon, LoaderCircle, Plus, RefreshCw, Save, Tags, Upload, X } from 'lucide-react'
import {
  semanticMappingApi,
  type InspectResponse,
  type ParsedSemanticResult,
  type PreviewResponse,
  type PreviewWidget,
  type RecipeMapping,
  type SemanticBinding,
  type SemanticCatalog,
  type SemanticFormat,
  type SemanticItemDefinition,
  type SemanticItemKind,
  type SemanticRecipeDefinition,
  type SemanticTemplateDefinition,
  type SemanticWidgetDefinition,
  type SemanticWidgetType,
  type VersionedDefinition,
} from '../../shared/api/semanticMapping'
import { semanticContextApi, type ContextProject } from '../../shared/api/semanticMapping'
import { SemanticWidgetGrid } from '../../shared/components/semanticResults'
import { AliasSuggestion } from './AliasSuggestion'
import { SemanticVocabularyTab } from './SemanticVocabularyTab'
import { SemanticImpactDialog } from './SemanticImpactDialog'
import { FolderDiscoveryWorkspace } from './FolderDiscoveryWorkspace'
import { semanticImpactApi, type ImpactResponse } from '../../shared/api/semanticImpact'
import { createClientId } from '../../shared/identity/clientId'
import './SemanticMappingWorkspace.css'

const emptyRecipe = (format: SemanticFormat = 'csv'): SemanticRecipeDefinition => ({ format, delimiter: ',', encoding: 'utf-8-sig', header_row: 1, records_path: '', required_fields: [], mappings: [] })
const defaultItem = (source = ''): SemanticItemDefinition => ({ key: source.toLowerCase().replace(/[^a-z0-9]+/gi, '_').replace(/^_|_$/g, '') || 'result_item', label: source || '새 결과 항목', kind: 'scalar', data_type: 'FLOAT', unit: '', dimensions: [] })
const defaultWidget = (itemId = ''): SemanticWidgetDefinition => ({ id: `widget-${createClientId()}`, type: 'kpi', title: '결과 값', item_ids: itemId ? [itemId] : [], filters: {}, decimals: 2 })
const definitionVersion = (definition: { version?: number; latest_version?: number }) => definition.version ?? definition.latest_version ?? 1

type Tab = 'recipe' | 'discovery' | 'folder' | 'results' | 'vocabulary'
type StatusMessage = { kind: 'success' | 'error' | 'info'; text: string }

export function SemanticMappingWorkspace({ onLegacy, isVocabularyAdmin = false, selectedProjectId = '' }: { onLegacy: () => void; isVocabularyAdmin?: boolean; selectedProjectId?: string }) {
  const [catalog, setCatalog] = useState<SemanticCatalog>({ items: [], recipes: [], templates: [], bindings: [] })
  const [tab, setTab] = useState<Tab>('recipe')
  const [sample, setSample] = useState<File | null>(null)
  const [inspect, setInspect] = useState<InspectResponse | null>(null)
  const [recipe, setRecipe] = useState<SemanticRecipeDefinition>(emptyRecipe())
  const [recipeName, setRecipeName] = useState('결과 CSV 매핑 v1')
  const [recipeId, setRecipeId] = useState('')
  const [recipeVersion, setRecipeVersion] = useState<number | undefined>()
  const [template, setTemplate] = useState<SemanticTemplateDefinition>({ widgets: [] })
  const [templateName, setTemplateName] = useState('결과 요약 템플릿 v1')
  const [templateId, setTemplateId] = useState('')
  const [templateVersion, setTemplateVersion] = useState<number | undefined>()
  const [itemDialog, setItemDialog] = useState<{ mappingIndex: number; definition: SemanticItemDefinition } | null>(null)
  const [preview, setPreview] = useState<PreviewResponse | null>(null)
  const [message, setMessage] = useState<StatusMessage | null>(null)
  const [busy, setBusy] = useState('')
  const [scopeProjects, setScopeProjects] = useState<ContextProject[]>([])
  const [vocabularyScopeProjectId, setVocabularyScopeProjectId] = useState(selectedProjectId)
  const [impact, setImpact] = useState<ImpactResponse | null>(null)
  const [impactPayload, setImpactPayload] = useState<Parameters<typeof semanticMappingApi.activateBundle>[0] | null>(null)
  const impactGeneration = useRef(0)

  const loadCatalog = useCallback(async () => {
    setBusy('catalog')
    try { setCatalog(await semanticMappingApi.catalog()) } catch (reason) { setMessage({ kind: 'error', text: errorText(reason, '카탈로그를 불러오지 못했습니다.') }) } finally { setBusy('') }
  }, [])
  useEffect(() => { void loadCatalog() }, [loadCatalog])
  useEffect(() => { let active = true; semanticContextApi.projects().then((items) => { if (active) setScopeProjects(items) }).catch(() => undefined); return () => { active = false } }, [])
  useEffect(() => { setVocabularyScopeProjectId(selectedProjectId) }, [selectedProjectId])
  useEffect(() => { setPreview(null); setImpact(null); setImpactPayload(null); impactGeneration.current += 1 }, [recipe, template])
  useEffect(() => { impactGeneration.current += 1; setImpact(null); setImpactPayload(null) }, [catalog])

  const exportDefinitions = async () => {
    setBusy('export')
    try {
      const payload = await semanticMappingApi.exportDefinitions()
      const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }))
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'semantic-definitions-draft.json'; anchor.click(); URL.revokeObjectURL(url)
      setMessage({ kind: 'success', text: '결과 항목·레시피·템플릿을 초안 JSON으로 내보냈습니다.' })
    } catch (reason) { setMessage({ kind: 'error', text: errorText(reason, '정의 내보내기에 실패했습니다.') }) } finally { setBusy('') }
  }
  const importDefinitions = async (file: File | null) => {
    if (!file) return
    setBusy('import-definitions')
    try { const payload = JSON.parse(await file.text()) as unknown; const result = await semanticMappingApi.importDefinitions(payload); await loadCatalog(); setMessage({ kind: 'success', text: `${result.created.length}개 정의를 초안으로 반입했습니다. 참조를 확인한 뒤 활성화하세요.` }) } catch (reason) { setMessage({ kind: 'error', text: errorText(reason, '정의 JSON 반입에 실패했습니다.') }) } finally { setBusy('') }
  }

  const selectSample = async (file: File | null) => {
    setSample(file); setInspect(null); setPreview(null)
    if (!file) return
    setBusy('inspect'); setMessage(null)
    try {
      const inspected = await semanticMappingApi.inspect(file)
      setInspect(inspected)
      const fields = inspected.fields
      setRecipe((current) => recipeId ? current : ({ ...current, format: inspected.format, required_fields: [], mappings: fields.slice(0, 6).map((field) => ({ result_item_id: '', source: field, dimensions: {}, missing: 'error', aggregate: 'none' })) }))
      setMessage({ kind: 'success', text: `${file.name}에서 ${inspected.fields.length}개 필드와 ${inspected.rows.length}개 샘플 행을 확인했습니다.` })
    } catch (reason) { setMessage({ kind: 'error', text: errorText(reason, '샘플 구조를 확인하지 못했습니다.') }) } finally { setBusy('') }
  }

  const updateMapping = (index: number, patch: Partial<RecipeMapping>) => setRecipe((current) => ({ ...current, mappings: current.mappings.map((mapping, row) => row === index ? { ...mapping, ...patch } : mapping) }))
  const addMapping = () => setRecipe((current) => ({ ...current, mappings: [...current.mappings, { result_item_id: '', source: inspect?.fields[0] ?? '', dimensions: {}, missing: 'error', aggregate: 'none' }] }))
  const removeMapping = (index: number) => setRecipe((current) => ({ ...current, mappings: current.mappings.filter((_, row) => row !== index) }))

  const createItem = (mappingIndex: number) => setItemDialog({ mappingIndex, definition: defaultItem(recipe.mappings[mappingIndex]?.source ?? '') })
  const saveItem = async (definition: SemanticItemDefinition) => {
    if (!itemDialog) return
    const mappingIndex = itemDialog.mappingIndex
    try {
      const item = await semanticMappingApi.createItem(definition)
      setCatalog((current) => ({ ...current, items: [item, ...current.items] }))
      updateMapping(mappingIndex, { result_item_id: item.id })
      setItemDialog(null)
      setMessage({ kind: 'success', text: `결과 항목 ${definition.label}을 저장했습니다.` })
    } catch (reason) { setMessage({ kind: 'error', text: errorText(reason, '결과 항목을 저장하지 못했습니다.') }) } finally { setBusy('') }
  }

  const saveRecipe = async () => {
    if (!recipe.mappings.length || recipe.mappings.some((mapping) => !mapping.result_item_id || !mapping.source)) { setMessage({ kind: 'error', text: '모든 매핑에 원본 필드와 결과 항목을 지정하세요.' }); return }
    setBusy('save-recipe'); setMessage(null)
    try {
      const saved = await semanticMappingApi.saveRecipe({ id: recipeId || undefined, name: recipeName, definition: recipe, expected_version: recipeVersion }, sample ?? undefined)
      setRecipeId(saved.id); setRecipeVersion(definitionVersion(saved))
      setCatalog((current) => { const previous = current.recipes.find((item) => item.id === saved.id); return { ...current, recipes: [{ ...previous, ...saved, active_version: saved.active_version ?? previous?.active_version ?? null }, ...current.recipes.filter((item) => item.id !== saved.id)] } })
      setMessage({ kind: 'success', text: `레시피 ${recipeName} v${definitionVersion(saved)}을 저장했습니다. 내용과 단위 검증 후 활성화하세요.` })
    } catch (reason) { setMessage({ kind: 'error', text: errorText(reason, '레시피 저장에 실패했습니다. 다른 편집자가 새 버전을 저장했는지 확인하세요.') }) } finally { setBusy('') }
  }
  const saveTemplate = async () => {
    if (!template.widgets.length || template.widgets.some((widget) => widget.type === 'scatter' ? !widget.x_item_id || !widget.y_item_id : !widget.item_ids.length)) { setMessage({ kind: 'error', text: '템플릿 위젯마다 호환되는 결과 항목 입력을 연결하세요.' }); return }
    setBusy('save-template'); setMessage(null)
    try {
      const saved = await semanticMappingApi.saveTemplate({ id: templateId || undefined, name: templateName, definition: template, expected_version: templateVersion })
      setTemplateId(saved.id); setTemplateVersion(definitionVersion(saved))
      setCatalog((current) => { const previous = current.templates.find((item) => item.id === saved.id); return { ...current, templates: [{ ...previous, ...saved, active_version: saved.active_version ?? previous?.active_version ?? null }, ...current.templates.filter((item) => item.id !== saved.id)] } })
      setMessage({ kind: 'success', text: `템플릿 ${templateName} v${definitionVersion(saved)}을 저장했습니다.` })
    } catch (reason) { setMessage({ kind: 'error', text: errorText(reason, '템플릿 저장에 실패했습니다.') }) } finally { setBusy('') }
  }
  const selectRecipe = (id: string) => {
    if (!id) { setRecipeId(''); setRecipeVersion(undefined); setRecipeName('새 레시피'); setRecipe(emptyRecipe()); setPreview(null); return }
    const selected = catalog.recipes.find((item) => item.id === id)
    if (!selected) return
    setRecipeId(selected.id); setRecipeVersion(definitionVersion(selected)); setRecipeName(selected.name ?? selected.id); setRecipe({ ...emptyRecipe(), ...selected.definition, mappings: selected.definition.mappings.map((mapping) => ({ ...mapping, dimensions: mapping.dimensions ?? {}, missing: mapping.missing ?? 'error', aggregate: mapping.aggregate ?? 'none' })) })
    setPreview(null); setMessage({ kind: 'info', text: `레시피 ${selected.name ?? selected.id} v${definitionVersion(selected)}을 편집 중입니다.` })
  }
  const selectTemplate = (id: string) => {
    if (!id) { setTemplateId(''); setTemplateVersion(undefined); setTemplateName('새 템플릿'); setTemplate({ widgets: [] }); setPreview(null); return }
    const selected = catalog.templates.find((item) => item.id === id)
    if (!selected) return
    setTemplateId(selected.id); setTemplateVersion(definitionVersion(selected)); setTemplateName(selected.name ?? selected.id); setTemplate(selected.definition); setMessage({ kind: 'info', text: `템플릿 ${selected.name ?? selected.id} v${definitionVersion(selected)}을 편집 중입니다.` })
  }
  const bundlePayload = () => {
    if (!recipeId || !recipeVersion || !templateId || !templateVersion) { setMessage({ kind: 'error', text: '레시피와 템플릿을 각각 저장한 뒤 함께 활성화하세요.' }); return }
    if (!preview || preview.widgets.some((widget) => widget.status !== 'READY')) { setMessage({ kind: 'error', text: '샘플 미리보기에서 모든 위젯이 READY인지 확인한 뒤 활성화하세요.' }); return }
    const savedRecipe = catalog.recipes.find((item) => item.id === recipeId)
    const savedTemplate = catalog.templates.find((item) => item.id === templateId)
    if (JSON.stringify(savedRecipe?.definition) !== JSON.stringify(recipe) || JSON.stringify(savedTemplate?.definition) !== JSON.stringify(template)) { setMessage({ kind: 'error', text: '수정한 레시피와 템플릿을 먼저 저장하세요. 활성화는 저장된 버전에 적용됩니다.' }); return }
    return { recipe_id: recipeId, recipe_version: recipeVersion, template_id: templateId, template_version: templateVersion, expected_recipe_active_version: savedRecipe?.active_version ?? null, expected_template_active_version: savedTemplate?.active_version ?? null }
  }
  const activateBundle = async () => {
    const payload = bundlePayload()
    if (!payload) return
    const generation = ++impactGeneration.current
    setImpact(null); setImpactPayload(payload); setBusy('impact-bundle')
    try {
      const result = await semanticImpactApi.previewBundle(payload)
      if (generation === impactGeneration.current) setImpact(result)
    } catch (reason) { if (generation === impactGeneration.current) setMessage({ kind: 'error', text: errorText(reason, '활성화 영향 미리보기를 불러오지 못했습니다.') }) } finally { if (generation === impactGeneration.current) setBusy('') }
  }
  const confirmBundleActivation = async () => {
    const payload = bundlePayload()
    if (!payload || !impactPayload || JSON.stringify(payload) !== JSON.stringify(impactPayload)) { setImpact(null); setImpactPayload(null); setMessage({ kind: 'error', text: '영향 미리보기 이후 레시피·템플릿 또는 활성 포인터가 바뀌었습니다. 최신 상태를 다시 조회하세요.' }); return }
    if (!impact?.activation_allowed || impact.validation.status !== 'READY') return
    const generation = ++impactGeneration.current
    setBusy('activate-bundle')
    try {
      await semanticMappingApi.activateBundle(impactPayload)
      if (generation !== impactGeneration.current) return
      setImpact(null); setImpactPayload(null); await loadCatalog(); setMessage({ kind: 'success', text: `레시피 v${impactPayload.recipe_version}와 템플릿 v${impactPayload.template_version}을 함께 활성화했습니다.` })
    } catch (reason) { if (generation === impactGeneration.current) setMessage({ kind: 'error', text: errorText(reason, '묶음 활성화에 실패했습니다. 다른 편집자의 활성 버전 변경 여부를 확인하세요.') }) } finally { if (generation === impactGeneration.current) setBusy('') }
  }
  const runPreview = async () => {
    if (!sample) { setMessage({ kind: 'error', text: '먼저 샘플 CSV 또는 JSON을 선택하세요.' }); return }
    setBusy('preview'); setMessage(null)
    const activeTemplate = template.widgets.length ? template : { widgets: recipe.mappings.filter((mapping) => mapping.result_item_id).map((mapping) => defaultWidget(mapping.result_item_id)) }
    setTemplate(activeTemplate)
    try { setPreview(await semanticMappingApi.preview(sample, recipe, activeTemplate)); setMessage({ kind: 'success', text: '파싱 결과와 위젯 입력을 함께 검증했습니다.' }) } catch (reason) { setMessage({ kind: 'error', text: errorText(reason, '미리보기에 실패했습니다.') }) } finally { setBusy('') }
  }

  return <section className="semantic-page">
    <header className="semantic-hero"><div><span className="semantic-eyebrow">SEMANTIC RESULT MAPPING</span><h1>결과 의미 연결</h1><p>샘플 필드의 의미와 단위를 한 번 정의하면 다음 결과 파일과 위젯에 같은 결과 항목을 재사용합니다.</p></div><div className="semantic-hero-actions"><button className="ghost-button" onClick={onLegacy}><ChevronRight /> 기존 폴더 스키마</button><button className="ghost-button" onClick={() => void loadCatalog()} disabled={busy === 'catalog'}><RefreshCw className={busy === 'catalog' ? 'spin' : ''} /> 카탈로그 새로고침</button><button className="ghost-button" onClick={() => void exportDefinitions()} disabled={busy === 'export'}><FileJson /> 초안 내보내기</button><label className="ghost-button"><Upload /> 초안 반입<input className="semantic-hidden-file" type="file" accept="application/json,.json" onChange={(event) => void importDefinitions(event.target.files?.[0] ?? null)} /></label></div></header>
    <nav className="semantic-tabs" aria-label="의미 연결 단계"><button className={tab === 'recipe' ? 'active' : ''} onClick={() => setTab('recipe')}><FileJson /> 1. 샘플·레시피</button><button className={tab === 'discovery' ? 'active' : ''} onClick={() => setTab('discovery')}><FolderOpen /> 폴더 조사·업무 생성</button><button className={tab === 'folder' ? 'active' : ''} onClick={() => setTab('folder')}><FolderOpen /> 2. 폴더 연결</button><button className={tab === 'results' ? 'active' : ''} onClick={() => setTab('results')}><Gauge /> 3. 결과 조회</button><button className={tab === 'vocabulary' ? 'active' : ''} onClick={() => setTab('vocabulary')}><Tags /> 기준 정의·별칭</button></nav>
    {message ? <div className={`semantic-message ${message.kind}`} role={message.kind === 'error' ? 'alert' : 'status'}>{message.kind === 'error' ? <AlertTriangle /> : <Check />}{message.text}<button aria-label="메시지 닫기" onClick={() => setMessage(null)}><X /></button></div> : null}
    {tab === 'recipe' ? <RecipeTab sample={sample} selectSample={selectSample} inspect={inspect} recipe={recipe} setRecipe={setRecipe} catalog={catalog} updateMapping={updateMapping} addMapping={addMapping} removeMapping={removeMapping} createItem={createItem} busy={busy} recipeName={recipeName} setRecipeName={setRecipeName} saveRecipe={saveRecipe} activateBundle={() => void activateBundle()} template={template} setTemplate={setTemplate} templateName={templateName} setTemplateName={setTemplateName} saveTemplate={saveTemplate} preview={preview} runPreview={runPreview} recipeId={recipeId} templateId={templateId} selectRecipe={selectRecipe} selectTemplate={selectTemplate} vocabularyScopeProjectId={vocabularyScopeProjectId} setVocabularyScopeProjectId={setVocabularyScopeProjectId} scopeProjects={scopeProjects} /> : tab === 'discovery' ? <FolderDiscoveryWorkspace onComplete={() => { void loadCatalog() }} onOpenFolder={() => setTab('folder')} /> : tab === 'folder' ? <FolderTab catalog={catalog} onMessage={setMessage} busy={busy} setBusy={setBusy} onCatalog={setCatalog} onOpenDiscovery={() => setTab('discovery')} /> : tab === 'results' ? <ResultsTab catalog={catalog} onMessage={setMessage} /> : <SemanticVocabularyTab isAdmin={isVocabularyAdmin} selectedProjectId={selectedProjectId} />}
    {itemDialog ? <ItemDefinitionDialog value={itemDialog.definition} busy={busy === 'item-save'} onCancel={() => setItemDialog(null)} onSave={(definition) => { setBusy('item-save'); void saveItem(definition) }} /> : null}
    {impact || busy === 'impact-bundle' ? <SemanticImpactDialog impact={impact} loading={busy === 'impact-bundle'} confirming={busy === 'activate-bundle'} onClose={() => { impactGeneration.current += 1; setImpact(null); setImpactPayload(null); setBusy('') }} onConfirm={() => void confirmBundleActivation()} /> : null}
  </section>
}

function ItemDefinitionDialog({ value, busy, onCancel, onSave }: { value: SemanticItemDefinition; busy: boolean; onCancel: () => void; onSave: (value: SemanticItemDefinition) => void }) {
  const [draft, setDraft] = useState(value)
  const update = (patch: Partial<SemanticItemDefinition>) => setDraft((current) => ({ ...current, ...patch }))
  return <div className="semantic-dialog-backdrop" role="presentation"><form className="semantic-dialog" role="dialog" aria-modal="true" aria-labelledby="item-dialog-title" onSubmit={(event) => { event.preventDefault(); onSave(draft) }}><div className="semantic-card-heading"><div><span>NEW RESULT ITEM</span><h2 id="item-dialog-title">결과 항목 정의</h2></div><button type="button" className="icon-button" aria-label="닫기" onClick={onCancel}><X /></button></div><p className="dialog-help">고정 항목 키는 레시피와 위젯이 참조하는 식별자입니다. 표시명은 나중에 바꿀 수 있습니다.</p><label>표시명<input autoFocus required value={draft.label} onChange={(event) => update({ label: event.target.value })} /></label><label>고정 항목 키<input required value={draft.key} onChange={(event) => update({ key: event.target.value })} /></label><div className="dialog-grid"><label>종류<select value={draft.kind} onChange={(event) => update({ kind: event.target.value as SemanticItemKind })}><option value="scalar">scalar</option><option value="curve">curve</option></select></label><label>자료형<select value={draft.data_type} onChange={(event) => update({ data_type: event.target.value as SemanticItemDefinition['data_type'] })}><option value="FLOAT">FLOAT</option><option value="INTEGER">INTEGER</option><option value="TEXT">TEXT</option><option value="BOOLEAN">BOOLEAN</option></select></label></div><label>기준 단위<input value={draft.unit} placeholder="예: MPa, s, - (선택)" onChange={(event) => update({ unit: event.target.value })} /></label><label>측정 차원<input value={draft.dimensions.join(', ')} placeholder="예: node, component" onChange={(event) => update({ dimensions: event.target.value.split(',').map((item) => item.trim()).filter(Boolean) })} /><small>차원이 있으면 각 매핑에서 해당 위치를 읽을 원본 필드를 연결합니다.</small></label><div className="dialog-actions"><button type="button" className="ghost-button" onClick={onCancel}>취소</button><button type="submit" className="primary-button" disabled={busy}><Save /> {busy ? '저장 중' : '결과 항목 저장'}</button></div></form></div>
}

function RecipeTabContent(props: {
  sample: File | null; selectSample: (file: File | null) => void; inspect: InspectResponse | null; recipe: SemanticRecipeDefinition; setRecipe: (recipe: SemanticRecipeDefinition) => void; catalog: SemanticCatalog; updateMapping: (index: number, patch: Partial<RecipeMapping>) => void; addMapping: () => void; removeMapping: (index: number) => void; createItem: (index: number) => void; busy: string; recipeName: string; setRecipeName: (name: string) => void; saveRecipe: () => void; activateBundle: () => void; template: SemanticTemplateDefinition; setTemplate: (template: SemanticTemplateDefinition) => void; templateName: string; setTemplateName: (name: string) => void; saveTemplate: () => void; preview: PreviewResponse | null; runPreview: () => void; recipeId: string; templateId: string; selectRecipe: (id: string) => void; selectTemplate: (id: string) => void; vocabularyScopeProjectId: string; setVocabularyScopeProjectId: (value: string) => void; scopeProjects: ContextProject[]
}) {
  const { sample, selectSample, inspect, recipe, setRecipe, catalog, updateMapping, addMapping, removeMapping, createItem, busy, recipeName, setRecipeName, saveRecipe, activateBundle, template, setTemplate, templateName, setTemplateName, saveTemplate, preview, runPreview, recipeId, templateId, selectRecipe, selectTemplate, vocabularyScopeProjectId, setVocabularyScopeProjectId, scopeProjects } = props
  return <div className="semantic-flow">
    <div className="semantic-card sample-card"><div className="semantic-card-heading"><div><span>STEP 01 · INSPECT</span><h2>샘플 파일 업로드</h2></div><Upload /></div><label className="file-drop"><input type="file" accept=".csv,.json" onChange={(event) => void selectSample(event.target.files?.[0] ?? null)} /><Upload /><strong>{sample ? sample.name : 'CSV 또는 JSON을 선택하세요'}</strong><small>확장자보다 실제 내용과 필수 필드를 확인합니다.</small></label>{inspect ? <div className="inspect-summary"><span><b>{inspect.format.toUpperCase()}</b> 형식</span><span><b>{inspect.fields.length}</b> 필드</span><span><b>{inspect.rows.length}</b> 샘플 행</span></div> : null}<label className="vocabulary-scope-select">별칭 검색 프로젝트 (전역 기본)<select aria-label="별칭 검색 프로젝트" value={vocabularyScopeProjectId} onChange={(event) => setVocabularyScopeProjectId(event.target.value)}><option value="">전역만</option>{scopeProjects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select><small>RESULT_ITEM 후보는 이 프로젝트 범위부터 찾습니다. 레시피에는 범위가 저장되지 않습니다.</small></label></div>
    <div className="semantic-card"><div className="semantic-card-heading"><div><span>STEP 02 · DEFINE</span><h2>필드 → 결과 항목 · 단위</h2></div><button className="ghost-button" onClick={addMapping}><Plus /> 매핑 추가</button></div><div className="semantic-inline-fields"><label>파일 형식<select value={recipe.format} onChange={(event) => setRecipe({ ...recipe, format: event.target.value as SemanticFormat })}><option value="csv">CSV</option><option value="json">JSON</option></select></label><label>구분자<input value={recipe.delimiter} maxLength={1} onChange={(event) => setRecipe({ ...recipe, delimiter: event.target.value })} /></label><label>JSON records 경로<input value={recipe.records_path} placeholder="예: results.rows" onChange={(event) => setRecipe({ ...recipe, records_path: event.target.value })} /></label><label>인코딩<select value={recipe.encoding} onChange={(event) => setRecipe({ ...recipe, encoding: event.target.value })}><option value="utf-8-sig">UTF-8 BOM</option><option value="utf-8">UTF-8</option><option value="cp949">CP949</option><option value="utf-16">UTF-16</option></select></label><label>헤더 행<input type="number" min={1} max={100} value={recipe.header_row} onChange={(event) => setRecipe({ ...recipe, header_row: Number(event.target.value) })} /></label></div><div className="mapping-table"><div className="mapping-row mapping-head"><span>원본 필드</span><span>결과 항목</span><span>변환 단위</span><span>측정 위치 필드</span><span>결측·집계</span><span /></div>{recipe.mappings.length ? recipe.mappings.map((mapping, index) => <div className="mapping-row" key={index}><div className="mapping-source-with-alias"><input aria-label="원본 필드" list="semantic-source-fields" value={mapping.source} onChange={(event) => updateMapping(index, { source: event.target.value })} placeholder="필드 경로" /><AliasSuggestion term={mapping.source} targetKinds={['RESULT_ITEM']} scopeProjectId={vocabularyScopeProjectId || undefined} onSelect={(entry) => updateMapping(index, { result_item_id: entry.target_id })} /></div><div className="mapping-item-select"><select aria-label="결과 항목" value={mapping.result_item_id} onChange={(event) => updateMapping(index, { result_item_id: event.target.value })}><option value="">결과 항목 선택</option>{catalog.items.map((item) => <option key={item.id} value={item.id}>{item.definition.label} · {item.definition.unit || '단위 없음'} · v{definitionVersion(item)}</option>)}</select><button title="새 결과 항목" onClick={() => void createItem(index)} disabled={busy === `item-${index}`}><Plus /></button></div><div className="unit-pair"><input aria-label="원본 단위" placeholder="원본 단위" value={mapping.source_unit ?? ''} onChange={(event) => updateMapping(index, { source_unit: event.target.value || undefined })} /><span>→</span><input aria-label="대상 단위" placeholder="대상 단위" value={catalog.items.find((item) => item.id === mapping.result_item_id)?.definition.unit ?? ''} readOnly /></div><DimensionFields value={mapping.dimensions} dimensions={catalog.items.find((item) => item.id === mapping.result_item_id)?.definition.dimensions ?? []} onChange={(dimensions) => updateMapping(index, { dimensions })} /><div className="mapping-policies"><select value={mapping.missing} onChange={(event) => updateMapping(index, { missing: event.target.value as 'error' | 'skip' })}><option value="error">누락이면 오류</option><option value="skip">누락 건너뜀</option></select><select value={mapping.aggregate ?? 'none'} onChange={(event) => updateMapping(index, { aggregate: event.target.value as RecipeMapping['aggregate'] })}><option value="none">집계 없음</option><option value="max">최대</option><option value="min">최소</option><option value="mean">평균</option></select></div><button className="icon-button danger-icon" aria-label="매핑 삭제" onClick={() => removeMapping(index)}><X /></button></div>) : <div className="empty-inline">샘플을 올리면 필드 매핑을 시작할 수 있습니다.</div>}</div></div>
    <div className="semantic-card"><div className="semantic-card-heading"><div><span>STEP 03 · WIDGET CONTRACT</span><h2>위젯 입력 역할</h2></div><button className="ghost-button" onClick={() => setTemplate({ ...template, widgets: [...template.widgets, defaultWidget(recipe.mappings.find((mapping) => mapping.result_item_id)?.result_item_id)] })}><Plus /> 위젯 추가</button></div><div className="widget-config-list">{template.widgets.length ? template.widgets.map((widget, index) => <WidgetEditor key={widget.id} widget={widget} items={catalog.items} onChange={(next) => setTemplate({ ...template, widgets: template.widgets.map((item, row) => row === index ? next : item) })} onRemove={() => setTemplate({ ...template, widgets: template.widgets.filter((_, row) => row !== index) })} />) : <div className="empty-inline">위젯을 추가하면 결과 항목을 카드·표·선 그래프·산점도 입력 역할에 연결합니다.</div>}</div><div className="definition-actions"><label>레시피 이름<input value={recipeName} onChange={(event) => setRecipeName(event.target.value)} /></label><label>템플릿 이름<input value={templateName} onChange={(event) => setTemplateName(event.target.value)} /></label><button className="primary-button" onClick={saveRecipe} disabled={busy === 'save-recipe'}><Save /> {busy === 'save-recipe' ? '저장 중' : recipeId ? '레시피 새 버전 저장' : '레시피 저장'}</button><button className="primary-button" onClick={saveTemplate} disabled={busy === 'save-template'}><Save /> {busy === 'save-template' ? '저장 중' : templateId ? '템플릿 새 버전 저장' : '템플릿 저장'}</button><button className="secondary-button" onClick={activateBundle} disabled={!recipeId || !templateId || busy.startsWith('activate') || busy === 'impact-bundle'}><Check /> 함께 활성화 · 활성화 영향 미리보기</button></div></div>
    <div className="semantic-card preview-card"><div className="semantic-card-heading"><div><span>STEP 04 · VERIFY</span><h2>변환 + 위젯 통합 미리보기</h2></div><button className="primary-button" onClick={runPreview} disabled={!sample || busy === 'preview'}>{busy === 'preview' ? <LoaderCircle className="spin" /> : <RefreshCw />} 미리보기 실행</button></div>{preview ? <PreviewPanel parsed={preview.parsed} widgets={preview.widgets} /> : <div className="empty-preview"><LineChartIcon /><p>저장 전에도 현재 레시피로 실행할 수 있습니다. 결과 항목이 없거나 여러 값이 연결되면 원인을 표시합니다.</p></div>}</div>
  </div>
}

type RecipeTabProps = Parameters<typeof RecipeTabContent>[0]

function DimensionFields({ value, dimensions, onChange }: { value: Record<string, string>; dimensions: string[]; onChange: (value: Record<string, string>) => void }) {
  if (!dimensions.length) return <small>단일 관측값</small>
  return <div>{dimensions.map((dimension) => <label key={dimension}>{dimension}<input aria-label={`${dimension} 원본 필드`} list="semantic-source-fields" value={value[dimension] ?? ''} onChange={(event) => onChange({ ...value, [dimension]: event.target.value })} /></label>)}</div>
}

function RecipeTab(props: RecipeTabProps) {
  return <><SavedDefinitionSelectors props={props} /><RecipeTabContent {...props} /><div className="semantic-card semantic-advanced-mappings"><div className="semantic-card-heading"><div><span>ADVANCED FIELD ROLES</span><h2>원본 경로·곡선 축·series 입력</h2></div><LineChartIcon /></div>{props.recipe.mappings.map((mapping, index) => { const item = props.catalog.items.find((candidate) => candidate.id === mapping.result_item_id); return <div className="curve-role-row" key={index}><strong>{item?.definition.label ?? `매핑 ${index + 1}`}</strong><label>값 원본 경로<input list="semantic-source-fields" value={mapping.source} onChange={(event) => props.updateMapping(index, { source: event.target.value })} /></label>{item?.definition.kind === 'curve' ? <><label>X 원본 필드<input list="semantic-source-fields" value={mapping.x_source ?? ''} onChange={(event) => props.updateMapping(index, { x_source: event.target.value })} /></label><label>X 단위<input value={mapping.x_unit ?? ''} onChange={(event) => props.updateMapping(index, { x_unit: event.target.value || undefined })} /></label><label>표시 X 단위<input value={mapping.target_x_unit ?? ''} onChange={(event) => props.updateMapping(index, { target_x_unit: event.target.value || undefined })} /></label><label>series 필드<input list="semantic-source-fields" value={mapping.series_source ?? ''} onChange={(event) => props.updateMapping(index, { series_source: event.target.value || undefined })} /></label></> : null}</div> })}<datalist id="semantic-source-fields">{(props.inspect?.fields ?? []).map((field) => <option key={field} value={field} />)}</datalist></div></>
}

function SavedDefinitionSelectors({ props }: { props: RecipeTabProps }) {
  return <div className="semantic-card saved-definition-selectors"><label>저장된 레시피<select aria-label="저장된 레시피" value={props.recipeId} onChange={(event) => props.selectRecipe(event.target.value)}><option value="">새 레시피</option>{props.catalog.recipes.map((item) => <option key={item.id} value={item.id}>{item.name ?? item.id} · v{definitionVersion(item)}</option>)}</select></label><label>저장된 템플릿<select aria-label="저장된 템플릿" value={props.templateId} onChange={(event) => props.selectTemplate(event.target.value)}><option value="">새 템플릿</option>{props.catalog.templates.map((item) => <option key={item.id} value={item.id}>{item.name ?? item.id} · v{definitionVersion(item)}</option>)}</select></label></div>
}

function WidgetEditor({ widget, items, onChange, onRemove }: { widget: SemanticWidgetDefinition; items: VersionedDefinition<SemanticItemDefinition>[]; onChange: (next: SemanticWidgetDefinition) => void; onRemove: () => void }) {
  const [filterText, setFilterText] = useState(JSON.stringify(widget.filters ?? {}))
  const [filterError, setFilterError] = useState('')
  const itemOptions = items.map((item) => <option key={item.id} value={item.id}>{item.definition.label} ({item.definition.unit || '-'})</option>)
  const updateFilters = (value: string) => { setFilterText(value); try { const parsed = JSON.parse(value) as unknown; if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || Object.values(parsed).some((item) => typeof item !== 'string')) throw new Error('차원 필터는 문자열 값의 JSON 객체여야 합니다.'); const selectedIds = widget.type === 'scatter' ? [widget.x_item_id, widget.y_item_id] : widget.item_ids; const dimensions = new Set(items.filter((item) => selectedIds.includes(item.id)).flatMap((item) => item.definition.dimensions)); const unknown = Object.keys(parsed as Record<string, string>).filter((key) => !dimensions.has(key)); if (unknown.length) throw new Error(`정의되지 않은 차원 필터입니다: ${unknown.join(', ')}`); setFilterError(''); onChange({ ...widget, filters: parsed as Record<string, string> }) } catch (reason) { setFilterError(reason instanceof Error ? reason.message : '필터 JSON이 올바르지 않습니다.') } }
  const setType = (type: SemanticWidgetType) => onChange({ ...widget, type, item_ids: type === 'scatter' ? [] : widget.item_ids })
  return <div className="widget-editor"><input aria-label="위젯 제목" value={widget.title} onChange={(event) => onChange({ ...widget, title: event.target.value })} /><select aria-label="위젯 종류" value={widget.type} onChange={(event) => setType(event.target.value as SemanticWidgetType)}>{(['kpi', 'gauge', 'table', 'bar', 'line', 'scatter'] as SemanticWidgetType[]).map((type) => <option key={type} value={type}>{type}</option>)}</select>{widget.type === 'scatter' ? <><select aria-label="산점도 X 항목" value={widget.x_item_id ?? ''} onChange={(event) => onChange({ ...widget, x_item_id: event.target.value })}><option value="">X 항목 선택</option>{itemOptions}</select><select aria-label="산점도 Y 항목" value={widget.y_item_id ?? ''} onChange={(event) => onChange({ ...widget, y_item_id: event.target.value })}><option value="">Y 항목 선택</option>{itemOptions}</select><input aria-label="X 표시 단위" placeholder="X 단위" value={widget.x_display_unit ?? ''} onChange={(event) => onChange({ ...widget, x_display_unit: event.target.value || undefined })} /><input aria-label="Y 표시 단위" placeholder="Y 단위" value={widget.y_display_unit ?? ''} onChange={(event) => onChange({ ...widget, y_display_unit: event.target.value || undefined })} /></> : <select aria-label="위젯 결과 항목" multiple value={widget.item_ids} onChange={(event) => onChange({ ...widget, item_ids: Array.from(event.target.selectedOptions, (option) => option.value) })}>{itemOptions}</select>}<input type="number" min={0} max={8} aria-label="표시 자릿수" value={widget.decimals} onChange={(event) => onChange({ ...widget, decimals: Number(event.target.value) })} /><input aria-label="표시 단위" placeholder="표시 단위" value={widget.display_unit ?? ''} onChange={(event) => onChange({ ...widget, display_unit: event.target.value || undefined })} />{widget.type === 'gauge' ? <label className="widget-threshold">기준값(항목 기준 단위)<input type="number" value={widget.threshold ?? ''} onChange={(event) => onChange({ ...widget, threshold: event.target.value === '' ? undefined : Number(event.target.value) })} /></label> : null}<input aria-label="차원 필터 JSON" className={filterError ? 'field-invalid' : ''} value={filterText} onChange={(event) => updateFilters(event.target.value)} />{filterError ? <small className="field-error">{filterError}</small> : null}<button className="icon-button danger-icon" onClick={onRemove} aria-label="위젯 삭제"><X /></button></div>
}

function PreviewPanel({ parsed, widgets }: { parsed: ParsedSemanticResult; widgets: PreviewWidget[] }) {
  return <div className="preview-panel"><div className="parsed-summary"><span><b>{parsed.scalars?.length ?? 0}</b> scalar</span><span><b>{parsed.curves?.length ?? 0}</b> curve</span><span><b>{parsed.media?.length ?? 0}</b> media</span><span><b>{parsed.observations?.length ?? 0}</b> observations</span></div>{parsed.warnings?.length ? <div className="warning-list">{parsed.warnings.map((warning) => <span key={warning}><AlertTriangle />{warning}</span>)}</div> : null}<SemanticWidgetGrid widgets={widgets} /></div>
}


function errorText(reason: unknown, fallback: string) { return reason instanceof Error ? reason.message : fallback }
