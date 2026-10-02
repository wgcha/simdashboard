import { apiFetch } from './auth'
import { apiErrorFromResponse } from './errors'
import { apiUrl } from './url'

export type MaterialsEnvironment = 'DISTRIBUTION'
export type MaterialsSource = { file: string | null; line: number | null }
export type MaterialsScene = {
  scene_id: string
  label: string
  relative_path: string
  hierarchy: Record<string, Record<string, unknown>>
  kind: 'SCENE' | 'RESULTS'
  has_deck: boolean
  /** Shared Folder Schema hierarchy ids; '' when the Scene could not be placed. */
  case_id: string
  load_case_id: string
  execution_run_id: string
  run_option_id: string
}
export type MaterialsHierarchyCase = { id: string; label: string; relative_path: string }
export type MaterialsHierarchyLoadCase = MaterialsHierarchyCase & { case_id: string; match_status: string | null }
export type MaterialsHierarchyRun = MaterialsHierarchyCase & { case_id: string; load_case_id: string }
export type MaterialsHierarchyOption = {
  id: string
  label: string
  relative_path: string
  case_id: string
  execution_run_id: string
  run_option_id: string
  option_status: 'PRESENT' | 'ABSENT' | 'UNRESOLVED'
  option_label: string | null
  mode: string | null
}
/** Schema-only (capture_id null) choices; ids match the Case results dashboard catalog. */
export type MaterialsHierarchy = {
  cases: MaterialsHierarchyCase[]
  load_cases: MaterialsHierarchyLoadCase[]
  execution_runs: MaterialsHierarchyRun[]
  run_options: MaterialsHierarchyOption[]
}
export type MaterialsCatalog = { request_id: string | null; environment: string | null; scenes: MaterialsScene[]; hierarchy: MaterialsHierarchy }
export type MaterialsPart = {
  id: string
  title: string | null
  property_id: string | null
  material_id: string | null
  thickness: number | null
  raw_fields: Record<string, unknown>
  source: MaterialsSource | null
}
export type MaterialsProperty = {
  id: string
  subtype: string | null
  title: string | null
  fields: Record<string, unknown>
  raw_fields: Record<string, unknown>
  thickness: number | null
  thickness_display: string | null
  source: MaterialsSource | null
}
export type MaterialsFailure = {
  id: string
  subtype: string | null
  title: string | null
  fields: Record<string, unknown>
  raw_fields: Record<string, unknown>
  source: MaterialsSource | null
}
export type MaterialsMaterial = {
  id: string
  subtype: string | null
  title: string | null
  fields: Record<string, unknown>
  raw_fields: Record<string, unknown>
  density: {
    raw: string | null
    value: number | null
    unit: string | null
    converted_value: number | null
    converted_unit: string | null
  } | null
  representative_e: number | null
  law_id: number | null
  failures: MaterialsFailure[]
  source: MaterialsSource | null
}
export type MaterialsCurveUse = {
  owner_type: string | null
  owner_id: string | null
  role: string | null
  x_unit: string | null
  y_unit: string | null
}
export type MaterialsFunction = {
  id: string
  title: string | null
  points: Array<{ x: number; y: number }>
  uses: MaterialsCurveUse[]
  point_count: number
  source: MaterialsSource | null
}
export type MaterialsWarning = { code: string; message: string; source: MaterialsSource | null }
export type MaterialsDeck = {
  parts: MaterialsPart[]
  properties: MaterialsProperty[]
  materials: MaterialsMaterial[]
  functions: MaterialsFunction[]
  warnings: MaterialsWarning[]
  unit_system: {
    input: { mass: string | null; length: string | null; time: string | null }
    work: { mass: string | null; length: string | null; time: string | null }
  }
}
export type MaterialsDeckResponse = {
  scene: MaterialsScene
  files: Array<{ relative_path: string; size_bytes: number | null }>
  candidate_warnings: Array<{ code: string; relative_paths: string[] }>
  deck: MaterialsDeck
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function rows(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function id(value: unknown): string | null {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : null
}

function optionalText(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function source(value: unknown): MaterialsSource | null {
  const item = record(value)
  if (!item) return null
  return {
    file: optionalText(item.file),
    line: typeof item.line === 'number' && Number.isFinite(item.line) ? item.line : null,
  }
}

function scene(value: unknown): MaterialsScene | null {
  const item = record(value)
  if (!item || typeof item.scene_id !== 'string' || typeof item.label !== 'string' || typeof item.relative_path !== 'string') return null
  if (item.kind !== 'SCENE' && item.kind !== 'RESULTS') return null
  const hierarchy = record(item.hierarchy) ?? {}
  return {
    scene_id: item.scene_id,
    label: item.label,
    relative_path: item.relative_path,
    kind: item.kind,
    hierarchy: Object.fromEntries(Object.entries(hierarchy).flatMap(([key, entry]) => {
      const parsed = record(entry)
      return parsed ? [[key, parsed]] : []
    })),
    has_deck: item.has_deck === true,
    case_id: idText(item.case_id),
    load_case_id: idText(item.load_case_id),
    execution_run_id: idText(item.execution_run_id),
    run_option_id: idText(item.run_option_id),
  }
}

function idText(value: unknown): string {
  return id(value) ?? ''
}

function hierarchyBase(value: unknown): (MaterialsHierarchyCase & { item: Record<string, unknown> }) | null {
  const item = record(value)
  const itemId = id(item?.id)
  if (!item || !itemId) return null
  return { id: itemId, label: optionalText(item.label) ?? itemId, relative_path: optionalText(item.relative_path) ?? '', item }
}

function hierarchy(value: unknown): MaterialsHierarchy {
  const payload = record(value)
  const cases = rows(payload?.cases).flatMap((entry): MaterialsHierarchyCase[] => {
    const base = hierarchyBase(entry)
    return base ? [{ id: base.id, label: base.label, relative_path: base.relative_path }] : []
  })
  const loadCases = rows(payload?.load_cases).flatMap((entry): MaterialsHierarchyLoadCase[] => {
    const base = hierarchyBase(entry)
    return base ? [{ id: base.id, label: base.label, relative_path: base.relative_path, case_id: idText(base.item.case_id), match_status: optionalText(base.item.match_status) }] : []
  })
  const runs = rows(payload?.execution_runs).flatMap((entry): MaterialsHierarchyRun[] => {
    const base = hierarchyBase(entry)
    return base ? [{ id: base.id, label: base.label, relative_path: base.relative_path, case_id: idText(base.item.case_id), load_case_id: idText(base.item.load_case_id) }] : []
  })
  const options = rows(payload?.run_options).flatMap((entry): MaterialsHierarchyOption[] => {
    const base = hierarchyBase(entry)
    if (!base) return []
    const status = base.item.option_status
    return [{
      id: base.id, label: base.label, relative_path: base.relative_path, case_id: idText(base.item.case_id),
      execution_run_id: idText(base.item.execution_run_id), run_option_id: idText(base.item.run_option_id) || base.id,
      option_status: status === 'ABSENT' || status === 'UNRESOLVED' ? status : 'PRESENT',
      option_label: optionalText(base.item.option_label), mode: optionalText(base.item.mode),
    }]
  })
  return { cases, load_cases: loadCases, execution_runs: runs, run_options: options }
}

function keyedRecord(value: unknown): Record<string, unknown> {
  return record(value) ?? {}
}

function parseCatalog(value: unknown): MaterialsCatalog {
  const payload = record(value)
  if (!payload || !Array.isArray(payload.scenes)) throw new Error('소재 카탈로그 응답 형식이 올바르지 않습니다.')
  const scenes = payload.scenes.map(scene).filter((item): item is MaterialsScene => item !== null)
  return { request_id: optionalText(payload.request_id), environment: optionalText(payload.environment), scenes, hierarchy: hierarchy(payload.hierarchy) }
}

function parseDeck(value: unknown): MaterialsDeckResponse {
  const payload = record(value)
  const rawDeck = record(payload?.deck)
  const parsedScene = scene(payload?.scene)
  if (!payload || !rawDeck || !parsedScene) throw new Error('소재 덱 응답 형식이 올바르지 않습니다.')

  const parts = rows(rawDeck.parts).flatMap((value): MaterialsPart[] => {
    const item = record(value)
    const partId = id(item?.id)
    if (!item || partId === null) return []
    return [{
      id: partId, title: optionalText(item.title), property_id: id(item.property_id), material_id: id(item.material_id),
      thickness: finite(item.thickness), raw_fields: keyedRecord(item.raw_fields), source: source(item.source),
    }]
  })
  const properties = rows(rawDeck.properties).flatMap((value): MaterialsProperty[] => {
    const item = record(value)
    const itemId = id(item?.id)
    if (!item || itemId === null) return []
    return [{
      id: itemId, subtype: optionalText(item.subtype), title: optionalText(item.title), fields: keyedRecord(item.fields),
      raw_fields: keyedRecord(item.raw_fields), thickness: finite(item.thickness), thickness_display: optionalText(item.thickness_display), source: source(item.source),
    }]
  })
  const materials = rows(rawDeck.materials).flatMap((value): MaterialsMaterial[] => {
    const item = record(value)
    const itemId = id(item?.id)
    if (!item || itemId === null) return []
    const density = record(item.density)
    return [{
      id: itemId, subtype: optionalText(item.subtype), title: optionalText(item.title), fields: keyedRecord(item.fields),
      raw_fields: keyedRecord(item.raw_fields),
      density: density ? {
        raw: optionalText(density.raw), value: finite(density.value), unit: optionalText(density.unit),
        converted_value: finite(density.converted_value), converted_unit: optionalText(density.converted_unit),
      } : null,
      representative_e: finite(item.representative_e), law_id: finite(item.law_id),
      failures: rows(item.failures).flatMap((entry): MaterialsFailure[] => {
        const failure = record(entry)
        const failureId = id(failure?.id)
        if (!failure || failureId === null) return []
        return [{ id: failureId, subtype: optionalText(failure.subtype), title: optionalText(failure.title), fields: keyedRecord(failure.fields), raw_fields: keyedRecord(failure.raw_fields), source: source(failure.source) }]
      }),
      source: source(item.source),
    }]
  })
  const functions = rows(rawDeck.functions).flatMap((value): MaterialsFunction[] => {
    const item = record(value)
    const itemId = id(item?.id)
    if (!item || itemId === null) return []
    const points = rows(item.points).flatMap((entry): Array<{ x: number; y: number }> => {
      const point = record(entry)
      const x = finite(point?.x)
      const y = finite(point?.y)
      return x === null || y === null ? [] : [{ x, y }]
    })
    const uses = rows(item.uses).flatMap((entry): MaterialsCurveUse[] => {
      const use = record(entry)
      return use ? [{
        owner_type: optionalText(use.owner_type), owner_id: id(use.owner_id), role: optionalText(use.role),
        x_unit: optionalText(use.x_unit), y_unit: optionalText(use.y_unit),
      }] : []
    })
    return [{ id: itemId, title: optionalText(item.title), points, uses, point_count: finite(item.point_count) ?? points.length, source: source(item.source) }]
  })
  const warnings = rows(rawDeck.warnings).flatMap((value): MaterialsWarning[] => {
    const item = record(value)
    if (!item || typeof item.code !== 'string' || typeof item.message !== 'string') return []
    return [{ code: item.code, message: item.message, source: source({ file: item.file, line: item.line }) }]
  })
  const unitSystem = record(rawDeck.unit_system)
  const unitGroup = (value: unknown) => {
    const group = record(value)
    return { mass: optionalText(group?.mass), length: optionalText(group?.length), time: optionalText(group?.time) }
  }
  return {
    scene: parsedScene,
    files: rows(payload.files).flatMap((value) => {
      const item = record(value)
      return item && typeof item.relative_path === 'string' ? [{ relative_path: item.relative_path, size_bytes: finite(item.size_bytes) }] : []
    }),
    candidate_warnings: rows(payload.candidate_warnings).flatMap((value) => {
      const item = record(value)
      return item && typeof item.code === 'string' ? [{ code: item.code, relative_paths: rows(item.relative_paths).filter((path): path is string => typeof path === 'string') }] : []
    }),
    deck: { parts, properties, materials, functions, warnings, unit_system: { input: unitGroup(unitSystem?.input), work: unitGroup(unitSystem?.work) } },
  }
}

async function requestJson(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await apiFetch(apiUrl(path as never), { signal })
  if (!response.ok) throw await apiErrorFromResponse(response)
  return await response.json() as unknown
}

export const materialsApi = {
  async catalog(requestId: string, environment: MaterialsEnvironment, signal?: AbortSignal) {
    const query = new URLSearchParams({ request_id: requestId, environment })
    return parseCatalog(await requestJson(`/api/materials/catalog?${query}`, signal))
  },
  async deck(requestId: string, sceneId: string, environment: MaterialsEnvironment, signal?: AbortSignal) {
    const query = new URLSearchParams({ request_id: requestId, scene_id: sceneId, environment })
    return parseDeck(await requestJson(`/api/materials/deck?${query}`, signal))
  },
}
