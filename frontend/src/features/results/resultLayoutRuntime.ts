import type { DashboardWidget } from '../../types'
import type { RequestResultLayout, ResultLayoutBindings } from '../../shared/api/resultLayouts'

export const RESULT_WIDGET_STATES = ['UNCONFIGURED', 'WAITING', 'EMPTY', 'PARTIAL', 'READY', 'ERROR', 'FAILED', 'NOT_APPLICABLE', 'UNSUPPORTED'] as const
export type ResultWidgetState = typeof RESULT_WIDGET_STATES[number]

const supportedTypes = new Set<DashboardWidget['type']>([
  'open_cell_map', 'open_cell_summary', 'kpi', 'verdict', 'gauge', 'edge_bar', 'summary', 'time_series', 'scatter', 'note', 'result_table', 'contour', 'video', 'video_grid', 'model3d', 'workflow', 'chassis_summary', 'chassis_diagram', 'chassis_bar', 'chassis_table', 'run_comparison',
])

const messages: Record<ResultWidgetState, string> = {
  UNCONFIGURED: '이 위젯의 결과 바인딩이 아직 구성되지 않았습니다.',
  WAITING: '요청된 결과 데이터가 아직 연결되지 않았습니다.',
  EMPTY: '연결된 결과에서 표시할 값이 아직 없습니다.',
  PARTIAL: '일부 결과만 연결되어 있습니다. 수집이 완료되면 자동으로 갱신됩니다.',
  READY: '결과 데이터가 준비되었습니다.',
  ERROR: '결과 수집 또는 변환에서 오류가 발생했습니다. 실행 기록을 확인하세요.',
  FAILED: '결과 수집 또는 변환이 실패했습니다. 실행 기록을 확인하세요.',
  NOT_APPLICABLE: '이 요청의 조건에는 적용되지 않는 위젯입니다.',
  UNSUPPORTED: '현재 런타임에서 지원하지 않는 위젯 유형입니다.',
}

const EMPTY_BINDINGS: ResultLayoutBindings = {
  available_data_contracts: [],
  load_cases: [],
  latest_result_run: null,
  scalars: [],
  error: null,
}

function normalizeContracts(values: unknown): string[] {
  if (!Array.isArray(values)) return []
  return [...new Set(values.filter((item): item is string => typeof item === "string").map((item) => item.trim().toUpperCase()).filter(Boolean))]
}

function widgetContracts(widget: DashboardWidget, requiredDataContracts: string[], bindings: ResultLayoutBindings) {
  const runtimeContracts = bindings.widget_data_contracts?.[widget.id]
  const configured = Array.isArray(runtimeContracts) ? runtimeContracts : widget.settings?.data_contracts
  const contracts = Array.isArray(configured) ? configured : []
  return normalizeContracts([...requiredDataContracts, ...contracts])
}

export function resultWidgetState(
  widget: DashboardWidget,
  requiredDataContracts: string[],
  bindings: ResultLayoutBindings = EMPTY_BINDINGS,
): ResultWidgetState {
  if (!supportedTypes.has(widget.type)) return "UNSUPPORTED"
  if (widget.settings?.not_applicable === true) return "NOT_APPLICABLE"
  const liveState = bindings.widget_states?.[widget.id]
  if (typeof liveState === "string" && RESULT_WIDGET_STATES.includes(liveState as ResultWidgetState)) return liveState as ResultWidgetState
  if (bindings.widget_errors?.[widget.id]) return "ERROR"
  if (bindings.error) return bindings.latest_result_run?.status.toUpperCase() === "FAILED" ? "FAILED" : "ERROR"
  const declared = widget.settings?.result_state
  if (typeof declared === "string" && RESULT_WIDGET_STATES.includes(declared as ResultWidgetState)) return declared as ResultWidgetState
  const availableContracts = new Set(normalizeContracts(bindings.available_data_contracts))
  if (widgetContracts(widget, requiredDataContracts, bindings).some((item) => !availableContracts.has(item))) return "WAITING"
  if (!bindings.load_cases.length && !bindings.latest_result_run && !bindings.scalars.length) return "EMPTY"
  if (["kpi", "gauge", "verdict", "result_table"].includes(widget.type) && !bindings.scalars.length) return "EMPTY"
  return "READY"
}

export function resultWidgetMessage(state: ResultWidgetState) {
  return messages[state]
}

export function shouldPollResultLayout(layout: RequestResultLayout): boolean {
  const snapshot = layout.snapshot
  if (!snapshot) return false
  return snapshot.pages.some((page) => page.widgets.some((widget) => {
    const state = resultWidgetState(widget, snapshot.required_data_contracts, layout.bindings)
    return state === 'WAITING' || state === 'PARTIAL'
  }))
}


/** One timer only; hidden tabs back off, a focus event resumes immediately. */
export function resultLayoutPollDelay(attempt: number, hidden = false) {
  const delay = Math.min(60_000, 5_000 * 2 ** Math.min(Math.max(0, attempt), 4))
  return hidden ? Math.max(delay, 60_000) : delay
}

export function preservesResultLayoutOnLoadCaseChange(activeDashboardId: string) {
  return activeDashboardId === 'request-result-layout'
}

export function explicitCustomAnalysisPage<T extends { page: { analysis_key: string; is_system: boolean } }>(pages: T[]) {
  return pages.find((page) => page.page.analysis_key === "custom" && !page.page.is_system)
}
