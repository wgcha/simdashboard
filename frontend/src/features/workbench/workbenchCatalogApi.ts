import { apiClient, unwrapGenerated } from '../../shared/api/client'
import type { BatchProfile, WorkbenchRequestType, WorkbenchTaskType } from './types'
import { adaptBatchProfile, adaptRequestType, adaptTaskType } from './workbenchAdapters'

type TaskInput = Omit<WorkbenchTaskType, 'id' | 'version' | 'created_at'>
type BatchInput = Omit<BatchProfile, 'id' | 'version' | 'created_at' | 'updated_at'>
type RequestInput = Omit<WorkbenchRequestType, 'id' | 'version' | 'created_at'>

export const workbenchCatalogApi = {
  createTaskType: async (payload: TaskInput) => adaptTaskType(unwrapGenerated(await apiClient.POST('/api/admin/workbench/task-types', { body: payload as never }))),
  updateTaskType: async (id: string, payload: TaskInput) => adaptTaskType(unwrapGenerated(await apiClient.PUT('/api/admin/workbench/task-types/{task_type_id}', { params: { path: { task_type_id: id } }, body: payload as never }))),
  deactivateTaskType: async (id: string) => { unwrapGenerated(await apiClient.DELETE('/api/admin/workbench/task-types/{task_type_id}', { params: { path: { task_type_id: id } } })) },
  createBatchProfile: async (payload: BatchInput) => adaptBatchProfile(unwrapGenerated(await apiClient.POST('/api/admin/workbench/batch-profiles', { body: payload as never }))),
  saveBatchProfile: async (id: string, payload: BatchInput) => adaptBatchProfile(unwrapGenerated(await apiClient.PUT('/api/admin/workbench/batch-profiles/{profile_id}', { params: { path: { profile_id: id } }, body: payload as never }))),
  deactivateBatchProfile: async (id: string) => { unwrapGenerated(await apiClient.DELETE('/api/admin/workbench/batch-profiles/{profile_id}', { params: { path: { profile_id: id } } })) },
  createRequestType: async (payload: RequestInput) => adaptRequestType(unwrapGenerated(await apiClient.POST('/api/admin/workbench/request-types', { body: payload as never }))),
  updateRequestType: async (id: string, payload: RequestInput) => adaptRequestType(unwrapGenerated(await apiClient.PUT('/api/admin/workbench/request-types/{request_type_id}', { params: { path: { request_type_id: id } }, body: payload as never }))),
  deactivateRequestType: async (id: string) => { unwrapGenerated(await apiClient.DELETE('/api/admin/workbench/request-types/{request_type_id}', { params: { path: { request_type_id: id } } })) },
}
