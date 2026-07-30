import { authenticatedFetch } from '../../auth'
import type { Workflow } from '../../types'
import type { CreateDemoRunInput, DemoRun, RequestTypeResolution, WorkbenchRequestType, WorkbenchTaskType } from './types'

async function readJson<T>(response: Response | Promise<Response>): Promise<T> {
  response = await response
  if (!response.ok) {
    const body = await response.text()
    try {
      const parsed = JSON.parse(body) as { detail?: string | { code?: string; [key: string]: unknown } }
      const detail = parsed.detail
      throw new Error(typeof detail === 'string' ? detail : detail?.code ? `${detail.code}: ${JSON.stringify(detail)}` : body)
    } catch (reason) {
      if (reason instanceof Error && reason.message !== body) throw reason
      throw new Error(body || `요청 실패 (${response.status})`)
    }
  }
  return response.json() as Promise<T>
}

async function readText(response: Response | Promise<Response>): Promise<string> {
  response = await response
  if (!response.ok) throw new Error(`텍스트 데모 파일을 읽지 못했습니다. (${response.status})`)
  return response.text()
}

export const workbenchApi = {
  taskTypes: (allVersions = false) => readJson<WorkbenchTaskType[]>(authenticatedFetch(`/api/workbench/task-types${allVersions ? '?all_versions=true' : ''}`)),
  requestTypes: () => readJson<WorkbenchRequestType[]>(authenticatedFetch('/api/workbench/request-types')),
  requestTypeResolution: (requestId: string) => readJson<RequestTypeResolution>(authenticatedFetch(`/api/workbench/requests/${encodeURIComponent(requestId)}/request-type`)),
  assignRequestType: (requestId: string, requestTypeId: string, requestTypeVersion: number) => readJson<RequestTypeResolution>(authenticatedFetch(`/api/workbench/requests/${encodeURIComponent(requestId)}/request-type`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_type_id: requestTypeId, request_type_version: requestTypeVersion }),
  })),
  demoRuns: (requestId?: string) => readJson<DemoRun[]>(authenticatedFetch(`/api/workbench/demo-runs${requestId ? `?request_id=${encodeURIComponent(requestId)}` : ''}`)),
  demoRun: (runId: string) => readJson<DemoRun>(authenticatedFetch(`/api/workbench/demo-runs/${encodeURIComponent(runId)}`)),
  demoTextArtifact: (url: string) => readText(authenticatedFetch(url)),
  createDemoRun: (payload: CreateDemoRunInput) => readJson<DemoRun>(authenticatedFetch('/api/workbench/demo-runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })),
  startWorkItem: (itemId: string, startedBy: string) => readJson<Omit<Workflow, 'request'>>(authenticatedFetch(`/api/workbench/work-items/${encodeURIComponent(itemId)}/start`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ started_by: startedBy }),
  })),
  completeWorkItem: (itemId: string, completedBy: string, demoRunId?: string) => readJson<Omit<Workflow, 'request'>>(authenticatedFetch(`/api/workbench/work-items/${encodeURIComponent(itemId)}/complete`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ completed_by: completedBy, ...(demoRunId ? { demo_run_id: demoRunId } : {}) }),
  })),
  createRequestType: (payload: Omit<WorkbenchRequestType, 'version' | 'created_at'>) => readJson<WorkbenchRequestType>(authenticatedFetch('/api/admin/workbench/request-types', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })),
}
