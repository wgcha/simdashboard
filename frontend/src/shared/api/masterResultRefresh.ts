import { apiClient, unwrapGenerated } from './client'

export type MasterResultRefreshItem = {
  manifest_path: string
  status: 'IMPORTED' | 'SKIPPED' | 'FAILED'
  load_case_id: string | null
  analysis_run_id: string | null
  message: string | null
}

export type MasterResultRefreshResponse = {
  scanned_count: number
  imported_count: number
  skipped_count: number
  failed_count: number
  items: MasterResultRefreshItem[]
}

export async function refreshMasterResultFolder(): Promise<MasterResultRefreshResponse> {
  return unwrapGenerated(await apiClient.POST('/api/result-imports/refresh')) as MasterResultRefreshResponse
}
