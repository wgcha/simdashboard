import { apiFetch } from './auth'
import { apiClient, unwrapGenerated } from './client'
import { apiErrorFromResponse } from './errors'
import { apiUrl } from './url'

export type FolderEnvironment = 'USAGE' | 'DISTRIBUTION'
/** EVALUATION, INPUT and RESULTS are legacy (pre DEPTH_V1) roles kept only for reading old snapshots. */
export type FolderEnvironmentRole = 'PROJECT' | 'REQUEST' | 'WORKING' | 'FINAL' | 'FINAL_CAE' | 'FINAL_REPORTS' | 'FINAL_CAD' | 'FINAL_VERSION' | 'SIMULATION_CASE' | 'EVALUATION' | 'LOAD_CASE' | 'EXECUTION_RUN' | 'RUN_OPTION' | 'INPUT' | 'RESULTS' | 'SCENE' | 'CONTAINER' | 'EXCLUDE'
/** DEPTH_V1 preview assignments: only EXCLUDE and PROJECT/REQUEST LINK are accepted (§6). */
export type FolderAssignment = { node_id: string; role_kind: string; confirm: boolean; target_mode: 'CREATE' | 'LINK'; target_id?: string | null; propagate_same_level: false }
export type FolderEnvironmentProfileRule = { role_kind: string; parent_role?: string | null; pattern: string; match_mode: 'glob' | 'contains' | 'level'; depth?: number }
export type UsageSourceProfile = { version?: number; selection?: { json?: boolean; video?: boolean; image?: boolean; csv?: boolean }; metric_paths?: Record<string, string[]> }
export type FolderEnvironmentProfile = { id: string; environment: FolderEnvironment; name: string; revision: number; rules: { rules?: FolderEnvironmentProfileRule[]; usage_sources?: UsageSourceProfile; description?: string; level_base?: 'WORKING' }; message?: string }
export type DepthSchemaSegment = 'UPPER' | 'LOWER' | 'FINAL'
export type DepthNodeStatus = 'CONFIRMED' | 'UNRESOLVED' | 'CONTENT'
export type DepthDeviationCode = 'ENV_KEYWORD_BOTH' | 'ENV_KEYWORD_NONE' | 'WORKING_MISSING' | 'UNEXPECTED_REQUEST_CHILD' | 'UNEXPECTED_FINAL_CHILD' | 'FINAL_VERSION_INVALID'
export type DepthDeviation = { code: DepthDeviationCode | (string & {}); message: string }
/** Scan/preview node. `level`…`info` are the DEPTH_V1 fields of contract §5; older snapshots may omit them. */
export type FolderEnvironmentNode = { id: string; relative_path: string; parent_path: string | null; name: string; depth: number; file_count?: number; allowed_roles: string[]; role_kind: string | null; status: DepthNodeStatus | (string & {}); target_id?: string; target_mode?: string; project_id?: string; request_id?: string; message?: string
  level?: number; segment?: DepthSchemaSegment; role_basis?: 'DEPTH_SCHEMA' | (string & {}); deviation?: DepthDeviation | null; info?: 'BRANCH_INCOMPLETE' | null }
export type FolderEnvironmentScan = { id: string; environment: FolderEnvironment; profile_id?: string; relative_path: string; nodes: FolderEnvironmentNode[]; status: string; issues: Array<{ message: string; code?: string }>; usage_sources?: UsageSourceProfile }
export type FolderEnvironmentPreview = { id: string; scan_id: string; environment: FolderEnvironment; can_apply: boolean; message?: string | null; rows: FolderEnvironmentNode[]; unresolved_count: number; summary: { new: number; existing: number; conflicts?: number }; usage_review?: UsageSourceReview | null; source_review?: UsageSourceReview | null
  /** §14.2: why a manual registration is blocked (e.g. MULTIPLE_REQUESTS, DEPTH_SCHEMA_REQUEST_LEVEL_MISMATCH) and the REQUEST paths in the plan. */
  blocking_code?: string | null; request_paths?: string[]; created_at?: string }
export type UsageSourceReviewRow = { id?: string; evaluation: string; direction?: 'common' | 'front' | 'back' | 'rear' | string; metric?: string | null; file?: string | null; key?: string | null; key_path?: string[] | null; value?: unknown; value_type?: string | null; unit?: string | null; status: string; candidates?: string[]; selected?: boolean; excluded?: boolean; message?: string | null }
export type UsageSourceReviewEntry = { evaluation: string; direction: string; source?: string | null; candidates?: string[]; status: string; values?: Record<string, unknown>; metrics: Array<{ key: string; path: string[]; value?: unknown; value_type?: string; expected_type?: string; status: string; exclude_reason?: string | null }> }
export type UsageSourceReview = { status?: string; can_register?: boolean; can_publish?: boolean; acknowledged?: boolean; partial?: boolean; rows?: UsageSourceReviewRow[]; entries?: UsageSourceReviewEntry[]; selection?: { json: boolean; video: boolean; image: boolean; csv: boolean }; contract?: Record<string, unknown>; selected_count?: number; excluded_count?: number; excluded_files?: Array<{ relative_path: string; reason: string }>; review_count?: number; review_required_count?: number; blocking_count?: number; missing_count?: number; message?: string | null }
export type FolderCaptureJob = { id: string; status: string; case_id: string; capture_id: string | null; error_code: string | null; project_id?: string; request_id?: string }
export type FolderEnvironmentRegistration = { registration_id: string; preview_id: string; environment: FolderEnvironment; project_id: string; request_id: string; status: string; created_at: string; deleted_at?: string | null; relative_path?: string; capture_jobs: FolderCaptureJob[]; usage_source_reviews?: Record<string, Pick<UsageSourceReview, 'entries' | 'excluded_count' | 'blocking_count' | 'missing_count' | 'can_publish'>> }
export type FolderEnvironmentRefresh = { snapshot_id: string | null; project_id: string; request_id: string; environment: FolderEnvironment; status: 'REFRESHED' | 'UNCHANGED' | 'CONFLICT'; activated?: boolean; changed: boolean; message?: string; structure_fingerprint: string; content_fingerprint: string; diff: { added: number; removed: number; changed: number }; nodes: Array<{ relative_path: string; role_kind: string | null; status: string; role_basis: string; level?: number; segment?: DepthSchemaSegment; deviation?: DepthDeviation | null; info?: 'BRANCH_INCOMPLETE' | null }> }
export type FolderEnvironmentSyncStatus = 'UNCHANGED' | 'REFRESHED' | 'CONFLICT' | 'FAILED'
/** Response of the viewer-level folder auto-sync check (shape documented by the backend; OpenAPI declares it untyped). */
export type FolderEnvironmentSync = {
  status: FolderEnvironmentSyncStatus
  changed: boolean
  snapshot_id: string | null
  diff: { added: number; removed: number; changed: number }
  code: string | null
  message: string | null
  check_mode: 'QUICK' | 'FULL'
  checked_at: string
  coalesced: boolean
}
/** Response of automatic SPDM project/request folder discovery (OpenAPI declares it untyped). */
export type FolderDiscoveryResult = {
  created_projects: Array<{ id: string; name: string }>
  created_requests: Array<{ id: string; name: string; environment: FolderEnvironment; project_id: string }>
  /** Global administrators only; empty for other accounts. */
  needs_review: Array<{ relative_path: string; reason: string; code?: string; environment?: FolderEnvironment | null }>
  checked_at: string
  coalesced: boolean
  status?: 'CHECKED' | 'ROOT_UNSET'
}

// ---- Depth schema (contract docs/contracts/depth-schema.md §4, §6) ----
export type DepthUpperRole = 'CONTAINER' | 'PROJECT' | 'REQUEST'
export type DepthLowerRole = 'WORKING' | 'SIMULATION_CASE' | 'LOAD_CASE' | 'EXECUTION_RUN' | 'RUN_OPTION' | 'SCENE' | 'CONTAINER'
export type DepthLevel<Role extends string = string> = { level: number; role: Role; fixed_name?: string }
export type DepthUpper = { levels: DepthLevel<DepthUpperRole>[] }
export type DepthLower = { levels: DepthLevel<DepthLowerRole>[]; below_last: 'CONTENT' }
export type DepthEnvironmentKeyword = '사용' | '유통'
export type DepthEnvironmentSchema = { profile_id: string; environment_keyword: DepthEnvironmentKeyword; lower: DepthLower; usage_sources?: UsageSourceProfile | null }
export type DepthSchema = { schema_set_id: string; upper: DepthUpper; environments: Record<FolderEnvironment, DepthEnvironmentSchema>; created_at: string; created_by: string | null }
/** Draft sent to PUT and check. `final` and the keywords are server constants and are never sent as editable values. */
export type DepthSchemaEnvironmentsInput = Record<FolderEnvironment, { environment_keyword: DepthEnvironmentKeyword; lower: DepthLower; usage_sources?: UsageSourceProfile | null }>
export type DepthSampleSegment = 'UPPER' | FolderEnvironment
export type DepthSampleLevel = { level: number; folder_count: number; samples: Array<{ name: string; count: number }>; truncated: boolean }
export type DepthRunOptionName = { name: string; count: number; request_count: number }
export type DepthSchemaSamples = { levels: DepthSampleLevel[]; requests_sampled: number; run_option_names?: DepthRunOptionName[]; run_option_names_truncated?: boolean }
export type DepthSchemaCheck = { by_code: Record<string, number>; examples: Array<{ relative_path: string; code: string }> }
export type DepthDeviationItem = { relative_path?: string; code: string; message?: string }
/** POST /api/requests/{id}/reinterpret. The contract fixes only "blocked → deviation list"; other fields are optional. */
export type RequestReinterpretResult = { status?: string; registered?: boolean; registration_id?: string | null; message?: string | null; deviations?: DepthDeviationItem[] }

// ---- Registration delete (contract §13.6) ----
export type RegistrationDeleteCounts = { projects: number; requests: number; cases: number; captures: number; assets: number; finalizations: number; other: number }
export type RegistrationDeleteBlocker = { table: string; id: string; reason: string }
export type RegistrationDeletePreviewItem = { registration_id: string; deletable: boolean; counts: RegistrationDeleteCounts; blockers: RegistrationDeleteBlocker[] }
export type RegistrationDeletePreview = { items: RegistrationDeletePreviewItem[]; confirm_token: string }
export type RegistrationDeleteResult = { deleted: string[]; counts: RegistrationDeleteCounts }

// ---- Project cleanup (contract depth-schema §16) ----
export type ProjectCleanupCategory = 'DEMO' | 'EMPTY' | 'REGISTERED'
export type ProjectCleanupCandidate = { project_id: string; name: string; category: ProjectCleanupCategory; selectable: boolean; requests: number; cases: number; runs: number; user_data: Record<string, number>; user_data_total: number }
export type ProjectCleanupPreviewItem = { project_id: string; name: string; category: ProjectCleanupCategory; deletable: boolean; counts: Record<string, number>; blockers: RegistrationDeleteBlocker[]; user_data: Record<string, number> }
export type ProjectCleanupPreview = { items: ProjectCleanupPreviewItem[]; totals: Record<string, number>; confirm_token: string }
export type ProjectCleanupResult = { deleted: string[]; counts: Record<string, number> }

// Endpoints added by the depth-schema contract are not in the generated OpenAPI client yet.
async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await apiFetch(path, init)
  if (!response.ok) throw await apiErrorFromResponse(response)
  return await response.json() as T
}
const jsonBody = (method: 'POST' | 'PUT', body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const folderEnvironmentApi = {
  refresh: async (body: { project_id: string; request_id: string; environment: FolderEnvironment }): Promise<FolderEnvironmentRefresh> => {
    return unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/refresh', { body })) as FolderEnvironmentRefresh
  },
  sync: async (body: { project_id: string; request_id: string; environment: FolderEnvironment; force?: boolean }, signal?: AbortSignal): Promise<FolderEnvironmentSync> => {
    return unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/sync', { body: { ...body, force: body.force ?? false }, signal })) as FolderEnvironmentSync
  },
  discover: async (body: { force?: boolean } = {}, signal?: AbortSignal): Promise<FolderDiscoveryResult> => {
    return unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/discover', { body: { force: body.force ?? false }, signal })) as FolderDiscoveryResult
  },
  profiles: async (signal?: AbortSignal) => (unwrapGenerated(await apiClient.GET('/api/folder-discovery/environments', { signal })) as { items: FolderEnvironmentProfile[] }).items,
  history: async (offset = 0, signal?: AbortSignal, includeDeleted = false) => requestJson<{ items: FolderEnvironmentRegistration[]; total: number }>(apiUrl('/api/folder-discovery/environments/history', {}, { offset, limit: 50, include_deleted: includeDeleted }), { signal }),
  getDepthSchema: (signal?: AbortSignal) => requestJson<DepthSchema>(apiUrl('/api/folder-discovery/environments/depth-schema' as never), { signal }),
  /** 409 `DEPTH_SCHEMA_CONFLICT` when another save happened first; see `isDepthSchemaConflict`. */
  saveDepthSchema: (expectedSchemaSetId: string, upper: DepthUpper, environments: DepthSchemaEnvironmentsInput) => requestJson<DepthSchema>(apiUrl('/api/folder-discovery/environments/depth-schema' as never), jsonBody('PUT', { expected_schema_set_id: expectedSchemaSetId, upper, environments })),
  depthSchemaSamples: (segment: DepthSampleSegment, upper?: DepthUpper, signal?: AbortSignal) => requestJson<DepthSchemaSamples>(apiUrl('/api/folder-discovery/environments/depth-schema/samples' as never), { ...jsonBody('POST', upper ? { segment, upper } : { segment }), signal }),
  depthSchemaCheck: (upper: DepthUpper, environments: DepthSchemaEnvironmentsInput) => requestJson<DepthSchemaCheck>(apiUrl('/api/folder-discovery/environments/depth-schema/check' as never), jsonBody('POST', { upper, environments })),
  reinterpretRequest: (requestId: string) => requestJson<RequestReinterpretResult>(apiUrl('/api/requests/{request_id}/reinterpret' as never, { request_id: requestId }), jsonBody('POST', {})),
  registrationDeletePreview: (registrationIds: string[]) => requestJson<RegistrationDeletePreview>(apiUrl('/api/folder-discovery/environments/registrations/delete-preview' as never), jsonBody('POST', { registration_ids: registrationIds })),
  /** 409 `REGISTRATION_DELETE_BLOCKED` (detail carries preview items) or `DELETE_PREVIEW_STALE`. */
  deleteRegistrations: (registrationIds: string[], confirmToken: string) => requestJson<RegistrationDeleteResult>(apiUrl('/api/folder-discovery/environments/registrations/delete' as never), jsonBody('POST', { registration_ids: registrationIds, confirm_token: confirmToken })),
  projectCleanupCandidates: (signal?: AbortSignal) => requestJson<{ items: ProjectCleanupCandidate[] }>(apiUrl('/api/folder-discovery/environments/project-cleanup' as never), { signal }),
  projectCleanupPreview: (projectIds: string[]) => requestJson<ProjectCleanupPreview>(apiUrl('/api/folder-discovery/environments/project-cleanup/preview' as never), jsonBody('POST', { project_ids: projectIds })),
  /** 409 `PROJECT_CLEANUP_BLOCKED` (detail carries preview items) or `DELETE_PREVIEW_STALE`. */
  deleteProjects: (projectIds: string[], confirmToken: string) => requestJson<ProjectCleanupResult>(apiUrl('/api/folder-discovery/environments/project-cleanup/delete' as never), jsonBody('POST', { project_ids: projectIds, confirm_token: confirmToken })),
  scan: async (body: { environment: FolderEnvironment; relative_path: string; project_id?: string; request_id?: string }, signal?: AbortSignal) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/scan', { body, signal })) as FolderEnvironmentScan,
  preview: async (body: { scan_id: string; assignments: FolderAssignment[]; require_usage_review: boolean }, signal?: AbortSignal) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/previews', { body, signal })) as FolderEnvironmentPreview,
  usageReview: async (previewId: string, body: { case_relative_path: string; selection: { json: boolean; video: boolean; image: boolean; csv: boolean }; selected_sources?: Record<string, string>; metric_paths?: Record<string, string[]>; excludes?: Record<string, string>; acknowledge_partial: boolean }) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/previews/{preview_id}/usage-review', { params: { path: { preview_id: previewId } }, body })) as UsageSourceReview,
  register: async (body: { preview_id: string; idempotency_key: string; capture: boolean; review_acknowledged?: string[] }) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/registrations', { body })) as FolderEnvironmentRegistration,
  registration: async (id: string) => unwrapGenerated(await apiClient.GET('/api/folder-discovery/environments/registrations/{registration_id}', { params: { path: { registration_id: id } } })) as FolderEnvironmentRegistration,
  retryCapture: async (id: string) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/registrations/{registration_id}/capture/retry', { params: { path: { registration_id: id } }, body: {} })) as FolderEnvironmentRegistration,
}
