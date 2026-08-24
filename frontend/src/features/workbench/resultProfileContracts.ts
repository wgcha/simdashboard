import type { RequestResultDefinition, ResultProfile, WorkbenchTaskType } from './types'

const OUTPUT_CONTRACTS: Record<string, readonly string[]> = {
  LOAD_CASE: ['RESULT_MANIFEST', 'POST_RESULT', 'ANALYSIS_RUN_REFERENCE'],
  RESULT_RUN: ['ANALYSIS_RUN_REFERENCE'],
  SCALAR_RESULT: ['ANALYSIS_RUN_REFERENCE'],
  TIME_SERIES: ['ANALYSIS_RUN_REFERENCE'],
  CURVE: ['ANALYSIS_RUN_REFERENCE'],
  MEDIA_ASSET: ['ANALYSIS_RUN_REFERENCE'],
}

export const RESULT_DATA_CONTRACTS = Object.keys(OUTPUT_CONTRACTS)

export function normalizeResultDataContracts(values: unknown): string[] {
  if (!Array.isArray(values)) return []
  return [...new Set(values.filter((item): item is string => typeof item === "string").map((item) => item.trim().toUpperCase()).filter(Boolean))]
}

function widgetContracts(profile: ResultProfile, included: Set<string>) {
  return normalizeResultDataContracts(profile.template.page_definitions.flatMap((page) => page.widgets)
    .filter((widget) => included.has(widget.id))
    .flatMap((widget) => Array.isArray(widget.settings?.data_contracts) ? widget.settings.data_contracts : []))
}

export function workflowDataContracts(tasks: WorkbenchTaskType[]) {
  const outputs = new Set(normalizeResultDataContracts(tasks.flatMap((task) => task.output_artifact_types)))
  return RESULT_DATA_CONTRACTS.filter((contract) => OUTPUT_CONTRACTS[contract].some((output) => outputs.has(output)))
}

export function requestResultDefinitionValidation(definition: RequestResultDefinition | null, _tasks: WorkbenchTaskType[]) {
  if (!definition || definition.widgets.length === 0) return ''
  const requested = normalizeResultDataContracts(definition.widgets.flatMap((widget) => widget.data_contracts))
  const unknown = requested.filter((contract) => !RESULT_DATA_CONTRACTS.includes(contract))
  return unknown.length ? '지원하지 않는 결과 데이터 계약: ' + unknown.join(', ') : ''
}

export function resultProfileValidation(profile: ResultProfile | null, _tasks: WorkbenchTaskType[]) {
  if (!profile) return ''
  const widgets = profile.template.page_definitions.flatMap((page) => page.widgets)
  const allWidgetIds = widgets.map((widget) => widget.id)
  const included = new Set(profile.included_widget_ids?.length ? profile.included_widget_ids : allWidgetIds)
  const required = widgets.filter((widget) => widget.settings?.required === true || widget.settings?.required_widget === true).map((widget) => widget.id)
  if (!required.every((widgetId) => included.has(widgetId))) return '필수 결과 위젯은 결과 구성에서 제외할 수 없습니다.'
  const requested = normalizeResultDataContracts([...profile.required_data_contracts, ...widgetContracts(profile, included)])
  const unknown = requested.filter((contract) => !RESULT_DATA_CONTRACTS.includes(contract))
  return unknown.length ? '지원하지 않는 결과 데이터 계약: ' + unknown.join(', ') : ''
}
