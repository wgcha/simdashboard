import type { Overview, ReportLayoutDefinition } from '../../types'

export function reportVariables(overview: Overview) {
  const items = new Map<string, string>()
  overview.scalar_results.forEach((item) => items.set(item.variable_key, item.display_name))
  overview.time_series.forEach((item) => items.set(item.variable_key, item.display_name))
  return [...items].map(([key, name]) => ({ key, name }))
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
