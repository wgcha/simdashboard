import type { AnalysisRequest, AnalysisRunSummary, AutomationTemplate, DashboardDefinition, DashboardPageSummary, DashboardSummary, DashboardVersion, DashboardVersionDefinition, DropVideoPage, FeatureExample, ImportSchema, ImportSchemaDefinition, LoadCase, Overview, PortfolioLayout, PortfolioOverview, Project, QualityThreshold, ReportLayout, ReportLayoutDefinition, ReportLayoutVersion, ReportTemplateAsset, ReviewItem, RunComparison, RunTrust, VariableDefinition, VariableDefinitionInput, WidgetCatalogItem, Workflow, WorkflowDashboardLayout, WorkflowStep, WorkspaceLayout, WorkspaceLayoutVersion } from './types'
import { authenticatedFetch, clearSession } from './auth'
import type { AuthUser, ProjectRole } from './auth'
import type { MenuPolicy } from './features/auth/access'

export type AssigneeCandidate = {
  user_id: string
  display_name: string
  employee_id: string | null
  department: string | null
  job_title: string | null
  role: ProjectRole
}

const fetch = authenticatedFetch

async function json<T>(response: Response | Promise<Response>): Promise<T> {
  response = await response
  if (!response.ok) {
    const body = await response.text()
    let detail = body
    try {
      const parsed = JSON.parse(body) as { detail?: string | { code?: string; [key: string]: unknown } }
      const value = parsed.detail
      detail = typeof value === 'string' ? value : value?.code ? `${value.code}: ${JSON.stringify(value)}` : body
    } catch { /* plain-text response */ }
    if (response.status === 401 && !response.url.endsWith('/api/auth/login')) {
      clearSession()
      window.dispatchEvent(new CustomEvent('analysis-auth-expired'))
    }
    if (response.status === 403) window.dispatchEvent(new CustomEvent('analysis-access-changed'))
    throw new Error(detail || `요청 실패 (${response.status})`)
  }
  return response.json() as Promise<T>
}

function arrayBufferToBase64(buffer: ArrayBuffer) {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, Math.min(offset + 0x8000, bytes.length)))
  }
  return btoa(binary)
}

export const api = {
  authStatus: () => json<{ mode: 'disabled' | 'password' | 'oidc'; authentication_required: boolean; oidc_start_url: string | null }>(fetch('/api/auth/status')),
  login: (username: string, password: string) => json<{ access_token: string; token_type: 'bearer'; expires_at: number; user: AuthUser }>(fetch('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }),
  })),
  me: () => json<AuthUser>(fetch('/api/auth/me')),
  menuPolicy: () => json<MenuPolicy>(fetch('/api/navigation/menu-policy')),
  logout: () => json<{ status: string }>(fetch('/api/auth/logout', { method: 'POST' })),
  health: () => json<{ status: string; database_backend: 'duckdb' | 'postgresql' }>(fetch('/api/health')),
  portfolio: (params: URLSearchParams) => json<PortfolioOverview>(fetch(`/api/portfolio/overview?${params}`)),
  portfolioCsvUrl: (params: URLSearchParams) => `/api/portfolio/export.csv?${params}`,
  projects: () => json<Project[]>(fetch('/api/projects')),
  featureExamples: () => json<FeatureExample[]>(fetch('/api/feature-examples')),
  importSchemas: () => json<ImportSchema[]>(fetch('/api/import-schemas')),
  createImportSchema: (payload: { name: string; description: string; definition: ImportSchemaDefinition; updated_by: string }) =>
    json<ImportSchema>(fetch('/api/import-schemas', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  updateImportSchema: (schemaId: string, payload: { name: string; description: string; definition: ImportSchemaDefinition; updated_by: string }) =>
    json<ImportSchema>(fetch(`/api/import-schemas/${schemaId}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  deleteImportSchema: (schemaId: string) => json<{ status: string; id: string }>(fetch(`/api/import-schemas/${schemaId}`, { method: 'DELETE' })),
  createProject: (payload: { name: string; product_name: string; description: string; manufacturer: string; display_size_inch: number | null }) =>
    json<Project>(fetch('/api/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  requests: (projectId: string) => json<AnalysisRequest[]>(fetch(`/api/projects/${projectId}/requests`)),
  assigneeCandidates: (projectId: string, query = '') => json<AssigneeCandidate[]>(fetch(`/api/projects/${encodeURIComponent(projectId)}/assignee-candidates${query.trim() ? `?q=${encodeURIComponent(query.trim())}` : ''}`)),
  createRequest: (projectId: string, payload: { title: string; owner_user_id: string; due_in_days: number; overall_note: string; source_type: 'EXTERNAL_SYSTEM' | 'DEPARTMENT_HEAD'; source_reference: string; requested_by: string; request_type_id: 'design-reliability-validation' | 'design-doe-exploration'; request_type_version: number }) =>
    json<AnalysisRequest>(fetch(`/api/projects/${projectId}/requests`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  loadCases: (requestId: string) => json<LoadCase[]>(fetch(`/api/requests/${requestId}/load-cases`)),
  dropVideos: (loadCaseId: string, page = 1, pageSize = 20, signal?: AbortSignal) =>
    json<DropVideoPage>(fetch(`/api/load-cases/${encodeURIComponent(loadCaseId)}/drop-videos?page=${page}&page_size=${pageSize}`, { signal })),
  createLoadCase: (requestId: string, payload: { name: string; analysis_type: 'DROP' | 'SIDE_CLAMP'; parameters: Record<string, string | number | string[]> }) =>
    json<LoadCase>(fetch(`/api/requests/${requestId}/load-cases`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  importResults: (loadCaseId: string, payload: { filename: string; content: string; author: string; validate_only: boolean }) =>
    json<{
      status: 'VALID' | 'IMPORTED'
      run_id?: string
      run_no?: number
      filename: string
      source_format: 'SUMMARY_RESULT' | 'RADIOSS_MESH_CSV'
      node_count: number
      element_count: number
      frame_count: number
      final_time: number | null
      scalar_count: number
      time_series_count: number
      open_cell_count: number
      chassis_rear_count: number
      fail_count: number
      overall_verdict: 'PASS' | 'FAIL'
      warnings: string[]
      results: Array<{ variable_key: string; display_name: string; value: number; unit: string; threshold: number; verdict: 'PASS' | 'FAIL'; analysis: string }>
    }>(fetch(`/api/load-cases/${loadCaseId}/results/import`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  importTypedFolderExample: (loadCaseId: string) =>
    json<{ status: 'IMPORTED'; job_id: string; run_id: string; run_no: number; schema_id: string; summary: { scalar_count: number; curve_count: number; media_count: number } }>(fetch(`/api/load-cases/${loadCaseId}/folder-import/example`, { method: 'POST' })),
  overview: (loadCaseId: string, runId?: string) => json<Overview>(fetch(`/api/load-cases/${loadCaseId}/overview${runId ? `?run_id=${encodeURIComponent(runId)}` : ''}`)),
  analysisRuns: (loadCaseId: string) => json<AnalysisRunSummary[]>(fetch(`/api/load-cases/${loadCaseId}/runs`)),
  runComparison: (loadCaseId: string, baselineRunId: string, targetRunId: string, variableKey?: string) => {
    const params = new URLSearchParams({ baseline_run_id: baselineRunId, target_run_id: targetRunId })
    if (variableKey) params.set('variable_key', variableKey)
    return json<RunComparison>(fetch(`/api/load-cases/${loadCaseId}/run-comparison?${params}`))
  },
  runTrust: (runId: string) => json<RunTrust>(fetch(`/api/analysis-runs/${runId}/trust`)),
  reviewItems: (runId: string) => json<ReviewItem[]>(fetch(`/api/analysis-runs/${runId}/review-items`)),
  createReviewItem: (runId: string, payload: { title: string; body: string; variable_key: string | null; time_value: number | null; entity_type: 'NODE' | 'ELEMENT' | null; entity_id: string | null; review_status: 'OPEN' | 'IN_REVIEW' | 'RESOLVED'; created_by: string }) =>
    json<ReviewItem>(fetch(`/api/analysis-runs/${runId}/review-items`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  updateReviewItem: (annotationId: string, reviewStatus: 'OPEN' | 'IN_REVIEW' | 'RESOLVED', body?: string) =>
    json<ReviewItem>(fetch(`/api/review-items/${annotationId}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ review_status: reviewStatus, ...(body ? { body } : {}) }) })),
  workflows: () => json<Workflow[]>(fetch('/api/workflows')),
  workspaceLayout: <T extends PortfolioLayout | WorkflowDashboardLayout>(projectId: string, kind: 'portfolio' | 'workflow') =>
    json<WorkspaceLayout<T>>(fetch(`/api/projects/${encodeURIComponent(projectId)}/workspace-layouts/${kind}`)),
  saveWorkspaceLayout: <T extends PortfolioLayout | WorkflowDashboardLayout>(projectId: string, kind: 'portfolio' | 'workflow', definition: T) =>
    json<WorkspaceLayout<T>>(fetch(`/api/projects/${encodeURIComponent(projectId)}/workspace-layouts/${kind}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ definition }),
    })),
  workspaceLayoutVersions: (projectId: string, kind: 'portfolio' | 'workflow') =>
    json<WorkspaceLayoutVersion[]>(fetch(`/api/projects/${encodeURIComponent(projectId)}/workspace-layouts/${kind}/versions`)),
  adminUsers: (params = new URLSearchParams()) => json<Array<{ id: string; username: string; display_name: string; employee_id: string | null; account_status: 'PENDING' | 'ACTIVE' | 'SUSPENDED'; is_global_admin: boolean; updated_at: string }>>(fetch(`/api/admin/users?${params}`)),
  updateUserStatus: (userId: string, payload: { account_status: 'PENDING' | 'ACTIVE' | 'SUSPENDED'; expected_updated_at: string; reason: string }) =>
    json<Record<string, unknown>>(fetch(`/api/admin/users/${encodeURIComponent(userId)}/status`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  updateGlobalAdmin: (userId: string, payload: { is_global_admin: boolean; expected_updated_at: string; reason: string }) =>
    json<Record<string, unknown>>(fetch(`/api/admin/users/${encodeURIComponent(userId)}/global-admin`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  projectMembers: (projectId: string) => json<Array<{ user_id: string; username: string; display_name: string; employee_id: string | null; role: ProjectRole; updated_at: string }>>(fetch(`/api/projects/${encodeURIComponent(projectId)}/members`)),
  updateProjectMember: (projectId: string, userId: string, role: ProjectRole, expectedUpdatedAt?: string) =>
    json<Record<string, unknown>>(fetch(`/api/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(userId)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role, expected_updated_at: expectedUpdatedAt }) })),
  deleteProjectMember: (projectId: string, userId: string) => json<Record<string, unknown>>(fetch(`/api/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(userId)}`, { method: 'DELETE' })),
  updateMenuPolicy: (payload: { expected_version: number; change_note: string; visibility: Partial<Record<ProjectRole, Record<string, boolean>>> }) =>
    json<MenuPolicy>(fetch('/api/admin/menu-policy', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  menuPolicyVersions: () => json<Array<{ version: number; created_by: string; created_at: string; source_version: number | null; change_note: string | null }>>(fetch('/api/admin/menu-policy/versions')),
  restoreMenuPolicy: (version: number) => json<MenuPolicy>(fetch(`/api/admin/menu-policy/versions/${version}/restore`, { method: 'POST' })),
  auditEvents: (limit = 200) => json<Array<Record<string, unknown>>>(fetch(`/api/audit-events?limit=${limit}`)),
  qualityThresholds: (projectId: string) => json<QualityThreshold[]>(fetch(`/api/projects/${projectId}/quality-thresholds`)),
  updateQualityThreshold: (projectId: string, criterionKey: string, thresholdDouble: number) =>
    json<QualityThreshold>(
      fetch(`/api/projects/${encodeURIComponent(projectId)}/quality-thresholds/${encodeURIComponent(criterionKey)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ threshold_double: thresholdDouble, updated_by: '관리자' }),
      }),
    ),
  updateWorkflowStep: (stepId: string, payload: { name: string; status: 'READY' | 'COMPLETED' | 'IN_PROGRESS' | 'WAITING' | 'BLOCKED' | 'FAILED'; owner_user_id: string; progress: number; is_optional: boolean; note: string }) =>
    json<WorkflowStep>(
      fetch(`/api/workflow-steps/${stepId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }),
    ),
  replaceWorkflowSteps: (requestId: string, steps: Array<{ id: string | null; name: string; status: 'READY' | 'COMPLETED' | 'IN_PROGRESS' | 'WAITING' | 'BLOCKED' | 'FAILED'; owner_user_id: string; progress: number; is_optional: boolean; note: string }>) =>
    json<WorkflowStep[]>(fetch(`/api/requests/${requestId}/workflow-steps`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ steps }) })),
  dashboard: (id = 'dashboard-drop-default') => json<DashboardDefinition>(fetch(`/api/dashboards/${id}`)),
  dashboards: (projectId?: string) => json<DashboardSummary[]>(fetch(`/api/dashboards${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`)),
  dashboardPages: (loadCaseId: string) => json<DashboardPageSummary[]>(fetch(`/api/dashboard-pages?load_case_id=${encodeURIComponent(loadCaseId)}`)),
  adminDashboardPages: (loadCaseId: string, includeArchived = true) => json<DashboardPageSummary[]>(fetch(`/api/admin/dashboard-pages?load_case_id=${encodeURIComponent(loadCaseId)}&include_archived=${includeArchived}`)),
  createDashboardPage: (payload: { load_case_id: string; name: string; description: string }) =>
    json<DashboardDefinition>(fetch('/api/admin/dashboard-pages', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  updateDashboardPage: (dashboardId: string, payload: { name?: string; description?: string; status?: 'draft' | 'published' | 'archived' }) =>
    json<DashboardDefinition>(fetch(`/api/admin/dashboard-pages/${dashboardId}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  deleteDashboardPage: (dashboardId: string, loadCaseId: string) =>
    json<{ status: 'deleted'; id: string; load_case_id: string }>(fetch(`/api/admin/dashboard-pages/${dashboardId}?load_case_id=${encodeURIComponent(loadCaseId)}`, { method: 'DELETE' })),
  reorderDashboardPages: (loadCaseId: string, pageIds: string[]) =>
    json<DashboardPageSummary[]>(fetch('/api/admin/dashboard-pages/order', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ load_case_id: loadCaseId, page_ids: pageIds }) })),
  variables: (loadCaseId: string) => json<VariableDefinition[]>(fetch(`/api/load-cases/${loadCaseId}/variables`)),
  createVariable: (loadCaseId: string, payload: VariableDefinitionInput) =>
    json<VariableDefinition>(fetch(`/api/load-cases/${loadCaseId}/variables`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  updateVariable: (loadCaseId: string, variableKey: string, payload: Omit<VariableDefinitionInput, 'variable_key' | 'data_type'>) =>
    json<VariableDefinition>(fetch(`/api/load-cases/${loadCaseId}/variables/${encodeURIComponent(variableKey)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  deleteVariable: (loadCaseId: string, variableKey: string) =>
    json<{ status: string; variable_key: string }>(fetch(`/api/load-cases/${loadCaseId}/variables/${encodeURIComponent(variableKey)}`, { method: 'DELETE' })),
  widgetCatalog: () => json<WidgetCatalogItem[]>(fetch('/api/widget-catalog')),
  automationTemplates: (projectId?: string) => json<AutomationTemplate[]>(fetch(`/api/automation-templates${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`)),
  reportLayouts: () => json<ReportLayout[]>(fetch('/api/report-layouts')),
  createReportLayout: (payload: { name: string; description: string; definition: ReportLayoutDefinition; updated_by: string }) =>
    json<ReportLayout>(fetch('/api/report-layouts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  updateReportLayout: (layoutId: string, payload: { name: string; description: string; definition: ReportLayoutDefinition; updated_by: string }) =>
    json<ReportLayout>(fetch(`/api/report-layouts/${layoutId}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  deleteReportLayout: (layoutId: string) => json<{ status: string; id: string }>(fetch(`/api/report-layouts/${layoutId}`, { method: 'DELETE' })),
  reportLayoutVersions: (layoutId: string) => json<ReportLayoutVersion[]>(fetch(`/api/report-layouts/${layoutId}/versions`)),
  reportLayoutVersion: (layoutId: string, version: number) =>
    json<{ layout_id: string; version: number; definition: ReportLayoutDefinition; created_by: string; created_at: string }>(fetch(`/api/report-layouts/${layoutId}/versions/${version}`)),
  reportTemplates: () => json<ReportTemplateAsset[]>(fetch('/api/report-templates')),
  uploadReportTemplate: async (name: string, file: File) => json<ReportTemplateAsset>(fetch('/api/report-templates', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, filename: file.name, content_base64: arrayBufferToBase64(await file.arrayBuffer()), updated_by: '보고서 편집자' }),
  })),
  deleteReportTemplate: (templateId: string) => json<{ status: string; id: string }>(fetch(`/api/report-templates/${templateId}`, { method: 'DELETE' })),
  renderReportTemplate: async (templateId: string, replacements: Record<string, string>, filename: string) => {
    const response = await fetch(`/api/report-templates/${templateId}/render`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ replacements, filename }) })
    if (!response.ok) throw new Error(await response.text() || `요청 실패 (${response.status})`)
    return response.blob()
  },
  dashboardVersions: (dashboardId: string, includeInvalid = false) =>
    json<DashboardVersion[]>(fetch(`/api/dashboards/${dashboardId}/versions?include_invalid=${includeInvalid}`)),
  dashboardVersion: (dashboardId: string, version: number, includeInvalid = false) =>
    json<DashboardVersionDefinition>(fetch(`/api/dashboards/${dashboardId}/versions/${version}?include_invalid=${includeInvalid}`)),
  deleteDashboardVersion: (dashboardId: string, version: number) =>
    json<{ status: 'invalidated'; dashboard_id: string; version: number }>(fetch(`/api/dashboards/${dashboardId}/versions/${version}`, { method: 'DELETE' })),
  cloneDashboard: (dashboardId: string, name: string, description: string) => json<{ id: string; version: number }>(fetch(`/api/dashboards/${dashboardId}/clone`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, description, created_by: '대시보드 사용자' }) })),
  restoreDashboard: (dashboardId: string, version: number) => json<{ version: number; restored_from: number }>(fetch(`/api/dashboards/${dashboardId}/restore/${version}`, { method: 'POST' })),
  saveDashboard: (definition: DashboardDefinition) =>
    json<{ status: string; version: number; updated_at: string }>(
      fetch(`/api/dashboards/${definition.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(definition),
      }),
    ),
  previewCommand: (command: string) =>
    json<{
      recognized: boolean
      message: string
      proposal: null | ({ action: 'add_widget'; widget: DashboardDefinition['widgets'][number] } | { action: 'update_widgets'; updates: Array<{ widget_type: string; x?: number; y?: number; w?: number; h?: number; settings?: Record<string, unknown> }> })
    }>(
      fetch('/api/dashboard-commands/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command }),
      }),
    ),
}
