import type { Overview, ReportLayoutDefinition } from '../../types'

export function reportVariables(overview: Overview) {
  const items = new Map<string, { key: string; name: string; kind: 'scalar' | 'series'; unit: string; preview: string; sampleCount: number }>()
  overview.scalar_results.forEach((item) => items.set(item.variable_key, {
    key: item.variable_key,
    name: item.display_name,
    kind: 'scalar',
    unit: item.unit,
    preview: Number.isFinite(item.value_double) ? `${item.value_double.toLocaleString('ko-KR', { maximumFractionDigits: 2 })} ${item.unit} · ${item.verdict}` : `값 없음 · ${item.unit}`,
    sampleCount: 1,
  }))
  const seriesGroups = new Map<string, Overview['time_series']>()
  overview.time_series.forEach((item) => seriesGroups.set(item.variable_key, [...(seriesGroups.get(item.variable_key) ?? []), item]))
  seriesGroups.forEach((points, key) => {
    const first = points[0]
    const peak = points.reduce((best, point) => point.value > best.value ? point : best, first)
    items.set(key, {
      key,
      name: first.display_name,
      kind: 'series',
      unit: first.value_unit,
      preview: `${points.length.toLocaleString('ko-KR')} points · Peak ${peak.value.toLocaleString('ko-KR', { maximumFractionDigits: 2 })} ${first.value_unit}`,
      sampleCount: points.length,
    })
  })
  return [...items.values()]
}

export function withReportVariables(layout: ReportLayoutDefinition, overview: Overview): ReportLayoutDefinition {
  if (layout.variablePlacements.length) {
    return { ...layout, variablePlacements: [...layout.variablePlacements].sort((a, b) => a.order - b.order) }
  }
  return {
    ...layout,
    variablePlacements: reportVariables(overview).map((item, order) => ({ variableKey: item.key, presentation: 'both', order })),
  }
}
