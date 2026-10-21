import { apiFetch } from './auth'
import { apiClient, unwrapGenerated } from './client'
import { apiErrorFromResponse } from './errors'
import { apiUrl } from './url'
import { timeoutGuard, timeoutMessage } from './requestTimeout'

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
  /** SCX drive mode only (plan D2): drive files waiting for confirmation and the version token form. */
  drive?: FolderEnvironmentDriveSync | null
}
export type FolderEnvironmentDriveSync = {
  pending_changes: number
  missing: number
  ignored: number
  new_files?: number
  /** 'sha1' | 'size_mtime' | 'mixed' (C1/C9: whether drive listings carry sha1). */
  token_kind?: string | null
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

/** One folder the discovery did not register, with the reason (global administrators only). */
export type FolderDiscoveryReview = { relative_path: string; reason: string; code?: string; environment?: FolderEnvironment | null; deviation_code?: string; deviation_count?: number; deviation_path?: string; /** ADMIN_EXCLUDED only: the exclusion was made from the rescan result and can be undone. */ restorable?: boolean }
/** A request the rescan reinterpreted (or could not) with the current depth schema (issue #55). */
export type StorageRescanReinterpret = {
  project_id: string; project_name: string; request_id: string; request_name: string; environment: string
  registration_id?: string | null; code?: string; message: string
  deviation_count: number; deviations: Array<{ code: string; message: string; relative_path: string; blocking: boolean }>
}
export type FolderExclusionResult = { excluded?: string[]; restored?: string[]; skipped: Array<{ relative_path: string; code: string; message: string }> }
export type StorageRescanFailure = { project_id: string; project_name: string; request_id: string; request_name: string; environment: string; code: string; message: string }
/** [저장 폴더 새로고침] full rescan (POST/GET /api/folder-discovery/environments/rescan, 2026-10-17). */
export type StorageRescanState = {
  status: 'IDLE' | 'RUNNING' | 'DONE' | 'FAILED' | 'CANCELLED'
  started_at: string | null
  finished_at: string | null
  started_by: string | null
  phase: 'DISCOVER' | 'REINTERPRET' | 'SYNC' | null
  /** Live progress (issue #55): requests done/total of REINTERPRET/SYNC; DISCOVER step, current folder and projects. */
  progress: {
    done: number; total: number
    step?: string | null; current_path?: string | null
    projects_done?: number; projects_total?: number; pass_number?: number
  }
  /** Drive calls of this rescan (SCX drive mode): listings, errors after retries, DRIVE_BUSY retries, last error. */
  drive?: { calls: number; listings: number; errors: number; retries: number; last_error: { code: string; operation?: string | null; relative_path?: string | null; at?: string } | null }
  elapsed_seconds?: number
  cancel_requested?: boolean
  /** Requests the SYNC phase left for the next rescan (phase time limit). */
  sync_deferred?: number
  created_projects: Array<{ id: string; name: string }>
  created_requests: Array<{ id: string; name: string; environment: FolderEnvironment; project_id: string }>
  needs_review: FolderDiscoveryReview[]
  /** Requests registered with an older depth schema that were reinterpreted / blocked by a deviation (issue #55). */
  reinterpreted: StorageRescanReinterpret[]
  reinterpret_blocked: StorageRescanReinterpret[]
  reinterpret_unchanged: number
  reinterpret_deferred: number
  rounds: number
  scopes: number
  refreshed: number
  failed: StorageRescanFailure[]
  /** The round limit was reached before every folder was examined. */
  partial: boolean
  error: string | null
  /** POST only: false when a rescan was already running. */
  started?: boolean
  /** POST .../rescan/cancel only: false when no rescan was running. */
  requested?: boolean
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
export type DepthSchemaSamples = { levels: DepthSampleLevel[]; requests_sampled: number; run_option_names?: DepthRunOptionName[]; run_option_names_truncated?: boolean; /** the server stopped at its read budget: later folders are missing */ partial?: boolean; issues?: Array<{ relative_path: string; code: string }> }
export type DepthSchemaCheck = { by_code: Record<string, number>; examples: Array<{ relative_path: string; code: string }>; requests_checked?: number; partial?: boolean }
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
export type ProjectCleanupSystemPage = { id: string; name: string; versions: number; user_versions: number }
export type ProjectCleanupAcknowledgeReason = 'RETAINED_DATA' | 'USER_DATA' | 'SYSTEM_ANALYSIS_PAGES'
/** 보관 데이터 (imported/registered results, Cases, legacy links, media), 직접 만든 데이터, hosted system pages. */
export type ProjectCleanupDataSummary = { user_data: Record<string, number>; user_data_total: number; retained_data: Record<string, number>; retained_total: number; system_pages: ProjectCleanupSystemPage[]; acknowledge_reasons: ProjectCleanupAcknowledgeReason[]; requires_acknowledgement: boolean }
export type ProjectCleanupCandidate = ProjectCleanupDataSummary & { project_id: string; name: string; category: ProjectCleanupCategory; selectable: boolean; requests: number; cases: number; runs: number }
/** A live registration a force delete turns into a tombstone (2026-10-17). */
export type ProjectCleanupForcedRegistration = { id: string; project_id: string | null; environment: string; status: string; root_key: string; relative_path: string }
export type ProjectCleanupPreviewItem = ProjectCleanupDataSummary & { project_id: string; name: string; category: ProjectCleanupCategory; deletable: boolean; counts: Record<string, number>; blockers: RegistrationDeleteBlocker[]; forced?: boolean; forced_registrations?: ProjectCleanupForcedRegistration[]; overridden?: string[] }
export type ProjectCleanupPreview = { items: ProjectCleanupPreviewItem[]; totals: Record<string, number>; confirm_token: string }
export type ProjectCleanupResult = { deleted: string[]; counts: Record<string, number> }

/** [새 결과 확인] summary: every registered environment of one request (2026-10-14). */
export type RequestResultsCheck = {
  checked_at: string
  registered: boolean
  changed: boolean
  added: number
  failed: Array<{ environment: string; status: string | null; code: string | null; message: string | null }>
  environments: Array<{ environment: string; status: string | null; changed: boolean | null }>
}

// Endpoints added by the depth-schema contract are not in the generated OpenAPI client yet.
/** `timeout`: stop waiting after `ms` and fail with a readable message (a caller's own abort still wins). */
async function requestJson<T>(path: string, init: RequestInit = {}, timeout?: { ms: number; what: string }): Promise<T> {
  const guard = timeout ? timeoutGuard(init.signal ?? undefined, timeout.ms) : null
  try {
    const response = await apiFetch(path, guard ? { ...init, signal: guard.signal } : init)
    if (!response.ok) throw await apiErrorFromResponse(response)
    return await response.json() as T
  } catch (error) {
    if (timeout && guard?.timedOut()) throw new Error(timeoutMessage(timeout.ms, timeout.what))
    throw error
  } finally { guard?.dispose() }
}
/** Client waits: a little above the server's own read budgets (environment_folder_profiles SAMPLE_/CHECK_BUDGET_*). */
export const DEPTH_SCHEMA_LOAD_TIMEOUT_MS = 30_000
export const DEPTH_SAMPLES_TIMEOUT_MS = 45_000
export const DEPTH_CHECK_TIMEOUT_MS = 90_000
const jsonBody = (method: 'POST' | 'PUT', body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const folderEnvironmentApi = {
  refresh: async (body: { project_id: string; request_id: string; environment: FolderEnvironment }): Promise<FolderEnvironmentRefresh> => {
    return unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/refresh', { body })) as FolderEnvironmentRefresh
  },
  sync: async (body: { project_id: string; request_id: string; environment: FolderEnvironment; force?: boolean }, signal?: AbortSignal): Promise<FolderEnvironmentSync> => {
    return unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/sync', { body: { ...body, force: body.force ?? false }, signal })) as FolderEnvironmentSync
  },
  syncRequest: async (body: { project_id: string; request_id: string }, signal?: AbortSignal): Promise<RequestResultsCheck> => {
    return unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/sync-request', { body, signal })) as RequestResultsCheck
  },
  discover: async (body: { force?: boolean } = {}, signal?: AbortSignal): Promise<FolderDiscoveryResult> => {
    return unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/discover', { body: { force: body.force ?? false }, signal })) as FolderDiscoveryResult
  },
  rescanStart: async (): Promise<StorageRescanState> => unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/rescan', {})) as StorageRescanState,
  rescanStatus: async (signal?: AbortSignal): Promise<StorageRescanState> => unwrapGenerated(await apiClient.GET('/api/folder-discovery/environments/rescan', { signal })) as StorageRescanState,
  /** [중지]: the running rescan stops at its next check point and ends CANCELLED (issue #55). */
  rescanCancel: () => requestJson<StorageRescanState>(apiUrl('/api/folder-discovery/environments/rescan/cancel' as never), jsonBody('POST', {})),
  /** [제외] / [모두 제외] in the rescan result: request folders the discovery never registers (issue #55). */
  excludeFolders: (relativePaths: string[]) => requestJson<FolderExclusionResult>(apiUrl('/api/folder-discovery/environments/exclusions' as never), jsonBody('POST', { relative_paths: relativePaths })),
  /** [다시 포함]: undo an exclusion made from the rescan result. */
  restoreFolders: (relativePaths: string[]) => requestJson<FolderExclusionResult>(apiUrl('/api/folder-discovery/environments/exclusions/restore' as never), jsonBody('POST', { relative_paths: relativePaths })),
  profiles: async (signal?: AbortSignal) => (unwrapGenerated(await apiClient.GET('/api/folder-discovery/environments', { signal })) as { items: FolderEnvironmentProfile[] }).items,
  history: async (offset = 0, signal?: AbortSignal, includeDeleted = false) => requestJson<{ items: FolderEnvironmentRegistration[]; total: number }>(apiUrl('/api/folder-discovery/environments/history', {}, { offset, limit: 50, include_deleted: includeDeleted }), { signal }),
  getDepthSchema: (signal?: AbortSignal) => requestJson<DepthSchema>(apiUrl('/api/folder-discovery/environments/depth-schema' as never), { signal }, { ms: DEPTH_SCHEMA_LOAD_TIMEOUT_MS, what: '깊이 스키마' }),
  /** 409 `DEPTH_SCHEMA_CONFLICT` when another save happened first; see `isDepthSchemaConflict`. */
  saveDepthSchema: (expectedSchemaSetId: string, upper: DepthUpper, environments: DepthSchemaEnvironmentsInput) => requestJson<DepthSchema>(apiUrl('/api/folder-discovery/environments/depth-schema' as never), jsonBody('PUT', { expected_schema_set_id: expectedSchemaSetId, upper, environments })),
  depthSchemaSamples: (segment: DepthSampleSegment, upper?: DepthUpper, signal?: AbortSignal) => requestJson<DepthSchemaSamples>(apiUrl('/api/folder-discovery/environments/depth-schema/samples' as never), { ...jsonBody('POST', upper ? { segment, upper } : { segment }), signal }, { ms: DEPTH_SAMPLES_TIMEOUT_MS, what: '예시 폴더' }),
  depthSchemaCheck: (upper: DepthUpper, environments: DepthSchemaEnvironmentsInput) => requestJson<DepthSchemaCheck>(apiUrl('/api/folder-discovery/environments/depth-schema/check' as never), jsonBody('POST', { upper, environments }), { ms: DEPTH_CHECK_TIMEOUT_MS, what: '확인' }),
  reinterpretRequest: (requestId: string) => requestJson<RequestReinterpretResult>(apiUrl('/api/requests/{request_id}/reinterpret' as never, { request_id: requestId }), jsonBody('POST', {})),
  registrationDeletePreview: (registrationIds: string[]) => requestJson<RegistrationDeletePreview>(apiUrl('/api/folder-discovery/environments/registrations/delete-preview' as never), jsonBody('POST', { registration_ids: registrationIds })),
  /** 409 `REGISTRATION_DELETE_BLOCKED` (detail carries preview items) or `DELETE_PREVIEW_STALE`. */
  deleteRegistrations: (registrationIds: string[], confirmToken: string) => requestJson<RegistrationDeleteResult>(apiUrl('/api/folder-discovery/environments/registrations/delete' as never), jsonBody('POST', { registration_ids: registrationIds, confirm_token: confirmToken })),
  projectCleanupCandidates: (signal?: AbortSignal) => requestJson<{ items: ProjectCleanupCandidate[] }>(apiUrl('/api/folder-discovery/environments/project-cleanup' as never), { signal }),
  /** `forceProjectIds`: global administrator force delete of registered projects (their registrations become tombstones). */
  projectCleanupPreview: (projectIds: string[], forceProjectIds: string[] = []) => requestJson<ProjectCleanupPreview>(apiUrl('/api/folder-discovery/environments/project-cleanup/preview' as never), jsonBody('POST', { project_ids: projectIds, force_project_ids: forceProjectIds })),
  /** 409 `PROJECT_CLEANUP_BLOCKED` (detail carries preview items), `PROJECT_CLEANUP_CONFIRM_REQUIRED` or `DELETE_PREVIEW_STALE`. */
  deleteProjects: (projectIds: string[], confirmToken: string, acknowledgedProjectIds: string[] = [], forceProjectIds: string[] = []) => requestJson<ProjectCleanupResult>(apiUrl('/api/folder-discovery/environments/project-cleanup/delete' as never), jsonBody('POST', { project_ids: projectIds, confirm_token: confirmToken, acknowledge_data_project_ids: acknowledgedProjectIds, force_project_ids: forceProjectIds })),
  scan: async (body: { environment: FolderEnvironment; relative_path: string; project_id?: string; request_id?: string }, signal?: AbortSignal) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/scan', { body, signal })) as FolderEnvironmentScan,
  preview: async (body: { scan_id: string; assignments: FolderAssignment[]; require_usage_review: boolean }, signal?: AbortSignal) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/previews', { body, signal })) as FolderEnvironmentPreview,
  usageReview: async (previewId: string, body: { case_relative_path: string; selection: { json: boolean; video: boolean; image: boolean; csv: boolean }; selected_sources?: Record<string, string>; metric_paths?: Record<string, string[]>; excludes?: Record<string, string>; acknowledge_partial: boolean }) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/previews/{preview_id}/usage-review', { params: { path: { preview_id: previewId } }, body })) as UsageSourceReview,
  register: async (body: { preview_id: string; idempotency_key: string; capture: boolean; review_acknowledged?: string[] }) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/registrations', { body })) as FolderEnvironmentRegistration,
  registration: async (id: string) => unwrapGenerated(await apiClient.GET('/api/folder-discovery/environments/registrations/{registration_id}', { params: { path: { registration_id: id } } })) as FolderEnvironmentRegistration,
  retryCapture: async (id: string) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/registrations/{registration_id}/capture/retry', { params: { path: { registration_id: id } }, body: {} })) as FolderEnvironmentRegistration,
}
