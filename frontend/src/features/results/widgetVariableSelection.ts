import type { DashboardWidget, Overview, WidgetType } from '../../types'

export const multiVariableWidgetTypes = new Set<WidgetType>([
  'result_table',
  'name_value',
  'edge_bar',
  'time_series',
  'chassis_bar',
  'chassis_table',
])

export function supportsMultipleVariables(type: WidgetType): boolean {
  return multiVariableWidgetTypes.has(type)
}

export function hasExplicitVariableSelection(settings: DashboardWidget['settings']): boolean {
  return Array.isArray(settings?.variableIds)
}

export function selectedVariableIds(settings: DashboardWidget['settings']): string[] | undefined {
  if (Array.isArray(settings?.variableIds)) return settings.variableIds.filter((value): value is string => typeof value === 'string' && value.length > 0)
  const legacyId = settings?.variableId
  return typeof legacyId === 'string' && legacyId.length > 0 ? [legacyId] : undefined
}

export function orderByVariableIds<T extends { variable_key: string }>(items: T[], ids: string[]): T[] {
  const byId = new Map(items.map((item) => [item.variable_key, item]))
  return ids.flatMap((id) => {
    const item = byId.get(id)
    return item ? [item] : []
  })
}

/** Mirrors each legacy widget's implicit source when no variable binding was saved. */
export function legacyDefaultVariableIds(type: WidgetType, overview: Overview | undefined, selectedEdges: string[]): string[] {
  if (!overview) return []
  const numericScalars = overview.scalar_results.filter((item) => typeof item.value_double === 'number' && Number.isFinite(item.value_double))
  if (type === 'name_value') return numericScalars.map((item) => item.variable_key)
  if (type === 'result_table' || type === 'edge_bar') return numericScalars
    .filter((item) => item.result_group === 'OPEN_CELL' && item.unit.toLowerCase() === 'mpa' && item.variable_key.toLowerCase().includes('stress') && selectedEdges.some((edge) => item.variable_key.startsWith(edge)))
    .sort((left, right) => ['top', 'bottom', 'left', 'right'].indexOf(left.variable_key.split('_')[0]) - ['top', 'bottom', 'left', 'right'].indexOf(right.variable_key.split('_')[0]))
    .map((item) => item.variable_key)
  if (type === 'time_series') return selectedEdges.map((edge) => `${edge}_edge_stress_time`)
  if (type === 'chassis_bar' || type === 'chassis_table') return numericScalars
    .filter((item) => item.result_group === 'CHASSIS_REAR' && item.unit.toLowerCase() === 'mm' && item.variable_key.includes('permanent_deformation'))
    .map((item) => item.variable_key)
  return []
}
