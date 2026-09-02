import { GripVertical, Plus, Trash2, X } from 'lucide-react'
import { useMemo } from 'react'
import type { WidgetType } from '../../types'
import type { RequestResultDefinition, RequestResultWidget, WorkbenchTaskType } from './types'
import { normalizeResultDataContracts } from './resultProfileContracts'
import { REQUEST_RESULT_WIDGET_CATALOG, catalogItemForWidget, widgetIdForType } from './requestResultWidgetCatalog'
import { requestResultDefinitionValidation } from './requestResultDefinition'

function updateWidget(definition: RequestResultDefinition, id: string, patch: Partial<RequestResultWidget>): RequestResultDefinition {
  return { ...definition, widgets: definition.widgets.map((widget) => widget.id === id ? { ...widget, ...patch } : widget) }
}

export function RequestResultWidgetConfiguration({ value, tasks, onChange }: { value: RequestResultDefinition | null; tasks: WorkbenchTaskType[]; onChange: (value: RequestResultDefinition | null) => void }) {
  const definition = value ?? { page_name: '요청 결과', page_description: '', widgets: [] }
  const selectedTypes = useMemo(() => new Set(definition.widgets.map((widget) => widget.type)), [definition.widgets])
  const validationError = requestResultDefinitionValidation(value, tasks)
  const groups = useMemo(() => REQUEST_RESULT_WIDGET_CATALOG.reduce<Record<string, typeof REQUEST_RESULT_WIDGET_CATALOG[number][]>>((result, item) => { (result[item.group] ??= []).push(item); return result }, {}), [])
  const addWidget = (type: WidgetType) => {
    const catalog = catalogItemForWidget({ type, id: '' })
    if (selectedTypes.has(type)) return
    onChange({ ...definition, widgets: [...definition.widgets, { id: widgetIdForType(type), type, title: catalog.label, variable_key: catalog.variable_key ?? null, data_contracts: normalizeResultDataContracts(catalog.data_contracts), required: false }] })
  }
  const removeWidget = (id: string) => onChange({ ...definition, widgets: definition.widgets.filter((widget) => widget.id !== id) })
  const moveWidget = (index: number, direction: -1 | 1) => {
    const target = index + direction
    if (target < 0 || target >= definition.widgets.length) return
    const widgets = [...definition.widgets]; const [item] = widgets.splice(index, 1); widgets.splice(target, 0, item); onChange({ ...definition, widgets })
  }
  return <section className="request-result-widget-configuration" aria-labelledby="request-result-heading">
    <header><div><span>03 · REQUEST RESULTS</span><h3 id="request-result-heading">요청 결과 위젯</h3><p>수행 완료 후 상세 요약 보기에 표시할 결과를 태그처럼 추가하세요. 별도 분석 템플릿 게시가 필요 없습니다.</p></div></header>
    <div className="request-result-page-fields"><label><span>결과 페이지 이름</span><input aria-label="요청 결과 페이지 이름" value={definition.page_name ?? ''} onChange={(event) => onChange({ ...definition, page_name: event.target.value })} /></label><label><span>설명</span><input aria-label="요청 결과 페이지 설명" value={definition.page_description ?? ''} onChange={(event) => onChange({ ...definition, page_description: event.target.value })} /></label></div>
    <div className="request-result-catalog" aria-label="결과 위젯 카탈로그">{Object.entries(groups).map(([group, items]) => <div key={group} className="request-result-catalog-group"><strong>{group}</strong><div>{items.map((item) => { const selected = selectedTypes.has(item.type); return <button type="button" key={item.type} className={selected ? 'selected' : ''} disabled={selected} title={item.description} onClick={() => addWidget(item.type)}><Plus aria-hidden="true" /><span>{item.label}</span>{selected && <small>추가됨</small>}</button> })}</div></div>)}</div>
    <div className="request-result-selected" aria-label="선택된 요청 결과 위젯">{definition.widgets.length === 0 ? <p>아직 선택한 결과가 없습니다. 위에서 위젯 태그를 추가하세요.</p> : definition.widgets.map((widget, index) => { const item = catalogItemForWidget(widget); return <article key={widget.id}><div className="request-result-chip"><GripVertical aria-hidden="true" /><strong>{item.label}</strong><code>{widget.type}</code><button type="button" aria-label={`${widget.title} 위젯 삭제`} onClick={() => removeWidget(widget.id)}><X aria-hidden="true" /></button></div><div className="request-result-widget-fields"><label><span>표시 제목</span><input aria-label={`${widget.title} 표시 제목`} value={widget.title} onChange={(event) => onChange(updateWidget(definition, widget.id, { title: event.target.value }))} /></label><label><span>변수 키</span><input aria-label={`${widget.title} 변수 키`} placeholder="예: max_stress" value={widget.variable_key ?? ''} onChange={(event) => onChange(updateWidget(definition, widget.id, { variable_key: event.target.value || null }))} /></label><label className="request-result-required"><input type="checkbox" checked={widget.required} onChange={(event) => onChange(updateWidget(definition, widget.id, { required: event.target.checked }))} /><span>필수 결과</span></label></div><footer><span className="request-result-contracts">계약 · {widget.data_contracts.join(', ') || '없음'}</span><div><button type="button" aria-label={`${widget.title} 위로 이동`} disabled={index === 0} onClick={() => moveWidget(index, -1)}>↑</button><button type="button" aria-label={`${widget.title} 아래로 이동`} disabled={index === definition.widgets.length - 1} onClick={() => moveWidget(index, 1)}>↓</button><button type="button" className="request-result-remove" onClick={() => removeWidget(widget.id)}><Trash2 aria-hidden="true" /> 삭제</button></div></footer></article> })}</div>
    {validationError && <p className="request-result-validation" role="alert">{validationError}</p>}
  </section>
}
