import { apiFetch } from '../../shared/api/auth'
import { requireStringField, responseRecord, responseRecordArray } from '../../shared/api/adapters'
import { apiClient, unwrapGenerated } from '../../shared/api/client'
import { apiErrorFromResponse } from '../../shared/api/errors'
import type { Workflow } from '../../types'
import type { BatchExecutionAttempt, BatchProfile, CreateDemoRunInput, DemoRun, RequestTypeResolution, WorkbenchRequestType, WorkbenchTaskType } from './types'

function validatedWorkbenchRecord(value: unknown, label: string, stringFields: readonly string[] = []): Record<string, unknown> {
  const item = responseRecord(value, label)
  for (const field of stringFields) requireStringField(item, field, label)
  // Workbench response models are still structurally untyped in OpenAPI. This
  // is the explicit feature-boundary adapter until those server schemas exist.
  return item
}

function validatedWorkbenchArray(value: unknown, label: string, stringFields: readonly string[] = []): Record<string, unknown>[] {
  return responseRecordArray(value, label).map((item) => validatedWorkbenchRecord(item, label, stringFields))
}

function adaptTaskType(value: unknown): WorkbenchTaskType { return validatedWorkbenchRecord(value, 'taskType', ['id', 'display_name']) as unknown as WorkbenchTaskType }
function adaptTaskTypes(value: unknown): WorkbenchTaskType[] { return responseRecordArray(value, 'taskTypes').map(adaptTaskType) }
function adaptRequestType(value: unknown): WorkbenchRequestType { return validatedWorkbenchRecord(value, 'requestType', ['id', 'display_name']) as unknown as WorkbenchRequestType }
function adaptRequestTypes(value: unknown): WorkbenchRequestType[] { return responseRecordArray(value, 'requestTypes').map(adaptRequestType) }
function adaptRequestTypeResolution(value: unknown): RequestTypeResolution { return validatedWorkbenchRecord(value, 'requestTypeResolution', ['request_id', 'resolution']) as unknown as RequestTypeResolution }
function adaptDemoRun(value: unknown): DemoRun { return validatedWorkbenchRecord(value, 'demoRun', ['id', 'name']) as unknown as DemoRun }
function adaptDemoRuns(value: unknown): DemoRun[] { return responseRecordArray(value, 'demoRuns').map(adaptDemoRun) }
function adaptWorkItem(value: unknown): Omit<Workflow, 'request'> { return validatedWorkbenchRecord(value, 'workItem') as unknown as Omit<Workflow, 'request'> }
function adaptBatchProfile(value: unknown): BatchProfile {
  const item = validatedWorkbenchRecord(value, 'batchProfile', ['id', 'name']) as unknown as BatchProfile
  const taskTypeIds = Array.isArray(item.task_type_ids) ? item.task_type_ids.filter((id): id is string => typeof id === 'string' && Boolean(id)) : []
  // A legacy many-to-many row is not auto-assigned to the first task: it must
  // be remediated by an administrator before it can participate in dispatch.
  const taskTypeId = typeof item.task_type_id === 'string' && item.task_type_id
    ? item.task_type_id
    : taskTypeIds.length === 1 ? taskTypeIds[0] : undefined
  return { ...item, task_type_id: taskTypeId, task_type_ids: taskTypeId ? [taskTypeId] : taskTypeIds }
}
function adaptBatchProfiles(value: unknown): BatchProfile[] { return responseRecordArray(value, 'batchProfiles').map(adaptBatchProfile) }
function adaptBatchAttempt(value: unknown): BatchExecutionAttempt { return validatedWorkbenchRecord(value, 'batchAttempt', ['id', 'work_item_id']) as unknown as BatchExecutionAttempt }
function adaptBatchAttempts(value: unknown): BatchExecutionAttempt[] { return responseRecordArray(value, 'batchAttempts').map(adaptBatchAttempt) }

async function readText(response: Response | Promise<Response>): Promise<string> {
  response = await response
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.text()
}

async function jsonRequest<T>(url: string, method: 'POST' | 'PUT', body: unknown): Promise<T> {
  const response = await apiFetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.json() as Promise<T>
}

export const workbenchApi = {
  taskTypes: async (allVersions = false) => adaptTaskTypes(unwrapGenerated(await apiClient.GET('/api/workbench/task-types', { params: { query: { all_versions: allVersions } } }))),
  requestTypes: async () => adaptRequestTypes(unwrapGenerated(await apiClient.GET('/api/workbench/request-types'))),
  requestTypeResolution: async (requestId: string) => adaptRequestTypeResolution(unwrapGenerated(await apiClient.GET('/api/workbench/requests/{request_id}/request-type', { params: { path: { request_id: requestId } } }))),
  assignRequestType: async (requestId: string, requestTypeId: string, requestTypeVersion: number) => adaptRequestTypeResolution(unwrapGenerated(await apiClient.PUT('/api/workbench/requests/{request_id}/request-type', {
    params: { path: { request_id: requestId } }, body: { request_type_id: requestTypeId, request_type_version: requestTypeVersion },
  }))),
  demoRuns: async (requestId?: string) => adaptDemoRuns(unwrapGenerated(await apiClient.GET('/api/workbench/demo-runs', { params: { query: { request_id: requestId } } }))),
  demoRun: async (runId: string) => adaptDemoRun(unwrapGenerated(await apiClient.GET('/api/workbench/demo-runs/{run_id}', { params: { path: { run_id: runId } } }))),
  demoTextArtifact: (url: string) => readText(apiFetch(url)),
  createDemoRun: async (payload: CreateDemoRunInput) => adaptDemoRun(unwrapGenerated(await apiClient.POST('/api/workbench/demo-runs', { body: payload }))),
  startWorkItem: async (itemId: string, startedBy: string) => adaptWorkItem(unwrapGenerated(await apiClient.POST('/api/workbench/work-items/{item_id}/start', {
    params: { path: { item_id: itemId } }, body: { started_by: startedBy },
  }))),
  completeWorkItem: async (itemId: string, completedBy: string, demoRunId?: string) => adaptWorkItem(unwrapGenerated(await apiClient.POST('/api/workbench/work-items/{item_id}/complete', {
    params: { path: { item_id: itemId } }, body: { completed_by: completedBy, ...(demoRunId ? { demo_run_id: demoRunId } : {}) },
  }))),
  updateWorkItemProgress: async (itemId: string, progress: number, updatedBy: string) => adaptWorkItem(unwrapGenerated(await apiClient.PATCH('/api/workbench/work-items/{item_id}/progress', {
    params: { path: { item_id: itemId } }, body: { progress, updated_by: updatedBy },
  }))),
  createTaskType: async (payload: Omit<WorkbenchTaskType, 'id' | 'version' | 'created_at'>) => adaptTaskType(await jsonRequest('/api/admin/workbench/task-types', 'POST', payload)),
  updateTaskType: async (taskTypeId: string, payload: Omit<WorkbenchTaskType, 'id' | 'version' | 'created_at'>) => adaptTaskType(await jsonRequest(`/api/admin/workbench/task-types/${encodeURIComponent(taskTypeId)}`, 'PUT', payload)),
  deactivateTaskType: async (taskTypeId: string) => {
    const response = await apiFetch(`/api/admin/workbench/task-types/${encodeURIComponent(taskTypeId)}`, { method: 'DELETE' })
    if (!response.ok) throw await apiErrorFromResponse(response)
  },
  batchProfiles: async (includeInactive = false) => adaptBatchProfiles(unwrapGenerated(await apiClient.GET('/api/workbench/batch-profiles', { params: { query: { include_inactive: includeInactive } } }))),
  createBatchProfile: async (profile: Omit<BatchProfile, 'id' | 'version' | 'created_at' | 'updated_at'>) => adaptBatchProfile(await jsonRequest('/api/admin/workbench/batch-profiles', 'POST', profile)),
  batchAttempts: async (itemId: string) => adaptBatchAttempts(unwrapGenerated(await apiClient.GET('/api/workbench/work-items/{item_id}/batch-attempts', { params: { path: { item_id: itemId } } }))),
  saveBatchProfile: async (profileId: string, profile: Omit<BatchProfile, 'id' | 'version' | 'created_at' | 'updated_at'>) => adaptBatchProfile(await jsonRequest(`/api/admin/workbench/batch-profiles/${encodeURIComponent(profileId)}`, 'PUT', profile)),
  deactivateBatchProfile: async (profileId: string) => {
    const response = await apiFetch(`/api/admin/workbench/batch-profiles/${encodeURIComponent(profileId)}`, { method: 'DELETE' })
    if (!response.ok) throw await apiErrorFromResponse(response)
  },
  dispatchBatch: async (itemId: string, createdBy: string, idempotencyKey: string) => adaptDemoRun(unwrapGenerated(await apiClient.POST('/api/workbench/work-items/{item_id}/batch-dispatch', {
    params: { path: { item_id: itemId } }, body: { idempotency_key: idempotencyKey, created_by: createdBy } as never,
  }))),
  createRequestType: async (payload: Omit<WorkbenchRequestType, 'id' | 'version' | 'created_at'>) => adaptRequestType(unwrapGenerated(await apiClient.POST('/api/admin/workbench/request-types', { body: payload as never }))),
  updateRequestType: async (requestTypeId: string, payload: Omit<WorkbenchRequestType, 'id' | 'version' | 'created_at'>) => adaptRequestType(await jsonRequest(`/api/admin/workbench/request-types/${encodeURIComponent(requestTypeId)}`, 'PUT', payload)),
  deactivateRequestType: async (requestTypeId: string) => { unwrapGenerated(await apiClient.DELETE('/api/admin/workbench/request-types/{request_type_id}', { params: { path: { request_type_id: requestTypeId } } })) },
}
