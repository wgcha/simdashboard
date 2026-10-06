/**
 * W5 Case 비교: pure model for comparing 2–4 Cases of one request and
 * environment. Every Case is read from its merged latest result
 * (`latest:<dashboard_case_id>`) with the same API calls and value rules as the
 * 요약 tab (distribution) and the 다섯 평가 종합 table (usage).
 */
import type { DashboardCatalog, DashboardChoice, DashboardDistribution, DashboardEnvironment, UsageDashboard } from '../../shared/api/simulationDashboard'
import { noEdgeSelected, sceneEnvelopes } from './distributionValues'
import { USAGE_DIRECTION_LABELS, USAGE_DIRECTIONS, usageCell, usageEvaluationLabel, usageEvaluationRows, type UsageCellView, type UsageMetric } from './usageEvaluations'

export const COMPARE_MIN_CASES = 2
export const COMPARE_MAX_CASES = 4
export const COMPARE_MISSING = '없음'

export type CompareCandidate = { id: string; label: string; dashboardCaseId: string; captureId: string; latestAt: string }

/** Cases that have a merged latest result, in catalog order. */
export function compareCandidates(catalog: DashboardCatalog | null): CompareCandidate[] {
  if (!catalog) return []
  const seen = new Set<string>()
  return catalog.cases.flatMap((item) => {
    if (seen.has(item.id)) return []
    seen.add(item.id)
    const latest = catalog.captures.find((capture) => capture.case_id === item.id && capture.kind === 'LATEST')
    if (!latest) return []
    const stored = catalog.captures.filter((capture) => capture.case_id === item.id && capture.kind !== 'LATEST').map((capture) => capture.label)
    const latestAt = stored.reduce((max, label) => label > max ? label : max, '')
    return [{ id: item.id, label: item.label, dashboardCaseId: latest.dashboard_case_id ?? item.dashboard_case_id ?? item.id, captureId: latest.id, latestAt }]
  })
}

/** All Cases when there are at most four, otherwise the four with the newest results. */
export function defaultCompareSelection(candidates: CompareCandidate[]): string[] {
  if (candidates.length <= COMPARE_MAX_CASES) return candidates.map((item) => item.id)
  const order = new Map(candidates.map((item, index) => [item.id, index]))
  const newest = [...candidates].sort((a, b) => b.latestAt.localeCompare(a.latestAt) || (order.get(a.id)! - order.get(b.id)!)).slice(0, COMPARE_MAX_CASES)
  const chosen = new Set(newest.map((item) => item.id))
  return candidates.filter((item) => chosen.has(item.id)).map((item) => item.id)
}

/** The on-screen path (labels of the current Case's selection) every compared Case is matched by. */
export type ComparePath = { loadCase: string; run: string; option: string; component: string; basis: 'REPORTED_SUMMARY' | 'DETAIL'; edgeKeys: string; lineIndices: string }
export type CompareMember = { runId: string; captureId: string; optionId: string; mode: string; componentId: string }
export type CompareResolution = { member: CompareMember } | { reason: string }

const optionText = (item: DashboardChoice) => item.option_label || item.label

/**
 * Finds the same 하중경우 · Run Case · Run Option · Component (by name) in a
 * Case's merged latest result, with the Case results path's candidate rules.
 */
export function resolveCompareMember(catalog: DashboardCatalog, candidate: CompareCandidate, path: ComparePath): CompareResolution {
  const capture = candidate.captureId
  // Prefer the entry of the latest result; a folder-only entry (no capture) means "no result yet".
  const pick = (items: DashboardChoice[], match: (item: DashboardChoice) => boolean) => items.find((item) => match(item) && item.capture_id === capture) ?? (items.some((item) => match(item) && item.capture_id == null) ? null : undefined)
  const missing = (level: string, name: string, found: null | undefined) => ({ reason: `${level} ${name} ${found === null ? '결과 없음' : '없음'}` })
  const load = pick(catalog.load_cases, (item) => item.case_id === candidate.id && item.label === path.loadCase)
  if (!load) return missing('하중경우', path.loadCase, load)
  const run = pick(catalog.execution_runs, (item) => item.case_id === candidate.id && item.load_case_id === load.id && item.label === path.run)
  if (!run) return missing('Run Case', path.run, run)
  const explicit = catalog.run_options ?? []
  const option = pick(explicit.length ? explicit : catalog.modes, (item) => item.execution_run_id === run.id && optionText(item) === path.option)
  if (!option) return missing('Run Option', path.option, option)
  const mode = option.mode ?? option.id
  const component = catalog.components.find((item) => (item.execution_run_id === run.id || item.run_id === run.id) && item.mode === mode && item.capture_id === capture && (!item.run_option_id || item.run_option_id === option.id) && item.label === path.component)
  if (!component) return { reason: `Component ${path.component} 없음` }
  return { member: { runId: run.id, captureId: capture, optionId: option.id, mode, componentId: component.id } }
}

export type CompareColumnInput<T> = { id: string; label: string; data: T | null; reason?: string }

// ---------------------------------------------------------------- distribution

export type DistributionCompareCell = { value: number | null; unit: string | null; missing: boolean; delta: number | null; percent: number | null; best: boolean; columnMax: boolean }
export type DistributionCompareRow = { key: string; label: string; sequence: number | null; missingIn: string[]; cells: DistributionCompareCell[] }
export type DistributionCompareColumn = { id: string; label: string; baseline: boolean; reason: string; unit: string | null; hasData: boolean }
export type DistributionCompareModel = { columns: DistributionCompareColumn[]; rows: DistributionCompareRow[]; summary: DistributionCompareCell[]; units: Array<string | null>; unitMismatch: boolean; noEdge: boolean }

function compareCell(value: number | null, unit: string | null, missing: boolean): DistributionCompareCell {
  return { value, unit, missing, delta: null, percent: null, best: false, columnMax: false }
}

/** Δ vs the baseline (value and %), only when units agree; lowest value per row is "best". */
function rankRow(cells: DistributionCompareCell[], baselineIndex: number, unitMismatch: boolean) {
  const base = cells[baselineIndex]?.value
  const numeric = cells.filter((cell) => cell.value != null)
  const lowest = numeric.length >= 2 ? Math.min(...numeric.map((cell) => cell.value!)) : null
  cells.forEach((cell, index) => {
    cell.best = lowest != null && cell.value === lowest
    if (unitMismatch || index === baselineIndex || base == null || cell.value == null) return
    cell.delta = cell.value - base
    cell.percent = base === 0 ? null : (cell.delta / Math.abs(base)) * 100
  })
}

/** Scene rows aligned by exact Scene folder name (W6 warns about near-identical names). */
export function buildDistributionCompare(columns: Array<CompareColumnInput<DashboardDistribution>>, baselineId: string, edgeKeys: string): DistributionCompareModel {
  const perColumn = columns.map((column) => {
    const values = new Map<string, { label: string; sequence: number | null; value: number | null; unit: string | null }>()
    for (const item of column.data ? sceneEnvelopes(column.data, edgeKeys) : []) {
      const key = item.scene.label.trim()
      if (!values.has(key)) values.set(key, { label: item.scene.label, sequence: item.scene.scene_sequence_number, value: item.value, unit: item.unit })
    }
    return values
  })
  const order: Array<{ key: string; label: string; sequence: number | null; index: number }> = []
  perColumn.forEach((values) => values.forEach((item, key) => { if (!order.some((row) => row.key === key)) order.push({ key, label: item.label, sequence: item.sequence, index: order.length }) }))
  order.sort((a, b) => (a.sequence ?? Number.POSITIVE_INFINITY) - (b.sequence ?? Number.POSITIVE_INFINITY) || a.index - b.index)
  const unitsByColumn = perColumn.map((values) => Array.from(new Set(Array.from(values.values()).filter((item) => item.value != null).map((item) => item.unit ?? null))))
  const units = Array.from(new Set(unitsByColumn.flat()))
  const unitMismatch = units.length > 1
  const baselineIndex = Math.max(0, columns.findIndex((column) => column.id === baselineId))
  const noEdge = !edgeKeys.trim() || columns.some((column) => column.data != null && noEdgeSelected(column.data, edgeKeys))
  const rows = order.map((row): DistributionCompareRow => {
    const cells = perColumn.map((values) => {
      const item = values.get(row.key)
      return compareCell(item?.value ?? null, item?.unit ?? null, !item)
    })
    rankRow(cells, baselineIndex, unitMismatch)
    return { key: row.key, label: row.label, sequence: row.sequence, missingIn: columns.filter((_, index) => cells[index].missing).map((column) => column.label), cells }
  })
  // Case-level summary: the highest Scene value of each Case; the Scene holding it is marked per column.
  const summary = columns.map((_, index) => {
    const numeric = rows.map((row) => row.cells[index]).filter((cell) => cell.value != null)
    const maximum = numeric.length ? Math.max(...numeric.map((cell) => cell.value!)) : null
    for (const cell of numeric) cell.columnMax = cell.value === maximum
    return compareCell(maximum, numeric.find((cell) => cell.value === maximum)?.unit ?? null, maximum == null)
  })
  rankRow(summary, baselineIndex, unitMismatch)
  return {
    columns: columns.map((column, index) => ({ id: column.id, label: column.label, baseline: index === baselineIndex, reason: column.reason ?? '', unit: unitsByColumn[index].length === 1 ? unitsByColumn[index][0] : null, hasData: column.data != null })),
    rows, summary, units, unitMismatch, noEdge,
  }
}

const trim = (value: number) => Number(value.toPrecision(6))
export function compareNumber(value: number) { return String(trim(value)) }
export function compareValueText(cell: Pick<DistributionCompareCell, 'value' | 'unit'>) {
  return cell.value == null ? '값 없음' : `${compareNumber(cell.value)}${cell.unit ? ` ${cell.unit}` : ''}`
}
export function compareDeltaText(cell: Pick<DistributionCompareCell, 'delta' | 'percent'>) {
  if (cell.delta == null) return ''
  const sign = (value: number) => value > 0 ? '+' : value < 0 ? '−' : '±'
  const percent = cell.percent == null ? '' : ` (${sign(cell.percent)}${Math.abs(cell.percent).toFixed(1)}%)`
  return `Δ ${sign(cell.delta)}${compareNumber(Math.abs(cell.delta))}${percent}`
}
export function compareUnitText(unit: string | null) { return unit || '단위 미확인' }
export function unitMismatchText(model: Pick<DistributionCompareModel, 'columns' | 'unitMismatch'>) {
  return model.unitMismatch ? `Case마다 단위가 달라 증감(Δ)을 계산하지 않았습니다: ${model.columns.filter((column) => column.hasData).map((column) => `${column.label} ${compareUnitText(column.unit)}`).join(', ')}` : ''
}

// ----------------------------------------------------------------------- usage

export type UsageCompareTone = UsageCellView['tone'] | 'ok' | 'ng'
export type UsageCompareCell = { lines: Array<{ direction: string; text: string; label: string; tone: UsageCompareTone }>; missing: boolean }
export type UsageCompareRow = { key: string; label: string; sourceKey: string; metric: UsageMetric; cells: UsageCompareCell[] }
export type UsageCompareModel = { columns: Array<{ id: string; label: string; baseline: boolean; reason: string; hasData: boolean }>; rows: UsageCompareRow[] }

/** The five evaluations (Slope_Angle with its OK/NG row) with each Case's values, as the 다섯 평가 종합 table shows them. */
export function buildUsageCompare(columns: Array<CompareColumnInput<UsageDashboard>>, baselineId: string): UsageCompareModel {
  const perColumn = columns.map((column) => new Map((column.data ? usageEvaluationRows(column.data) : []).map((row) => [`${row.evaluation.id}:${row.metric}`, row])))
  const order: Array<{ key: string; label: string; sourceKey: string; metric: UsageMetric }> = []
  perColumn.forEach((rows) => rows.forEach((row, key) => {
    if (!order.some((item) => item.key === key)) order.push({ key, label: row.label || `${usageEvaluationLabel(row.evaluation)} 판정`, sourceKey: row.key ?? '', metric: row.metric })
  }))
  const baselineIndex = Math.max(0, columns.findIndex((column) => column.id === baselineId))
  return {
    columns: columns.map((column, index) => ({ id: column.id, label: column.label, baseline: index === baselineIndex, reason: column.reason ?? '', hasData: column.data != null })),
    rows: order.map((item) => ({ ...item, cells: perColumn.map((rows) => {
      const row = rows.get(item.key)
      if (!row) return { lines: [], missing: true }
      const lines = USAGE_DIRECTIONS.flatMap((direction) => {
        const value = row.evaluation[direction]
        if (!value) return []
        const view = usageCell(value, row.metric)
        const tone: UsageCompareTone = row.metric === 'verdict' && view.tone === 'ready' ? (view.text === 'OK' ? 'ok' : view.text === 'NG' ? 'ng' : view.tone) : view.tone
        return [{ direction: USAGE_DIRECTION_LABELS[direction], text: view.text, label: view.label, tone }]
      })
      return { lines, missing: false }
    }) })),
  }
}

// ---------------------------------------------------------------------- report

/** Frozen "후보 Case 비교" table for the Case report (text only). */
export type CaseCompareReport = { environment: DashboardEnvironment; title: string; scopeRows: Array<{ label: string; value: string }>; note: string; headers: string[]; rows: string[][] }

export function distributionCompareReport(model: DistributionCompareModel, scopeRows: Array<{ label: string; value: string }>): CaseCompareReport {
  const cellText = (cell: DistributionCompareCell, column: DistributionCompareColumn) => {
    if (cell.missing) return COMPARE_MISSING
    return [compareValueText(cell), column.baseline ? '' : compareDeltaText(cell), cell.best ? '최저' : '', cell.columnMax ? 'Case 최대' : ''].filter(Boolean).join('\n')
  }
  const baseline = model.columns.find((column) => column.baseline)
  return {
    environment: 'DISTRIBUTION',
    title: '후보 Case 비교',
    scopeRows: [...scopeRows, { label: '기준 Case', value: baseline?.label ?? '' }, { label: '단위', value: model.units.map(compareUnitText).join(', ') || '단위 미확인' }],
    note: unitMismatchText(model) || '최저: Scene별 가장 낮은 값 · Case 최대: Case의 가장 높은 Scene 값 · Δ: 기준 Case 대비',
    headers: ['Scene', ...model.columns.map((column) => `${column.label}${column.baseline ? ' (기준)' : ''}${column.reason ? ` · ${column.reason}` : ''}`)],
    rows: [
      ...model.rows.map((row) => [row.label, ...row.cells.map((cell, index) => cellText(cell, model.columns[index]))]),
      ['Case 최대 (Scene 전체)', ...model.summary.map((cell, index) => cell.missing ? COMPARE_MISSING : [compareValueText(cell), model.columns[index].baseline ? '' : compareDeltaText(cell), cell.best ? '최저' : ''].filter(Boolean).join('\n'))],
    ],
  }
}

export function usageCompareReport(model: UsageCompareModel): CaseCompareReport {
  const baseline = model.columns.find((column) => column.baseline)
  return {
    environment: 'USAGE',
    title: '후보 Case 비교',
    scopeRows: [{ label: '환경', value: '사용환경' }, { label: '기준 Case', value: baseline?.label ?? '' }],
    note: '각 Case의 최신 결과 · 다섯 평가 종합과 같은 값',
    headers: ['평가', '원문 키', ...model.columns.map((column) => `${column.label}${column.baseline ? ' (기준)' : ''}${column.reason ? ` · ${column.reason}` : ''}`)],
    rows: model.rows.map((row) => [row.label, row.sourceKey, ...row.cells.map((cell) => cell.missing ? COMPARE_MISSING : cell.lines.map((line) => `${line.direction}: ${line.text}${line.label ? ` · ${line.label}` : ''}`).join('\n') || '해당 없음')]),
  }
}
