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
function adaptBatchProfile(value: unknown): BatchProfile { return validatedWorkbenchRecord(value, 'batchProfile', ['id', 'name']) as unknown as BatchProfile }
function adaptBatchProfiles(value: unknown): BatchProfile[] { return responseRecordArray(value, 'batchProfiles').map(adaptBatchProfile) }
function adaptBatchAttempt(value: unknown): BatchExecutionAttempt { return validatedWorkbenchRecord(value, 'batchAttempt', ['id', 'work_item_id']) as unknown as BatchExecutionAttempt }
function adaptBatchAttempts(value: unknown): BatchExecutionAttempt[] { return responseRecordArray(value, 'batchAttempts').map(adaptBatchAttempt) }

async function readText(response: Response | Promise<Response>): Promise<string> {
  response = await response
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.text()
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
  batchProfiles: async (includeInactive = false) => adaptBatchProfiles(unwrapGenerated(await apiClient.GET('/api/workbench/batch-profiles', { params: { query: { include_inactive: includeInactive } } }))),
  batchAttempts: async (itemId: string) => adaptBatchAttempts(unwrapGenerated(await apiClient.GET('/api/workbench/work-items/{item_id}/batch-attempts', { params: { path: { item_id: itemId } } }))),
  saveBatchProfile: async (profile: Omit<BatchProfile, 'version' | 'created_at' | 'updated_at'>) => adaptBatchProfile(unwrapGenerated(await apiClient.PUT('/api/admin/workbench/batch-profiles/{profile_id}', {
    params: { path: { profile_id: profile.id } }, body: profile,
  }))),
  dispatchBatch: async (itemId: string, batchProfileId: string, createdBy: string, idempotencyKey: string) => adaptDemoRun(unwrapGenerated(await apiClient.POST('/api/workbench/work-items/{item_id}/batch-dispatch', {
    params: { path: { item_id: itemId } }, body: { batch_profile_id: batchProfileId, idempotency_key: idempotencyKey, created_by: createdBy },
  }))),
  createRequestType: async (payload: Omit<WorkbenchRequestType, 'version' | 'created_at'>) => adaptRequestType(unwrapGenerated(await apiClient.POST('/api/admin/workbench/request-types', {
    body: payload,
  }))),
}
