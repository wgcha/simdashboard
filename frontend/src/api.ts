import type { AnalysisRequest, AutomationTemplate, DashboardDefinition, DashboardSummary, DashboardVersion, LoadCase, Overview, PortfolioOverview, Project, QualityThreshold, VariableDefinition, VariableDefinitionInput, WidgetCatalogItem, Workflow } from './types'

async function json<T>(response: Response | Promise<Response>): Promise<T> {
  response = await response
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `요청 실패 (${response.status})`)
  }
  return response.json() as Promise<T>
}

export const api = {
  portfolio: (params: URLSearchParams) => json<PortfolioOverview>(fetch(`/api/portfolio/overview?${params}`)),
  portfolioCsvUrl: (params: URLSearchParams) => `/api/portfolio/export.csv?${params}`,
  projects: () => json<Project[]>(fetch('/api/projects')),
  createProject: (payload: { name: string; product_name: string; description: string; manufacturer: string; display_size_inch: number | null }) =>
    json<Project>(fetch('/api/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  requests: (projectId: string) => json<AnalysisRequest[]>(fetch(`/api/projects/${projectId}/requests`)),
  createRequest: (projectId: string, payload: { title: string; owner: string; due_in_days: number; overall_note: string }) =>
    json<AnalysisRequest>(fetch(`/api/projects/${projectId}/requests`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  loadCases: (requestId: string) => json<LoadCase[]>(fetch(`/api/requests/${requestId}/load-cases`)),
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
  overview: (loadCaseId: string) => json<Overview>(fetch(`/api/load-cases/${loadCaseId}/overview`)),
  workflows: () => json<Workflow[]>(fetch('/api/workflows')),
  qualityThresholds: (projectId: string) => json<QualityThreshold[]>(fetch(`/api/projects/${projectId}/quality-thresholds`)),
  updateQualityThreshold: (criterionKey: string, thresholdDouble: number) =>
    json<QualityThreshold>(
      fetch(`/api/quality-thresholds/${criterionKey}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ threshold_double: thresholdDouble, updated_by: '관리자' }),
      }),
    ),
  renameWorkflowStep: (stepId: string, name: string) =>
    json<{ id: string; name: string; status: string }>(
      fetch(`/api/workflow-steps/${stepId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      }),
    ),
  dashboard: (id = 'dashboard-drop-default') => json<DashboardDefinition>(fetch(`/api/dashboards/${id}`)),
  dashboards: (projectId?: string) => json<DashboardSummary[]>(fetch(`/api/dashboards${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`)),
  variables: (loadCaseId: string) => json<VariableDefinition[]>(fetch(`/api/load-cases/${loadCaseId}/variables`)),
  createVariable: (loadCaseId: string, payload: VariableDefinitionInput) =>
    json<VariableDefinition>(fetch(`/api/load-cases/${loadCaseId}/variables`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  updateVariable: (loadCaseId: string, variableKey: string, payload: Omit<VariableDefinitionInput, 'variable_key' | 'data_type'>) =>
    json<VariableDefinition>(fetch(`/api/load-cases/${loadCaseId}/variables/${encodeURIComponent(variableKey)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  deleteVariable: (loadCaseId: string, variableKey: string) =>
    json<{ status: string; variable_key: string }>(fetch(`/api/load-cases/${loadCaseId}/variables/${encodeURIComponent(variableKey)}`, { method: 'DELETE' })),
  widgetCatalog: () => json<WidgetCatalogItem[]>(fetch('/api/widget-catalog')),
  automationTemplates: (projectId?: string) => json<AutomationTemplate[]>(fetch(`/api/automation-templates${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`)),
  dashboardVersions: (dashboardId: string) => json<DashboardVersion[]>(fetch(`/api/dashboards/${dashboardId}/versions`)),
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
