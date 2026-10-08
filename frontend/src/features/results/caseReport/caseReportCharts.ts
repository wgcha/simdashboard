/**
 * Charts of the Case 결과 보고서: the same charts the Case results screen draws,
 * from the same data (distribution result, Scene detail, 사용환경 evaluations).
 *
 * - 유통환경: 요약 "Scene별 엣지 최대응력" (bar), Scene 비교 "엣지별 수준"
 *   TOP/BOTTOM/LEFT/RIGHT (bar ×4), Scene 상세 TOP/BOT/LH/RH line values
 *   along `ref_coord` per line L1–L4 (XY line ×4 per Scene).
 * - 사용환경: one bar chart per evaluation (except Slope_Angle_360) of
 *   공통/전방/후방 × 현재 Case/Reference.
 *
 * Specs are pure data (`ReportChartSpec`): PPTX renders them as native charts
 * (`reportExport.ts`), HTML as inline SVG without scripts (`chartSvg`). Long XY
 * series are reduced deterministically (LTTB) to `CHART_MAX_POINTS` per series
 * and the reduction is written in the chart notes.
 */
import type { DashboardDistribution, DashboardMember, DashboardSceneDetail, UsageDashboard } from '../../../shared/api/simulationDashboard'
import type { ReportChartSpec } from '../../../reportExport'
import { USAGE_DIRECTION_LABELS, USAGE_DIRECTIONS, usageEvaluationLabel, usageMetricStatus } from '../usageEvaluations'
import { distributionMember, noEdgeSelected } from '../distributionValues'

export type CaseReportChart = ReportChartSpec & { id: string }
/** Charts shown together (one slide: a single chart or a 2×2 grid). */
export type CaseReportChartGroup = { id: string; title: string; charts: CaseReportChart[] }
/** Scene 상세 of one Scene and position as read for the report (null + error when it could not be read). */
export type CaseReportSceneDetail = { sceneId: string; position: string; detail: DashboardSceneDetail | null; error?: string }

export const CHART_MAX_POINTS = 2000
export const SCENE_DETAIL_POSITIONS = ['TOP', 'BOT', 'LH', 'RH'] as const
const EDGES = ['TOP', 'BOTTOM', 'LEFT', 'RIGHT'] as const
const LINE_COLORS = ['var(--color-chart-series-1)', 'var(--color-chart-series-2)', 'var(--color-chart-series-3)', 'var(--color-chart-series-4)']
const NO_EDGE_NOTE = '선택 없음: 표시 옵션에서 엣지를 하나 이상 선택하세요.'
const GROUP_SIZE = 4

const number = new Intl.NumberFormat('ko-KR')

/**
 * Largest-Triangle-Three-Buckets: keeps the first and last point and, per
 * bucket, the point forming the largest triangle with its neighbours.
 * Deterministic; returns the input (copied) when it is already small enough.
 */
export function lttb(points: Array<[number, number]>, threshold = CHART_MAX_POINTS): Array<[number, number]> {
  const count = points.length
  if (threshold >= count || threshold < 3) return points.slice()
  const sampled: Array<[number, number]> = [points[0]]
  const every = (count - 2) / (threshold - 2)
  let anchor = 0
  for (let bucket = 0; bucket < threshold - 2; bucket += 1) {
    const averageStart = Math.floor((bucket + 1) * every) + 1
    const averageEnd = Math.min(Math.floor((bucket + 2) * every) + 1, count)
    let averageX = 0
    let averageY = 0
    for (let index = averageStart; index < averageEnd; index += 1) { averageX += points[index][0]; averageY += points[index][1] }
    const length = Math.max(1, averageEnd - averageStart)
    averageX /= length
    averageY /= length
    const rangeStart = Math.floor(bucket * every) + 1
    const rangeEnd = Math.floor((bucket + 1) * every) + 1
    let maxArea = -1
    let chosen = rangeStart
    for (let index = rangeStart; index < rangeEnd; index += 1) {
      const area = Math.abs((points[anchor][0] - averageX) * (points[index][1] - points[anchor][1]) - (points[anchor][0] - points[index][0]) * (averageY - points[anchor][1]))
      if (area > maxArea) { maxArea = area; chosen = index }
    }
    sampled.push(points[chosen])
    anchor = chosen
  }
  sampled.push(points[count - 1])
  return sampled
}

const unitText = (unit: string | null | undefined) => unit || '단위 미확인'
const firstUnit = (units: Array<string | null | undefined>) => units.find((unit) => unit) ?? null
/** Same x labels as the screen: Scene 순번, or the Scene name when the number is unknown. */
const sceneAxisLabel = (scene: { label: string; scene_sequence_number: number | null }) => scene.scene_sequence_number == null ? scene.label : String(scene.scene_sequence_number)

function memberColor(member: DashboardMember | undefined, index: number) { return member?.color || LINE_COLORS[index % LINE_COLORS.length] }

/** Charts of one distribution section (요약, 엣지별 수준, Scene 상세). Pure. */
export function distributionChartGroups(input: { prefix: string; distribution: DashboardDistribution; edgeKeys: string; sceneDetails?: CaseReportSceneDetail[] }): CaseReportChartGroup[] {
  const { prefix, distribution } = input
  const member = distributionMember(distribution)
  const memberId = member?.id
  const memberIndex = Math.max(0, distribution.members.findIndex((item) => item.id === memberId))
  const categories = distribution.scenes.map(sceneAxisLabel)
  const groups: CaseReportChartGroup[] = []

  // 요약: Scene별 엣지 최대응력 (selected-edge envelope).
  const series = new Map(distribution.series.filter((point) => point.member_id === memberId).map((point) => [point.scene_id, point]))
  const envelopeUnit = firstUnit([...series.values()].map((point) => point.unit))
  const noEdge = noEdgeSelected(distribution, input.edgeKeys)
  groups.push({ id: `${prefix}chart:summary`, title: '요약 · Scene별 엣지 최대응력', charts: [{
    id: `${prefix}chart:summary`, title: 'Scene별 엣지 최대응력', kind: 'bar', categories,
    series: [{ name: `${member?.label ?? 'Case'} · 선택 엣지 envelope`, color: memberColor(member, memberIndex), values: distribution.scenes.map((scene) => { const point = series.get(scene.id); return point?.selected_edge_envelope ?? point?.value ?? null }) }],
    xTitle: 'Scene (순번)', yTitle: `선택 엣지 최대응력 (${unitText(envelopeUnit)})`, notes: [], empty: noEdge ? NO_EDGE_NOTE : '',
  }] })

  // Scene 비교 · 엣지별 수준: one chart per edge.
  const peaks = new Map(distribution.edge_peaks.filter((peak) => peak.member_id === memberId).map((peak) => [`${peak.edge}:${peak.scene_id}`, peak]))
  groups.push({ id: `${prefix}chart:edges`, title: 'Scene 비교 · 엣지별 수준', charts: EDGES.map((edge): CaseReportChart => {
    const values = distribution.scenes.map((scene) => peaks.get(`${edge}:${scene.id}`)?.value ?? null)
    const unit = firstUnit(distribution.scenes.map((scene) => peaks.get(`${edge}:${scene.id}`)?.unit))
    return { id: `${prefix}chart:edge:${edge}`, title: `${edge} 엣지 수준`, kind: 'bar', categories, series: [{ name: `${member?.label ?? 'Case'} · ${edge}`, color: memberColor(member, memberIndex), values }], xTitle: 'Scene (순번)', yTitle: `${edge} 최대응력 (${unitText(unit)})`, notes: [], empty: '' }
  }) })

  // Scene 상세: line values along ref_coord per line, for each position.
  const details = input.sceneDetails ?? []
  for (const scene of distribution.scenes) {
    const forScene = details.filter((item) => item.sceneId === scene.id)
    if (!forScene.length) continue
    const sceneUnit = firstUnit(distribution.edge_peaks.filter((peak) => peak.scene_id === scene.id && peak.member_id === memberId).map((peak) => peak.unit))
    groups.push({ id: `${prefix}chart:scene:${scene.id}`, title: `Scene 상세 · ${scene.label}`, charts: SCENE_DETAIL_POSITIONS.map((position): CaseReportChart => {
      const item = forScene.find((candidate) => candidate.position === position)
      const id = `${prefix}chart:scene:${scene.id}:${position}`
      const base = { id, title: `${scene.label} · ${position}`, kind: 'scatter' as const, xTitle: 'ref_coord (기준 좌표)', yTitle: `값 (${unitText(sceneUnit)})` }
      if (!item?.detail) return { ...base, series: [], notes: [], empty: item?.error ? `Scene 상세를 불러오지 못했습니다: ${item.error}` : 'Scene 상세 없음' }
      const notes: string[] = []
      const lines = [1, 2, 3, 4].flatMap((line) => {
        const points = item.detail!.line_points
          .filter((point) => point.line_index === line && point.ref_coord != null && point.value != null && Number.isFinite(point.ref_coord) && Number.isFinite(point.value))
          .map((point): [number, number] => [point.ref_coord!, point.value!])
          .sort((a, b) => a[0] - b[0])
        if (!points.length) return []
        const reduced = lttb(points)
        if (reduced.length < points.length) notes.push(`L${line}: 원본 ${number.format(points.length)}점 → ${number.format(reduced.length)}점(LTTB 축약)`)
        return [{ name: `L${line}`, color: LINE_COLORS[line - 1], x: reduced.map((point) => point[0]), values: reduced.map((point) => point[1]) }]
      })
      return { ...base, series: lines, notes, empty: lines.length ? '' : '선 데이터 없음' }
    }) })
  }
  return groups
}

/** One bar chart per 사용환경 evaluation (as the screen; Slope_Angle_360 has no chart), 4 per group. Pure. */
export function usageChartGroups(usage: UsageDashboard, withReference: boolean): CaseReportChartGroup[] {
  const charts = usage.evaluations.filter((evaluation) => evaluation.id !== 'Slope_Angle_360').map((evaluation): CaseReportChart => {
    const directions = USAGE_DIRECTIONS.filter((key) => evaluation[key] != null || evaluation.reference?.[key] != null)
    const ready = (value: (typeof evaluation)['common']) => value && usageMetricStatus(value, 'value') === 'READY' ? value.value : null
    const unit = evaluation.common?.unit || evaluation.front?.unit || evaluation.rear?.unit || null
    const series = [
      { name: '현재 Case', color: 'var(--color-chart-series-1)', values: directions.map((key) => ready(evaluation[key])) },
      ...(withReference ? [{ name: 'Reference', color: 'var(--color-chart-series-2)', values: directions.map((key) => ready(evaluation.reference?.[key] ?? null)) }] : []),
    ]
    return { id: `usage:chart:${evaluation.id}`, title: usageEvaluationLabel(evaluation), kind: 'bar', categories: directions.map((key) => USAGE_DIRECTION_LABELS[key]), series, xTitle: '방향', yTitle: `값 (${unitText(unit)})`, notes: [], empty: directions.length ? '' : '값 없음' }
  })
  const groups: CaseReportChartGroup[] = []
  for (let index = 0; index < charts.length; index += GROUP_SIZE) {
    const page = Math.floor(index / GROUP_SIZE) + 1
    const pages = Math.ceil(charts.length / GROUP_SIZE)
    groups.push({ id: `usage:chart:${page}`, title: pages > 1 ? `평가별 값 (${page}/${pages})` : '평가별 값', charts: charts.slice(index, index + GROUP_SIZE) })
  }
  return groups
}

// ---------------------------------------------------------------- inline SVG (HTML)

const SVG_ESCAPES: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
const esc = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, (character) => SVG_ESCAPES[character])
/** HTML colours of the series (light theme; CSS variables are not resolved in a standalone file). */
const SVG_PALETTE = ['#0369a1', '#be123c', '#047857', '#a16207', '#6d28d9', '#0e7490', '#c2410c', '#4d7c0f']
function svgColor(value: string | undefined, index: number) {
  if (value && /^#[0-9a-f]{6}$/i.test(value)) return value
  const variable = value?.match(/--color-chart-series-(\d+)/)
  return SVG_PALETTE[variable ? (Number(variable[1]) - 1) % SVG_PALETTE.length : index % SVG_PALETTE.length]
}

function niceTicks(min: number, max: number, count = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1]
  if (min === max) { const pad = Math.abs(min) || 1; min -= pad / 2; max += pad / 2 }
  const raw = (max - min) / count
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((factor) => factor * magnitude).find((candidate) => candidate >= raw) ?? raw
  const start = Math.floor(min / step) * step
  const ticks: number[] = []
  for (let value = start; value <= max + step * 0.5 && ticks.length < 20; value += step) ticks.push(Number(value.toPrecision(12)))
  if (ticks[ticks.length - 1] < max) ticks.push(Number((ticks[ticks.length - 1] + step).toPrecision(12)))
  return ticks
}
const tickText = (value: number) => Math.abs(value) >= 1e5 || (Math.abs(value) > 0 && Math.abs(value) < 1e-3) ? value.toExponential(1) : String(Number(value.toPrecision(6)))

/** Static inline SVG (no script, no external reference, no namespace URL) of one chart: axes, ticks, axis titles, legend. */
export function chartSvg(chart: ReportChartSpec): string {
  const width = 560
  const height = 320
  const left = 64
  const right = 16
  const top = 30
  const series = chart.series.map((item, index) => ({ ...item, color: svgColor(item.color, index) })).filter((item) => item.values.some((value) => value != null))
  const legendRows = Math.ceil(series.length / 4)
  const bottom = 58 + legendRows * 16
  const plotW = width - left - right
  const plotH = height - top - bottom
  const parts: string[] = [`<svg viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="${esc(chart.title)}" font-family="Malgun Gothic, Apple SD Gothic Neo, Noto Sans KR, sans-serif" font-size="11">`]
  parts.push(`<text x="${width / 2}" y="18" text-anchor="middle" font-size="13" font-weight="600" fill="#142033">${esc(chart.title)}</text>`)
  if (chart.empty || !series.length) {
    parts.push(`<rect x="${left}" y="${top}" width="${plotW}" height="${plotH}" fill="none" stroke="#d7e0ea" stroke-dasharray="4 3"/><text x="${left + plotW / 2}" y="${top + plotH / 2}" text-anchor="middle" fill="#536276">${esc(chart.empty || '표시할 값이 없습니다.')}</text></svg>`)
    return parts.join('')
  }
  const values = series.flatMap((item) => item.values.filter((value): value is number => value != null && Number.isFinite(value)))
  const yTicks = niceTicks(Math.min(0, ...values), Math.max(0, ...values))
  const yMin = yTicks[0]
  const yMax = yTicks[yTicks.length - 1]
  const yAt = (value: number) => top + plotH - ((value - yMin) / (yMax - yMin || 1)) * plotH
  for (const tick of yTicks) parts.push(`<line x1="${left}" x2="${left + plotW}" y1="${yAt(tick).toFixed(1)}" y2="${yAt(tick).toFixed(1)}" stroke="#e3eaf0"/><text x="${left - 6}" y="${(yAt(tick) + 4).toFixed(1)}" text-anchor="end" fill="#536276">${esc(tickText(tick))}</text>`)
  if (chart.kind === 'bar') {
    const categories = chart.categories ?? []
    const band = plotW / Math.max(1, categories.length)
    const barW = Math.max(1, (band * 0.7) / series.length)
    const labelEvery = Math.max(1, Math.ceil(categories.length / 24))
    categories.forEach((category, index) => {
      const x0 = left + index * band + band * 0.15
      series.forEach((item, position) => {
        const value = item.values[index]
        if (value == null || !Number.isFinite(value)) return
        const y = yAt(Math.max(0, value))
        const h = Math.abs(yAt(value) - yAt(0))
        parts.push(`<rect x="${(x0 + position * barW).toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${Math.max(0.5, h).toFixed(1)}" fill="${item.color}"><title>${esc(`${item.name} · ${category}: ${value}`)}</title></rect>`)
      })
      if (index % labelEvery === 0) parts.push(`<text x="${(left + index * band + band / 2).toFixed(1)}" y="${top + plotH + 14}" text-anchor="middle" fill="#536276">${esc(category.length > 14 ? `${category.slice(0, 13)}…` : category)}</text>`)
    })
  } else {
    const xs = series.flatMap((item) => item.x ?? [])
    const xTicks = niceTicks(Math.min(...xs), Math.max(...xs))
    const xMin = xTicks[0]
    const xMax = xTicks[xTicks.length - 1]
    const xAt = (value: number) => left + ((value - xMin) / (xMax - xMin || 1)) * plotW
    for (const tick of xTicks) parts.push(`<text x="${xAt(tick).toFixed(1)}" y="${top + plotH + 14}" text-anchor="middle" fill="#536276">${esc(tickText(tick))}</text>`)
    for (const item of series) {
      const points = (item.x ?? []).flatMap((x, index) => { const y = item.values[index]; return y == null ? [] : [`${xAt(x).toFixed(1)},${yAt(y).toFixed(1)}`] })
      parts.push(`<polyline points="${points.join(' ')}" fill="none" stroke="${item.color}" stroke-width="1.5"><title>${esc(item.name)}</title></polyline>`)
      if (points.length <= 60) for (const point of points) { const [cx, cy] = point.split(','); parts.push(`<circle cx="${cx}" cy="${cy}" r="2.5" fill="${item.color}"/>`) }
    }
  }
  parts.push(`<line x1="${left}" x2="${left + plotW}" y1="${yAt(Math.max(yMin, Math.min(0, yMax))).toFixed(1)}" y2="${yAt(Math.max(yMin, Math.min(0, yMax))).toFixed(1)}" stroke="#8a99ab"/><line x1="${left}" x2="${left}" y1="${top}" y2="${top + plotH}" stroke="#8a99ab"/>`)
  if (chart.xTitle) parts.push(`<text x="${left + plotW / 2}" y="${top + plotH + 32}" text-anchor="middle" fill="#142033">${esc(chart.xTitle)}</text>`)
  if (chart.yTitle) parts.push(`<text transform="translate(14 ${top + plotH / 2}) rotate(-90)" text-anchor="middle" fill="#142033">${esc(chart.yTitle)}</text>`)
  const legendY = top + plotH + 48
  series.forEach((item, index) => {
    const x = left + (index % 4) * (plotW / 4)
    const y = legendY + Math.floor(index / 4) * 16
    parts.push(`<rect x="${x}" y="${y - 9}" width="10" height="10" fill="${item.color}"/><text x="${x + 14}" y="${y}" fill="#142033">${esc(item.name.length > 28 ? `${item.name.slice(0, 27)}…` : item.name)}</text>`)
  })
  parts.push('</svg>')
  return parts.join('')
}
