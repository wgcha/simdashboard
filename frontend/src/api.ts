import type { AnalysisRequest, DashboardDefinition, LoadCase, Overview, Project, QualityThreshold, Workflow } from './types'

async function json<T>(response: Response | Promise<Response>): Promise<T> {
  response = await response
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `요청 실패 (${response.status})`)
  }
  return response.json() as Promise<T>
}

export const api = {
  projects: () => json<Project[]>(fetch('/api/projects')),
  createProject: (payload: { name: string; product_name: string; description: string }) =>
    json<Project>(fetch('/api/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  requests: (projectId: string) => json<AnalysisRequest[]>(fetch(`/api/projects/${projectId}/requests`)),
  createRequest: (projectId: string, payload: { title: string; owner: string; due_in_days: number; overall_note: string }) =>
    json<AnalysisRequest>(fetch(`/api/projects/${projectId}/requests`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  loadCases: (requestId: string) => json<LoadCase[]>(fetch(`/api/requests/${requestId}/load-cases`)),
  createLoadCase: (requestId: string, payload: { name: string; analysis_type: 'DROP' | 'SIDE_CLAMP'; parameters: Record<string, string | number | string[]> }) =>
    json<LoadCase>(fetch(`/api/requests/${requestId}/load-cases`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
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
  dashboard: () => json<DashboardDefinition>(fetch('/api/dashboards/dashboard-drop-default')),
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
      proposal: null | { action: 'add_widget'; widget: DashboardDefinition['widgets'][number] }
    }>(
      fetch('/api/dashboard-commands/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command }),
      }),
    ),
}
