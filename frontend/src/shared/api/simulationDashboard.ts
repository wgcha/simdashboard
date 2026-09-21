import { apiFetch } from './auth'
import { apiErrorFromResponse } from './errors'
import { apiUrl } from './url'

export type DashboardEnvironment = 'USAGE' | 'DISTRIBUTION'
export type DashboardBasis = 'REPORTED_SUMMARY' | 'DETAIL'
export type DashboardOptionStatus = 'PRESENT' | 'ABSENT' | 'UNRESOLVED'
export type DashboardChoice = { id: string; label: string; disabled?: boolean; reason?: string | null; case_id?: string | null; load_case_id?: string | null; execution_run_id?: string | null; run_id?: string | null; run_option_id?: string | null; option_label?: string | null; option_status?: DashboardOptionStatus | null; mode?: string | null; capture_id?: string | null; component_id?: string | null }
export type DashboardAsset = { asset_id: string; kind: 'IMAGE' | 'VIDEO' | string; status: string; url?: string | null; title?: string | null; frame_role?: string | null; frame_index?: number | null; time_value?: number | null; time_unit?: string | null; provenance?: string | null }
export type DashboardValue = { verdict?: string | null; value: number | null; unit: string | null; unit_status?: string | null; completeness?: string | null; status?: string | null; value_status?: string | null; verdict_status?: string | null; value_key?: string | null; verdict_key?: string | null; basis?: string | null; scope?: string | null }
export type DashboardContext = { project_id: string; request_id: string; simulation_case_id?: string; load_case_id?: string; execution_run_id?: string; run_option_id?: string; option_label?: string | null; option_status?: DashboardOptionStatus; mode?: string; capture_id?: string; component_id?: string; basis?: DashboardBasis; scene_id?: string }
export type DashboardContextEcho = DashboardContext & { context_key: string }
export type DashboardCatalog = { environment: DashboardEnvironment; cases: DashboardChoice[]; load_cases: DashboardChoice[]; execution_runs: DashboardChoice[]; run_options?: DashboardChoice[]; modes: DashboardChoice[]; captures: DashboardChoice[]; components: DashboardChoice[]; bases: DashboardChoice[] }
export type DashboardScan = { storage_root_id: string; cases: Array<{ root_relative_path: string; source_name: string; environment: DashboardEnvironment }>; issues: string[] }
export type UsageEvaluation = { id: string; name: string; condition?: string | null; status: string; common?: DashboardValue | null; front?: DashboardValue | null; rear?: DashboardValue | null; verdict?: string | null; media?: DashboardAsset[]; reference?: { status: string; reason?: string | null; common?: DashboardValue | null; front?: DashboardValue | null; rear?: DashboardValue | null } | null }
export type UsageDashboard = { context: DashboardContextEcho; status: string; evaluations: UsageEvaluation[]; quality_issues: string[] }
export type DashboardScene = { id: string; label: string; scene_sequence_number: number | null; scenario_number: number | null; description?: string | null; contact_code?: string | null; repetition?: string | null; order_status?: string | null; match_key?: string | null }
export type DashboardMember = { id: string; label: string; simulation_case_id: string; design_description?: string | null; color?: string | null; pattern?: string |null; load_case_id: string; execution_run_id: string; run_option_id?: string; mode: string; capture_id: string; component_id: string; basis: DashboardBasis }
export type DashboardEdgePeak = DashboardValue & { edge: 'LEFT' | 'RIGHT' | 'TOP' | 'BOTTOM'; scene_id: string; member_id: string; line_index?: number | null; is_selected_maximum?: boolean; source_refs?: Array<{ asset_id: string; row?: number | null; column?: string | null }> }
export type DashboardSeriesPoint = DashboardValue & { id: string; scene_id: string; scene_sequence_number: number | null; member_id: string; edge: 'LEFT' | 'RIGHT' | 'TOP' | 'BOTTOM'; selected_edge_envelope?: number | null }
export type DashboardContourCell = { cell_id: string; scene_id: string; member_id: string; asset?: DashboardAsset | null; status: string; reason?: string | null; value?: DashboardValue | null; scale_status?: string | null }
export type DashboardBehaviorCell = { cell_id: string; scene_id: string; member_id: string; subject_role: 'CELL' | 'CUSHION' | 'BOX' | 'UNKNOWN'; asset?: DashboardAsset | null; status: string; reason?: string | null }
export type DashboardDistribution = { context: DashboardContextEcho; status: string; members: DashboardMember[]; scenes: DashboardScene[]; edge_peaks: DashboardEdgePeak[]; series: DashboardSeriesPoint[]; contours: DashboardContourCell[]; behaviors: DashboardBehaviorCell[]; location_peaks?: Array<DashboardValue & { scene_id: string; member_id: string; locations: Array<{position: string; line_index?: number | null}> }>; quality_issues: string[] }
export type DashboardComparisonMember = { simulation_case_id: string; load_case_id: string; execution_run_id: string; run_option_id?: string; capture_id: string; mode: string; component_id: string; basis: DashboardBasis }
export type DashboardLinePoint = { node_id?: string | null; ref_coord: number | null; value: number | null; line_index: number; status?: string | null }
export type DashboardSceneDetail = { context: DashboardContextEcho; scene: DashboardScene; edge_peaks: DashboardEdgePeak[]; line_points: DashboardLinePoint[]; corner_points?: Array<{ node_id: string; value: number | null; status?: string | null }>; assets: DashboardAsset[]; quality_issues: string[] }

const dashboardPath = (...segments: string[]) => `/${['api', 'dashboard', ...segments].join('/')}`

async function read<T>(path: string, parameters: Record<string, string | undefined>, signal?: AbortSignal): Promise<T> {
  const response = await apiFetch(apiUrl(path as never, {}, parameters), { signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.json() as Promise<T>
}

async function write<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const response = await apiFetch(apiUrl(path as never), { method: 'POST', signal, headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.json() as Promise<T>
}

export const simulationDashboardApi = {
  catalog: (requestId: string, environment: DashboardEnvironment, signal?: AbortSignal) => read<DashboardCatalog>(dashboardPath('catalog'), { request_id: requestId, environment }, signal),
  scan: (payload: { project_id: string; request_id: string; root_relative_path: string; environment: DashboardEnvironment }, signal?: AbortSignal) => write<DashboardScan>(dashboardPath('scans'), payload, signal),
  capture: (payload: { project_id: string; request_id: string; root_relative_path: string; environment: DashboardEnvironment; storage_root_id?: string }, signal?: AbortSignal) => write<unknown>(dashboardPath('captures'), payload, signal),
  usage: (caseId: string, captureId: string, referenceCaseId?: string, referenceCaptureId?: string, signal?: AbortSignal) => read<UsageDashboard>(dashboardPath('usage', 'cases', encodeURIComponent(caseId)), { capture_id: captureId, reference_case_id: referenceCaseId, reference_capture_id: referenceCaptureId }, signal),
  distribution: (runId: string, context: Pick<DashboardContext, 'capture_id' | 'run_option_id' | 'mode' | 'component_id' | 'basis'> & { edge_keys?: string; line_indices?: string }, signal?: AbortSignal) => read<DashboardDistribution>(dashboardPath('distribution', 'runs', encodeURIComponent(runId)), { capture_id: context.capture_id, run_option_id: context.run_option_id, mode: context.mode, component_id: context.component_id, basis: context.basis, edge_keys: context.edge_keys, line_indices: context.line_indices }, signal),
  comparison: (members: DashboardComparisonMember[], edgeKeys: string, lineIndices: string, signal?: AbortSignal) => write<DashboardDistribution>(dashboardPath('distribution', 'comparison'), { members, edge_keys: edgeKeys, line_indices: lineIndices }, signal),
  sceneDetail: (sceneId: string, context: Pick<DashboardContext, 'execution_run_id' | 'capture_id' | 'run_option_id' | 'mode' | 'component_id' | 'basis'> & { line_indices?: string; position?: string }, signal?: AbortSignal) => read<DashboardSceneDetail>(dashboardPath('distribution', 'scenes', encodeURIComponent(sceneId)), { run_id: context.execution_run_id, capture_id: context.capture_id, run_option_id: context.run_option_id, mode: context.mode, component_id: context.component_id, basis: context.basis, line_indices: context.line_indices, position: context.position }, signal),
  assetUrl: (assetId: string) => apiUrl(dashboardPath('assets', encodeURIComponent(assetId)) as never),
}
