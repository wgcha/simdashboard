import type { AnalysisRequest, AnalysisRunSummary, AutomationTemplate, DashboardDefinition, DashboardPageSummary, DashboardSummary, DashboardVersion, DashboardVersionDefinition, DropVideoPage, FeatureExample, ImportSchema, ImportSchemaDefinition, LoadCase, Overview, PortfolioLayout, PortfolioOverview, Project, QualityThreshold, ReportTemplateAsset, ReviewItem, RunComparison, RunTrust, VariableDefinition, VariableDefinitionInput, WidgetCatalogItem, Workflow, WorkflowDashboardLayout, WorkflowStep, WorkspaceLayout, WorkspaceLayoutVersion } from './types'
import { apiFetch } from './shared/api/auth'
import { requireStringField, responseRecord, responseRecordArray } from './shared/api/adapters'
import { apiClient, unwrapGenerated } from './shared/api/client'
import { apiErrorFromResponse } from './shared/api/errors'
import { apiUrl } from './shared/api/url'
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

function requireBooleanField(value: Record<string, unknown>, key: string, label: string) {
  if (typeof value[key] !== 'boolean') throw new TypeError(`${label}.${key} 응답 형식이 올바르지 않습니다.`)
}

function requireNumberField(value: Record<string, unknown>, key: string, label: string) {
  if (typeof value[key] !== 'number' || !Number.isFinite(value[key])) throw new TypeError(`${label}.${key} 응답 형식이 올바르지 않습니다.`)
}

function adaptAuthStatus(value: unknown) {
  const item = responseRecord(value, 'authStatus')
  requireStringField(item, 'mode', 'authStatus')
  requireBooleanField(item, 'authentication_required', 'authStatus')
  if (!['disabled', 'password', 'oidc'].includes(item.mode as string)) throw new TypeError('authStatus.mode 응답 값이 올바르지 않습니다.')
  return item as { mode: 'disabled' | 'password' | 'oidc'; authentication_required: boolean; oidc_start_url: string | null }
}

function adaptLogin(value: unknown) {
  const item = responseRecord(value, 'login')
  requireStringField(item, 'access_token', 'login')
  requireStringField(item, 'token_type', 'login')
  requireNumberField(item, 'expires_at', 'login')
  if (item.token_type !== 'bearer') throw new TypeError('login.token_type 응답 값이 올바르지 않습니다.')
  item.user = adaptCompactLoginUser(item.user)
  return item as { access_token: string; token_type: 'bearer'; expires_at: number; user: AuthUser }
}

function adaptAuthUser(value: unknown): AuthUser {
  const item = responseRecord(value, 'authUser')
  for (const key of ['id', 'username', 'display_name', 'account_status']) requireStringField(item, key, 'authUser')
  requireBooleanField(item, 'is_global_admin', 'authUser')
  if (!Array.isArray(item.memberships) || !Array.isArray(item.company_permissions)) throw new TypeError('authUser 권한 응답 형식이 올바르지 않습니다.')
  item.memberships.forEach((membership) => {
    const member = responseRecord(membership, 'authUser.membership')
    requireStringField(member, 'project_id', 'authUser.membership')
    requireStringField(member, 'role', 'authUser.membership')
  })
  if (!item.company_permissions.every((permission) => typeof permission === 'string')) throw new TypeError('authUser.company_permissions 응답 형식이 올바르지 않습니다.')
  return item as unknown as AuthUser
}

function adaptCompactLoginUser(value: unknown): AuthUser {
  const item = responseRecord(value, 'login.user')
  // Login intentionally returns a compact identity. Only this endpoint may
  // supply empty permission arrays; /auth/me must prove its full contract.
  return adaptAuthUser({ ...item, memberships: item.memberships ?? [], company_permissions: item.company_permissions ?? [] })
}

function adaptMenuPolicy(value: unknown): MenuPolicy {
  const item = responseRecord(value, 'menuPolicy')
  requireStringField(item, 'updated_by', 'menuPolicy')
  requireStringField(item, 'updated_at', 'menuPolicy')
  requireNumberField(item, 'version', 'menuPolicy')
  if (!Array.isArray(item.menus)) throw new TypeError('menuPolicy.menus 응답 형식이 올바르지 않습니다.')
  item.menus.forEach((menu) => {
    const candidate = responseRecord(menu, 'menuPolicy.menu')
    for (const key of ['id', 'label', 'required_permission', 'context_kind']) requireStringField(candidate, key, 'menuPolicy.menu')
    requireNumberField(candidate, 'sequence_no', 'menuPolicy.menu')
    requireBooleanField(candidate, 'is_policy_editable', 'menuPolicy.menu')
    responseRecord(candidate.visibility, 'menuPolicy.menu.visibility')
  })
  return item as unknown as MenuPolicy
}

function adaptProject(value: unknown): Project {
  const item = responseRecord(value, 'project')
  requireStringField(item, 'id', 'project')
  requireStringField(item, 'name', 'project')
  return item as unknown as Project
}

function adaptRequest(value: unknown): AnalysisRequest {
  const item = responseRecord(value, 'analysisRequest')
  for (const key of ['id', 'project_id', 'title', 'status']) requireStringField(item, key, 'analysisRequest')
  return item as unknown as AnalysisRequest
}

function adaptLoadCase(value: unknown): LoadCase {
  const item = responseRecord(value, 'loadCase')
  for (const key of ['id', 'request_id', 'name', 'analysis_type', 'status']) requireStringField(item, key, 'loadCase')
  return item as unknown as LoadCase
}

function adaptOverview(value: unknown): Overview {
  const item = responseRecord(value, 'overview')
  requireStringField(item, 'overall_verdict', 'overview')
  return item as unknown as Overview
}

function adaptAnalysisRun(value: unknown): AnalysisRunSummary {
  const item = responseRecord(value, 'analysisRun')
  for (const key of ['id', 'load_case_id', 'status']) requireStringField(item, key, 'analysisRun')
  return item as unknown as AnalysisRunSummary
}

function adaptWorkflow(value: unknown): Workflow {
  const item = responseRecord(value, 'workflow')
  responseRecord(item.request, 'workflow.request')
  if (!Array.isArray(item.steps)) throw new TypeError('workflow.steps 응답 형식이 올바르지 않습니다.')
  item.steps.forEach((step) => responseRecord(step, 'workflow.step'))
  return item as unknown as Workflow
}

function validatedRecord(value: unknown, label: string, stringFields: readonly string[] = [], numberFields: readonly string[] = [], arrayFields: readonly string[] = []): Record<string, unknown> {
  const item = responseRecord(value, label)
  for (const field of stringFields) requireStringField(item, field, label)
  for (const field of numberFields) requireNumberField(item, field, label)
  for (const field of arrayFields) if (!Array.isArray(item[field])) throw new TypeError(`${label}.${field} 응답 형식이 올바르지 않습니다.`)
  return item
}

function validatedRecordArray(value: unknown, label: string, stringFields: readonly string[] = []): Record<string, unknown>[] {
  return responseRecordArray(value, label).map((item) => validatedRecord(item, label, stringFields))
}

type ImportResultsResponse = {
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
}

type FolderImportResponse = { status: 'IMPORTED'; job_id: string; run_id: string; run_no: number; schema_id: string; summary: { scalar_count: number; curve_count: number; media_count: number } }
type AdminUser = { id: string; username: string; display_name: string; employee_id: string | null; account_status: 'PENDING' | 'ACTIVE' | 'SUSPENDED'; is_global_admin: boolean; updated_at: string }
type ProjectMember = { user_id: string; username: string; display_name: string; employee_id: string | null; role: ProjectRole; updated_at: string }
type MenuPolicyVersion = { version: number; created_by: string; created_at: string; source_version: number | null; change_note: string | null }
type DashboardMutation = { status: string; version: number; updated_at: string }
type DashboardPreview = { recognized: boolean; message: string; proposal: null | ({ action: 'add_widget'; widget: DashboardDefinition['widgets'][number] } | { action: 'update_widgets'; updates: Array<{ widget_type: string; x?: number; y?: number; w?: number; h?: number; settings?: Record<string, unknown> }> }) }

function adaptPortfolio(value: unknown): PortfolioOverview { return validatedRecord(value, 'portfolio', ['grain', 'source'], [], ['trend', 'records']) as unknown as PortfolioOverview }
function adaptFeatureExample(value: unknown): FeatureExample { return validatedRecord(value, 'featureExample', ['id', 'title']) as unknown as FeatureExample }
function adaptFeatureExamples(value: unknown): FeatureExample[] { return responseRecordArray(value, 'featureExamples').map(adaptFeatureExample) }
function adaptImportSchema(value: unknown): ImportSchema { return validatedRecord(value, 'importSchema', ['id', 'name']) as unknown as ImportSchema }
function adaptImportSchemas(value: unknown): ImportSchema[] { return responseRecordArray(value, 'importSchemas').map(adaptImportSchema) }
function adaptStatusId(value: unknown): { status: string; id: string } { return validatedRecord(value, 'statusId', ['status', 'id']) as unknown as { status: string; id: string } }
function adaptAssigneeCandidate(value: unknown): AssigneeCandidate { return validatedRecord(value, 'assigneeCandidate', ['user_id', 'display_name']) as unknown as AssigneeCandidate }
function adaptAssigneeCandidates(value: unknown): AssigneeCandidate[] { return responseRecordArray(value, 'assigneeCandidates').map(adaptAssigneeCandidate) }
function adaptDropVideoPage(value: unknown): DropVideoPage { return validatedRecord(value, 'dropVideos', [], [], ['videos']) as unknown as DropVideoPage }
function adaptImportResults(value: unknown): ImportResultsResponse { return validatedRecord(value, 'importResults', ['status', 'filename'], [], ['warnings', 'results']) as unknown as ImportResultsResponse }
function adaptFolderImport(value: unknown): FolderImportResponse { return validatedRecord(value, 'folderImport', ['status', 'job_id', 'run_id', 'schema_id'], ['run_no']) as unknown as FolderImportResponse }
function adaptRunComparison(value: unknown): RunComparison { return validatedRecord(value, 'runComparison', [], [], ['scalar_comparison', 'available_series']) as unknown as RunComparison }
function adaptRunTrust(value: unknown): RunTrust { return validatedRecord(value, 'runTrust', ['trust_status']) as unknown as RunTrust }
function adaptReviewItem(value: unknown): ReviewItem { return validatedRecord(value, 'reviewItem', ['id', 'title']) as unknown as ReviewItem }
function adaptReviewItems(value: unknown): ReviewItem[] { return responseRecordArray(value, 'reviewItems').map(adaptReviewItem) }
function adaptPortfolioWorkspaceLayout(value: unknown): WorkspaceLayout<PortfolioLayout> { return validatedRecord(value, 'portfolioWorkspaceLayout', ['layout_kind', 'updated_by'], ['version']) as unknown as WorkspaceLayout<PortfolioLayout> }
function adaptWorkflowWorkspaceLayout(value: unknown): WorkspaceLayout<WorkflowDashboardLayout> { return validatedRecord(value, 'workflowWorkspaceLayout', ['layout_kind', 'updated_by'], ['version']) as unknown as WorkspaceLayout<WorkflowDashboardLayout> }
function adaptWorkspaceLayoutVersions(value: unknown): WorkspaceLayoutVersion[] { return validatedRecordArray(value, 'workspaceLayoutVersions', ['layout_kind', 'created_by']) as unknown as WorkspaceLayoutVersion[] }
function adaptAdminUsers(value: unknown): AdminUser[] { return validatedRecordArray(value, 'adminUsers', ['id', 'username', 'display_name']) as unknown as AdminUser[] }
function adaptProjectMembers(value: unknown): ProjectMember[] { return validatedRecordArray(value, 'projectMembers', ['user_id', 'username', 'display_name', 'role']) as unknown as ProjectMember[] }
function adaptMutationResult(value: unknown, label: string): Record<string, unknown> { return validatedRecord(value, label) }
function adaptMenuPolicyVersions(value: unknown): MenuPolicyVersion[] { return validatedRecordArray(value, 'menuPolicyVersions', ['created_by', 'created_at']) as unknown as MenuPolicyVersion[] }
function adaptAuditEvents(value: unknown): Array<Record<string, unknown>> { return validatedRecordArray(value, 'auditEvents') }
function adaptQualityThreshold(value: unknown): QualityThreshold { return validatedRecord(value, 'qualityThreshold', ['criterion_key', 'project_id']) as unknown as QualityThreshold }
function adaptQualityThresholds(value: unknown): QualityThreshold[] { return responseRecordArray(value, 'qualityThresholds').map(adaptQualityThreshold) }
function adaptWorkflowStep(value: unknown): WorkflowStep { return validatedRecord(value, 'workflowStep', ['id', 'name']) as unknown as WorkflowStep }
function adaptWorkflowSteps(value: unknown): WorkflowStep[] { return responseRecordArray(value, 'workflowSteps').map(adaptWorkflowStep) }
function adaptDashboard(value: unknown): DashboardDefinition { return validatedRecord(value, 'dashboard', ['id', 'name'], [], ['widgets']) as unknown as DashboardDefinition }
function adaptDashboardSummary(value: unknown): DashboardSummary { return validatedRecord(value, 'dashboardSummary', ['id', 'name']) as unknown as DashboardSummary }
function adaptDashboardSummaries(value: unknown): DashboardSummary[] { return responseRecordArray(value, 'dashboards').map(adaptDashboardSummary) }
function adaptDashboardPage(value: unknown): DashboardPageSummary { return validatedRecord(value, 'dashboardPage', ['id', 'name']) as unknown as DashboardPageSummary }
function adaptDashboardPages(value: unknown): DashboardPageSummary[] { return responseRecordArray(value, 'dashboardPages').map(adaptDashboardPage) }
function adaptDeletedDashboardPage(value: unknown): { status: 'deleted'; id: string; load_case_id: string } { return validatedRecord(value, 'deleteDashboardPage', ['status', 'id', 'load_case_id']) as unknown as { status: 'deleted'; id: string; load_case_id: string } }
function adaptVariable(value: unknown): VariableDefinition { return validatedRecord(value, 'variable', ['id', 'display_name']) as unknown as VariableDefinition }
function adaptVariables(value: unknown): VariableDefinition[] { return responseRecordArray(value, 'variables').map(adaptVariable) }
function adaptDeletedVariable(value: unknown): { status: string; variable_key: string } { return validatedRecord(value, 'deleteVariable', ['status', 'variable_key']) as unknown as { status: string; variable_key: string } }
function adaptWidgetCatalogItem(value: unknown): WidgetCatalogItem { return validatedRecord(value, 'widgetCatalogItem', ['type', 'label']) as unknown as WidgetCatalogItem }
function adaptWidgetCatalog(value: unknown): WidgetCatalogItem[] { return responseRecordArray(value, 'widgetCatalog').map(adaptWidgetCatalogItem) }
function adaptAutomationTemplate(value: unknown): AutomationTemplate { return validatedRecord(value, 'automationTemplate', ['id', 'template_name']) as unknown as AutomationTemplate }
function adaptAutomationTemplates(value: unknown): AutomationTemplate[] { return responseRecordArray(value, 'automationTemplates').map(adaptAutomationTemplate) }
function adaptReportTemplate(value: unknown): ReportTemplateAsset { return validatedRecord(value, 'reportTemplate', ['id', 'name', 'filename']) as unknown as ReportTemplateAsset }
function adaptReportTemplates(value: unknown): ReportTemplateAsset[] { return responseRecordArray(value, 'reportTemplates').map(adaptReportTemplate) }
function adaptDashboardVersions(value: unknown): DashboardVersion[] { return validatedRecordArray(value, 'dashboardVersions', ['dashboard_id', 'created_by']) as unknown as DashboardVersion[] }
function adaptDashboardVersion(value: unknown): DashboardVersionDefinition { return validatedRecord(value, 'dashboardVersion', ['dashboard_id', 'created_by'], ['version']) as unknown as DashboardVersionDefinition }
function adaptInvalidatedDashboardVersion(value: unknown): { status: 'invalidated'; dashboard_id: string; version: number } { return validatedRecord(value, 'deleteDashboardVersion', ['status', 'dashboard_id'], ['version']) as unknown as { status: 'invalidated'; dashboard_id: string; version: number } }
function adaptDashboardClone(value: unknown): { id: string; version: number } { return validatedRecord(value, 'cloneDashboard', ['id'], ['version']) as unknown as { id: string; version: number } }
function adaptDashboardRestore(value: unknown): { version: number; restored_from: number } { return validatedRecord(value, 'restoreDashboard', [], ['version', 'restored_from']) as unknown as { version: number; restored_from: number } }
function adaptDashboardMutation(value: unknown): DashboardMutation { return validatedRecord(value, 'saveDashboard', ['status', 'updated_at'], ['version']) as unknown as DashboardMutation }
function adaptDashboardPreview(value: unknown): DashboardPreview { return validatedRecord(value, 'previewCommand', ['message']) as unknown as DashboardPreview }

function adminUserStatus(value: string | null): 'PENDING' | 'ACTIVE' | 'SUSPENDED' | undefined {
  return value === 'PENDING' || value === 'ACTIVE' || value === 'SUSPENDED' ? value : undefined
}

async function loadWorkspaceLayout(projectId: string, kind: 'portfolio'): Promise<WorkspaceLayout<PortfolioLayout>>
async function loadWorkspaceLayout(projectId: string, kind: 'workflow'): Promise<WorkspaceLayout<WorkflowDashboardLayout>>
async function loadWorkspaceLayout(projectId: string, kind: 'portfolio' | 'workflow') {
  const value = unwrapGenerated(await apiClient.GET('/api/projects/{project_id}/workspace-layouts/{layout_kind}', {
    params: { path: { project_id: projectId, layout_kind: kind } },
  }))
  return kind === 'portfolio' ? adaptPortfolioWorkspaceLayout(value) : adaptWorkflowWorkspaceLayout(value)
}

async function persistWorkspaceLayout(projectId: string, kind: 'portfolio', definition: PortfolioLayout): Promise<WorkspaceLayout<PortfolioLayout>>
async function persistWorkspaceLayout(projectId: string, kind: 'workflow', definition: WorkflowDashboardLayout): Promise<WorkspaceLayout<WorkflowDashboardLayout>>
async function persistWorkspaceLayout(projectId: string, kind: 'portfolio' | 'workflow', definition: PortfolioLayout | WorkflowDashboardLayout) {
  const value = unwrapGenerated(await apiClient.PUT('/api/projects/{project_id}/workspace-layouts/{layout_kind}', {
    params: { path: { project_id: projectId, layout_kind: kind } }, body: { definition },
  }))
  return kind === 'portfolio' ? adaptPortfolioWorkspaceLayout(value) : adaptWorkflowWorkspaceLayout(value)
}

async function generatedText(url: string): Promise<string> {
  const response = await apiFetch(url)
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.text()
}

async function generatedBlob(url: string, init: RequestInit): Promise<Blob> {
  const response = await apiFetch(url, init)
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.blob()
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
  authStatus: async () => adaptAuthStatus(unwrapGenerated(await apiClient.GET('/api/auth/status'))),
  login: async (username: string, password: string) => adaptLogin(unwrapGenerated(await apiClient.POST('/api/auth/login', { body: { username, password } }))),
  me: async () => adaptAuthUser(unwrapGenerated(await apiClient.GET('/api/auth/me'))),
  menuPolicy: async () => adaptMenuPolicy(unwrapGenerated(await apiClient.GET('/api/navigation/menu-policy'))),
  logout: async () => {
    const item = responseRecord(unwrapGenerated(await apiClient.POST('/api/auth/logout')), 'logout')
    requireStringField(item, 'status', 'logout')
    return item as { status: string }
  },
  health: async () => {
    const item = responseRecord(unwrapGenerated(await apiClient.GET('/api/health')), 'health')
    requireStringField(item, 'status', 'health')
    requireStringField(item, 'database_backend', 'health')
    return item as { status: string; database_backend: 'duckdb' | 'postgresql' }
  },
  portfolio: async (params: URLSearchParams) => adaptPortfolio(unwrapGenerated(await apiClient.GET('/api/portfolio/overview', { params: { query: {
    date_from: params.get('date_from') ?? undefined,
    date_to: params.get('date_to') ?? undefined,
    project_id: params.get('project_id') ?? undefined,
    analysis_type: params.get('analysis_type') ?? undefined,
    status: params.get('status') ?? undefined,
    search: params.get('search') ?? undefined,
  } } }))),
  portfolioCsvUrl: (params: URLSearchParams) => `${apiUrl('/api/portfolio/export.csv')}?${params}`,
  projects: async () => responseRecordArray(unwrapGenerated(await apiClient.GET('/api/projects')), 'projects').map(adaptProject),
  featureExamples: async () => adaptFeatureExamples(unwrapGenerated(await apiClient.GET('/api/feature-examples'))),
  importSchemas: async () => adaptImportSchemas(unwrapGenerated(await apiClient.GET('/api/import-schemas'))),
  createImportSchema: async (payload: { name: string; description: string; definition: ImportSchemaDefinition; updated_by: string }) =>
    adaptImportSchema(unwrapGenerated(await apiClient.POST('/api/import-schemas', { body: payload }))),
  updateImportSchema: async (schemaId: string, payload: { name: string; description: string; definition: ImportSchemaDefinition; updated_by: string }) =>
    adaptImportSchema(unwrapGenerated(await apiClient.PUT('/api/import-schemas/{schema_id}', { params: { path: { schema_id: schemaId } }, body: payload }))),
  deleteImportSchema: async (schemaId: string) =>
    adaptStatusId(unwrapGenerated(await apiClient.DELETE('/api/import-schemas/{schema_id}', { params: { path: { schema_id: schemaId } } }))),
  createProject: async (payload: { name: string; product_name: string; description: string; manufacturer: string; display_size_inch: number | null }) =>
    adaptProject(unwrapGenerated(await apiClient.POST('/api/projects', { body: payload }))),
  requests: async (projectId: string) => responseRecordArray(unwrapGenerated(await apiClient.GET('/api/projects/{project_id}/requests', { params: { path: { project_id: projectId } } })), 'requests').map(adaptRequest),
  assigneeCandidates: async (projectId: string, query = '') => adaptAssigneeCandidates(unwrapGenerated(await apiClient.GET('/api/projects/{project_id}/assignee-candidates', { params: { path: { project_id: projectId }, query: query.trim() ? { q: query.trim() } : {} } }))),
  createRequest: async (projectId: string, payload: { title: string; owner_user_id: string; due_in_days: number; overall_note: string; source_type: 'EXTERNAL_SYSTEM' | 'DEPARTMENT_HEAD'; source_reference: string; requested_by: string; request_type_id: string; request_type_version: number }) =>
    adaptRequest(unwrapGenerated(await apiClient.POST('/api/projects/{project_id}/requests', { params: { path: { project_id: projectId } }, body: payload }))),
  loadCases: async (requestId: string) => responseRecordArray(unwrapGenerated(await apiClient.GET('/api/requests/{request_id}/load-cases', { params: { path: { request_id: requestId } } })), 'loadCases').map(adaptLoadCase),
  dropVideos: async (loadCaseId: string, page = 1, pageSize = 20, signal?: AbortSignal) =>
    adaptDropVideoPage(unwrapGenerated(await apiClient.GET('/api/load-cases/{load_case_id}/drop-videos', { params: { path: { load_case_id: loadCaseId }, query: { page, page_size: pageSize } }, signal }))),
  createLoadCase: async (requestId: string, payload: { name: string; analysis_type: 'DROP' | 'SIDE_CLAMP'; parameters: Record<string, string | number | string[]> }) =>
    adaptLoadCase(unwrapGenerated(await apiClient.POST('/api/requests/{request_id}/load-cases', { params: { path: { request_id: requestId } }, body: payload }))),
  importResults: async (loadCaseId: string, payload: { filename: string; content: string; author: string; validate_only: boolean }) =>
    adaptImportResults(unwrapGenerated(await apiClient.POST('/api/load-cases/{load_case_id}/results/import', { params: { path: { load_case_id: loadCaseId } }, body: payload }))),
  resultImportTemplateUrl: (format: 'csv' | 'json' | 'radioss-csv') => apiUrl('/api/result-import/template/{file_format}', { file_format: format }),
  resultImportTemplate: (format: 'csv' | 'json' | 'radioss-csv') => generatedText(apiUrl('/api/result-import/template/{file_format}', { file_format: format })),
  importTypedFolderExample: async (loadCaseId: string) =>
    adaptFolderImport(unwrapGenerated(await apiClient.POST('/api/load-cases/{load_case_id}/folder-import/example', { params: { path: { load_case_id: loadCaseId } } }))),
  overview: async (loadCaseId: string, runId?: string) => adaptOverview(unwrapGenerated(await apiClient.GET('/api/load-cases/{load_case_id}/overview', { params: { path: { load_case_id: loadCaseId }, query: { run_id: runId } } }))),
  analysisRuns: async (loadCaseId: string) => responseRecordArray(unwrapGenerated(await apiClient.GET('/api/load-cases/{load_case_id}/runs', { params: { path: { load_case_id: loadCaseId } } })), 'analysisRuns').map(adaptAnalysisRun),
  runComparison: async (loadCaseId: string, baselineRunId: string, targetRunId: string, variableKey?: string) => adaptRunComparison(unwrapGenerated(await apiClient.GET('/api/load-cases/{load_case_id}/run-comparison', { params: { path: { load_case_id: loadCaseId }, query: { baseline_run_id: baselineRunId, target_run_id: targetRunId, variable_key: variableKey } } }))),
  runTrust: async (runId: string) => adaptRunTrust(unwrapGenerated(await apiClient.GET('/api/analysis-runs/{run_id}/trust', { params: { path: { run_id: runId } } }))),
  reviewItems: async (runId: string) => adaptReviewItems(unwrapGenerated(await apiClient.GET('/api/analysis-runs/{run_id}/review-items', { params: { path: { run_id: runId } } }))),
  createReviewItem: async (runId: string, payload: { title: string; body: string; variable_key: string | null; time_value: number | null; entity_type: 'NODE' | 'ELEMENT' | null; entity_id: string | null; review_status: 'OPEN' | 'IN_REVIEW' | 'RESOLVED'; created_by: string }) =>
    adaptReviewItem(unwrapGenerated(await apiClient.POST('/api/analysis-runs/{run_id}/review-items', { params: { path: { run_id: runId } }, body: payload }))),
  updateReviewItem: async (annotationId: string, reviewStatus: 'OPEN' | 'IN_REVIEW' | 'RESOLVED', body?: string) =>
    adaptReviewItem(unwrapGenerated(await apiClient.PATCH('/api/review-items/{annotation_id}', { params: { path: { annotation_id: annotationId } }, body: { review_status: reviewStatus, ...(body ? { body } : {}) } }))),
  workflows: async () => responseRecordArray(unwrapGenerated(await apiClient.GET('/api/workflows')), 'workflows').map(adaptWorkflow),
  workspaceLayout: loadWorkspaceLayout,
  saveWorkspaceLayout: persistWorkspaceLayout,
  workspaceLayoutVersions: async (projectId: string, kind: 'portfolio' | 'workflow') =>
    adaptWorkspaceLayoutVersions(unwrapGenerated(await apiClient.GET('/api/projects/{project_id}/workspace-layouts/{layout_kind}/versions', { params: { path: { project_id: projectId, layout_kind: kind } } }))),
  adminUsers: async (params = new URLSearchParams()) => adaptAdminUsers(unwrapGenerated(await apiClient.GET('/api/admin/users', { params: { query: { status: adminUserStatus(params.get('status')), search: params.get('search') ?? undefined } } }))),
  updateUserStatus: async (userId: string, payload: { account_status: 'PENDING' | 'ACTIVE' | 'SUSPENDED'; expected_updated_at: string; reason: string }) =>
    adaptMutationResult(unwrapGenerated(await apiClient.PATCH('/api/admin/users/{user_id}/status', { params: { path: { user_id: userId } }, body: payload })), 'updateUserStatus'),
  updateGlobalAdmin: async (userId: string, payload: { is_global_admin: boolean; expected_updated_at: string; reason: string }) =>
    adaptMutationResult(unwrapGenerated(await apiClient.PATCH('/api/admin/users/{user_id}/global-admin', { params: { path: { user_id: userId } }, body: payload })), 'updateGlobalAdmin'),
  projectMembers: async (projectId: string) => adaptProjectMembers(unwrapGenerated(await apiClient.GET('/api/projects/{project_id}/members', { params: { path: { project_id: projectId } } }))),
  updateProjectMember: async (projectId: string, userId: string, role: ProjectRole, expectedUpdatedAt?: string) =>
    adaptMutationResult(unwrapGenerated(await apiClient.PATCH('/api/projects/{project_id}/members/{user_id}', { params: { path: { project_id: projectId, user_id: userId } }, body: { role, expected_updated_at: expectedUpdatedAt } })), 'updateProjectMember'),
  deleteProjectMember: async (projectId: string, userId: string) => adaptMutationResult(unwrapGenerated(await apiClient.DELETE('/api/projects/{project_id}/members/{user_id}', { params: { path: { project_id: projectId, user_id: userId } } })), 'deleteProjectMember'),
  updateMenuPolicy: async (payload: { expected_version: number; change_note: string; visibility: Partial<Record<ProjectRole, Record<string, boolean>>> }) =>
    adaptMenuPolicy(unwrapGenerated(await apiClient.PUT('/api/admin/menu-policy', { body: payload }))),
  menuPolicyVersions: async () => adaptMenuPolicyVersions(unwrapGenerated(await apiClient.GET('/api/admin/menu-policy/versions'))),
  restoreMenuPolicy: async (version: number) => adaptMenuPolicy(unwrapGenerated(await apiClient.POST('/api/admin/menu-policy/versions/{version}/restore', { params: { path: { version } } }))),
  auditEvents: async (limit = 200) => adaptAuditEvents(unwrapGenerated(await apiClient.GET('/api/audit-events', { params: { query: { limit } } }))),
  qualityThresholds: async (projectId: string) => adaptQualityThresholds(unwrapGenerated(await apiClient.GET('/api/projects/{project_id}/quality-thresholds', { params: { path: { project_id: projectId } } }))),
  updateQualityThreshold: async (projectId: string, criterionKey: string, thresholdDouble: number) =>
    adaptQualityThreshold(unwrapGenerated(await apiClient.PUT('/api/projects/{project_id}/quality-thresholds/{criterion_key}', { params: { path: { project_id: projectId, criterion_key: criterionKey } }, body: { threshold_double: thresholdDouble, updated_by: '관리자' } }))),
  updateWorkflowStep: async (stepId: string, payload: { name: string; status: 'READY' | 'COMPLETED' | 'IN_PROGRESS' | 'WAITING' | 'BLOCKED' | 'FAILED'; owner_user_id: string; progress: number; is_optional: boolean; note: string }) =>
    adaptWorkflowStep(unwrapGenerated(await apiClient.PATCH('/api/workflow-steps/{step_id}', { params: { path: { step_id: stepId } }, body: payload }))),
  replaceWorkflowSteps: async (requestId: string, steps: Array<{ id: string | null; name: string; status: 'READY' | 'COMPLETED' | 'IN_PROGRESS' | 'WAITING' | 'BLOCKED' | 'FAILED'; owner_user_id: string; progress: number; is_optional: boolean; note: string }>) =>
    adaptWorkflowSteps(unwrapGenerated(await apiClient.PUT('/api/requests/{request_id}/workflow-steps', { params: { path: { request_id: requestId } }, body: { steps } }))),
  dashboard: async (id = 'dashboard-drop-default') => adaptDashboard(unwrapGenerated(await apiClient.GET('/api/dashboards/{dashboard_id}', { params: { path: { dashboard_id: id } } }))),
  dashboards: async (projectId?: string) => adaptDashboardSummaries(unwrapGenerated(await apiClient.GET('/api/dashboards', { params: { query: { project_id: projectId } } }))),
  dashboardPages: async (loadCaseId: string) => adaptDashboardPages(unwrapGenerated(await apiClient.GET('/api/dashboard-pages', { params: { query: { load_case_id: loadCaseId } } }))),
  adminDashboardPages: async (loadCaseId: string, includeArchived = true) => adaptDashboardPages(unwrapGenerated(await apiClient.GET('/api/admin/dashboard-pages', { params: { query: { load_case_id: loadCaseId, include_archived: includeArchived } } }))),
  createDashboardPage: async (payload: { load_case_id: string; name: string; description: string }) =>
    adaptDashboard(unwrapGenerated(await apiClient.POST('/api/admin/dashboard-pages', { body: payload }))),
  updateDashboardPage: async (dashboardId: string, payload: { name?: string; description?: string; status?: 'draft' | 'published' | 'archived' }) =>
    adaptDashboard(unwrapGenerated(await apiClient.PATCH('/api/admin/dashboard-pages/{dashboard_id}', { params: { path: { dashboard_id: dashboardId } }, body: payload }))),
  deleteDashboardPage: async (dashboardId: string, loadCaseId: string) =>
    adaptDeletedDashboardPage(unwrapGenerated(await apiClient.DELETE('/api/admin/dashboard-pages/{dashboard_id}', { params: { path: { dashboard_id: dashboardId }, query: { load_case_id: loadCaseId } } }))),
  reorderDashboardPages: async (loadCaseId: string, pageIds: string[]) =>
    adaptDashboardPages(unwrapGenerated(await apiClient.PUT('/api/admin/dashboard-pages/order', { body: { load_case_id: loadCaseId, page_ids: pageIds } }))),
  variables: async (loadCaseId: string) => adaptVariables(unwrapGenerated(await apiClient.GET('/api/load-cases/{load_case_id}/variables', { params: { path: { load_case_id: loadCaseId } } }))),
  createVariable: async (loadCaseId: string, payload: VariableDefinitionInput) =>
    adaptVariable(unwrapGenerated(await apiClient.POST('/api/load-cases/{load_case_id}/variables', { params: { path: { load_case_id: loadCaseId } }, body: payload }))),
  updateVariable: async (loadCaseId: string, variableKey: string, payload: Omit<VariableDefinitionInput, 'variable_key' | 'data_type'>) =>
    adaptVariable(unwrapGenerated(await apiClient.PUT('/api/load-cases/{load_case_id}/variables/{variable_key}', { params: { path: { load_case_id: loadCaseId, variable_key: variableKey } }, body: payload }))),
  deleteVariable: async (loadCaseId: string, variableKey: string) =>
    adaptDeletedVariable(unwrapGenerated(await apiClient.DELETE('/api/load-cases/{load_case_id}/variables/{variable_key}', { params: { path: { load_case_id: loadCaseId, variable_key: variableKey } } }))),
  widgetCatalog: async () => adaptWidgetCatalog(unwrapGenerated(await apiClient.GET('/api/widget-catalog'))),
  automationTemplates: async (projectId?: string) => adaptAutomationTemplates(unwrapGenerated(await apiClient.GET('/api/automation-templates', { params: { query: { project_id: projectId } } }))),
  reportTemplates: async () => adaptReportTemplates(unwrapGenerated(await apiClient.GET('/api/report-templates'))),
  uploadReportTemplate: async (name: string, file: File) => adaptReportTemplate(unwrapGenerated(await apiClient.POST('/api/report-templates', {
    body: { name, filename: file.name, content_base64: arrayBufferToBase64(await file.arrayBuffer()), updated_by: '보고서 편집자' },
  }))),
  deleteReportTemplate: async (templateId: string) => adaptStatusId(unwrapGenerated(await apiClient.DELETE('/api/report-templates/{template_id}', { params: { path: { template_id: templateId } } }))),
  renderReportTemplate: (templateId: string, replacements: Record<string, string>, filename: string) =>
    generatedBlob(apiUrl('/api/report-templates/{template_id}/render', { template_id: templateId }), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ replacements, filename }) }),
  dashboardVersions: async (dashboardId: string, includeInvalid = false) =>
    adaptDashboardVersions(unwrapGenerated(await apiClient.GET('/api/dashboards/{dashboard_id}/versions', { params: { path: { dashboard_id: dashboardId }, query: { include_invalid: includeInvalid } } }))),
  dashboardVersion: async (dashboardId: string, version: number, includeInvalid = false) =>
    adaptDashboardVersion(unwrapGenerated(await apiClient.GET('/api/dashboards/{dashboard_id}/versions/{version}', { params: { path: { dashboard_id: dashboardId, version }, query: { include_invalid: includeInvalid } } }))),
  deleteDashboardVersion: async (dashboardId: string, version: number) =>
    adaptInvalidatedDashboardVersion(unwrapGenerated(await apiClient.DELETE('/api/dashboards/{dashboard_id}/versions/{version}', { params: { path: { dashboard_id: dashboardId, version } } }))),
  cloneDashboard: async (dashboardId: string, name: string, description: string) => adaptDashboardClone(unwrapGenerated(await apiClient.POST('/api/dashboards/{dashboard_id}/clone', { params: { path: { dashboard_id: dashboardId } }, body: { name, description, created_by: '대시보드 사용자' } }))),
  restoreDashboard: async (dashboardId: string, version: number) => adaptDashboardRestore(unwrapGenerated(await apiClient.POST('/api/dashboards/{dashboard_id}/restore/{version}', { params: { path: { dashboard_id: dashboardId, version } } }))),
  saveDashboard: async (definition: DashboardDefinition) =>
    adaptDashboardMutation(unwrapGenerated(await apiClient.PUT('/api/dashboards/{dashboard_id}', { params: { path: { dashboard_id: definition.id } }, body: definition }))),
  previewCommand: async (command: string) =>
    adaptDashboardPreview(unwrapGenerated(await apiClient.POST('/api/dashboard-commands/preview', { body: { command } }))),
}
