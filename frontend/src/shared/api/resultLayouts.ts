import type { DashboardDefinition } from '../../types'
import { requireStringField, responseRecord, responseRecordArray } from './adapters'
import { apiClient, unwrapGenerated } from './client'

export type AnalysisTemplateVersion = {
  template_id: string
  version: number
  scope_kind: 'SYSTEM' | 'PROJECT'
  project_id: string | null
  display_name: string
  description: string
  lifecycle_status: 'DRAFT' | 'PUBLISHED' | 'ARCHIVED'
  page_definitions: DashboardDefinition[]
  created_by: string
  created_at: string
}

export type ResultProfileInput = {
  template_id: string
  template_version: number
  included_widget_ids: string[] | null
  overrides: Record<string, unknown>
  required_data_contracts: string[]
}

export type ResultProfile = {
  request_type_id: string
  request_type_version: number
  template_id: string
  template_version: number
  template: AnalysisTemplateVersion
  included_widget_ids: string[] | null
  overrides: Record<string, unknown>
  required_data_contracts: string[]
  profile_scope?: 'SYSTEM_DEFAULT' | 'PROJECT_OVERRIDE'
  project_id?: string | null
  binding_version?: number
}

export type ResultLayoutSnapshot = {
  template_id: string
  template_version: number
  template_name: string
  request_type_id: string
  request_type_version: number
  profile_scope?: 'SYSTEM_DEFAULT' | 'PROJECT_OVERRIDE'
  profile_project_id?: string | null
  profile_binding_version?: number | null
  pages: DashboardDefinition[]
  required_data_contracts: string[]
}

export type ResultScalarBinding = {
  variable_key: string
  display_name: string
  value: string | number | null
  unit: string | null
  threshold: number | null
  verdict: string | null
}

export type ResultLayoutBindings = {
  available_data_contracts: string[]
  load_cases: Array<{
    id: string
    name: string
    analysis_type: string
    status: string
  }>
  latest_result_run: {
    id: string
    run_no: number
    solver: string | null
    status: string
    load_case_name: string
  } | null
  scalars: ResultScalarBinding[]
  error: string | null
  widget_data_contracts?: Record<string, string[]>
  widget_errors?: Record<string, string>
  widget_states?: Record<string, string>
}

export type RequestResultLayout = {
  request_id: string
  compatibility?: { route_kind: 'DOMAIN'; renderer: 'LEGACY_DOMAIN' } | null
  status?: 'UNCONFIGURED'
  message?: string
  snapshot?: ResultLayoutSnapshot
  source_request_type_id?: string
  source_request_type_version?: number
  source_template_id?: string
  source_template_version?: number
  bindings?: ResultLayoutBindings
}

function validatedRecord(value: unknown, label: string, stringFields: readonly string[] = []): Record<string, unknown> {
  const item = responseRecord(value, label)
  for (const field of stringFields) requireStringField(item, field, label)
  return item
}

function adaptAnalysisTemplate(value: unknown): AnalysisTemplateVersion { return validatedRecord(value, 'analysisTemplate', ['template_id', 'display_name']) as unknown as AnalysisTemplateVersion }
function adaptAnalysisTemplates(value: unknown): AnalysisTemplateVersion[] { return responseRecordArray(value, 'analysisTemplates').map(adaptAnalysisTemplate) }
function adaptResultProfile(value: unknown): ResultProfile | null {
  const item = validatedRecord(value, 'resultProfile', ['request_type_id'])
  return item.status === 'UNCONFIGURED' ? null : item as unknown as ResultProfile
}
function adaptRequestResultLayout(value: unknown): RequestResultLayout {
  return validatedRecord(value, 'requestResultLayout', ['request_id']) as unknown as RequestResultLayout
}

async function materializeRequestResultLayout(requestId: string, loadCaseId: string, pageId?: string): Promise<DashboardDefinition> {
  const result = await apiClient.POST('/api/workbench/requests/{request_id}/result-layout/materialize', {
    params: { path: { request_id: requestId } },
    body: { load_case_id: loadCaseId, ...(pageId ? { page_id: pageId } : {}) },
  })
  return validatedRecord(unwrapGenerated(result), 'materializedDashboard', ['id', 'name']) as unknown as DashboardDefinition
}

// The generated OpenAPI contract owns transport paths; these adapters define
// the richer DashboardDefinition payload returned by the versioned endpoints.
export function requestResultLayoutQuery(loadCaseId?: string) {
  return loadCaseId ? { load_case_id: loadCaseId } : undefined
}

export const resultLayoutApi = {
  analysisTemplates: async (allVersions = false, projectId?: string) => adaptAnalysisTemplates(unwrapGenerated(await apiClient.GET('/api/workbench/analysis-templates', { params: { query: { all_versions: allVersions, project_id: projectId } } }))),
  resultProfile: async (requestTypeId: string, version: number, projectId?: string) => adaptResultProfile(unwrapGenerated(await apiClient.GET('/api/workbench/request-types/{request_type_id}/{version}/result-profile', { params: { path: { request_type_id: requestTypeId, version }, query: { project_id: projectId } } }))),
  requestResultLayout: async (requestId: string, loadCaseId?: string) => adaptRequestResultLayout(unwrapGenerated(await apiClient.GET('/api/workbench/requests/{request_id}/result-layout', { params: { path: { request_id: requestId }, query: requestResultLayoutQuery(loadCaseId) } }))),
  materializeRequestResultLayout,
  saveProjectResultProfile: async (projectId: string, requestTypeId: string, version: number, payload: ResultProfileInput) => adaptResultProfile(unwrapGenerated(await apiClient.PUT('/api/projects/{project_id}/admin/workbench/request-types/{request_type_id}/{version}/result-profile', { params: { path: { project_id: projectId, request_type_id: requestTypeId, version } }, body: payload }))),
}
