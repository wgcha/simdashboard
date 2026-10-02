import { apiClient, unwrapGenerated } from './client'
import type { DashboardEnvironment } from './simulationDashboard'

export type CaseFinalizationInput = {
  project_id: string
  request_id: string
  environment: DashboardEnvironment
  case_id: string
  capture_id: string
}

export type CaseFinalizationFile = {
  source_relative_path: string
  case_relative_path: string
  category: 'CAE' | 'Reports'
  source_basis: 'SELECTED_CAPTURE' | 'CURRENT_CONFIRMED_SCENE'
  size: number
  sha256: string
}

export type CaseFinalizationRecord = {
  operation_id: string
  status: 'COMPLETE'
  case_id: string
  case_label: string
  case_path: string
  capture_id: string
  capture_fingerprint: string
  folder_schema_snapshot_id: string
  output_paths: Record<'CAE' | 'Reports', string>
  files: CaseFinalizationFile[]
  counts: { CAE: number; Reports: number; input_decks: number; rad_decks: number; inc_decks: number; reports: number; results: number }
  missing: { input_decks: boolean; rad_decks: boolean; inc_decks: boolean; reports: boolean }
  excluded_capture_file_count: number
  created_by: string | null
  confirmed_at: string
}

export type CaseFinalizationPreview = Omit<CaseFinalizationRecord, 'status' | 'created_by' | 'confirmed_at' | 'output_paths'> & {
  status: 'PREVIEW'
  project_id: string
  request_id: string
  environment: DashboardEnvironment
  case_path: string
  case_label: string
  previewed_at: string
  plan_sha256: string
  can_confirm: boolean
}

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

export const caseFinalizationApi = {
  preview: async (input: CaseFinalizationInput) => unwrapGenerated(await apiClient.POST('/api/dashboard/finalizations/preview', { body: input })) as CaseFinalizationPreview,
  confirm: async (input: CaseFinalizationInput & { operation_id: string }) => unwrapGenerated(await apiClient.POST('/api/dashboard/finalizations/confirm', { body: input })) as CaseFinalizationRecord,
  status: async (input: Omit<CaseFinalizationInput, 'capture_id'>) => unwrapGenerated(await apiClient.GET('/api/dashboard/finalizations/status', {
    params: { query: { project_id: input.project_id, request_id: input.request_id, environment: input.environment, case_id: input.case_id } },
  })) as CaseFinalizationStatus,
}
