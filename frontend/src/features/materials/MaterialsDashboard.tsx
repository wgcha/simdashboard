import { useEffect, useMemo, useRef, useState } from 'react'
import { Download, Search, RotateCw, Box, Layers, AlertTriangle, ChevronLeft, ChevronRight } from 'lucide-react'
import { folderEnvironmentApi } from '../../shared/api/folderEnvironment'
import {
  materialsApi,
  type MaterialsCatalog,
  type MaterialsDeck,
  type MaterialsEnvironment,
  type MaterialsFailure,
  type MaterialsFunction,
  type MaterialsMaterial,
  type MaterialsPart,
  type MaterialsProperty,
  type MaterialsScene,
  type MaterialsSource,
} from '../../shared/api/materials'
import { HierarchyChoice } from '../../shared/components/HierarchyChoice'
import { useCaseHierarchyParams } from '../../shared/hooks/useCaseHierarchyParams'
import './MaterialsDashboard.css'

type Props = {
  projectId: string
  requestId: string
  canRefreshSchema?: boolean
  refreshToken?: number
}

type SortKey = 'part' | 'material' | 'property' | 'thickness' | 'density'

function text(value: unknown, depth = 0): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (depth > 2) return '…'
  if (Array.isArray(value)) {
    const preview = value.slice(0, 6).map((item) => text(item, depth + 1)).join(', ')
    return `[${preview}${value.length > 6 ? ', …' : ''}] · ${value.length}개`
  }
  if (typeof value === 'object') {
    const fields = Object.entries(value as Record<string, unknown>).slice(0, 8)
    return `{${fields.map(([key, item]) => `${key}: ${text(item, depth + 1)}`).join(', ')}${Object.keys(value).length > 8 ? ', …' : ''}}`
  }
  return String(value)
}

function sourceLabel(source: MaterialsSource | null | undefined): string {
  if (!source?.file) return '출처 정보 없음'
  return source.line == null ? source.file : `${source.file}:${source.line}`
}

function numberLabel(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('ko-KR', { maximumSignificantDigits: 7 }).format(value)
}

function thicknessLabel(value: number | null | undefined, lengthUnit: string | null): string {
  return value == null ? '—' : `${numberLabel(value)} ${lengthUnit ?? '단위 미확인'}`
}

function entries(record: Record<string, unknown>): Array<[string, unknown]> {
  return Object.entries(record).filter(([, value]) => value !== null && value !== undefined && value !== '')
}

function csvCell(value: unknown): string {
  let output = value === null || value === undefined ? '' : String(value)
  if (/^\s*[=+\-@]/.test(output)) output = `'${output}`
  return `"${output.replaceAll('"', '""')}"`
}

function downloadCsv(rows: MaterialsPart[], materialById: Map<string, MaterialsMaterial>, propertyById: Map<string, MaterialsProperty>, scene: MaterialsScene | null, requestId: string, lengthUnit: string | null) {
  const header = ['Part ID', 'Part name', 'Property ID', 'Property name', 'Material ID', 'Material name', 'Material subtype', 'Density raw', 'Density value', 'Density unit', 'Density converted', 'Density converted unit', 'Thickness', 'Part source', 'Property source', 'Material source']
  const lines = [header.map(csvCell).join(',')]
  for (const part of rows) {
    const property = part.property_id ? propertyById.get(part.property_id) : undefined
    const material = part.material_id ? materialById.get(part.material_id) : undefined
    lines.push([
      part.id, part.title, part.property_id, property?.title, part.material_id, material?.title, material?.subtype,
      material?.density?.raw, material?.density?.value, material?.density?.unit, material?.density?.converted_value,
      material?.density?.converted_unit, thicknessLabel(part.thickness ?? property?.thickness, lengthUnit) === '—' ? property?.thickness_display : thicknessLabel(part.thickness ?? property?.thickness, lengthUnit), sourceLabel(part.source),
      sourceLabel(property?.source), sourceLabel(material?.source),
    ].map(csvCell).join(','))
  }
  const blob = new Blob([`\uFEFF${lines.join('\r\n')}`], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `materials-${requestId}-${scene?.scene_id ?? 'scene'}.csv`.replace(/[^\w.-]+/g, '_')
  anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

function FieldTable({ title, fields, rawFields, source }: { title: string; fields: Record<string, unknown>; rawFields?: Record<string, unknown>; source?: MaterialsSource | null }) {
  const values = entries(fields)
  const raw = rawFields ?? {}
  return <section className="materials-detail-section">
    <header><h3>{title}</h3>{source && <small title={sourceLabel(source)}>{sourceLabel(source)}</small>}</header>
    {values.length ? <dl className="materials-fields">{values.map(([name, value]) => <div key={name} title={text(value)}><dt>{name}</dt><dd>{text(value)}{raw[name] !== undefined && raw[name] !== null && raw[name] !== '' && String(raw[name]) !== String(value) ? <small>원본: {text(raw[name])}</small> : null}</dd></div>)}</dl> : <p className="materials-empty-inline">표시할 물성이 없습니다.</p>}
  </section>
}

function CurveCard({ curve, uses }: { curve: MaterialsFunction; uses: MaterialsFunction['uses'] }) {
  const points = curve.points.filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y))
  if (!points.length) return <article className="materials-curve-card"><header><strong>FUNCT {curve.id}</strong><span>{curve.title ?? '이름 없음'}</span></header><p>표시할 점이 없습니다. 출처: {sourceLabel(curve.source)}</p></article>
  let minX = points[0].x, maxX = points[0].x, minY = points[0].y, maxY = points[0].y
  points.forEach((point) => { minX = Math.min(minX, point.x); maxX = Math.max(maxX, point.x); minY = Math.min(minY, point.y); maxY = Math.max(maxY, point.y) })
  const xRange = maxX - minX || 1, yRange = maxY - minY || 1
  const plotPoints = envelopePoints(points)
  const coords = plotPoints.map((point) => `${30 + ((point.x - minX) / xRange) * 570},${150 - ((point.y - minY) / yRange) * 132}`).join(' ')
  const firstUse = uses[0]
  return <article className="materials-curve-card">
    <header><strong>FUNCT {curve.id}</strong><span title={curve.title ?? undefined}>{curve.title ?? `${curve.point_count}개 점`}</span></header>
    <div className="materials-curve-units">{firstUse?.role ?? '참조 함수'} · X: {firstUse?.x_unit ?? '원본 단위'} · Y: {firstUse?.y_unit ?? '원본 단위'} · 전체 {curve.point_count}점{plotPoints.length < points.length ? ` · 극값 포함 ${plotPoints.length}점 표시` : ''}</div>
    <svg viewBox="0 0 620 180" role="img" aria-label={`함수 ${curve.id} 곡선`}>
      <path className="materials-curve-axis" d="M30 12 V150 H600" />
      <polyline points={coords} />
    </svg>
    <div className="materials-curve-range"><span>{numberLabel(minX)} → {numberLabel(maxX)}</span><span>{numberLabel(minY)} → {numberLabel(maxY)}</span></div>
    <small title={sourceLabel(curve.source)}>{sourceLabel(curve.source)}</small>
  </article>
}

function envelopePoints(points: Array<{ x: number; y: number }>, maximum = 1200) {
  if (points.length <= maximum) return points
  const bucketCount = Math.floor(maximum / 4)
  const bucketSize = Math.ceil(points.length / bucketCount)
  const sampled: Array<{ x: number; y: number }> = []
  for (let start = 0; start < points.length; start += bucketSize) {
    const end = Math.min(points.length, start + bucketSize)
    let minIndex = start, maxIndex = start
    for (let index = start + 1; index < end; index += 1) {
      if (points[index].y < points[minIndex].y) minIndex = index
      if (points[index].y > points[maxIndex].y) maxIndex = index
    }
    const indices = [...new Set([start, minIndex, maxIndex, end - 1])].sort((left, right) => left - right)
    indices.forEach((index) => sampled.push(points[index]))
  }
  return sampled
}

function MaterialsDetails({ part, material, property, deck }: { part: MaterialsPart; material?: MaterialsMaterial; property?: MaterialsProperty; deck: MaterialsDeck }) {
  const relatedParts = deck.parts.filter((candidate) => candidate.material_id && candidate.material_id === material?.id)
  const failureIds = new Set((material?.failures ?? []).map((failure) => failure.id))
  const curves = deck.functions.flatMap((curve) => {
    const uses = curve.uses.filter((use) =>
      (use.owner_type === 'material' && use.owner_id === material?.id) ||
      (use.owner_type === 'property' && use.owner_id === property?.id) ||
      (use.owner_type === 'failure' && failureIds.has(use.owner_id ?? '')))
    return uses.length ? [{ curve, uses }] : []
  })

  return <aside className="materials-detail-panel" aria-label="선택 Part 상세">
    <header className="materials-detail-heading">
      <span>선택 Part</span>
      <h2 title={part.title ?? undefined}>{part.title || `Part ${part.id}`}</h2>
      <code>/{`PART/${part.id}`}</code>
    </header>
    <section className="materials-detail-section materials-reference-summary">
      <header><h3>참조 상태</h3><small title={sourceLabel(part.source)}>{sourceLabel(part.source)}</small></header>
      <dl>
        <div><dt>Property</dt><dd title={property?.title ?? part.property_id ?? undefined}>{property ? `${property.subtype ?? 'PROP'} · ${property.title ?? property.id}` : part.property_id ? `누락 · ${part.property_id}` : '참조 없음'}</dd></div>
        <div><dt>Material</dt><dd title={material?.title ?? part.material_id ?? undefined}>{material ? `${material.subtype ?? 'MAT'} · ${material.title ?? material.id}` : part.material_id ? `누락 · ${part.material_id}` : '참조 없음'}</dd></div>
        <div><dt>두께</dt><dd>{thicknessLabel(part.thickness ?? property?.thickness, deck.unit_system.input.length) === '—' ? property?.thickness_display ?? '—' : thicknessLabel(part.thickness ?? property?.thickness, deck.unit_system.input.length)}</dd></div>
      </dl>
      {property && part.material_id && property.fields.material_id !== undefined && String(property.fields.material_id) !== part.material_id && <p className="materials-reference-warning"><AlertTriangle /> Part가 가리키는 Material과 Property의 Material ID가 다릅니다.</p>}
    </section>
    {material ? <>
      <section className="materials-detail-section">
        <header><h3>Material · {material.subtype ?? '유형 미상'} / {material.id}</h3><small title={sourceLabel(material.source)}>{sourceLabel(material.source)}</small></header>
        <div className="materials-density-grid">
          <div><span>원본 RHO_I</span><strong title={material.density?.raw ?? undefined}>{material.density?.raw ?? '—'}</strong><small>{material.density?.unit ?? '원본 단위 미상'}</small></div>
          <div><span>환산 밀도</span><strong>{numberLabel(material.density?.converted_value ?? null)}</strong><small>{material.density?.converted_unit ?? '환산 불가'}</small></div>
          <div><span>대표 탄성계수</span><strong>{numberLabel(material.representative_e)}</strong><small>추출 대표값 · 단위 정보 없음</small></div>
          <div><span>Material ID</span><strong>{material.id}</strong><small>{material.law_id == null ? material.subtype ?? 'LAW 미지정' : `LAW ${material.law_id}`}</small></div>
        </div>
      </section>
      <FieldTable title="Material 물성 필드" fields={material.fields} rawFields={material.raw_fields} source={material.source} />
      <section className="materials-detail-section">
        <header><h3>Failure 모델</h3><small>{material.failures.length}개</small></header>
        {material.failures.length ? <div className="materials-failure-list">{material.failures.map((failure) => <FailureCard key={`${failure.id}-${failure.subtype}`} failure={failure} />)}</div> : <p className="materials-empty-inline">연결된 Failure 모델이 없습니다.</p>}
      </section>
      {relatedParts.length > 1 && <section className="materials-detail-section">
        <header><h3>같은 Material을 쓰는 Part</h3><small>{relatedParts.length}개</small></header>
        <p className="materials-context-note">Material 상세를 공유하더라도 선택한 Part의 Property와 두께 문맥은 유지됩니다.</p>
        <div className="materials-related-parts">{relatedParts.map((candidate) => <span className={candidate.id === part.id ? 'selected' : ''} key={candidate.id} title={`${candidate.id} · ${candidate.title ?? '이름 없음'}`}>{candidate.id} · {candidate.title || '이름 없음'}</span>)}</div>
      </section>}
      <section className="materials-detail-section">
        <header><h3>함수 곡선</h3><small>{curves.length}개 연결</small></header>
        {curves.length ? <div className="materials-curves">{curves.map(({ curve, uses }) => <CurveCard key={curve.id} curve={curve} uses={uses} />)}</div> : <p className="materials-empty-inline">이 Material, Property 또는 Failure가 참조하는 함수 곡선이 없습니다.</p>}
      </section>
    </> : <section className="materials-detail-missing"><AlertTriangle /><div><strong>{part.material_id ? `Material ${part.material_id}을 찾을 수 없습니다.` : '이 Part에는 Material 참조가 없습니다.'}</strong><span>참조를 추정해 연결하지 않고 원본 덱의 ID 관계를 그대로 표시합니다.</span></div></section>}
    {property && <FieldTable title={`Property · ${property.subtype ?? '유형 미상'} / ${property.id}`} fields={property.fields} rawFields={property.raw_fields} source={property.source} />}
  </aside>
}

function FailureCard({ failure }: { failure: MaterialsFailure }) {
  return <details className="materials-failure-card">
    <summary><strong>FAIL {failure.id}</strong><span>{failure.subtype ?? '유형 미상'} · {failure.title ?? '이름 없음'}</span></summary>
    <FieldTable title="Failure 필드" fields={failure.fields} rawFields={failure.raw_fields} source={failure.source} />
  </details>
}

type HierarchyLevel = 'case' | 'case_load' | 'case_run' | 'case_option'

function sceneMatches(scene: MaterialsScene, path: { caseId: string; loadCaseId: string; runId: string; optionId: string }) {
  // A Scene level id of '' means the Folder Schema omits that level (for
  // example no load-case folder); it then matches whatever the path holds.
  return scene.case_id === path.caseId
    && (!scene.load_case_id || scene.load_case_id === path.loadCaseId)
    && (!scene.execution_run_id || scene.execution_run_id === path.runId)
    && (!scene.run_option_id || scene.run_option_id === path.optionId)
}

function materialsPath(catalog: MaterialsCatalog | null, path: { caseId: string; loadCaseId: string; runId: string; optionId: string }) {
  const hierarchy = catalog?.hierarchy
  const cases = hierarchy?.cases ?? []
  const loads = (hierarchy?.load_cases ?? []).filter((item) => item.case_id === path.caseId)
  const runs = (hierarchy?.execution_runs ?? []).filter((item) => item.case_id === path.caseId && item.load_case_id === path.loadCaseId)
  const options = (hierarchy?.run_options ?? []).filter((item) => item.case_id === path.caseId && item.execution_run_id === path.runId)
  // Without any Folder Schema Case the Scene list is the only level left.
  const flat = !cases.length
  const ready = flat || (Boolean(path.caseId)
    && (!loads.length || Boolean(path.loadCaseId))
    && (!runs.length || Boolean(path.runId))
    && (!options.length || Boolean(path.optionId)))
  const scenes = !catalog || !ready ? [] : flat ? catalog.scenes : catalog.scenes.filter((item) => sceneMatches(item, path))
  return { cases, loads, runs, options, scenes, ready, flat }
}

export function MaterialsDashboard({ projectId, requestId, canRefreshSchema = false, refreshToken = 0 }: Props) {
  const hierarchyParams = useCaseHierarchyParams()
  const { caseId, loadCaseId, runId, optionId, get: getParam, update: updateParams, select: selectLevel } = hierarchyParams
  const [catalogState, setCatalogState] = useState<{ requestId: string; value: MaterialsCatalog } | null>(null)
  // Ignore a catalog that belongs to the previous request until the new one arrives.
  const catalog = catalogState?.requestId === requestId ? catalogState.value : null
  const [deck, setDeck] = useState<MaterialsDeck | null>(null)
  const [loadingCatalog, setLoadingCatalog] = useState(false)
  const [loadingDeck, setLoadingDeck] = useState(false)
  const [error, setError] = useState('')
  const [manualRefresh, setManualRefresh] = useState(0)
  const [refreshBusy, setRefreshBusy] = useState(false)
  const [refreshNotice, setRefreshNotice] = useState('')
  const refreshScope = useRef('')
  const deckKey = useRef('')
  const [sort, setSort] = useState<{ key: SortKey; direction: 'asc' | 'desc' }>({ key: 'part', direction: 'asc' })
  const [page, setPage] = useState(0)
  const environment: MaterialsEnvironment = 'DISTRIBUTION'
  useEffect(() => { refreshScope.current = `${projectId}:${requestId}` }, [projectId, requestId])
  const refreshSchema = async () => {
    const requestedScope = refreshScope.current
    setRefreshBusy(true); setRefreshNotice('')
    try {
      const result = await folderEnvironmentApi.refresh({ project_id: projectId, request_id: requestId, environment })
      if (refreshScope.current !== requestedScope) return
      if (result.status === 'CONFLICT') { setRefreshNotice(result.message || '역할 충돌이 있어 기존 덱 위치를 유지했습니다. 폴더 연결·규칙에서 확인하세요.'); return }
      setRefreshNotice(result.changed ? '저장소와 덱 위치를 갱신했습니다.' : '저장소 내용이 이미 최신입니다.')
      setManualRefresh((value) => value + 1)
    } catch (reason) { if (refreshScope.current === requestedScope) setRefreshNotice(reason instanceof Error ? reason.message : '저장소를 갱신하지 못했습니다.') }
    finally { setRefreshBusy(false) }
  }
  const selectedSceneId = getParam('scene')
  const selectedPartId = getParam('part')
  const filter = getParam('filter')
  const path = useMemo(() => materialsPath(catalog, { caseId, loadCaseId, runId, optionId }), [caseId, catalog, loadCaseId, optionId, runId])
  const selectedScene = path.scenes.find((item) => item.scene_id === selectedSceneId) ?? null

  const updateQuery = (patch: Record<string, string | null>, replace = true) => updateParams(patch, { replace })
  const chooseLevel = (level: HierarchyLevel, value: string) => selectLevel(level, value)

  useEffect(() => {
    if (!getParam('environment')) return
    updateParams({ environment: null }, { replace: true })
  }, [getParam, updateParams])

  useEffect(() => {
    if (!requestId) {
      setCatalogState(null)
      setDeck(null)
      return
    }
    const controller = new AbortController()
    setLoadingCatalog(true)
    setDeck(null)
    setError('')
    materialsApi.catalog(requestId, environment, controller.signal).then((value) => {
      if (!controller.signal.aborted) setCatalogState({ requestId, value })
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '소재 덱 위치를 불러오지 못했습니다.')
    }).finally(() => { if (!controller.signal.aborted) setLoadingCatalog(false) })
    return () => controller.abort()
  }, [requestId, environment, refreshToken, manualRefresh])

  // URL repair: restore parents of an old `scene`-only link, drop stale ids
  // (with their children) and auto-select single candidates. It never moves
  // to another branch just because the chosen one has no deck.
  useEffect(() => {
    if (!catalog) return
    const patch: Record<string, string | null> = {}
    const current = { caseId, loadCaseId, runId, optionId }
    const sceneRecord = selectedSceneId ? catalog.scenes.find((item) => item.scene_id === selectedSceneId) : undefined
    if (sceneRecord?.case_id && catalog.hierarchy.cases.length) {
      // A Scene link (old `scene`-only links, or one whose lower levels were
      // never written) fills the missing parents when the set ones agree.
      const fromScene = { caseId: sceneRecord.case_id, loadCaseId: sceneRecord.load_case_id, runId: sceneRecord.execution_run_id, optionId: sceneRecord.run_option_id }
      const fields = ['caseId', 'loadCaseId', 'runId', 'optionId'] as const
      const keys = { caseId: 'case', loadCaseId: 'case_load', runId: 'case_run', optionId: 'case_option' } as const
      if (fields.every((field) => !current[field] || current[field] === fromScene[field])) {
        for (const field of fields) {
          if (!current[field] && fromScene[field]) { current[field] = fromScene[field]; patch[keys[field]] = fromScene[field] }
        }
      }
    }
    const levels: Array<[HierarchyLevel, 'caseId' | 'loadCaseId' | 'runId' | 'optionId', (state: typeof current) => Array<{ id: string }>]> = [
      ['case', 'caseId', () => catalog.hierarchy.cases],
      ['case_load', 'loadCaseId', (state) => materialsPath(catalog, state).loads],
      ['case_run', 'runId', (state) => materialsPath(catalog, state).runs],
      ['case_option', 'optionId', (state) => materialsPath(catalog, state).options],
    ]
    let cleared = false
    for (const [key, field, candidatesFor] of levels) {
      if (cleared) {
        if (current[field]) { current[field] = ''; patch[key] = null }
        continue
      }
      const candidates = candidatesFor(current)
      if (current[field] && !candidates.some((item) => item.id === current[field])) {
        current[field] = ''; patch[key] = null; cleared = true
        continue
      }
      if (!current[field] && candidates.length === 1) { current[field] = candidates[0].id; patch[key] = current[field] }
      if (!current[field] && candidates.length) cleared = true
    }
    const resolved = materialsPath(catalog, current)
    if (selectedSceneId && !resolved.scenes.some((item) => item.scene_id === selectedSceneId)) {
      patch.scene = null; patch.part = null
    }
    if ((!selectedSceneId || patch.scene === null) && resolved.scenes.length === 1) {
      patch.scene = resolved.scenes[0].scene_id; patch.part = null
    }
    if (Object.keys(patch).length) updateParams(patch, { replace: true })
  }, [caseId, catalog, loadCaseId, optionId, runId, selectedSceneId, updateParams])

  useEffect(() => {
    if (!requestId || !selectedScene?.has_deck) {
      deckKey.current = ''
      setDeck(null)
      setLoadingDeck(false)
      return
    }
    const controller = new AbortController()
    const key = `${requestId}:${selectedScene.scene_id}`
    deckKey.current = key
    setLoadingDeck(true)
    setDeck(null)
    setError('')
    materialsApi.deck(requestId, selectedScene.scene_id, environment, controller.signal).then((value) => {
      if (!controller.signal.aborted && deckKey.current === key) setDeck(value.deck)
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted && deckKey.current === key) setError(reason instanceof Error ? reason.message : '선택한 씬의 소재 덱을 읽지 못했습니다.')
    }).finally(() => { if (!controller.signal.aborted && deckKey.current === key) setLoadingDeck(false) })
    return () => controller.abort()
  }, [requestId, selectedScene?.scene_id, selectedScene?.has_deck, environment, refreshToken, manualRefresh])

  const materialById = useMemo(() => new Map((deck?.materials ?? []).map((item) => [item.id, item])), [deck])
  const propertyById = useMemo(() => new Map((deck?.properties ?? []).map((item) => [item.id, item])), [deck])
  const selectedPart = deck?.parts.find((item) => item.id === selectedPartId) ?? null

  const visibleParts = useMemo(() => {
    const needle = filter.trim().toLocaleLowerCase()
    const filtered = (deck?.parts ?? []).filter((part) => {
      if (!needle) return true
      const material = part.material_id ? materialById.get(part.material_id) : undefined
      const property = part.property_id ? propertyById.get(part.property_id) : undefined
      return [part.id, part.title, part.material_id, material?.title, material?.subtype, part.property_id, property?.title, property?.subtype]
        .some((value) => value?.toLocaleLowerCase().includes(needle))
    })
    const direction = sort.direction === 'asc' ? 1 : -1
    return filtered.sort((left, right) => {
      let a: string | number = left.id, b: string | number = right.id
      if (sort.key === 'part') { a = left.title ?? left.id; b = right.title ?? right.id }
      if (sort.key === 'material') { a = materialById.get(left.material_id ?? '')?.title ?? left.material_id ?? ''; b = materialById.get(right.material_id ?? '')?.title ?? right.material_id ?? '' }
      if (sort.key === 'property') { a = propertyById.get(left.property_id ?? '')?.title ?? left.property_id ?? ''; b = propertyById.get(right.property_id ?? '')?.title ?? right.property_id ?? '' }
      if (sort.key === 'thickness') { a = left.thickness ?? Number.NEGATIVE_INFINITY; b = right.thickness ?? Number.NEGATIVE_INFINITY }
      if (sort.key === 'density') { a = materialById.get(left.material_id ?? '')?.density?.converted_value ?? Number.NEGATIVE_INFINITY; b = materialById.get(right.material_id ?? '')?.density?.converted_value ?? Number.NEGATIVE_INFINITY }
      const result = typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' })
      return result === 0 ? left.id.localeCompare(right.id, undefined, { numeric: true }) : result * direction
    })
  }, [deck, filter, materialById, propertyById, sort])

  useEffect(() => setPage(0), [filter, sort, selectedSceneId])
  const pageSize = 50
  useEffect(() => {
    if (!deck) return
    if (!visibleParts.length) {
      if (selectedPartId) updateQuery({ part: null }, true)
      setPage(0)
      return
    }
    const selectedIndex = visibleParts.findIndex((part) => part.id === selectedPartId)
    if (selectedIndex < 0) {
      updateQuery({ part: visibleParts[0].id }, true)
      setPage(0)
      return
    }
    const targetPage = Math.floor(selectedIndex / pageSize)
    setPage((current) => current === targetPage ? current : targetPage)
    // URL repair and page alignment follow the current filtered/sorted view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deck, visibleParts, selectedPartId])
  const pageCount = Math.max(1, Math.ceil(visibleParts.length / pageSize))
  const currentPage = Math.min(page, pageCount - 1)
  const shownParts = visibleParts.slice(currentPage * pageSize, (currentPage + 1) * pageSize)
  const sortBy = (key: SortKey) => setSort((current) => current.key === key ? { key, direction: current.direction === 'asc' ? 'desc' : 'asc' } : { key, direction: 'asc' })
  const sortButton = (key: SortKey, label: string) => <button type="button" className="materials-sort-button" onClick={() => sortBy(key)} aria-label={`${label}로 정렬${sort.key === key ? `, ${sort.direction === 'asc' ? '오름차순' : '내림차순'}` : ''}`}>{label}<span>{sort.key === key ? (sort.direction === 'asc' ? '↑' : '↓') : '↕'}</span></button>

  return <div className="materials-dashboard" data-ui-density="v1">
    <header className="materials-page-head">
      <div><span>RADioss STARTER DECK</span><h1>모델 소재·물성</h1><p>Part 참조를 따라 Material, Property, Failure 모델과 함수 곡선을 살펴봅니다.</p></div>
      <div className="materials-page-controls">
        {canRefreshSchema && <button type="button" className="materials-export-button" disabled={refreshBusy || !projectId || !requestId} onClick={() => void refreshSchema()}><RotateCw /> {refreshBusy ? '갱신 중…' : '저장소 Refresh'}</button>}
      </div>
      {catalog && (catalog.scenes.length || catalog.hierarchy.cases.length) ? <div className="materials-hierarchy" role="group" aria-label="소재 덱 경로">
        <div className="materials-hierarchy-path">
          {path.flat ? null : <>
            <HierarchyChoice label="Case" value={caseId} choices={path.cases.map((item) => ({ id: item.id, label: item.label, title: item.relative_path }))} onChange={(value) => chooseLevel('case', value)} disabledReason="이 의뢰에 확인된 Case가 없습니다." />
            <HierarchyChoice label="하중경우" value={loadCaseId} choices={path.loads.map((item) => ({ id: item.id, label: item.label, title: item.relative_path }))} disabled={!caseId} disabledReason={caseId ? '이 Case에 하중경우 폴더가 없습니다.' : 'Case를 먼저 선택하세요.'} onChange={(value) => chooseLevel('case_load', value)} />
            <HierarchyChoice label="Run Case" value={runId} choices={path.runs.map((item) => ({ id: item.id, label: item.label, title: item.relative_path }))} disabled={!loadCaseId} disabledReason={loadCaseId ? '이 하중경우에 Run Case가 없습니다.' : '하중경우를 먼저 선택하세요.'} onChange={(value) => chooseLevel('case_run', value)} />
            <HierarchyChoice label="Run Option" value={optionId} choices={path.options.map((item) => ({ id: item.id, label: item.label, title: item.relative_path || item.label }))} disabled={!runId} disabledReason={runId ? '이 Run Case에 Run Option이 없습니다.' : 'Run Case를 먼저 선택하세요.'} onChange={(value) => chooseLevel('case_option', value)} />
          </>}
          <HierarchyChoice label="Scene" value={selectedSceneId} choices={path.scenes.map((item) => ({ id: item.scene_id, label: `${item.label}${item.has_deck ? '' : ' · 덱 없음'}`, title: item.relative_path }))} disabled={!path.ready} disabledReason={path.ready ? '선택한 경로에 Scene이 없습니다.' : '상위 경로를 먼저 선택하세요.'} onChange={(value) => updateQuery({ scene: value || null, part: null }, false)} />
        </div>
        {selectedScene ? <div className="materials-scene-location">
          <span title={selectedScene.relative_path}>{selectedScene.relative_path}</span>
          <b className={selectedScene.has_deck ? 'present' : 'absent'}>{selectedScene.has_deck ? '덱 있음' : '덱 없음'}</b>
        </div> : null}
      </div> : null}
    </header>

    {refreshNotice && <div className="materials-error" role="status"><span>{refreshNotice}</span></div>}
    {error && <div className="materials-error" role="alert"><AlertTriangle /><span>{error}</span><button type="button" onClick={() => setManualRefresh((value) => value + 1)}><RotateCw /> 다시 불러오기</button></div>}
    {!requestId ? <div className="materials-empty-state"><Box /><strong>의뢰를 선택하면 소재 덱을 조회합니다.</strong><span>조회 권한이 있는 의뢰만 목록에 표시됩니다.</span></div> : loadingCatalog ? <div className="materials-empty-state" role="status">유통환경 덱 위치를 불러오고 있습니다…</div> : error && !catalog ? <div className="materials-empty-state"><AlertTriangle /><strong>조회 실패</strong><span>오류를 확인하고 다시 불러오세요.</span></div> : catalog && !catalog.scenes.length ? <div className="materials-empty-state"><Layers /><strong>이 의뢰에서 확인된 덱 위치가 없습니다.</strong><span>의뢰의 유통환경 결과 폴더를 확인하세요.</span></div> : !selectedScene ? <div className="materials-empty-state"><Layers /><strong>{path.ready && !path.scenes.length ? '선택한 경로에 Scene이 없습니다.' : '경로와 Scene을 선택하면 소재 덱을 조회합니다.'}</strong><span>Case → 하중경우 → Run Case → Run Option → Scene 순서로 선택하세요.</span></div> : !selectedScene.has_deck ? <div className="materials-empty-state"><Box /><strong>선택한 위치에서 Parts와 Materials 덱을 찾지 못했습니다.</strong><span title={selectedScene.relative_path}>{selectedScene.relative_path}</span></div> : <div className="materials-workspace">
      <section className="materials-list-panel" aria-label="Part 목록">
        <div className="materials-list-toolbar">
          <label className="materials-search"><Search /><input aria-label="Part 검색" placeholder="Part, Material, Property 검색" value={filter} onChange={(event) => updateQuery({ filter: event.target.value || null }, true)} /></label>
          <strong>{visibleParts.length.toLocaleString()} / {(deck?.parts.length ?? 0).toLocaleString()} Parts</strong>
          <button type="button" className="materials-export-button" disabled={!visibleParts.length} onClick={() => downloadCsv(visibleParts, materialById, propertyById, selectedScene, requestId, deck?.unit_system.input.length ?? null)}><Download /> CSV</button>
        </div>
        {loadingDeck ? <div className="materials-table-state" role="status">선택한 덱 위치를 분석하고 있습니다…</div> : deck && !deck.parts.length ? <div className="materials-table-state">파싱된 Part가 없습니다.</div> : <>
          <div className="materials-table-scroll">
            <table className="materials-part-table">
              <colgroup><col className="materials-col-part" /><col className="materials-col-material" /><col className="materials-col-property" /><col className="materials-col-thickness" /><col className="materials-col-density" /></colgroup>
              <thead><tr>
                <th scope="col">{sortButton('part', 'Part')}</th><th scope="col">{sortButton('material', 'Material')}</th><th scope="col">{sortButton('property', 'Property')}</th><th scope="col">{sortButton('thickness', '두께')}</th><th scope="col">{sortButton('density', '밀도')}</th>
              </tr></thead>
              <tbody>{shownParts.map((part) => {
                const material = part.material_id ? materialById.get(part.material_id) : undefined
                const property = part.property_id ? propertyById.get(part.property_id) : undefined
                const unresolved = (part.material_id && !material) || (part.property_id && !property)
                return <tr key={part.id} tabIndex={0} className={`${part.id === selectedPartId ? 'selected' : ''}${unresolved ? ' unresolved' : ''}`} aria-selected={part.id === selectedPartId} onClick={() => updateQuery({ part: part.id }, false)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); updateQuery({ part: part.id }, false) } }}>
                  <td title={`${part.id} · ${part.title ?? '이름 없음'} · ${sourceLabel(part.source)}`}><strong>{part.title || '이름 없음'}</strong><span>{part.id}</span></td>
                  <td title={material ? `${material.id} · ${material.title ?? '이름 없음'}` : `누락 참조 · ${part.material_id ?? 'Material 참조 없음'}`}><strong>{material?.title || (part.material_id ? `누락 · ${part.material_id}` : 'Material 없음')}</strong><span>{material ? `${material.subtype ?? 'MAT'} · ${material.id}` : '참조를 확인할 수 없음'}</span></td>
                  <td title={property ? `${property.id} · ${property.title ?? property.subtype ?? 'Property'}` : `누락 참조 · ${part.property_id ?? 'Property 참조 없음'}`}><strong>{property?.title || (part.property_id ? `${property?.subtype ?? '누락'} · ${part.property_id}` : 'Property 없음')}</strong><span>{property ? `${property.subtype ?? 'PROP'} · ${property.id}` : '참조를 확인할 수 없음'}</span></td>
                  <td>{thicknessLabel(part.thickness ?? property?.thickness, deck?.unit_system.input.length ?? null) === '—' ? property?.thickness_display ?? '—' : thicknessLabel(part.thickness ?? property?.thickness, deck?.unit_system.input.length ?? null)}</td>
                  <td>{material?.density?.converted_value == null ? '—' : `${numberLabel(material.density.converted_value)} ${material.density.converted_unit ?? ''}`}</td>
                </tr>
              })}</tbody>
            </table>
          </div>
          <footer className="materials-pagination"><span>{visibleParts.length ? `${(currentPage * pageSize + 1).toLocaleString()}–${Math.min((currentPage + 1) * pageSize, visibleParts.length).toLocaleString()}행` : '검색 결과 없음'} · 페이지 {currentPage + 1} / {pageCount}</span><div><button type="button" aria-label="이전 Part 페이지" disabled={currentPage === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}><ChevronLeft /></button><button type="button" aria-label="다음 Part 페이지" disabled={currentPage >= pageCount - 1} onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}><ChevronRight /></button></div></footer>
        </>}
        {deck?.warnings.length ? <details className="materials-warning-list"><summary><AlertTriangle /> 파서 알림 {deck.warnings.length}건</summary><ul>{deck.warnings.map((warning, index) => <li key={`${warning.code}-${index}`}><code>{warning.code}</code> {warning.message} <small>{sourceLabel(warning.source)}</small></li>)}</ul></details> : null}
      </section>
      {selectedPart && deck ? <MaterialsDetails part={selectedPart} material={selectedPart.material_id ? materialById.get(selectedPart.material_id) : undefined} property={selectedPart.property_id ? propertyById.get(selectedPart.property_id) : undefined} deck={deck} /> : <aside className="materials-detail-empty"><strong>{loadingDeck ? '상세 정보를 불러오고 있습니다.' : visibleParts.length ? 'Part를 선택하세요.' : filter ? '검색 결과가 없습니다.' : '선택한 씬에 Part가 없습니다.'}</strong><span>선택한 Part의 참조와 물성을 여기에 표시합니다.</span></aside>}
    </div>}
  </div>
}
