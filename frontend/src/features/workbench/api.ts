import { apiFetch } from '../../shared/api/auth'
import { apiClient, unwrapGenerated } from '../../shared/api/client'
import { apiErrorFromResponse } from '../../shared/api/errors'
import { resultLayoutApi } from '../../shared/api/resultLayouts'
import type { CreateDemoRunInput } from './types'
import { adaptBatchAttempts, adaptBatchProfiles, adaptDemoRun, adaptDemoRuns, adaptRequestTypeResolution, adaptRequestTypes, adaptTaskTypes, adaptWorkItem } from './workbenchAdapters'
import { workbenchCatalogApi } from './workbenchCatalogApi'

async function readText(response: Response | Promise<Response>) { const value = await response; if (!value.ok) throw await apiErrorFromResponse(value); return value.text() }
export const workbenchApi = {
  taskTypes: async (allVersions = false) => adaptTaskTypes(unwrapGenerated(await apiClient.GET('/api/workbench/task-types', { params: { query: { all_versions: allVersions } } }))),
  requestTypes: async () => adaptRequestTypes(unwrapGenerated(await apiClient.GET('/api/workbench/request-types'))),
  requestTypeResolution: async (requestId: string) => adaptRequestTypeResolution(unwrapGenerated(await apiClient.GET('/api/workbench/requests/{request_id}/request-type', { params: { path: { request_id: requestId } } }))),
  assignRequestType: async (requestId: string, requestTypeId: string, requestTypeVersion: number) => adaptRequestTypeResolution(unwrapGenerated(await apiClient.PUT('/api/workbench/requests/{request_id}/request-type', { params: { path: { request_id: requestId } }, body: { request_type_id: requestTypeId, request_type_version: requestTypeVersion } }))),
  demoRuns: async (requestId?: string) => adaptDemoRuns(unwrapGenerated(await apiClient.GET('/api/workbench/demo-runs', { params: { query: { request_id: requestId } } }))),
  demoRun: async (runId: string) => adaptDemoRun(unwrapGenerated(await apiClient.GET('/api/workbench/demo-runs/{run_id}', { params: { path: { run_id: runId } } }))),
  demoTextArtifact: (url: string) => readText(apiFetch(url)),
  createDemoRun: async (payload: CreateDemoRunInput) => adaptDemoRun(unwrapGenerated(await apiClient.POST('/api/workbench/demo-runs', { body: payload }))),
  startWorkItem: async (itemId: string, startedBy: string) => adaptWorkItem(unwrapGenerated(await apiClient.POST('/api/workbench/work-items/{item_id}/start', { params: { path: { item_id: itemId } }, body: { started_by: startedBy } }))),
  completeWorkItem: async (itemId: string, completedBy: string, demoRunId?: string) => adaptWorkItem(unwrapGenerated(await apiClient.POST('/api/workbench/work-items/{item_id}/complete', { params: { path: { item_id: itemId } }, body: { completed_by: completedBy, ...(demoRunId ? { demo_run_id: demoRunId } : {}) } }))),
  updateWorkItemProgress: async (itemId: string, progress: number, updatedBy: string) => adaptWorkItem(unwrapGenerated(await apiClient.PATCH('/api/workbench/work-items/{item_id}/progress', { params: { path: { item_id: itemId } }, body: { progress, updated_by: updatedBy } }))),
  batchProfiles: async (includeInactive = false) => adaptBatchProfiles(unwrapGenerated(await apiClient.GET('/api/workbench/batch-profiles', { params: { query: { include_inactive: includeInactive } } }))),
  batchAttempts: async (itemId: string) => adaptBatchAttempts(unwrapGenerated(await apiClient.GET('/api/workbench/work-items/{item_id}/batch-attempts', { params: { path: { item_id: itemId } } }))),
  dispatchBatch: async (itemId: string, createdBy: string, idempotencyKey: string) => adaptDemoRun(unwrapGenerated(await apiClient.POST('/api/workbench/work-items/{item_id}/batch-dispatch', { params: { path: { item_id: itemId } }, body: { idempotency_key: idempotencyKey, created_by: createdBy } as never }))),
  ...workbenchCatalogApi,
  ...resultLayoutApi,
}
