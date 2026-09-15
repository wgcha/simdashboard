import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { ChevronDown, FileJson, LoaderCircle, RotateCcw, Table2, Upload } from 'lucide-react'
import { semanticMappingApi, type InspectFieldDetail, type InspectResponse } from '../../shared/api/semanticMapping'

type InspectorProps = {
  sample: File | null
  inspect: InspectResponse | null
  busy: string
  onSelectSample: (file: File | null) => void
  onReset: () => void
  onAddMappings?: (sources: string[]) => void
  onCreateItems?: (sources: string[]) => void
  onInspectionPage?: (response: InspectResponse) => void
}

import { hasSampleValue, isNumericVectorSample } from './semanticSampleValue'

const PAGE_FIELDS = 100
const PAGE_ROWS = 50

function isSelectableField(detail: InspectFieldDetail, details: InspectFieldDetail[]) {
  const source = fieldName(detail)
  const parent = details.find((candidate) => candidate.source && source.startsWith(`${candidate.source}/`) && candidate.mappable !== false && isNumericVectorSample(candidate.value))
  return !parent && detail.mappable !== false && (detail.value === null || typeof detail.value !== 'object' || isNumericVectorSample(detail.value))
}
function fieldName(detail: InspectFieldDetail) { return typeof detail.source === 'string' ? detail.source : typeof detail.label === 'string' ? detail.label : '' }
function safeValue(value: unknown) {
  if (value === null) return <span className="sample-value null">값 없음 · null</span>
  if (value === undefined) return <span className="sample-value missing">—</span>
  if (typeof value === 'string' && value.trim().length === 0) return <span className="sample-value blank">값 없음</span>
  if (typeof value === 'string' && value.length > 280) return <details className="sample-value-object"><summary><ChevronDown /> {value.slice(0, 120)}… ({value.length}자)</summary><code>{value.slice(0, 2000)}{value.length > 2000 ? '…' : ''}</code></details>
  if (Array.isArray(value) || (typeof value === 'object' && value !== null)) return <details className="sample-value-object"><summary><ChevronDown /> {Array.isArray(value) ? `${hasSampleValue(value) ? '벡터/배열' : '값 없음'} · ${value.length}성분` : 'object'}</summary><code>{JSON.stringify(value, null, 2)}</code></details>
  return <span className="sample-value">{String(value)}</span>
}
function sourceValue(row: Record<string, unknown>, source: string): unknown {
  if (source.startsWith('#/')) {
    let current: unknown = row
    for (const segment of source.slice(2).split('/').map((part) => part.replaceAll('~1', '/').replaceAll('~0', '~'))) {
      if (Array.isArray(current)) current = current[Number(segment)]
      else if (current && typeof current === 'object') current = (current as Record<string, unknown>)[segment]
      else return source in row ? row[source] : undefined
    }
    return current
  }
  return row[source]
}

function detailFor(response: InspectResponse | null): InspectFieldDetail[] {
  if (!response) return []
  if (response.field_details?.length) return response.field_details
  const fields = response.fields ?? []
  const row = response.rows?.[0] ?? {}
  return fields.map((source) => ({ source, label: source, value: row[source], data_type: row[source] === null ? 'NULL' : Array.isArray(row[source]) ? 'ARRAY' : typeof row[source] }))
}

function mergeInspection(previous: InspectResponse, next: InspectResponse): InspectResponse {
  const bySource = new Map<string, InspectFieldDetail>()
  for (const detail of detailFor(previous)) { const key = fieldName(detail); if (key) bySource.set(key, detail) }
  for (const detail of detailFor(next)) { const key = fieldName(detail); if (key) bySource.set(key, detail) }
  const fields = [...new Set([...(previous.fields ?? []), ...(next.fields ?? []), ...[...bySource.keys()]])]
  return { ...previous, ...next, fields, field_details: [...bySource.values()], rows: next.rows?.length ? next.rows : previous.rows, field_count: next.field_count ?? previous.field_count, row_count: next.row_count ?? previous.row_count, upload_id: next.upload_id ?? previous.upload_id }
}

export function SemanticSampleInspector({ sample, inspect, busy, onSelectSample, onReset, onAddMappings, onCreateItems, onInspectionPage }: InspectorProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [loaded, setLoaded] = useState<InspectResponse | null>(inspect)
  const [showEmptyFields, setShowEmptyFields] = useState(true)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const selectionUpload = useRef<string | undefined>(undefined)
  const [fieldOffset, setFieldOffset] = useState(0)
  const [rowOffset, setRowOffset] = useState(0)
  const [loadingPage, setLoadingPage] = useState('')
  const [pageError, setPageError] = useState('')
  const pageAbort = useRef<AbortController | null>(null)
  const pageGeneration = useRef(0)
  const [rowPages, setRowPages] = useState<Map<number, Record<string, unknown>[]>>(new Map())
  const details = useMemo(() => detailFor(loaded), [loaded])
  const totalFields = loaded?.field_count ?? details.length
  const hasMoreFields = fieldOffset + PAGE_FIELDS < totalFields
  const hasMoreRows = rowOffset + PAGE_ROWS < (loaded?.row_count ?? 0)

  useEffect(() => {
    setLoaded(inspect)
    setShowEmptyFields(true)
    setFieldOffset(inspect?.field_offset ?? inspect?.page?.field_offset ?? 0)
    setRowOffset(inspect?.row_offset ?? inspect?.page?.row_offset ?? 0)
    if (!inspect?.upload_id || selectionUpload.current !== inspect.upload_id) {
      setSelected(new Set(detailFor(inspect).filter((detail) => isSelectableField(detail, detailFor(inspect))).map(fieldName).filter(Boolean)))
    }
    selectionUpload.current = inspect?.upload_id
    setPageError('')
    pageAbort.current?.abort()
    pageGeneration.current += 1
    setRowPages(inspect ? new Map([[inspect.row_offset ?? inspect.page?.row_offset ?? 0, inspect.rows ?? []]]) : new Map())
  }, [inspect?.upload_id, JSON.stringify(inspect?.recipe_suggestion)])

  const choose = (file: File | null) => {
    if (!file) return
    setSelected(new Set())
    onSelectSample(file)
  }
  const drop = (event: DragEvent<HTMLLabelElement>) => { event.preventDefault(); choose(event.dataTransfer.files?.[0] ?? null) }
  const reset = () => { if (inputRef.current) inputRef.current.value = ''; pageAbort.current?.abort(); pageGeneration.current += 1; setLoaded(null); setSelected(new Set()); setPageError(''); setRowPages(new Map()); onReset() }
  const page = async (kind: 'fields' | 'rows') => {
    if (!loaded?.upload_id || loadingPage) return
    const generation = pageGeneration.current
    pageAbort.current?.abort()
    const controller = new AbortController()
    pageAbort.current = controller
    const nextFieldOffset = kind === 'fields' ? fieldOffset + PAGE_FIELDS : fieldOffset
    const nextRowOffset = kind === 'rows' ? rowOffset + PAGE_ROWS : rowOffset
    setLoadingPage(kind); setPageError('')
    try {
      const next = await semanticMappingApi.inspectUpload(loaded.upload_id, { field_offset: nextFieldOffset, field_limit: PAGE_FIELDS, row_offset: nextRowOffset, row_limit: PAGE_ROWS, overrides: loaded.recipe_suggestion ?? undefined }, controller.signal)
      if (controller.signal.aborted || generation !== pageGeneration.current) return
      setLoaded((current) => current ? mergeInspection(current, next) : next)
      onInspectionPage?.(next)
      if (kind === 'fields') setFieldOffset(nextFieldOffset)
      if (kind === 'rows') { setRowOffset(nextRowOffset); setRowPages((current) => new Map(current).set(nextRowOffset, next.rows ?? [])) }
    } catch (reason) { if (!controller.signal.aborted && generation === pageGeneration.current) setPageError(reason instanceof Error ? reason.message : '페이지를 불러오지 못했습니다.')
    } finally { if (pageAbort.current === controller) { pageAbort.current = null; setLoadingPage('') } }
  }
  const pageDetails = details.slice(fieldOffset, fieldOffset + PAGE_FIELDS)
  const visibleDetails = showEmptyFields ? pageDetails : pageDetails.filter((detail) => hasSampleValue(detail.value))
  const visibleSources = pageDetails.map(fieldName).filter(Boolean)
  const selectableSources = visibleDetails.filter((detail) => isSelectableField(detail, details)).map(fieldName).filter(Boolean)
  const canSelect = (source: string) => { const detail = details.find((candidate) => fieldName(candidate) === source); if (!detail || detail.mappable === false || detail.value != null && typeof detail.value === 'object' && !isNumericVectorSample(detail.value)) return false; const parent = details.find((candidate) => candidate.source && source.startsWith(`${candidate.source}/`) && isNumericVectorSample(candidate.value)); return !parent?.source || !selected.has(parent.source) }
  const selectedSources = [...selected].filter(canSelect)
  const toggle = (source: string) => { if (!canSelect(source)) return; setSelected((current) => { const next = new Set(current); if (next.has(source)) next.delete(source); else { next.add(source); for (const candidate of next) if (candidate.startsWith(`${source}/`)) next.delete(candidate) }; return next }) }
  const toggleVisible = () => setSelected((current) => { const next = new Set(current); const all = selectableSources.every((source) => next.has(source)); selectableSources.forEach((source) => all ? next.delete(source) : next.add(source)); return next })
  const addSelected = () => { const sources = selectedSources; if (sources.length) { onAddMappings?.(sources); setSelected(new Set()) } }
  const currentRows = rowPages.get(rowOffset) ?? loaded?.rows ?? []
  const previousRows = () => setRowOffset(Math.max(0, rowOffset - PAGE_ROWS))

  return <div className="semantic-card sample-inspector" data-testid="semantic-sample-inspector">
    <div className="semantic-card-heading"><div><span>STEP 01 · INSPECT</span><h2>샘플 파일 업로드</h2></div><Table2 /></div>
    <label className="file-drop sample-file-drop" onDragOver={(event) => event.preventDefault()} onDrop={drop}>
      <input ref={inputRef} type="file" accept=".csv,.json,.tsv,.txt,text/csv,text/tab-separated-values,application/json,text/plain" onChange={(event) => choose(event.target.files?.[0] ?? null)} />
      {busy === 'inspect' ? <LoaderCircle className="spin" /> : <Upload />}
      <strong>{sample ? sample.name : 'CSV 또는 JSON을 끌어오거나 선택하세요'}</strong>
      <small>처음 50행·100필드부터 확인하고, 큰 파일은 필요한 페이지를 추가로 불러옵니다.</small>
    </label>
    {sample || loaded ? <div className="sample-inspector-toolbar"><span className="sample-file-name">{sample?.name ?? '업로드된 샘플'}</span><button className="ghost-button" type="button" onClick={reset}><RotateCcw /> 샘플 초기화</button></div> : null}
    {loaded ? <>
      <div className="inspect-summary"><span><b>{loaded.format.toUpperCase()}</b> 형식</span><span><b>{loaded.field_count ?? loaded.fields.length}</b> 필드</span><span><b>{loaded.row_count ?? loaded.rows.length}</b> 행</span>{loaded.recipe_suggestion ? <span className="suggestion-badge"><b>추천</b> 자동 감지 설정</span> : null}</div>
      {loaded.warnings?.length ? <div className="sample-inspect-warnings" role="status">{loaded.warnings.map((warning) => <span key={warning}>{warning}</span>)}</div> : null}
      {loaded.recipe_suggestion ? <div className="sample-recipe-suggestion"><strong>감지된 레시피 설정</strong><span>{loaded.recipe_suggestion.input_layout ?? loaded.recipe_suggestion.format ?? loaded.format}{loaded.recipe_suggestion.encoding ? ` · ${loaded.recipe_suggestion.encoding}` : ''}{loaded.recipe_suggestion.delimiter ? ` · 구분자 ${loaded.recipe_suggestion.delimiter}` : ''}</span></div> : null}
      <div className="sample-field-actions"><label><input type="checkbox" checked={showEmptyFields} onChange={(event) => setShowEmptyFields(event.target.checked)} /> 빈값 필드 표시</label><label><input type="checkbox" checked={selectableSources.length > 0 && selectableSources.every((source) => selected.has(source))} onChange={toggleVisible} /> 현재 페이지의 사용 가능한 값 선택</label><button type="button" className="ghost-button" onClick={addSelected} disabled={!selectedSources.length}>선택 필드 매핑 추가 ({selectedSources.length})</button><button type="button" className="ghost-button" onClick={() => { const sources = selectedSources; onCreateItems?.(sources); setSelected(new Set()) }} disabled={!selectedSources.length || !!busy}>선택 값을 결과 항목으로 연결 ({selectedSources.length})</button><small>정의와 샘플 값을 구분합니다. 빈 벡터도 항목으로 정의할 수 있고 값은 없음으로 표시합니다. 0과 false는 보존합니다. 좌표 배열은 하나의 벡터로 추천하며 성분 이름과 순서를 확인하세요. 같은 의미의 기존 항목은 재사용합니다.</small></div>
      <div className="sample-field-table-wrap"><table className="sample-field-table"><thead><tr><th aria-label="선택" /><th>원본 키</th><th>샘플 값</th><th>자료형</th><th>단위</th><th>상태</th></tr></thead><tbody>{visibleDetails.map((detail, index) => { const source = fieldName(detail); const counts = [detail.missing_count ? '누락 ' + detail.missing_count : '', detail.null_count ? 'null ' + detail.null_count : '', detail.empty_count ? '빈값 ' + detail.empty_count : ''].filter(Boolean); return <tr key={`${source}-${index}`}><td><input type="checkbox" aria-label={`${source} 선택`} checked={selected.has(source)} disabled={!canSelect(source)} onChange={() => toggle(source)} /></td><td><code>{source || '—'}</code>{detail.label && detail.label !== source ? <small>{detail.label}</small> : null}</td><td>{safeValue(detail.value)}</td><td>{isNumericVectorSample(detail.value) ? 'VECTOR 추천' : detail.data_type ?? '—'}</td><td>{detail.unit ?? '—'}</td><td>{!hasSampleValue(detail.value) ? <span className="field-not-mappable">값 없음</span> : detail.mappable === false ? <span className="field-not-mappable">지원 안 함</span> : <span className="field-mappable">연결 가능</span>}{counts.length ? <small className="sample-state-counts">{counts.join(" · ")}</small> : null}</td></tr> })}</tbody></table></div>
      <div className="sample-row-table-wrap"><div className="sample-row-heading"><strong>실제 샘플 행</strong><small>{rowOffset + 1}–{rowOffset + currentRows.length}행</small></div><table className="sample-row-table"><thead><tr>{visibleSources.map((source) => <th key={source}><code>{source}</code></th>)}</tr></thead><tbody>{currentRows.map((row, index) => <tr key={`${rowOffset}-${index}`}>{visibleSources.map((source) => <td key={source}>{safeValue(sourceValue(row, source))}</td>)}</tr>)}</tbody></table></div>
      {pageError ? <div className="sample-page-error" role="alert">{pageError}</div> : null}<div className="sample-page-actions"><button type="button" className="ghost-button" onClick={() => setFieldOffset(Math.max(0, fieldOffset - PAGE_FIELDS))} disabled={fieldOffset === 0 || !!loadingPage}>이전 필드</button><button type="button" className="ghost-button" onClick={() => void page('fields')} disabled={!hasMoreFields || !!loadingPage}>{loadingPage === 'fields' ? <LoaderCircle className="spin" /> : <FileJson />} 필드 더 보기</button><button type="button" className="ghost-button" onClick={previousRows} disabled={rowOffset === 0 || !!loadingPage}>이전 행</button><button type="button" className="ghost-button" onClick={() => void page('rows')} disabled={!hasMoreRows || !!loadingPage}>행 더 보기</button><small>{visibleSources.length}개 필드 표시 · 선택 {selected.size}개</small></div>
    </> : <div className="empty-inline">샘플을 올리면 키, 값, 자료형, 단위와 추천 입력 설정을 확인할 수 있습니다.</div>}
  </div>
}
