import { SemanticRecipeValidation } from './SemanticRecipeValidation'
import { FolderTab, type FolderConnectionPrefill } from './SemanticFolderTab'
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
  type VersionedDefinition,
} from '../../shared/api/semanticMapping'
import { semanticContextApi, type ContextProject } from '../../shared/api/semanticMapping'
import { SemanticWidgetGrid } from '../../shared/components/semanticResults'
import { AliasSuggestion } from './AliasSuggestion'
import { SemanticVocabularyTab } from './SemanticVocabularyTab'
import { SemanticImpactDialog } from './SemanticImpactDialog'
import { FolderDiscoveryWorkspace, type FolderDiscoveryTarget } from './FolderDiscoveryWorkspace'
import { semanticImpactApi, type ImpactResponse } from '../../shared/api/semanticImpact'
import { createClientId } from '../../shared/identity/clientId'
import './SemanticMappingWorkspace.css'
import { SemanticSampleInspector } from './SemanticSampleInspector'
import { SemanticWidgetEditor as WidgetEditor } from './SemanticWidgetEditor'

const emptyRecipe = (format: SemanticFormat = 'csv'): SemanticRecipeDefinition => ({ reader_version: 2, input_layout: 'csv_table', format, delimiter: ',', encoding: 'utf-8-sig', header_row: 1, records_path: '', required_fields: [], mappings: [] })
const sourceLabel = (source: string) => { const decoded = source.replace(/^#\//, '').split('/').map((part) => part.replaceAll('~1', '/').replaceAll('~0', '~')); const leaf = decoded.at(-1) ?? ''; const component = /^[012]$/.test(leaf) ? (/CG Coord/i.test(decoded.slice(0, -1).join(' ')) ? ['X', 'Y', 'Z'][Number(decoded.pop())] : `성분 ${Number(decoded.pop()) + 1}`) : ''; return `${decoded.join(' / ').replace(/\s*\([^)]*\)$/, '')}${component ? ` · ${component}` : ''}` }
const defaultItem = (source = ''): SemanticItemDefinition => ({ key: source.toLowerCase().replace(/[^a-z0-9]+/gi, '_').replace(/^_|_$/g, '') || 'result_item', label: sourceLabel(source) || '새 결과 항목', kind: 'scalar', data_type: 'FLOAT', unit: '', dimensions: [] })
const defaultWidget = (itemId = ''): SemanticWidgetDefinition => ({ id: `widget-${createClientId()}`, type: 'kpi', title: '결과 값', item_ids: itemId ? [itemId] : [], filters: {}, decimals: 2 })
const definitionVersion = (definition: { version?: number; latest_version?: number }) => definition.version ?? definition.latest_version ?? 1

type Tab = 'recipe' | 'discovery' | 'folder' | 'results' | 'vocabulary'
type StatusMessage = { kind: 'success' | 'error' | 'info'; text: string }

export function SemanticMappingWorkspace({ onLegacy, isVocabularyAdmin = false, selectedProjectId = '' }: { onLegacy: () => void; isVocabularyAdmin?: boolean; selectedProjectId?: string }) {
  const [catalog, setCatalog] = useState<SemanticCatalog>({ items: [], recipes: [], templates: [], bindings: [] })
  const [tab, setTab] = useState<Tab>('recipe')
  const [sample, setSample] = useState<File | null>(null)
  const [inspect, setInspect] = useState<InspectResponse | null>(null)
  const [sampleUploadId, setSampleUploadId] = useState('')
  const [recipe, setRecipe] = useState<SemanticRecipeDefinition>(emptyRecipe())
  const [recipeName, setRecipeName] = useState('결과 CSV 매핑 v1')
  const [recipeId, setRecipeId] = useState('')
  const [recipeVersion, setRecipeVersion] = useState<number | undefined>()
  const [template, setTemplate] = useState<SemanticTemplateDefinition>({ widgets: [] })
  const [templateName, setTemplateName] = useState('결과 요약 템플릿 v1')
  const [templateId, setTemplateId] = useState('')
  const [templateVersion, setTemplateVersion] = useState<number | undefined>()
  const [itemDialog, setItemDialog] = useState<{ mappingIndex: number; definition: SemanticItemDefinition; id?: string; expectedVersion?: number } | null>(null)
  const [removedMapping, setRemovedMapping] = useState<{ index: number; mapping: RecipeMapping; template: SemanticTemplateDefinition } | null>(null)
  const [itemUsage, setItemUsage] = useState<Record<string, unknown> | null>(null)
  const configurationGeneration = useRef(0)
  const configurationContextGeneration = useRef(0)
  const itemSaveInFlight = useRef(false)
  const configurationSaveInFlight = useRef(false)
  const itemDialogRef = useRef(itemDialog)
  itemDialogRef.current = itemDialog
  const [preview, setPreview] = useState<PreviewResponse | null>(null)
  const [message, setMessage] = useState<StatusMessage | null>(null)
  const [busy, setBusy] = useState('')
  const [scopeProjects, setScopeProjects] = useState<ContextProject[]>([])
  const [vocabularyScopeProjectId, setVocabularyScopeProjectId] = useState(selectedProjectId)
  const [impact, setImpact] = useState<ImpactResponse | null>(null)
  const [impactPayload, setImpactPayload] = useState<Parameters<typeof semanticMappingApi.activateBundle>[0] | null>(null)
  const [discoveryTarget, setDiscoveryTarget] = useState<FolderConnectionPrefill | null>(null)
  const impactGeneration = useRef(0)
  const sampleGeneration = useRef(0)
  const inspectAbort = useRef<AbortController | null>(null)
  const previewAbort = useRef<AbortController | null>(null)
  const previewGeneration = useRef(0)
  const automaticPreviewTimer = useRef<number | null>(null)

  const loadCatalog = useCallback(async () => {
    setBusy('catalog')
    try { setCatalog(await semanticMappingApi.catalog()) } catch (reason) { setMessage({ kind: 'error', text: errorText(reason, '카탈로그를 불러오지 못했습니다.') }) } finally { setBusy('') }
  }, [])
  useEffect(() => { void loadCatalog() }, [loadCatalog])
  useEffect(() => { let active = true; semanticContextApi.projects().then((items) => { if (active) setScopeProjects(items) }).catch(() => undefined); return () => { active = false } }, [])
  useEffect(() => { setVocabularyScopeProjectId(selectedProjectId) }, [selectedProjectId])
  useEffect(() => { previewAbort.current?.abort(); previewAbort.current = null; previewGeneration.current += 1; setPreview(null); setImpact(null); setImpactPayload(null); impactGeneration.current += 1; if (busy === 'preview') setBusy('') }, [recipe, template, catalog.items])
  useEffect(() => { impactGeneration.current += 1; setImpact(null); setImpactPayload(null) }, [catalog])

  const exportDefinitions = async () => {
    setBusy('export')
    try {
      const payload = await semanticMappingApi.exportDefinitions()
      const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }))
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'semantic-definitions-draft.json'; anchor.click(); URL.revokeObjectURL(url)
      setMessage({ kind: payload.warnings?.length ? 'info' : 'success', text: payload.warnings?.length ? `초안을 내보냈습니다. 과거 템플릿 버전 연결 ${payload.warnings.length}개는 생략했습니다. 반입 후 표시 구성을 다시 연결하세요.` : '결과 항목·레시피·템플릿을 초안 JSON으로 내보냈습니다.' })
    } catch (reason) { setMessage({ kind: 'error', text: errorText(reason, '정의 내보내기에 실패했습니다.') }) } finally { setBusy('') }
  }
  const importDefinitions = async (file: File | null) => {
    if (!file) return
    setBusy('import-definitions')
    try { const payload = JSON.parse(await file.text()) as unknown; const result = await semanticMappingApi.importDefinitions(payload); await loadCatalog(); setMessage({ kind: 'success', text: `${result.created.length}개 정의를 초안으로 반입했습니다. 참조를 확인한 뒤 활성화하세요.` }) } catch (reason) { setMessage({ kind: 'error', text: errorText(reason, '정의 JSON 반입에 실패했습니다.') }) } finally { setBusy('') }
  }

  const selectSample = async (file: File | null) => {
    configurationGeneration.current += 1; configurationContextGeneration.current += 1; setRemovedMapping(null); setItemDialog(null); itemDialogRef.current = null
    sampleGeneration.current += 1
    previewAbort.current?.abort(); previewAbort.current = null; previewGeneration.current += 1
    inspectAbort.current?.abort()
    inspectAbort.current = null
    setSample(file); setInspect(null); setPreview(null); setSampleUploadId('')
    if (!recipeId && !templateId) setTemplate({ widgets: [] })
    if (!file) return
    const controller = new AbortController()
    inspectAbort.current = controller
    setBusy('inspect'); setMessage(null)
    try {
      const inspected = await semanticMappingApi.inspect(file, { row_limit: 50, field_limit: 100 }, controller.signal)
      if (controller.signal.aborted) return
      setInspect(inspected)
      setSampleUploadId(inspected.upload_id ?? '')
      const fields = inspected.field_details?.map((detail) => detail.source).filter((field): field is string => Boolean(field)) ?? inspected.fields
      const suggestion = inspected.recipe_suggestion
      setRecipe((current) => recipeId ? current : ({ ...current, reader_version: suggestion?.reader_version ?? 2, input_layout: suggestion?.input_layout, format: inspected.format, delimiter: suggestion?.delimiter ?? current.delimiter, encoding: suggestion?.encoding ?? current.encoding, header_row: suggestion?.header_row ?? current.header_row, records_path: suggestion?.records_path ?? current.records_path, required_fields: [], mappings: [] }))
      const savedSources = recipeId ? new Set(catalog.recipes.find((candidate) => candidate.id === recipeId)?.definition.mappings.map((mapping) => mapping.source) ?? []) : null
      const missing = savedSources ? [...savedSources].filter((source) => !fields.includes(source)) : []
      const additions = savedSources ? fields.filter((source) => !savedSources.has(source)) : []
      const comparison = savedSources && (missing.length || additions.length) ? ` 저장 레시피 기준 누락 ${missing.length}개 · 새 필드 ${additions.length}개를 확인하세요.` : ''
      const recommendations = !recipeId ? catalog.recipes.map((candidate) => { const sources = new Set(candidate.definition.mappings.map((mapping) => mapping.source)); const overlap = fields.filter((field) => sources.has(field)).length; const format = candidate.definition.format === inspected.format ? 1 : 0; return { name: candidate.name ?? candidate.id, score: overlap * 2 + format } }).filter((candidate) => candidate.score > 0).sort((left, right) => right.score - left.score).slice(0, 2).map((candidate) => candidate.name).join(', ') : ''
      const recommendationText = recommendations ? ` 추천 레시피: ${recommendations}.` : ''
      setMessage({ kind: 'success', text: `${file.name}에서 ${inspected.field_count ?? fields.length}개 필드와 ${inspected.row_count ?? inspected.rows.length}개 샘플 행을 확인했습니다.${comparison}${recommendationText}` })
    } catch (reason) { if (!controller.signal.aborted) setMessage({ kind: 'error', text: errorText(reason, '샘플 구조를 확인하지 못했습니다.') }) } finally { if (inspectAbort.current === controller) { inspectAbort.current = null; setBusy('') } }
  }

  const reconcileWidgetItems = (mappings: RecipeMapping[]) => { const ids = new Set(mappings.map((mapping) => mapping.result_item_id)); setTemplate((current) => ({ ...current, widgets: current.widgets.map((widget) => ({ ...widget, item_ids: widget.item_ids.filter((id) => ids.has(id)), x_item_id: ids.has(widget.x_item_id ?? '') ? widget.x_item_id : undefined, y_item_id: ids.has(widget.y_item_id ?? '') ? widget.y_item_id : undefined })) })) }
  const updateMapping = (index: number, patch: Partial<RecipeMapping>) => { configurationGeneration.current += 1; const mappings = recipe.mappings.map((mapping, row) => row === index ? { ...mapping, ...patch } : mapping); setRecipe((current) => ({ ...current, mappings })); if ('result_item_id' in patch) reconcileWidgetItems(mappings) }
  const inspectionSources = () => inspect?.field_details?.map((detail) => detail.source).filter((source): source is string => Boolean(source)) ?? inspect?.fields ?? []
  const addMapping = () => { configurationGeneration.current += 1; setRecipe((current) => ({ ...current, mappings: [...current.mappings, { result_item_id: '', source: inspectionSources()[0] ?? '', dimensions: {}, missing: 'error', aggregate: 'none' }] })) }
  const removeMapping = (index: number) => { configurationGeneration.current += 1; setRemovedMapping({ index, mapping: recipe.mappings[index], template }); reconcileWidgetItems(recipe.mappings.filter((_, row) => row !== index)); setRecipe((current) => ({ ...current, mappings: current.mappings.filter((_, row) => row !== index) })) }
  const duplicateMapping = (index: number) => { configurationGeneration.current += 1; setRecipe((current) => ({ ...current, mappings: current.mappings.flatMap((mapping, row) => row === index ? [mapping, { ...mapping, dimensions: { ...mapping.dimensions } }] : [mapping]) })) }
  const undoMapping = () => { configurationGeneration.current += 1; if (!removedMapping) return; const removed = removedMapping; setTemplate(removed.template); setRecipe((current) => { const mappings = [...current.mappings]; mappings.splice(Math.min(removed.index, mappings.length), 0, removed.mapping); return { ...current, mappings } }); setRemovedMapping(null) }
  const editItem = async (mappingIndex: number) => {
    const item = catalog.items.find((candidate) => candidate.id === recipe.mappings[mappingIndex]?.result_item_id)
    if (!item) return
    const dialog = { mappingIndex, definition: item.definition, id: item.id, expectedVersion: definitionVersion(item) }
    setItemUsage(null); setItemDialog(dialog); itemDialogRef.current = dialog
    try { const usage = await semanticMappingApi.itemUsage(item.id); if (itemDialogRef.current === dialog) setItemUsage(usage) } catch { if (itemDialogRef.current === dialog) setItemUsage({ message: '사용 위치를 불러오지 못했습니다. 항목 의미 편집은 사용 위치 확인 후 가능합니다.' }) }
  }

  const changeItemLifecycle = async (item: VersionedDefinition<SemanticItemDefinition>, restore = false) => { if (!restore && (recipe.mappings.some((mapping) => mapping.result_item_id === item.id) || template.widgets.some((widget) => widget.item_ids.includes(item.id) || widget.x_item_id === item.id || widget.y_item_id === item.id))) { setMessage({ kind: 'error', text: '현재 설정에서 사용하는 항목입니다. 매핑과 위젯에서 제거한 뒤 보관하세요.' }); return }; setBusy('item-lifecycle'); try { await (restore ? semanticMappingApi.restoreItem : semanticMappingApi.archiveItem)(item.id, definitionVersion(item)); await loadCatalog(); setMessage({ kind: 'success', text: restore ? '결과 항목을 복원했습니다.' : '결과 항목을 보관했습니다. 이전 결과와 버전 참조는 유지됩니다.' }) } catch (reason) { setMessage({ kind: 'error', text: errorText(reason, '항목 상태 변경에 실패했습니다.') }) } finally { setBusy('') } }


  const createItem = (mappingIndex: number) => { const source = recipe.mappings[mappingIndex]?.source ?? ''; setItemUsage(null); const dialog = { mappingIndex, definition: { ...defaultItem(source), unit: inspect?.field_details?.find((detail) => detail.source === source)?.unit ?? '' } }; setItemDialog(dialog); itemDialogRef.current = dialog }
  const saveItem = async (definition: SemanticItemDefinition) => {
    const dialog = itemDialogRef.current
    if (!dialog || itemSaveInFlight.current) return
    itemSaveInFlight.current = true; setBusy('item-save')
    const generation = sampleGeneration.current
    try {
      const existing = !dialog.id ? catalog.items.find((candidate) => candidate.lifecycle_status !== 'ARCHIVED' && candidate.definition.key === definition.key && candidate.definition.kind === definition.kind && candidate.definition.data_type === definition.data_type && candidate.definition.unit === definition.unit && JSON.stringify(candidate.definition.dimensions) === JSON.stringify(definition.dimensions)) : undefined
      const item = existing ?? await semanticMappingApi.saveItem(definition, dialog.id, dialog.expectedVersion)
      setCatalog((current) => ({ ...current, items: [item, ...current.items.filter((candidate) => candidate.id !== item.id)] }))
      if (generation !== sampleGeneration.current || itemDialogRef.current !== dialog) return
      updateMapping(dialog.mappingIndex, { result_item_id: item.id }); setItemDialog(null); itemDialogRef.current = null
      setMessage({ kind: 'success', text: `결과 항목 ${definition.label}을 저장했습니다.` })
    } catch (reason) { if (generation === sampleGeneration.current && itemDialogRef.current === dialog) setMessage({ kind: 'error', text: errorText(reason, '결과 항목을 저장하지 못했습니다.') }) } finally { itemSaveInFlight.current = false; setBusy((current) => current === 'item-save' ? '' : current) }
  }

  const saveRecipe = async () => {
    if (!recipe.mappings.length || recipe.mappings.some((mapping) => !mapping.result_item_id || !mapping.source)) { setMessage({ kind: 'error', text: '모든 매핑에 원본 필드와 결과 항목을 지정하세요.' }); return }
    setBusy('save-recipe'); setMessage(null)
    try {
      const saved = await semanticMappingApi.saveRecipe({ id: recipeId || undefined, name: recipeName, definition: recipe, expected_version: recipeVersion, sample_upload_id: sampleUploadId || undefined }, sample ?? undefined)
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
  const saveConfiguration = async () => {
    if (configurationSaveInFlight.current) return
    if (!recipe.mappings.length || recipe.mappings.some((mapping) => !mapping.result_item_id || !mapping.source)) { setMessage({ kind: 'error', text: '모든 매핑에 결과 항목과 원본 필드를 지정하세요.' }); return }
    if (!template.widgets.length || template.widgets.some((widget) => widget.type === 'scatter' ? !widget.x_item_id || !widget.y_item_id : !widget.item_ids.length)) { setMessage({ kind: 'error', text: '위젯마다 결과 항목 입력을 연결하세요.' }); return }
    configurationSaveInFlight.current = true
    const generation = configurationGeneration.current; const contextEpoch = configurationContextGeneration.current; const sampleEpoch = sampleGeneration.current
    setBusy('save-configuration'); setMessage(null)
    try {
      const result = await semanticMappingApi.saveConfiguration({ id: recipeId || undefined, name: recipeName, definition: recipe, expected_version: recipeVersion, sample_upload_id: sampleUploadId || undefined }, { id: templateId || undefined, name: templateName, definition: template, expected_version: templateVersion }, sample ?? undefined)
      const saved = result.recipe; const display = result.template
      setCatalog((current) => ({ ...current, recipes: [{ ...saved, name: saved.name ?? recipeName, active_version: saved.active_version ?? current.recipes.find((item) => item.id === saved.id)?.active_version ?? null }, ...current.recipes.filter((item) => item.id !== saved.id)], templates: display ? [{ ...display, name: display.name ?? templateName, active_version: display.active_version ?? current.templates.find((item) => item.id === display.id)?.active_version ?? null }, ...current.templates.filter((item) => item.id !== display.id)] : current.templates }))
      if (contextEpoch !== configurationContextGeneration.current || sampleEpoch !== sampleGeneration.current) return
      setRecipeId(saved.id); setRecipeVersion(definitionVersion(saved))
      if (display) { setTemplateId(display.id); setTemplateVersion(definitionVersion(display)) }
      if (generation !== configurationGeneration.current) {
        setRecipe((current) => ({ ...current, display_template_id: saved.definition.display_template_id, display_template_version: saved.definition.display_template_version }))
        setMessage({ kind: 'info', text: '저장 요청 시점의 설정을 저장했습니다. 이후 편집 내용은 유지되며 다시 저장할 수 있습니다.' })
        return
      }
      setRecipe(saved.definition); setRecipeId(saved.id); setRecipeVersion(definitionVersion(saved))
      if (display) { setTemplate(display.definition); setTemplateId(display.id); setTemplateVersion(definitionVersion(display)) }
      setMessage({ kind: 'success', text: '결과 설정을 저장했습니다. 다시 열면 항목 매핑과 위젯이 함께 복원됩니다.' })
    } catch (reason) { if (generation === configurationGeneration.current && sampleEpoch === sampleGeneration.current) setMessage({ kind: 'error', text: errorText(reason, '결과 설정 저장에 실패했습니다. 매핑과 위젯 입력을 확인하세요.') }) } finally { configurationSaveInFlight.current = false; setBusy((current) => current === 'save-configuration' ? '' : current) }
  }

  const addSummaryTables = () => {
    const ids = [...new Set(recipe.mappings.map((mapping) => mapping.result_item_id).filter((id) => catalog.items.some((item) => item.id === id && item.definition.kind === 'scalar' && item.lifecycle_status !== 'ARCHIVED')))]
    if (!ids.length) { setMessage({ kind: 'info', text: '요약표에 연결할 단일값 항목을 먼저 정의하세요.' }); return }
    const count = Math.ceil(ids.length / 64)
    if (template.widgets.length + count > 32) { setMessage({ kind: 'error', text: `위젯은 최대 32개입니다. 요약표 ${count}개를 추가할 공간을 확보하세요.` }); return }
    configurationGeneration.current += 1; setRemovedMapping(null)
    const tables: SemanticWidgetDefinition[] = Array.from({ length: count }, (_, index) => ({ ...defaultWidget(), title: count > 1 ? `결과 항목 요약 ${index + 1}` : '결과 항목 요약', type: 'table', item_ids: ids.slice(index * 64, (index + 1) * 64) }))
    setTemplate((current) => ({ ...current, widgets: [...current.widgets, ...tables] }))
    setMessage({ kind: 'success', text: `${ids.length}개 단일값을 요약표 ${count}개에 연결했습니다. 곡선은 선 그래프로 추가하세요.` })
  }
  const selectRecipe = async (id: string) => {
    const generation = ++configurationGeneration.current
    configurationContextGeneration.current += 1
    setItemDialog(null); itemDialogRef.current = null
    setRemovedMapping(null)
    sampleGeneration.current += 1
    inspectAbort.current?.abort(); inspectAbort.current = null
    setBusy((current) => current === 'inspect' || current === 'bulk-items' ? '' : current)
    if (!id) { setRecipeId(''); setRecipeVersion(undefined); setRecipeName('새 레시피'); setRecipe(emptyRecipe()); setTemplate({ widgets: [] }); setTemplateId(''); setTemplateVersion(undefined); setPreview(null); return }
    const selected = catalog.recipes.find((item) => item.id === id)
    if (!selected) return
    setRecipeId(selected.id); setRecipeVersion(definitionVersion(selected)); setRecipeName(selected.name ?? selected.id); setRecipe({ ...emptyRecipe(), ...selected.definition, reader_version: selected.definition.reader_version, input_layout: selected.definition.input_layout, mappings: selected.definition.mappings.map((mapping) => ({ ...mapping, dimensions: mapping.dimensions ?? {}, missing: mapping.missing ?? 'error', aggregate: mapping.aggregate ?? 'none' })) })
    setPreview(null); setMessage({ kind: 'info', text: `레시피 ${selected.name ?? selected.id} v${definitionVersion(selected)}을 편집 중입니다.` })
    try { const linked = await semanticMappingApi.configuration(id); if (generation !== configurationGeneration.current) return; const saved = linked.recipe; setRecipe(saved.definition); setRecipeVersion(definitionVersion(saved)); const display = linked.template; setTemplate(display?.definition ?? { widgets: [] }); setTemplateId(display?.id ?? ''); setTemplateVersion(display ? definitionVersion(display) : undefined); setTemplateName(display?.name ?? '새 템플릿'); if (!display) setMessage({ kind: 'info', text: '이 레시피에는 연결된 위젯 설정이 없습니다. 위젯을 구성하여 결과 설정으로 저장하세요.' }) } catch (reason) { if (generation === configurationGeneration.current) { setTemplate({ widgets: [] }); setTemplateId(''); setTemplateVersion(undefined); setMessage({ kind: 'error', text: errorText(reason, '연결된 결과 설정을 불러오지 못했습니다.') }) } }
  }
  const selectTemplate = (id: string) => {
    configurationGeneration.current += 1; configurationContextGeneration.current += 1; setRemovedMapping(null)
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
  const resetSample = async () => {
    configurationGeneration.current += 1; configurationContextGeneration.current += 1; setRemovedMapping(null); setItemDialog(null); itemDialogRef.current = null
    sampleGeneration.current += 1
    inspectAbort.current?.abort()
    inspectAbort.current = null
    previewAbort.current?.abort(); previewAbort.current = null; previewGeneration.current += 1
    const uploadId = sampleUploadId
    setSample(null); setInspect(null); setPreview(null); setSampleUploadId(''); setBusy('')
    if (!recipeId) { setRecipe(emptyRecipe()); if (!templateId) setTemplate({ widgets: [] }) }
    if (uploadId) { try { await semanticMappingApi.deleteUpload(uploadId) } catch { /* local reset remains safe */ } }
  }
  const reinspectSample = async () => {
    configurationGeneration.current += 1
    sampleGeneration.current += 1
    if (!sample) return
    inspectAbort.current?.abort()
    const controller = new AbortController(); inspectAbort.current = controller
    setBusy('inspect'); setMessage(null)
    try {
      const inspected = await semanticMappingApi.inspect(sample, { row_limit: 50, field_limit: 100, overrides: { format: recipe.format, input_layout: recipe.reader_version === 2 ? recipe.input_layout : undefined, delimiter: recipe.delimiter, encoding: recipe.encoding, header_row: recipe.header_row, records_path: recipe.reader_version === 2 ? recipe.records_path : undefined } }, controller.signal)
      if (controller.signal.aborted) return
      setInspect(inspected); setSampleUploadId(inspected.upload_id ?? '')
      setRecipe((current) => current.reader_version !== 2 ? current : ({ ...current, ...inspected.recipe_suggestion, delimiter: inspected.recipe_suggestion?.delimiter ?? current.delimiter, header_row: inspected.recipe_suggestion?.header_row ?? current.header_row, reader_version: inspected.recipe_suggestion?.reader_version ?? 2, input_layout: inspected.recipe_suggestion?.input_layout ?? current.input_layout, format: inspected.format }))
    } catch (reason) { if (!controller.signal.aborted) setMessage({ kind: 'error', text: errorText(reason, '재검사에 실패했습니다.') }) } finally { if (inspectAbort.current === controller) { inspectAbort.current = null; setBusy('') } }
  }
  const addMappings = (sources: string[]) => { configurationGeneration.current += 1; setRecipe((current) => {
    const existing = new Set(current.mappings.map((mapping) => mapping.source))
    const additions = sources.filter((source) => source && !existing.has(source)).map((source) => ({ result_item_id: '', source, dimensions: {}, missing: 'error' as const, aggregate: 'none' as const }))
    return additions.length ? { ...current, mappings: [...current.mappings, ...additions] } : current
  }) }
  const mergeInspectionPage = (next: InspectResponse) => setInspect((current) => {
    if (!current) return next
    const details = new Map((current.field_details ?? []).map((detail) => [detail.source ?? detail.label ?? '', detail]))
    for (const detail of next.field_details ?? []) { const source = detail.source ?? detail.label ?? ''; if (source) details.set(source, detail) }
    return { ...current, ...next, fields: [...new Set([...(current.fields ?? []), ...(next.fields ?? [])])], field_details: [...details.values()], upload_id: next.upload_id ?? current.upload_id }
  })
  const createItems = async (sources: string[]) => {
    configurationGeneration.current += 1
    if (!sources.length) return
    const generation = sampleGeneration.current
    setBusy('bulk-items'); setMessage(null)
    const created: Array<{ source: string; item: VersionedDefinition<SemanticItemDefinition> }> = []
    const failures: string[] = []
    try {
      for (const source of sources) {
        if (generation !== sampleGeneration.current) break
        try {
          const detail = inspect?.field_details?.find((candidate) => candidate.source === source)
          const sampleValue = detail?.value
          const numericText = typeof sampleValue === 'string' && sampleValue.trim() !== '' && Number.isFinite(Number(sampleValue))
          const inferredType: SemanticItemDefinition['data_type'] = typeof sampleValue === 'boolean' ? 'BOOLEAN' : typeof sampleValue === 'number' ? 'FLOAT' : numericText ? 'FLOAT' : 'TEXT'
          const definition = { ...defaultItem(source), data_type: inferredType, unit: detail?.unit ?? '' }
          if (sampleValue == null || sampleValue === '' || detail?.mappable === false) continue
          const existing = [...catalog.items, ...created.map((entry) => entry.item)].find((item) => item.definition.key === definition.key && item.lifecycle_status !== 'ARCHIVED' && item.definition.kind === definition.kind && item.definition.data_type === definition.data_type && item.definition.unit === definition.unit && item.definition.dimensions.length === 0)
          created.push({ source, item: existing ?? await semanticMappingApi.createItem(definition) })
        } catch (reason) { failures.push(`${sourceLabel(source)}: ${errorText(reason, '항목 연결 실패')}`) }
      }
      setCatalog((current) => ({ ...current, items: [...new Map([...created.map(({ item }) => item), ...current.items].map((item) => [item.id, item])).values()] }))
      if (generation === sampleGeneration.current) setRecipe((current) => {
        const used = new Set<number>(); const additions: RecipeMapping[] = []; const mappings = current.mappings.map((mapping) => ({ ...mapping }))
        for (const { source, item } of created) {
          const blankIndex = mappings.findIndex((mapping, index) => !used.has(index) && mapping.source === source && !mapping.result_item_id)
          if (blankIndex >= 0) { used.add(blankIndex); mappings[blankIndex] = { ...mappings[blankIndex], result_item_id: item.id, source_unit: item.definition.unit || mappings[blankIndex].source_unit }; continue }
          if (!mappings.some((mapping) => mapping.source === source && mapping.result_item_id === item.id)) additions.push({ result_item_id: item.id, source, source_unit: item.definition.unit || undefined, dimensions: {}, missing: 'error', aggregate: 'none' })
        }
        return additions.length || used.size ? { ...current, mappings: [...mappings, ...additions] } : current
      })
      if (generation === sampleGeneration.current) setMessage({ kind: failures.length ? 'info' : 'success', text: `${created.length}개 결과 항목을 생성하고 샘플 필드에 연결했습니다.${failures.length ? ` 실패 ${failures.length}개: ${failures.slice(0, 3).join("; ")}` : ''}` })
    } finally { if (generation === sampleGeneration.current) setBusy('') }
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
    if (automaticPreviewTimer.current !== null) { window.clearTimeout(automaticPreviewTimer.current); automaticPreviewTimer.current = null }
    if (!sample) { setMessage({ kind: 'error', text: '먼저 샘플 CSV 또는 JSON을 선택하세요.' }); return }
    previewAbort.current?.abort(); const controller = new AbortController(); previewAbort.current = controller; const generation = ++previewGeneration.current
    setBusy('preview'); setMessage(null)
    const activeTemplate = template.widgets.length ? template : undefined
    try { const result = await semanticMappingApi.preview(sample, recipe, activeTemplate, sampleUploadId || undefined, controller.signal); if (!controller.signal.aborted && generation === previewGeneration.current) { setPreview(result); setMessage({ kind: 'success', text: '파싱 결과와 위젯 입력을 함께 검증했습니다.' }) } } catch (reason) { if (!controller.signal.aborted && generation === previewGeneration.current) setMessage({ kind: 'error', text: errorText(reason, '미리보기에 실패했습니다.') }) } finally { if (previewAbort.current === controller) { previewAbort.current = null; setBusy('') } }
  }

  useEffect(() => {
    if (tab !== 'recipe' || !sample || !recipe.mappings.length || recipe.mappings.some((mapping) => !mapping.source || !mapping.result_item_id) || !template.widgets.length || template.widgets.some((widget) => widget.type === 'scatter' ? !widget.x_item_id || !widget.y_item_id : !widget.item_ids.length)) return
    const controller = new AbortController()
    const timeout = window.setTimeout(() => {
      automaticPreviewTimer.current = null
      previewAbort.current?.abort(); previewAbort.current = controller; const generation = ++previewGeneration.current
      semanticMappingApi.preview(sample, recipe, template, sampleUploadId || undefined, controller.signal).then((result) => { if (!controller.signal.aborted && generation === previewGeneration.current) setPreview(result) }).catch((reason) => { if (!controller.signal.aborted && generation === previewGeneration.current) setMessage({ kind: 'error', text: errorText(reason, '자동 미리보기 입력을 확인하세요.') }) })
    }, 450)
    automaticPreviewTimer.current = timeout
    return () => { window.clearTimeout(timeout); if (automaticPreviewTimer.current === timeout) automaticPreviewTimer.current = null; controller.abort() }
  }, [recipe, template, sample, sampleUploadId, tab, catalog.items])

  return <section className="semantic-page">
    <header className="semantic-hero"><div><span className="semantic-eyebrow">SEMANTIC RESULT MAPPING</span><h1>결과 의미 연결</h1><p>샘플 필드의 의미와 단위를 한 번 정의하면 다음 결과 파일과 위젯에 같은 결과 항목을 재사용합니다.</p></div><div className="semantic-hero-actions"><button className="ghost-button" onClick={onLegacy}><ChevronRight /> 기존 폴더 스키마</button><button className="ghost-button" onClick={() => void loadCatalog()} disabled={busy === 'catalog'}><RefreshCw className={busy === 'catalog' ? 'spin' : ''} /> 카탈로그 새로고침</button><button className="ghost-button" onClick={() => void exportDefinitions()} disabled={busy === 'export'}><FileJson /> 초안 내보내기</button><label className="ghost-button"><Upload /> 초안 반입<input className="semantic-hidden-file" type="file" accept="application/json,.json" onChange={(event) => void importDefinitions(event.target.files?.[0] ?? null)} /></label></div></header>
    <nav className="semantic-tabs" aria-label="의미 연결 단계"><button className={tab === 'recipe' ? 'active' : ''} onClick={() => setTab('recipe')}><FileJson /> 1. 샘플·레시피</button><button className={tab === 'discovery' ? 'active' : ''} onClick={() => setTab('discovery')}><FolderOpen /> 폴더 조사·업무 생성</button><button className={tab === 'folder' ? 'active' : ''} onClick={() => setTab('folder')}><FolderOpen /> 2. 폴더 연결</button><button className={tab === 'results' ? 'active' : ''} onClick={() => setTab('results')}><Gauge /> 3. 결과 조회</button><button className={tab === 'vocabulary' ? 'active' : ''} onClick={() => setTab('vocabulary')}><Tags /> 기준 정의·별칭</button></nav>
    {message ? <div className={`semantic-message ${message.kind}`} role={message.kind === 'error' ? 'alert' : 'status'}>{message.kind === 'error' ? <AlertTriangle /> : <Check />}{message.text}<button aria-label="메시지 닫기" onClick={() => setMessage(null)}><X /></button></div> : null}
    {tab === 'recipe' ? <RecipeTab sample={sample} selectSample={selectSample} resetSample={resetSample} reinspectSample={reinspectSample} onAddMappings={addMappings} onCreateItems={createItems} onInspectionPage={mergeInspectionPage} inspect={inspect} recipe={recipe} setRecipe={(next) => { configurationGeneration.current += 1; setRecipe(next) }} catalog={catalog} updateMapping={updateMapping} addMapping={addMapping} removeMapping={removeMapping} duplicateMapping={duplicateMapping} undoMapping={undoMapping} canUndo={!!removedMapping} editItem={editItem} changeItemLifecycle={changeItemLifecycle} createItem={createItem} saveConfiguration={saveConfiguration} addSummaryTables={addSummaryTables} busy={busy} recipeName={recipeName} setRecipeName={(name) => { configurationGeneration.current += 1; setRecipeName(name) }} saveRecipe={saveRecipe} activateBundle={() => void activateBundle()} template={template} setTemplate={(next) => { configurationGeneration.current += 1; setRemovedMapping(null); setTemplate(next) }} templateName={templateName} setTemplateName={(name) => { configurationGeneration.current += 1; setTemplateName(name) }} saveTemplate={saveTemplate} preview={preview} runPreview={runPreview} recipeId={recipeId} templateId={templateId} selectRecipe={selectRecipe} selectTemplate={selectTemplate} vocabularyScopeProjectId={vocabularyScopeProjectId} setVocabularyScopeProjectId={setVocabularyScopeProjectId} scopeProjects={scopeProjects} /> : tab === 'discovery' ? <FolderDiscoveryWorkspace onComplete={() => { void loadCatalog() }} onOpenFolder={(target?: FolderDiscoveryTarget) => { if (target) setDiscoveryTarget(target); setTab('folder') }} /> : tab === 'folder' ? <FolderTab catalog={catalog} onMessage={setMessage} busy={busy} setBusy={setBusy} onCatalog={setCatalog} onOpenDiscovery={() => setTab('discovery')} initialTarget={discoveryTarget} /> : tab === 'results' ? <ResultsTab catalog={catalog} onMessage={setMessage} /> : <SemanticVocabularyTab isAdmin={isVocabularyAdmin} selectedProjectId={selectedProjectId} />}
    {itemDialog ? <ItemDefinitionDialog key={itemDialog.id ?? `new-${itemDialog.mappingIndex}`} usage={itemUsage} editing={!!itemDialog.id} value={itemDialog.definition} busy={busy === 'item-save'} onCancel={() => { setItemDialog(null); itemDialogRef.current = null }} onSave={(definition) => { void saveItem(definition) }} /> : null}
    {impact || busy === 'impact-bundle' ? <SemanticImpactDialog impact={impact} loading={busy === 'impact-bundle'} confirming={busy === 'activate-bundle'} onClose={() => { impactGeneration.current += 1; setImpact(null); setImpactPayload(null); setBusy('') }} onConfirm={() => void confirmBundleActivation()} /> : null}
  </section>
}

function ItemDefinitionDialog({ value, usage, editing, busy, onCancel, onSave }: { usage: Record<string, unknown> | null; editing: boolean; value: SemanticItemDefinition; busy: boolean; onCancel: () => void; onSave: (value: SemanticItemDefinition) => void }) {
  const [draft, setDraft] = useState(value)
  const meaningLocked = editing && (!usage || typeof usage.recipes !== 'number' || typeof usage.templates !== 'number' || usage.recipes > 0 || usage.templates > 0)
  const update = (patch: Partial<SemanticItemDefinition>) => setDraft((current) => ({ ...current, ...patch }))
  return <div className="semantic-dialog-backdrop" role="presentation"><form className="semantic-dialog" role="dialog" aria-modal="true" aria-labelledby="item-dialog-title" onSubmit={(event) => { event.preventDefault(); onSave(draft) }}><div className="semantic-card-heading"><div><span>{editing ? "EDIT RESULT ITEM" : "NEW RESULT ITEM"}</span><h2 id="item-dialog-title">결과 항목 정의</h2></div><button type="button" className="icon-button" aria-label="닫기" onClick={onCancel}><X /></button></div><p className="dialog-help">{meaningLocked ? "저장된 설정에서 참조하는 항목은 표시명만 수정할 수 있습니다. 참조가 없는 항목은 의미를 수정할 수 있습니다." : "좌표는 X/Y/Z 성분별 단일값으로 정의하세요. 실제 X–Y 데이터에만 곡선을 선택하세요."}</p>{usage ? <details><summary>이 항목의 사용 위치</summary>{typeof usage.recipes === 'number' && typeof usage.templates === 'number' ? <><p>참조하는 레시피 버전 {usage.recipes}개 · 템플릿 버전 {usage.templates}개 · 위젯 {Number(usage.widgets ?? 0)}개</p><small>개수는 저장된 버전의 참조 기준입니다. 이전 버전의 의미도 유지합니다.</small></> : <p>{String(usage.message ?? '사용 위치를 확인하고 있습니다.')}</p>}</details> : null}<p className="dialog-help">고정 항목 키는 레시피와 위젯이 참조하는 식별자입니다. 표시명은 나중에 바꿀 수 있습니다.</p><label>표시명<input autoFocus required value={draft.label} onChange={(event) => update({ label: event.target.value })} /></label><label>고정 항목 키<input required disabled={meaningLocked} value={draft.key} onChange={(event) => update({ key: event.target.value })} /></label><div className="dialog-grid"><label>종류<select disabled={meaningLocked} value={draft.kind} onChange={(event) => update({ kind: event.target.value as SemanticItemKind })}><option value="scalar">단일값 · 좌표 성분</option><option value="curve">곡선 · X-Y 점</option></select></label><label>자료형<select disabled={meaningLocked} value={draft.data_type} onChange={(event) => update({ data_type: event.target.value as SemanticItemDefinition['data_type'] })}><option value="FLOAT">FLOAT</option><option value="INTEGER">INTEGER</option><option value="TEXT">TEXT</option><option value="BOOLEAN">BOOLEAN</option></select></label></div><label>기준 단위<input disabled={meaningLocked} value={draft.unit} placeholder="예: MPa, s, - (선택)" onChange={(event) => update({ unit: event.target.value })} /></label><label>측정 차원<input disabled={meaningLocked} value={draft.dimensions.join(', ')} placeholder="예: node, component" onChange={(event) => update({ dimensions: event.target.value.split(',').map((item) => item.trim()).filter(Boolean) })} /><small>차원이 있으면 각 매핑에서 해당 위치를 읽을 원본 필드를 연결합니다.</small></label><div className="dialog-actions"><button type="button" className="ghost-button" onClick={onCancel}>취소</button><button type="submit" className="primary-button" disabled={busy}><Save /> {busy ? '저장 중' : '결과 항목 저장'}</button></div></form></div>
}

function RecipeTabContent(props: {
  sample: File | null; selectSample: (file: File | null) => void; resetSample: () => void; reinspectSample: () => void; onAddMappings: (sources: string[]) => void; onCreateItems: (sources: string[]) => void; onInspectionPage: (response: InspectResponse) => void; inspect: InspectResponse | null; recipe: SemanticRecipeDefinition; setRecipe: (recipe: SemanticRecipeDefinition) => void; catalog: SemanticCatalog; updateMapping: (index: number, patch: Partial<RecipeMapping>) => void; addMapping: () => void; removeMapping: (index: number) => void; duplicateMapping: (index: number) => void; undoMapping: () => void; canUndo: boolean; editItem: (index: number) => void; changeItemLifecycle: (item: VersionedDefinition<SemanticItemDefinition>, restore?: boolean) => void; saveConfiguration: () => void; addSummaryTables: () => void; createItem: (index: number) => void; busy: string; recipeName: string; setRecipeName: (name: string) => void; saveRecipe: () => void; activateBundle: () => void; template: SemanticTemplateDefinition; setTemplate: (template: SemanticTemplateDefinition) => void; templateName: string; setTemplateName: (name: string) => void; saveTemplate: () => void; preview: PreviewResponse | null; runPreview: () => void; recipeId: string; templateId: string; selectRecipe: (id: string) => void; selectTemplate: (id: string) => void; vocabularyScopeProjectId: string; setVocabularyScopeProjectId: (value: string) => void; scopeProjects: ContextProject[]
}) {
  const { sample, selectSample, resetSample, reinspectSample, onAddMappings, onCreateItems, onInspectionPage, inspect, recipe, setRecipe, catalog, updateMapping, addMapping, removeMapping, duplicateMapping, undoMapping, canUndo, editItem, changeItemLifecycle, saveConfiguration, addSummaryTables, createItem, busy, recipeName, setRecipeName, saveRecipe, activateBundle, template, setTemplate, templateName, setTemplateName, saveTemplate, preview, runPreview, recipeId, templateId, selectRecipe, selectTemplate, vocabularyScopeProjectId, setVocabularyScopeProjectId, scopeProjects } = props
  const [mappingPage, setMappingPage] = useState(0)
  const mappingPageSize = 50
  useEffect(() => { setMappingPage((page) => Math.min(page, Math.max(0, Math.ceil(recipe.mappings.length / mappingPageSize) - 1))) }, [recipe.mappings.length, recipeId])
  const mappingStart = mappingPage * mappingPageSize
  return <div className="semantic-flow">
    <SemanticSampleInspector sample={sample} inspect={inspect} busy={busy} onSelectSample={selectSample} onReset={resetSample} onAddMappings={onAddMappings} onCreateItems={onCreateItems} onInspectionPage={onInspectionPage} />
    <div className="semantic-card vocabulary-scope-card"><label className="vocabulary-scope-select">별칭 검색 프로젝트 (전역 기본)<select aria-label="별칭 검색 프로젝트" value={vocabularyScopeProjectId} onChange={(event) => setVocabularyScopeProjectId(event.target.value)}><option value="">전역만</option>{scopeProjects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select><small>RESULT_ITEM 후보는 이 프로젝트 범위부터 찾습니다. 레시피에는 범위가 저장되지 않습니다.</small></label></div>
    <div className="semantic-card"><div className="semantic-card-heading"><div><span>STEP 02 · DEFINE</span><h2>필드 → 결과 항목 · 단위</h2></div><button className="ghost-button" onClick={addMapping}><Plus /> 매핑 추가</button><button className="ghost-button" disabled={!canUndo} onClick={undoMapping}>삭제 되돌리기</button></div><details className="sample-advanced-overrides"><summary>자동 감지 설정 재설정</summary><div className="semantic-inline-fields"><label>입력 레이아웃<select value={recipe.input_layout ?? 'csv_table'} onChange={(event) => setRecipe({ ...recipe, input_layout: event.target.value, format: event.target.value.startsWith('json') ? 'json' : 'csv', records_path: event.target.value === 'json_object' ? '' : recipe.records_path })}><option value="csv_table">CSV/TSV 표</option><option value="json_records">JSON records</option><option value="csv_key_value">CSV 키-값</option><option value="json_object">JSON 객체</option></select></label><label>파일 형식<select value={recipe.format} onChange={(event) => setRecipe({ ...recipe, format: event.target.value as SemanticFormat, input_layout: event.target.value === 'json' ? 'json_object' : 'csv_table', records_path: '' })}><option value="csv">CSV</option><option value="json">JSON</option></select></label><label>구분자<input value={recipe.delimiter} maxLength={1} onChange={(event) => setRecipe({ ...recipe, delimiter: event.target.value })} /></label><label>JSON records 경로<input value={recipe.records_path} placeholder={recipe.reader_version === 2 ? "예: #/results/rows" : "예: results.rows"} onChange={(event) => setRecipe({ ...recipe, records_path: event.target.value })} /></label><label>인코딩<select value={recipe.encoding} onChange={(event) => setRecipe({ ...recipe, encoding: event.target.value })}><option value="utf-8-sig">UTF-8 BOM</option><option value="utf-8">UTF-8</option><option value="cp949">CP949</option><option value="utf-16">UTF-16</option></select></label><label>헤더 행<input type="number" min={1} value={recipe.header_row} onChange={(event) => setRecipe({ ...recipe, header_row: Number(event.target.value) })} /></label></div><button type="button" className="ghost-button" onClick={() => void reinspectSample()} disabled={!sample || busy === 'inspect'}>현재 설정으로 다시 검사</button></details><div className="mapping-table"><div className="mapping-row mapping-head"><span>원본 필드</span><span>결과 항목</span><span>변환 단위</span><span>측정 위치 필드</span><span>결측·집계</span><span /></div>{recipe.mappings.length ? recipe.mappings.slice(mappingStart, mappingStart + mappingPageSize).map((mapping, localIndex) => <div className="mapping-row" key={mappingStart + localIndex}><div className="mapping-source-with-alias"><input aria-label="원본 필드" list="semantic-source-fields" value={mapping.source} onChange={(event) => updateMapping(mappingStart + localIndex, { source: event.target.value })} placeholder="필드 경로" /><AliasSuggestion term={mapping.source} targetKinds={['RESULT_ITEM']} scopeProjectId={vocabularyScopeProjectId || undefined} onSelect={(entry) => updateMapping(mappingStart + localIndex, { result_item_id: entry.target_id })} /></div><div className="mapping-item-select"><select aria-label="결과 항목" value={mapping.result_item_id} onChange={(event) => updateMapping(mappingStart + localIndex, { result_item_id: event.target.value })}><option value="">결과 항목 선택</option>{catalog.items.filter((item) => item.lifecycle_status !== 'ARCHIVED' || item.id === mapping.result_item_id).map((item) => <option key={item.id} value={item.id}>{item.definition.label} · {item.definition.unit || '단위 없음'} · v{definitionVersion(item)}</option>)}</select><button title="새 결과 항목" onClick={() => void createItem(mappingStart + localIndex)} disabled={busy === `item-${mappingStart + localIndex}`}><Plus /></button><button type="button" title="결과 항목 수정" disabled={!mapping.result_item_id} onClick={() => editItem(mappingStart + localIndex)}>수정</button></div><div className="unit-pair"><input aria-label="원본 단위" placeholder="원본 단위" value={mapping.source_unit ?? ''} onChange={(event) => updateMapping(mappingStart + localIndex, { source_unit: event.target.value || undefined })} /><span>→</span><input aria-label="대상 단위" placeholder="대상 단위" value={catalog.items.find((item) => item.id === mapping.result_item_id)?.definition.unit ?? ''} readOnly /></div><DimensionFields value={mapping.dimensions} dimensions={catalog.items.find((item) => item.id === mapping.result_item_id)?.definition.dimensions ?? []} onChange={(dimensions) => updateMapping(mappingStart + localIndex, { dimensions })} /><div className="mapping-policies"><select value={mapping.missing} onChange={(event) => updateMapping(mappingStart + localIndex, { missing: event.target.value as 'error' | 'skip' })}><option value="error">누락이면 오류</option><option value="skip">누락 건너뜀</option></select><select value={mapping.aggregate ?? 'none'} onChange={(event) => updateMapping(mappingStart + localIndex, { aggregate: event.target.value as RecipeMapping['aggregate'] })}><option value="none">집계 없음</option><option value="max">최대</option><option value="min">최소</option><option value="mean">평균</option></select></div><div className="mapping-row-actions"><button type="button" aria-label="매핑 복제" onClick={() => duplicateMapping(mappingStart + localIndex)}>복제</button><button className="icon-button danger-icon" aria-label="매핑 삭제" onClick={() => removeMapping(mappingStart + localIndex)}><X /></button></div></div>) : <div className="empty-inline">샘플을 올리면 필드 매핑을 시작할 수 있습니다.</div>}</div><div className="mapping-pagination"><button type="button" className="ghost-button" onClick={() => setMappingPage((page) => Math.max(0, page - 1))} disabled={mappingPage === 0}>이전 매핑</button><span>{mappingStart + 1}–{Math.min(mappingStart + mappingPageSize, recipe.mappings.length)} / {recipe.mappings.length}</span><button type="button" className="ghost-button" onClick={() => setMappingPage((page) => page + 1)} disabled={mappingStart + mappingPageSize >= recipe.mappings.length}>다음 매핑</button></div></div>
    <div className="semantic-card"><div className="semantic-card-heading"><div><span>STEP 03 · WIDGET CONTRACT</span><h2>위젯 입력 역할</h2></div><button className="ghost-button" onClick={() => setTemplate({ ...template, widgets: [...template.widgets, (() => { const item = catalog.items.find((item) => item.id === recipe.mappings.find((mapping) => mapping.result_item_id)?.result_item_id); return { ...defaultWidget(item?.id), title: item?.definition.label ?? '새 위젯', type: item?.definition.kind === 'curve' ? 'line' as const : item?.definition.data_type === 'TEXT' || item?.definition.data_type === 'BOOLEAN' ? 'table' as const : 'kpi' as const } })()] })}><Plus /> 위젯 추가</button></div><div className="widget-config-list">{template.widgets.length ? template.widgets.map((widget, index) => <WidgetEditor key={widget.id} widget={widget} items={catalog.items.filter((item) => recipe.mappings.some((mapping) => mapping.result_item_id === item.id))} mappings={recipe.mappings} inspect={inspect} readerVersion={recipe.reader_version} onChange={(next) => setTemplate({ ...template, widgets: template.widgets.map((item, row) => row === index ? next : item) })} onRemove={() => setTemplate({ ...template, widgets: template.widgets.filter((_, row) => row !== index) })} />) : <div className="empty-inline">위젯을 추가하면 결과 항목을 카드·표·선 그래프·산점도 입력 역할에 연결합니다.</div>}</div><button type="button" className="ghost-button" onClick={addSummaryTables}>전체 항목 요약표 추가</button><div className="definition-actions"><button className="primary-button" onClick={saveConfiguration} disabled={!!busy}><Save />결과 설정 저장</button><label>레시피 이름<input value={recipeName} onChange={(event) => setRecipeName(event.target.value)} /></label><label>템플릿 이름<input value={templateName} onChange={(event) => setTemplateName(event.target.value)} /></label><details><summary>고급 · 레시피/템플릿 개별 저장</summary><button className="primary-button" onClick={saveRecipe} disabled={busy === 'save-recipe'}><Save /> {busy === 'save-recipe' ? '저장 중' : recipeId ? '레시피 새 버전 저장' : '레시피 저장'}</button><button className="primary-button" onClick={saveTemplate} disabled={busy === 'save-template'}><Save /> {busy === 'save-template' ? '저장 중' : templateId ? '템플릿 새 버전 저장' : '템플릿 저장'}</button></details><button className="secondary-button" onClick={activateBundle} disabled={!recipeId || !templateId || busy.startsWith('activate') || busy === 'impact-bundle'}><Check /> 함께 활성화 · 활성화 영향 미리보기</button></div></div>
    <details className="semantic-card"><summary>결과 항목 관리 · 보관 및 복원</summary>{catalog.items.map((item) => <div key={item.id} className="semantic-inline-fields"><span title={item.definition.key}>{item.definition.label} · {item.lifecycle_status === 'ARCHIVED' ? '보관됨' : '사용 가능'}</span><button type="button" className="ghost-button" disabled={!!busy} onClick={() => changeItemLifecycle(item, item.lifecycle_status === 'ARCHIVED')}>{item.lifecycle_status === 'ARCHIVED' ? '복원' : '보관'}</button></div>)}<small>항목 보관은 이전 결과와 버전 참조를 유지합니다. 현재 설정에서 사용하는 항목은 먼저 매핑과 위젯에서 제거하세요.</small></details><SemanticRecipeValidation recipe={recipe} /><div className="semantic-card preview-card"><div className="semantic-card-heading"><div><span>STEP 04 · VERIFY</span><h2>변환 + 위젯 통합 미리보기</h2></div><button className="primary-button" onClick={runPreview} disabled={!sample || busy === 'preview' || busy === 'inspect' || busy === 'bulk-items'}>{busy === 'preview' ? <LoaderCircle className="spin" /> : <RefreshCw />} 미리보기 실행</button></div>{preview ? <PreviewPanel parsed={preview.parsed} widgets={preview.widgets} /> : <div className="empty-preview"><LineChartIcon /><p>저장 전에도 현재 레시피로 실행할 수 있습니다. 결과 항목이 없거나 여러 값이 연결되면 원인을 표시합니다.</p></div>}</div>
  </div>
}

type RecipeTabProps = Parameters<typeof RecipeTabContent>[0]

function DimensionFields({ value, dimensions, onChange }: { value: Record<string, string>; dimensions: string[]; onChange: (value: Record<string, string>) => void }) {
  if (!dimensions.length) return <small>단일 관측값</small>
  return <div>{dimensions.map((dimension) => <label key={dimension}>{dimension}<input aria-label={`${dimension} 원본 필드`} list="semantic-source-fields" value={value[dimension] ?? ''} onChange={(event) => onChange({ ...value, [dimension]: event.target.value })} /></label>)}</div>
}

function RecipeTab(props: RecipeTabProps) {
  return <><SavedDefinitionSelectors props={props} /><RecipeTabContent {...props} /><div className="semantic-card semantic-advanced-mappings"><div className="semantic-card-heading"><div><span>ADVANCED FIELD ROLES</span><h2>원본 경로·곡선 축·series 입력</h2></div><LineChartIcon /></div>{props.recipe.mappings.map((mapping, index) => { const item = props.catalog.items.find((candidate) => candidate.id === mapping.result_item_id); return <div className="curve-role-row" key={index}><strong>{item?.definition.label ?? `매핑 ${index + 1}`}</strong><label>값 원본 경로<input list="semantic-source-fields" value={mapping.source} onChange={(event) => props.updateMapping(index, { source: event.target.value })} /></label>{item?.definition.kind === 'curve' ? <><label>X 원본 필드<input list="semantic-source-fields" value={mapping.x_source ?? ''} onChange={(event) => props.updateMapping(index, { x_source: event.target.value })} /></label><label>X 단위<input value={mapping.x_unit ?? ''} onChange={(event) => props.updateMapping(index, { x_unit: event.target.value || undefined })} /></label><label>표시 X 단위<input value={mapping.target_x_unit ?? ''} onChange={(event) => props.updateMapping(index, { target_x_unit: event.target.value || undefined })} /></label><label>series 필드<input list="semantic-source-fields" value={mapping.series_source ?? ''} onChange={(event) => props.updateMapping(index, { series_source: event.target.value || undefined })} /></label></> : null}</div> })}<datalist id="semantic-source-fields">{(props.inspect?.field_details?.map((detail) => detail.source).filter((field): field is string => Boolean(field)) ?? props.inspect?.fields ?? []).map((field) => <option key={field} value={field} />)}</datalist></div></>
}

function SavedDefinitionSelectors({ props }: { props: RecipeTabProps }) {
  return <div className="semantic-card saved-definition-selectors"><label>저장된 레시피<select aria-label="저장된 레시피" value={props.recipeId} onChange={(event) => props.selectRecipe(event.target.value)}><option value="">새 레시피</option>{props.catalog.recipes.map((item) => <option key={item.id} value={item.id}>{item.name ?? item.id} · v{definitionVersion(item)}</option>)}</select></label><label>저장된 템플릿<select aria-label="저장된 템플릿" value={props.templateId} onChange={(event) => props.selectTemplate(event.target.value)}><option value="">새 템플릿</option>{props.catalog.templates.map((item) => <option key={item.id} value={item.id}>{item.name ?? item.id} · v{definitionVersion(item)}</option>)}</select></label></div>
}

function PreviewPanel({ parsed, widgets }: { parsed: ParsedSemanticResult; widgets: PreviewWidget[] }) {
  const [page, setPage] = useState(0)
  useEffect(() => setPage(0), [parsed])
  const observations = parsed.observations ?? []
  const sampleRows = observations.slice(page * 50, (page + 1) * 50)

  return <div className="preview-panel"><div className="parsed-summary"><span><b>{parsed.scalars?.length ?? 0}</b> scalar</span><span><b>{parsed.curves?.length ?? 0}</b> curve</span><span><b>{parsed.media?.length ?? 0}</b> media</span><span><b>{parsed.observations?.length ?? 0}</b> observations</span></div>{parsed.warnings?.length ? <div className="warning-list">{parsed.warnings.map((warning) => <span key={warning}><AlertTriangle />{warning}</span>)}</div> : null}<div className="sample-row-table-wrap"><table className="sample-row-table"><thead><tr><th>결과 항목</th><th>정규화 값</th><th>단위</th></tr></thead><tbody>{sampleRows.map((row, index) => <tr key={index}><td>{String(row.label ?? row.item_id ?? '')}</td><td>{Array.isArray(row.points) ? `${row.points.length}개 곡선 점` : String(row.value ?? '')}</td><td>{String(row.unit ?? '')}</td></tr>)}</tbody></table></div><div className="sample-page-actions"><button className="ghost-button" disabled={page === 0} onClick={() => setPage(page - 1)}>이전 결과</button><span>{observations.length}개 결과</span><button className="ghost-button" disabled={(page + 1) * 50 >= observations.length} onClick={() => setPage(page + 1)}>다음 결과</button></div><SemanticWidgetGrid widgets={widgets} /></div>
}


function errorText(reason: unknown, fallback: string) { return reason instanceof Error ? reason.message : fallback }
