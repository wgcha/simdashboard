import { requireStringField, responseRecord, responseRecordArray } from '../../shared/api/adapters'
import type { Workflow } from '../../types'
import type { BatchExecutionAttempt, BatchProfile, DemoRun, RequestTypeResolution, WorkbenchRequestType, WorkbenchTaskType } from './types'

function record(value: unknown, label: string, fields: readonly string[] = []): Record<string, unknown> {
  const item = responseRecord(value, label)
  for (const field of fields) requireStringField(item, field, label)
  return item
}
export const adaptTaskType = (value: unknown) => record(value, 'taskType', ['id', 'display_name']) as unknown as WorkbenchTaskType
export const adaptTaskTypes = (value: unknown) => responseRecordArray(value, 'taskTypes').map(adaptTaskType)
export const adaptRequestType = (value: unknown) => record(value, 'requestType', ['id', 'display_name']) as unknown as WorkbenchRequestType
export const adaptRequestTypes = (value: unknown) => responseRecordArray(value, 'requestTypes').map(adaptRequestType)
export const adaptRequestTypeResolution = (value: unknown) => record(value, 'requestTypeResolution', ['request_id', 'resolution']) as unknown as RequestTypeResolution
export const adaptDemoRun = (value: unknown) => record(value, 'demoRun', ['id', 'name']) as unknown as DemoRun
export const adaptDemoRuns = (value: unknown) => responseRecordArray(value, 'demoRuns').map(adaptDemoRun)
export const adaptWorkItem = (value: unknown) => record(value, 'workItem') as unknown as Omit<Workflow, 'request'>
export function adaptBatchProfile(value: unknown): BatchProfile {
  const item = record(value, 'batchProfile', ['id', 'name']) as unknown as BatchProfile
  const ids = Array.isArray(item.task_type_ids) ? item.task_type_ids.filter((id): id is string => typeof id === 'string' && Boolean(id)) : []
  const taskTypeId = typeof item.task_type_id === 'string' && item.task_type_id ? item.task_type_id : ids.length === 1 ? ids[0] : undefined
  return { ...item, task_type_id: taskTypeId, task_type_ids: taskTypeId ? [taskTypeId] : ids }
}
export const adaptBatchProfiles = (value: unknown) => responseRecordArray(value, 'batchProfiles').map(adaptBatchProfile)
export const adaptBatchAttempt = (value: unknown) => record(value, 'batchAttempt', ['id', 'work_item_id']) as unknown as BatchExecutionAttempt
export const adaptBatchAttempts = (value: unknown) => responseRecordArray(value, 'batchAttempts').map(adaptBatchAttempt)
