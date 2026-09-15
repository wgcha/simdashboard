export type ComparisonSeriesPoint = {
  time_value: number
  time_unit: string
  baseline_value: number | null
  target_value: number | null
}

export type ChartPoint = ComparisonSeriesPoint & { baseline_value: number | null; target_value: number | null }

export function finiteComparisonValue(value: number | null): number | null { return value != null && Number.isFinite(value) ? value : null }

/** Sanitize and sort the union of both runs' observations without inventing samples. */
export function prepareComparisonSeries(points: ComparisonSeriesPoint[]): ChartPoint[] {
  return points.filter((point) => Number.isFinite(point.time_value)).map((point) => ({ ...point, baseline_value: finiteComparisonValue(point.baseline_value), target_value: finiteComparisonValue(point.target_value) })).sort((a, b) => a.time_value - b.time_value)
}

export function comparisonYAxisDomain(points: ComparisonSeriesPoint[], includeZero: boolean): [number, number] {
  let min = Infinity; let max = -Infinity
  for (const point of points) for (const value of [point.baseline_value, point.target_value]) { const finite = finiteComparisonValue(value); if (finite != null) { min = Math.min(min, finite); max = Math.max(max, finite) } }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1]
  if (includeZero) { min = Math.min(0, min); max = Math.max(0, max) }
  if (min === max) { const padding = min === 0 ? 1 : Math.max(Math.abs(min) * 0.1, 1); return [min - padding, max + padding] }
  return [min, max]
}

export function comparisonXDomain(points: ComparisonSeriesPoint[]): [number, number] {
  let min = Infinity; let max = -Infinity
  for (const point of points) if (Number.isFinite(point.time_value)) { min = Math.min(min, point.time_value); max = Math.max(max, point.time_value) }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1]
  if (min === max) { const padding = min === 0 ? 1 : Math.max(Math.abs(min) * 0.1, 1); return [min - padding, max + padding] }
  return [min, max]
}
