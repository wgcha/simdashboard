import { useId } from 'react'
import { X } from 'lucide-react'
import type { InspectResponse, RecipeMapping, SemanticChartStyle, SemanticItemDefinition, SemanticWidgetDefinition, SemanticWidgetType, VersionedDefinition } from '../../shared/api/semanticMapping'
import { ItemTransferPicker, type ItemTransferOption } from '../../shared/components/ItemTransferPicker'
import './SemanticWidgetEditor.css'
import { semanticSampleValue, formatSemanticSample } from './semanticSampleValue'
import { semanticWidgetTypeLabels } from '../../shared/components/semanticLabels'

type Item = VersionedDefinition<SemanticItemDefinition>
type Props = {
  widget: SemanticWidgetDefinition
  items: Item[]
  mappings?: RecipeMapping[]
  inspect?: InspectResponse | null
  readerVersion?: number
  onChange: (next: SemanticWidgetDefinition) => void
  onRemove: () => void
}

const widgetTypes: SemanticWidgetType[] = ['kpi', 'gauge', 'table', 'bar', 'name_value', 'line', 'scatter', 'image', 'video']
const widgetNames: Record<SemanticWidgetType, string> = Object.fromEntries(widgetTypes.map((type) => [type, semanticWidgetTypeLabels[type] ?? type])) as Record<SemanticWidgetType, string>
const isNumeric = (item: Item) => ['FLOAT', 'INTEGER'].includes(item.definition.data_type)
const componentWidget = (type: SemanticWidgetType) => ['kpi', 'gauge', 'bar', 'name_value'].includes(type)
function commonVectorComponents(items: Item[]) {
  const vectors = items.filter((item) => item.definition.kind === 'vector')
  return vectors.length ? vectors.map((item) => item.definition.components ?? []).reduce((left, right) => left.filter((value) => right.includes(value))) : []
}
function vectorComponentFor(type: SemanticWidgetType, items: Item[], current?: string) {
  if (!componentWidget(type)) return undefined
  const components = commonVectorComponents(items)
  return current && components.includes(current) ? current : components[0]
}
function compatible(type: SemanticWidgetType, item: Item) {
  if (item.definition.kind === 'vector') return ['table', 'kpi', 'gauge', 'bar', 'name_value'].includes(type) && isNumeric(item)
  const expected = type === 'line' ? 'curve' : type === 'image' || type === 'video' ? type : 'scalar'
  return item.definition.kind === expected && (!['gauge', 'bar', 'name_value', 'scatter'].includes(type) || isNumeric(item))
}
const singleInput = (type: SemanticWidgetType) => ['kpi', 'gauge', 'image', 'video'].includes(type)
const suggestedType = (item: Item): SemanticWidgetType => item.definition.kind === 'curve' ? 'line' : item.definition.kind === 'scalar' ? 'kpi' : item.definition.kind === 'vector' ? 'table' : item.definition.kind

/** Editors use item meanings, while the server preview remains authoritative for conversions and joins. */
export function SemanticWidgetEditor({ widget, items, mappings = [], inspect, readerVersion, onChange, onRemove }: Props) {
  const filterListId = useId()
  const ids = widget.type === 'scatter' ? [widget.x_item_id, widget.y_item_id].filter((id): id is string => Boolean(id)) : (widget.item_ids ?? [])
  const selected = items.filter((item) => ids.includes(item.id))
  const itemOptions: ItemTransferOption[] = [...items].sort((left, right) => left.definition.label.localeCompare(right.definition.label) || left.id.localeCompare(right.id)).map((item) => ({ id: item.id, label: item.definition.label, description: item.definition.unit || '단위 없음', disabled: !compatible(widget.type, item) }))
  const allowedFilters = selected.length ? selected.map((item) => [...item.definition.dimensions, ...(item.definition.kind === 'curve' ? ['series'] : [])]).reduce((left, right) => left.filter((key) => right.includes(key))) : []
  const changeItems = (nextIds: string[]) => {
    const next = items.filter((item) => nextIds.includes(item.id))
    const type = next.some((item) => item.definition.kind === 'vector') && widget.type !== 'name_value' ? 'table' : next.length && !next.every((item) => compatible(widget.type, item)) ? suggestedType(next[0]) : widget.type
    const retained = next.filter((item) => compatible(type, item))
    const actual = singleInput(type) ? retained.slice(0, 1) : retained
    const automaticTitle = !widget.title || widget.title === '결과 값' || widget.title === selected[0]?.definition.label
    onChange({ ...widget, type, item_ids: actual.map((item) => item.id),
      title: automaticTitle && actual.length ? actual[0].definition.label : widget.title,
      filters: {}, vector_component: vectorComponentFor(type, actual, widget.vector_component), display_unit: undefined, x_display_unit: undefined, y_display_unit: undefined, threshold: undefined, chart_style: type === 'name_value' ? widget.chart_style ?? 'bar' : undefined })
  }
  const changeMultiItems = (nextIds: string[]) => {
    const ids = [...new Set(nextIds)]
    const next = items.filter((item) => ids.includes(item.id))
    const automaticTitle = !widget.title || widget.title === '결과 값' || widget.title === selected[0]?.definition.label
    onChange({ ...widget, item_ids: ids,
      title: automaticTitle && next.length ? next[0].definition.label : widget.title,
      filters: {}, vector_component: vectorComponentFor(widget.type, next, widget.vector_component), display_unit: undefined, x_display_unit: undefined, y_display_unit: undefined, threshold: undefined, chart_style: widget.type === 'name_value' ? widget.chart_style ?? 'bar' : undefined })
  }
  const changeType = (type: SemanticWidgetType) => {
    const available = selected.filter((item) => compatible(type, item))
    onChange({ ...widget, type, item_ids: type === 'scatter' ? [] : (singleInput(type) ? available.slice(0, 1) : available).map((item) => item.id),
      x_item_id: type === 'scatter' ? available[0]?.id : undefined,
      y_item_id: type === 'scatter' ? available[1]?.id : undefined,
      filters: {}, vector_component: vectorComponentFor(type, available, widget.vector_component), display_unit: undefined, x_display_unit: undefined, y_display_unit: undefined, threshold: undefined, chart_style: type === 'name_value' ? 'bar' : undefined })
  }
  const vectors = selected.filter((item) => item.definition.kind === 'vector')
  const vectorComponents = commonVectorComponents(selected)
  const sampleDetails = (inspect?.field_details ?? []).filter((field) => mappings.some((mapping) => ids.includes(mapping.result_item_id) && mapping.source === field.source))
  const filterValues = (dimension: string) => {
    const sources = mappings.filter((mapping) => ids.includes(mapping.result_item_id)).map((mapping) => dimension === 'series' ? mapping.series_source : mapping.dimensions?.[dimension]).filter(Boolean)
    return [...new Set((inspect?.rows ?? []).flatMap((row) => sources.map((source) => semanticSampleValue(row, source!, readerVersion))).filter((value) => value !== null && value !== undefined && typeof value !== 'object').map(String))]
  }
  const option = (item: Item) => <option key={item.id} value={item.id}>{item.definition.label} · {item.definition.unit || '단위 없음'}</option>
  return <section className="widget-editor semantic-widget-editor" aria-label={`위젯 설정 ${widget.title}`}>
    <div className="semantic-widget-editor-heading"><strong>결과 항목 → 표시 방식</strong><button type="button" className="icon-button danger-icon" onClick={onRemove} aria-label="위젯 삭제"><X /></button></div>
    <div className="semantic-widget-fields">
      {widget.type === 'scatter' ? <>
        <label>X축 결과 항목<select aria-label="산점도 X 항목" value={widget.x_item_id ?? ''} onChange={(event) => onChange({ ...widget, x_item_id: event.target.value, filters: {}, x_display_unit: undefined })}><option value="">X 항목 선택</option>{items.filter((item) => compatible('scatter', item)).map(option)}</select></label>
        <label>Y축 결과 항목<select aria-label="산점도 Y 항목" value={widget.y_item_id ?? ''} onChange={(event) => onChange({ ...widget, y_item_id: event.target.value, filters: {}, y_display_unit: undefined })}><option value="">Y 항목 선택</option>{items.filter((item) => compatible('scatter', item)).map(option)}</select></label>
      </> : singleInput(widget.type) ? <label className="semantic-widget-item-picker">결과 항목<select aria-label="위젯 결과 항목" value={widget.item_ids?.[0] ?? ''} onChange={(event) => changeItems([event.target.value].filter(Boolean))}><option value="">항목 선택</option>{items.map(option)}</select></label> : <div className="semantic-widget-item-transfer"><ItemTransferPicker label="위젯 결과 항목" options={itemOptions} value={widget.item_ids ?? []} onChange={changeMultiItems} emptyMessage="검색 조건에 맞는 연결 가능 항목이 없습니다." /></div>}
      <label>표시 방식<select aria-label="위젯 종류" value={widget.type} onChange={(event) => changeType(event.target.value as SemanticWidgetType)}>{widgetTypes.map((type) => <option key={type} value={type} disabled={selected.length ? !selected.every((item) => compatible(type, item)) : !items.some((item) => compatible(type, item))}>{widgetNames[type]}</option>)}</select></label>
      {vectors.length && componentWidget(widget.type) ? <label>벡터 표시 성분<select aria-label="벡터 표시 성분" value={widget.vector_component ?? ''} onChange={(event) => onChange({ ...widget, vector_component: event.target.value || undefined })}><option value="">성분 선택</option>{vectorComponents.map((component) => <option key={component} value={component}>{component}</option>)}</select><small>선택한 성분 하나를 값으로 표시합니다. 빈 성분은 결과 없음으로 표시합니다.</small>{vectors.length > 1 && !vectorComponents.length ? <small className="field-error">선택한 벡터에 공통 성분이 없습니다. 표를 선택하거나 공통 성분이 있는 항목만 남기세요.</small> : null}</label> : null}
      {widget.type === 'name_value' ? <label>차트 스타일<select aria-label="이름·값 차트 스타일" value={widget.chart_style ?? 'bar'} onChange={(event) => onChange({ ...widget, chart_style: event.target.value as SemanticChartStyle })}><option value="bar">막대</option><option value="dot">점</option><option value="line">선</option></select><small>결과 항목 이름과 값을 비교합니다.</small></label> : null}
      <label>위젯 제목<input aria-label="위젯 제목" value={widget.title} onChange={(event) => onChange({ ...widget, title: event.target.value })} /></label>
      <label>소수 자릿수<input type="number" step={1} min={0} max={10} aria-label="표시 자릿수" value={widget.decimals ?? 2} onChange={(event) => onChange({ ...widget, decimals: Math.max(0, Math.min(10, Math.trunc(Number(event.target.value)))) })} /><small>예: 2 → 12.35 · 0 → 12</small></label>
      {widget.type === 'scatter' ? <><label>X축 표시 단위<input aria-label="X 표시 단위" placeholder={selected.find((item) => item.id === widget.x_item_id)?.definition.unit || '원본 단위 유지'} value={widget.x_display_unit ?? ''} onChange={(event) => onChange({ ...widget, x_display_unit: event.target.value || undefined })} /></label><label>Y축 표시 단위<input aria-label="Y 표시 단위" placeholder={selected.find((item) => item.id === widget.y_item_id)?.definition.unit || '원본 단위 유지'} value={widget.y_display_unit ?? ''} onChange={(event) => onChange({ ...widget, y_display_unit: event.target.value || undefined })} /></label></> : <label>표시 단위<input aria-label="표시 단위" placeholder={selected.length === 1 ? selected[0].definition.unit || '원본 단위 유지' : '항목별 단위 유지'} disabled={selected.some((item) => !isNumeric(item))} value={widget.display_unit ?? ''} onChange={(event) => onChange({ ...widget, display_unit: event.target.value || undefined })} /><small>비워 두면 각 항목의 기준 단위를 사용합니다.</small></label>}
      {widget.type === 'line' ? <label>X축 표시 단위<input aria-label="X 표시 단위" value={widget.x_display_unit ?? ''} placeholder="원본 X축 단위 유지" onChange={(event) => onChange({ ...widget, x_display_unit: event.target.value || undefined })} /></label> : null}
      {widget.type === 'gauge' ? <label>판정 기준값 ({selected[0]?.definition.unit || '기준 단위'})<input aria-label="판정 기준값" type="number" value={widget.threshold ?? ''} onChange={(event) => onChange({ ...widget, threshold: event.target.value === '' ? undefined : Number(event.target.value) })} /><small>기준 이하 PASS · 초과 FAIL</small></label> : null}
    </div>
    <p className="semantic-widget-help">{widget.type === 'line' ? 'X/Y 필드가 연결된 곡선 항목만 선택할 수 있습니다.' : widget.type === 'scatter' ? '같은 측정 위치의 X/Y 값을 짝지어 표시합니다. 한 쌍의 값은 한 점으로 표시됩니다.' : widget.type === 'name_value' ? '여러 숫자 결과를 항목 이름과 값으로 비교합니다. 막대·점·선 중 원하는 표시를 선택하세요.' : '벡터 전체는 표에 한 행으로 표시합니다. 값 카드·기준값·막대는 벡터 성분 하나를 선택합니다.'}</p>
    {allowedFilters.length ? <fieldset className="semantic-widget-filters"><legend>측정 위치 선택 · 비워 두면 전체</legend>{allowedFilters.map((dimension, index) => <label key={dimension}>{dimension}<input aria-label={`${dimension} 위치 필터`} list={`${filterListId}-${index}`} value={widget.filters?.[dimension] ?? ''} onChange={(event) => { const filters = { ...widget.filters }; if (event.target.value) filters[dimension] = event.target.value; else delete filters[dimension]; onChange({ ...widget, filters }) }} /><datalist id={`${filterListId}-${index}`}>{filterValues(dimension).map((value) => <option key={value} value={value} />)}</datalist></label>)}</fieldset> : null}
    {Object.keys(widget.filters ?? {}).some((key) => !allowedFilters.includes(key)) ? <div className="field-error">연결 항목에 없는 위치 필터가 있습니다. <button type="button" className="ghost-button" onClick={() => onChange({ ...widget, filters: Object.fromEntries(Object.entries(widget.filters ?? {}).filter(([key]) => allowedFilters.includes(key))) })}>유효하지 않은 필터 삭제</button></div> : null}
    {sampleDetails.length ? <details className="semantic-widget-source-preview"><summary>연결된 원본 샘플 {sampleDetails.length}개 보기</summary><p>아래는 변환 전 원본입니다. 적용 결과는 통합 미리보기에서 확인합니다.</p><ul>{sampleDetails.map((field) => <li key={field.source}><span>{field.label ?? field.source}</span><strong>{formatSemanticSample(field.value, selected.find((item) => mappings.some((mapping) => mapping.source === field.source && mapping.result_item_id === item.id))?.definition.components)} {field.unit ?? ''}</strong></li>)}</ul></details> : null}
  </section>
}
