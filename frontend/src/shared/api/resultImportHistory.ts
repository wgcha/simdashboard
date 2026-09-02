import { apiClient, unwrapGenerated } from './client'
import type { components } from './generated/openapi'

export type ResultImportStatus = components['schemas']['ResultImportHistoryItem']['status']
export type ResultImportHistoryItem = components['schemas']['ResultImportHistoryItem']
export type ResultImportHistoryResponse = components['schemas']['ResultImportHistoryResponse']
export type ResultImportRetryResponse = components['schemas']['MasterResultRefreshItem']

export async function fetchResultImportHistory(
  loadCaseId: string,
  options: { status?: ResultImportStatus; limit?: number; offset?: number; signal?: AbortSignal } = {},
): Promise<ResultImportHistoryResponse> {
  return unwrapGenerated(await apiClient.GET('/api/load-cases/{load_case_id}/result-imports', {
    params: {
      path: { load_case_id: loadCaseId },
      query: { status: options.status, limit: options.limit ?? 25, offset: options.offset ?? 0 },
    },
    signal: options.signal,
  })) as ResultImportHistoryResponse
}

export async function retryResultImport(jobId: string, signal?: AbortSignal): Promise<ResultImportRetryResponse> {
  return unwrapGenerated(await apiClient.POST('/api/result-imports/{job_id}/retry', {
    params: { path: { job_id: jobId } },
    signal,
  })) as ResultImportRetryResponse
}
