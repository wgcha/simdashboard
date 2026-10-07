import type { DriveUploadBatch } from './drive'
import { apiFetch } from './auth'
import { apiClient, unwrapGenerated } from './client'
import { apiErrorFromResponse } from './errors'
import type { DashboardEnvironment } from './simulationDashboard'
import { apiUrl } from './url'

export type CaseFinalizationReportFormat = 'pptx' | 'html'

export type CaseFinalizationInput = {
  project_id: string
  request_id: string
  environment: DashboardEnvironment
  case_id: string
  /** `latest:<dashboard_case_id>` (merged latest result) or one stored capture id. */
  capture_id: string
}

export type CaseFinalizationFile = {
  source_relative_path: string
  case_relative_path: string
  /** Internal record key (folder is `Final/Report`, §15 D21). Version 1 records (before 2026-10-03) also used it for result files. */
  category: 'CAE' | 'Reports'
  source_basis: 'SOURCE_CAPTURE' | 'CURRENT_CONFIRMED_SCENE' | 'SELECTED_CAPTURE'
  source_capture_id?: string
  size: number
  /** Plan version 3 (W2): `null` in a preview for files pinned by size/mtime; completed records always carry the hash. */
  sha256: string | null
  /** Plan version 3: modification time (ns) the preview pinned for a non-capture file. */
  modified_ns?: number
}

export type CaseFinalizationSceneSource = { scene_path: string; source_capture_id: string; source_capture_fingerprint?: string }

/** A current confirmed Scene left out of the latest basis (never replaced by an older capture). */
export type CaseFinalizationExcludedScene = {
  scene_path: string
  source_capture_id: string | null
  reason: 'NO_CAPTURE' | 'CAPTURE_SCHEMA_MISSING' | 'CAPTURE_SCHEMA_INCOMPATIBLE'
}

export type CaseFinalizationReport = { format: CaseFinalizationReportFormat; file_name: string; size: number; sha256: string; relative_path: string }

export type CaseFinalizationCounts = {
  CAE: number
  input_decks: number
  rad_decks: number
  inc_decks: number
  results: number
  scene_reports?: number
  /** Plan version 3 (W2): files that are neither decks, captured results nor Scene documents. */
  other_files?: number
  /** Plan version 3: bytes of every CAE file. */
  total_bytes?: number
  /** Version 1 records only. */
  Reports?: number
  reports?: number
}

export type CaseFinalizationRecord = {
  schema_version: number
  operation_id: string
  status: 'COMPLETE'
  case_id: string
  case_label: string
  case_path: string
  capture_id: string
  basis: 'LATEST' | 'CAPTURE'
  scene_sources: CaseFinalizationSceneSource[]
  capture_fingerprint: string
  folder_schema_snapshot_id: string
  output_paths: Record<'CAE' | 'Reports', string>
  files: CaseFinalizationFile[]
  reports: CaseFinalizationReport[]
  counts: CaseFinalizationCounts
  missing: { input_decks: boolean; rad_decks: boolean; inc_decks: boolean; reports?: boolean }
  excluded_capture_file_count: number
  /** Decks too large to parse at preview: copied, but their includes were not followed. */
  include_unchecked?: string[]
  created_by: string | null
  queued_at?: string | null
  confirmed_at: string
  /** Status only. `SHA256`: re-hashed now. Over the per-call hash budget: `STAT_SINCE_COMPLETION` (size/mtime/id unchanged since the hash check at completion — not proof of unchanged content) or `SIZE` (existence and sizes only). */
  /** ``DRIVE_UPLOAD``: SCX drive Final (D3), completed through the drive upload queue (record in the DB). */
  verification?: 'SHA256' | 'STAT_SINCE_COMPLETION' | 'SIZE' | 'DRIVE_UPLOAD'
}

export type CaseFinalizationPreview = Omit<CaseFinalizationRecord, 'status' | 'created_by' | 'confirmed_at' | 'reports'> & {
  status: 'PREVIEW'
  project_id: string
  request_id: string
  environment: DashboardEnvironment
  previewed_at: string
  plan_sha256: string
  can_confirm: boolean
  scene_paths: string[]
  /** Previews made before 2026-10-03 do not carry this field. */
  excluded_scenes?: CaseFinalizationExcludedScene[]
  report_files: Record<CaseFinalizationReportFormat, string>
  report_paths: Record<CaseFinalizationReportFormat, string>
  report_limits: Record<CaseFinalizationReportFormat, number>
  /** Free space on the Final volume (informational; rechecked on confirm and when copying starts). */
  disk?: { required_bytes: number; margin_bytes: number; free_bytes: number | null; sufficient: boolean }
}

export type CaseFinalizationJobState = 'QUEUED' | 'RUNNING' | 'FAILED' | 'COMPLETE'

/** Background Final copy job (W2): progress of one Final ID. */
export type CaseFinalizationJob = {
  operation_id: string
  state: CaseFinalizationJobState
  phase: 'COPYING' | 'VERIFYING' | 'PUBLISHING' | null
  files_done: number
  files_total: number
  bytes_done: number
  bytes_total: number
  current_file: string | null
  error: { code: string; message: string } | null
  attempt: number | null
  queued_at: string | null
  started_at: string | null
  updated_at: string | null
  case_id: string | null
  capture_id: string | null
  reports: Array<{ format: CaseFinalizationReportFormat; file_name: string; size: number; sha256: string }>
  output_paths: Record<'CAE' | 'Reports', string> | null
  /** A worker of this server process runs or queues it. */
  active: boolean
  /** The completed record once `state` is COMPLETE. */
  record: CaseFinalizationRecord | null
  /** SCX drive mode (D3): the Final is written by the drive upload queue. */
  storage?: 'scx'
  drive?: DriveUploadBatch | null
}

export type CaseFinalizationCurrent = {
  operation_id: string
  case_id: string
  case_label: string
  case_path: string
  designated_by: string | null
  designated_at: string | null
  schema_version: number
  output_paths: Record<'CAE' | 'Reports', string> | null
  /** false when the current Final's outputs failed re-hashing or its records/outputs are missing. */
  verified?: boolean
  verification?: 'SHA256' | 'STAT_SINCE_COMPLETION' | 'SIZE' | 'FAILED' | 'MISSING' | 'DRIVE_UPLOAD'
  /** The signed pointer names a Final whose records/outputs can no longer be found. */
  missing?: boolean
}

export type CaseFinalizationHistoryItem = {
  operation_id: string
  case_id: string
  case_label: string
  designated_by: string | null
  designated_at: string | null
  schema_version: number
  role: 'CURRENT' | 'PREVIOUS'
}

export type CaseFinalizationStagedReport = { operation_id: string; format: CaseFinalizationReportFormat; file_name: string; size: number; sha256: string; report_path: string; status: 'STAGED' }

export type CaseFinalizationStatus = {
  case_id: string
  request_id: string
  environment: DashboardEnvironment
  final_relative_path: string
  latest: CaseFinalizationRecord | null
  selected_case_latest: CaseFinalizationRecord | null
  retryable_operations: Array<{ operation_id: string; status: 'RETRYABLE'; capture_id: string; previewed_at: string; job?: CaseFinalizationJob | null }>
  /** Copy jobs of the selected Case (QUEUED/RUNNING/FAILED), newest first. */
  active_operations?: CaseFinalizationJob[]
  /** W3: the request + environment's current Final (newest completion), any Case. */
  current_final?: CaseFinalizationCurrent | null
  /** W3: completed Finals of the request + environment, newest first (at most 50). */
  final_history?: CaseFinalizationHistoryItem[]
  /** W3: state of the SPDM summary file (`Final/current.json`). */
  /** The signed-in user may use the summary repair override (global admin). */
  can_override_summary?: boolean
  /** SCX drive mode: ``PENDING`` while the new designation file is queued (append-only ``designations/<seq>-<id>.json``). */
  summary?: { state: 'NONE' | 'OK' | 'MISSING' | 'STALE' | 'CONFLICT' | 'CURRENT_UNVERIFIED' | 'CURRENT_MISSING' | 'PENDING'; path: string; final_id?: string | null }
  unverified_records: number
  storage?: 'scx'
}

const REPORT_CONTENT_TYPE: Record<CaseFinalizationReportFormat, string> = {
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  html: 'text/html; charset=utf-8',
}

export const caseFinalizationApi = {
  preview: async (input: CaseFinalizationInput) => unwrapGenerated(await apiClient.POST('/api/dashboard/finalizations/preview', { body: input })) as CaseFinalizationPreview,
  /** One raw-body request per report; the server picks the stored file name. */
  uploadReport: async (input: CaseFinalizationInput & { operation_id: string }, format: CaseFinalizationReportFormat, blob: Blob, signal?: AbortSignal) => {
    const url = apiUrl('/api/dashboard/finalizations/{operation_id}/reports/{report_format}', { operation_id: input.operation_id, report_format: format }, {
      project_id: input.project_id, request_id: input.request_id, environment: input.environment, case_id: input.case_id, capture_id: input.capture_id,
    })
    const response = await apiFetch(url, { method: 'PUT', body: blob, headers: { 'Content-Type': REPORT_CONTENT_TYPE[format] }, signal })
    if (!response.ok) throw await apiErrorFromResponse(response)
    return await response.json() as CaseFinalizationStagedReport
  },
  /** Records the copy job and returns at once (W2): QUEUED/RUNNING, or COMPLETE with `record` for a finished Final ID. */
  confirm: async (input: CaseFinalizationInput & { operation_id: string; report_formats: CaseFinalizationReportFormat[] }) => unwrapGenerated(await apiClient.POST('/api/dashboard/finalizations/confirm', { body: input })) as CaseFinalizationJob,
  /** W3: rewrite `Final/current.json` for the current Final (RESULT_IMPORT); returns the status. */
  /** `override` (admin, audited): move off a missing current Final or replace a Final/current.json the app did not write. */
  repairSummary: async (input: Omit<CaseFinalizationInput, 'capture_id'> & { override?: boolean }) => unwrapGenerated(await apiClient.POST('/api/dashboard/finalizations/summary/repair', { body: { ...input, override: input.override ?? false } })) as CaseFinalizationStatus,
  /** Copy job progress; polling also resumes a job interrupted by a server restart. */
  job: async (input: Omit<CaseFinalizationInput, 'capture_id'> & { operation_id: string }) => unwrapGenerated(await apiClient.GET('/api/dashboard/finalizations/{operation_id}/job', {
    params: { path: { operation_id: input.operation_id }, query: { project_id: input.project_id, request_id: input.request_id, environment: input.environment, case_id: input.case_id } },
  })) as CaseFinalizationJob,
  status: async (input: Omit<CaseFinalizationInput, 'capture_id'>) => unwrapGenerated(await apiClient.GET('/api/dashboard/finalizations/status', {
    params: { query: { project_id: input.project_id, request_id: input.request_id, environment: input.environment, case_id: input.case_id } },
  })) as CaseFinalizationStatus,
}
