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
  /** Version 1 records (before 2026-10-03) also used `Reports` for result files. */
  category: 'CAE' | 'Reports'
  source_basis: 'SOURCE_CAPTURE' | 'CURRENT_CONFIRMED_SCENE' | 'SELECTED_CAPTURE'
  source_capture_id?: string
  size: number
  sha256: string
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
  created_by: string | null
  confirmed_at: string
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
}

export type CaseFinalizationStagedReport = { operation_id: string; format: CaseFinalizationReportFormat; file_name: string; size: number; sha256: string; report_path: string; status: 'STAGED' }

export type CaseFinalizationStatus = {
  case_id: string
  request_id: string
  environment: DashboardEnvironment
  final_relative_path: string
  latest: CaseFinalizationRecord | null
  selected_case_latest: CaseFinalizationRecord | null
  retryable_operations: Array<{ operation_id: string; status: 'RETRYABLE'; capture_id: string; previewed_at: string }>
  unverified_records: number
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
  confirm: async (input: CaseFinalizationInput & { operation_id: string; report_formats: CaseFinalizationReportFormat[] }) => unwrapGenerated(await apiClient.POST('/api/dashboard/finalizations/confirm', { body: input })) as CaseFinalizationRecord,
  status: async (input: Omit<CaseFinalizationInput, 'capture_id'>) => unwrapGenerated(await apiClient.GET('/api/dashboard/finalizations/status', {
    params: { query: { project_id: input.project_id, request_id: input.request_id, environment: input.environment, case_id: input.case_id } },
  })) as CaseFinalizationStatus,
}
