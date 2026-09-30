import { apiFetch } from './auth'
import { apiErrorFromResponse } from './errors'
import { apiUrl } from './url'

export type ResultEnvironment = 'USAGE' | 'DISTRIBUTION'

export type ResultRegistrationContext = {
  simulation_case: { id: string; label: string; relative_path: string } | null
  evaluation: { label: string; relative_path: string } | null
  load_case: { id: string; label: string; relative_path: string } | null
  execution_run: { id: string; label: string; relative_path: string } | null
  run_option: { id: string | null; label: string | null; relative_path: string | null; status: 'PRESENT' | 'ABSENT' | 'UNRESOLVED' } | null
  scene: { id: string; label: string; relative_path: string } | null
}

export type ResultRegistrationCase = {
  relative_path: string
  name: string
  role_kind: 'SIMULATION_CASE'
  status?: string
  result_state: 'PRESENT' | 'MISSING' | 'UNAVAILABLE'
  context: ResultRegistrationContext
  can_prepare?: boolean
  suggested_relative_path?: string | null
}

export type ResultRegistrationTarget = {
  project_id: string
  project_name: string
  request_id: string
  request_name: string
  spdm_project_folder?: string | null
  spdm_request_folder?: string | null
  status: string
  cases: ResultRegistrationCase[]
}

export type ResultRegistrationTargets = {
  storage_root_id: string | null
  environment: ResultEnvironment
  targets: ResultRegistrationTarget[]
}

export type ResultFolderNode = {
  relative_path: string
  name: string
  role_kind: string | null
  context: ResultRegistrationContext
  result_state: 'PRESENT' | 'MISSING' | 'UNAVAILABLE' | 'NOT_APPLICABLE'
  can_prepare: boolean
  suggested_relative_path: string | null
  children_available: boolean
  selectable?: boolean
}

export type ResultFolders = {
  storage_root_id: string | null
  project_id: string
  request_id: string
  environment: ResultEnvironment
  parent_relative_path: string | null
  parent_context: ResultRegistrationContext
  nodes: ResultFolderNode[]
}

export type ResultLocationCandidate = {
  relative_path: string
  schema_parent_path: string
  schema_role_kind: 'EVALUATION' | 'SCENE'
  schema_target_id?: string | null
  schema_scan_id: string
  schema_profile_id: string
  schema_profile_revision: number
  exists: boolean
  context: ResultRegistrationContext
}

export type ResultLocationLink = {
  id: string
  root_key?: string
  relative_path: string
  schema_parent_path: string
  schema_role_kind: 'EVALUATION' | 'SCENE'
  schema_target_id?: string | null
  schema_scan_id: string
  schema_profile_id: string
  schema_profile_revision: number
  revision: number
  created_by: string
  created_at: string
  updated_by: string
  updated_at: string
  is_current: boolean
}

export type ResultLocations = {
  storage_root_id: string | null
  project_id: string
  request_id: string
  environment: ResultEnvironment
  request_relative_path: string
  candidates: ResultLocationCandidate[]
  links: ResultLocationLink[]
  schema_error?: { code: string; message: string } | null
}

export type ResultManifestItem = {
  relative_path: string
  size: number
  sha256?: string | null
  media_type: string
  upload_status?: string
  inspection_status?: string
}

export type ResultFileExclusion = { relative_path: string; reason: string }

export type ResultInspectionMetric = {
  evaluation?: string | null
  metric?: string | null
  key?: string | null
  value: unknown
  value_type?: string | null
  unit?: string | null
  source_path?: string | null
  source_sha256?: string | null
  status: string
}

export type ResultInspectionMedia = {
  relative_path: string
  sha256: string
  size: number
  media_type: string
  kind: 'IMAGE' | 'VIDEO'
  title?: string | null
  status: 'READY' | 'UNSUPPORTED' | string
}

export type ResultInspectionIssue = {
  severity?: string | null
  code?: string | null
  message?: string | null
  detail?: string | null
  source_path?: string | null
  [key: string]: unknown
}

export type ResultInspection = {
  draft_id: string
  status: string
  inspection_revision: string | number
  source_revision?: string | number
  manifest: ResultManifestItem[]
  exclusions?: ResultFileExclusion[]
  metrics: ResultInspectionMetric[]
  media: ResultInspectionMedia[]
  issues: ResultInspectionIssue[]
  missing_count: number
  blocking_count: number
}

export type ResultRegistrationDraft = {
  draft_id: string
  status: string
  inspection_revision: string | number | null
}

export type ResultRegistrationPublished = {
  draft_id: string
  status: string
  case_id: string
  capture_id: string
  environment: ResultEnvironment
  project_id: string
  request_id: string
  context: ResultRegistrationContext
  asset_count: number
  result_count: number
  media_count?: number
  image_count?: number
  video_count?: number
  mirror_status?: string | null
  error?: { code: string; message: string; relative_path?: string | null } | null
}

export type ResultRegistrationDraftRead = {
  draft_id: string
  status: string
  revision: number
  project_id: string
  request_id: string
  environment: ResultEnvironment
  storage_root_id: string
  case_relative_path: string
  result_relative_path: string
  context: ResultRegistrationContext
  inspection_revision: string | null
  source_revision: string
  manifest: ResultManifestItem[]
  exclusions?: ResultFileExclusion[]
  inspection: {
    metrics: ResultInspectionMetric[]
    media: ResultInspectionMedia[]
    issues: ResultInspectionIssue[]
    missing_count: number
    blocking_count: number
    metric_count: number
    result_count: number
  }
  mirror_status: string | null
  error: { code: string; message: string; relative_path?: string | null } | null
  capture_id: string | null
  case_id: string | null
  asset_count: number
  result_count: number
  media_count: number
  image_count: number
  video_count: number
  idempotency_key: string | null
  approved_by: string | null
  published_at: string | null
  approved_files: ResultManifestItem[]
}

export type ResultFolderPreparation = {
  status: string
  case_relative_path: string
  result_relative_path: string
  created: boolean
  proposed_paths?: Array<{ relative_path: string; role_kind: string; name: string; exists: boolean }>
  created_paths?: string[]
  context: ResultRegistrationContext
}

type DraftFile = { file: File; relative_path: string; sha256?: string | null; media_type: string }

const endpoint = (...segments: string[]) => `/${['api', 'result-registration', ...segments].join('/')}`

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await apiFetch(apiUrl(path as never), init)
  if (!response.ok) throw await apiErrorFromResponse(response)
  return await response.json() as T
}

export const resultRegistrationApi = {
  targets(environment: ResultEnvironment, signal?: AbortSignal) {
    const query = new URLSearchParams({ environment })
    return requestJson<ResultRegistrationTargets>(`${endpoint('targets')}?${query}`, { signal })
  },
  folders(input: { project_id: string; request_id: string; environment: ResultEnvironment; parent_relative_path?: string }, signal?: AbortSignal) {
    const query = new URLSearchParams({ project_id: input.project_id, request_id: input.request_id, environment: input.environment })
    if (input.parent_relative_path) query.set('parent_relative_path', input.parent_relative_path)
    return requestJson<ResultFolders>(`${endpoint('folders')}?${query}`, { signal })
  },
  locations(input: { project_id: string; request_id: string; environment: ResultEnvironment }, signal?: AbortSignal) {
    const query = new URLSearchParams(input)
    return requestJson<ResultLocations>(`${endpoint('locations')}?${query}`, { signal })
  },
  createLocation(input: { project_id: string; request_id: string; environment: ResultEnvironment; relative_path: string }) {
    return requestJson<ResultLocationLink>(endpoint('locations'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
    })
  },
  updateLocation(linkId: string, input: { project_id: string; request_id: string; environment: ResultEnvironment; relative_path: string; revision: number }) {
    return requestJson<ResultLocationLink>(endpoint('locations', encodeURIComponent(linkId)), {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
    })
  },
  deleteLocation(linkId: string, input: { project_id: string; request_id: string; environment: ResultEnvironment; revision: number }) {
    const query = new URLSearchParams({ project_id: input.project_id, request_id: input.request_id,
      environment: input.environment, revision: String(input.revision) })
    return requestJson<{ id: string; deleted: boolean }>(`${endpoint('locations', encodeURIComponent(linkId))}?${query}`, { method: 'DELETE' })
  },
  prepareFolder(input: { project_id: string; request_id: string; environment: ResultEnvironment; parent_relative_path?: string; segments: Array<{ role_kind: string; name: string }>; confirm_create: boolean }) {
    return requestJson<ResultFolderPreparation>(endpoint('folders', 'prepare'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) })
  },
  createDraft(input: {
    project_id: string
    request_id: string
    environment: ResultEnvironment
    case_relative_path: string
    result_relative_path: string
    context: ResultRegistrationContext
    files: Array<Pick<ResultManifestItem, 'relative_path' | 'size' | 'sha256' | 'media_type'>>
  }) {
    return requestJson<ResultRegistrationDraft>(endpoint('drafts'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) })
  },
  uploadFiles(draftId: string, files: DraftFile[]) {
    const body = new FormData()
    for (const entry of files) {
      body.append('files', entry.file, entry.file.name)
      body.append('relative_paths', entry.relative_path)
    }
    return requestJson<{ draft_id: string; status: string }>(endpoint('drafts', encodeURIComponent(draftId), 'files'), { method: 'POST', body })
  },
  inspect(draftId: string, input?: { exclusions?: ResultFileExclusion[] }) {
    return requestJson<ResultInspection>(endpoint('drafts', encodeURIComponent(draftId), 'inspect'), {
      method: 'POST',
      ...(input ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) } : {}),
    })
  },
  approve(draftId: string, input: { inspection_revision: string | number; acknowledge_partial: boolean; exclusions?: ResultFileExclusion[] }) {
    return requestJson<{ draft_id: string; status: string }>(endpoint('drafts', encodeURIComponent(draftId), 'approve'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) })
  },
  publish(draftId: string, input: { inspection_revision: string | number; idempotency_key: string }) {
    return requestJson<ResultRegistrationPublished>(endpoint('drafts', encodeURIComponent(draftId), 'publish'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) })
  },
  getDraft(draftId: string, signal?: AbortSignal) {
    return requestJson<ResultRegistrationDraftRead>(endpoint('drafts', encodeURIComponent(draftId)), { signal })
  },
  retryMirror(draftId: string) {
    return requestJson<ResultRegistrationPublished>(endpoint('drafts', encodeURIComponent(draftId), 'mirror', 'retry'), { method: 'POST' })
  },
  mediaUrl(draftId: string, relativePath: string) {
    return apiUrl('/api/result-registration/drafts/{draft_id}/media', { draft_id: draftId }, { relative_path: relativePath })
  },
}

export async function sha256Hex(file: File): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}
