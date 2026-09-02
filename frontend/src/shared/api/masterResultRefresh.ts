import { apiClient, unwrapGenerated } from './client'
import type { components } from './generated/openapi'

export type MasterResultRefreshItem = components['schemas']['MasterResultRefreshItem']
export type MasterResultRefreshResponse = components['schemas']['MasterResultRefreshResponse']

export async function refreshMasterResultFolder(): Promise<MasterResultRefreshResponse> {
  return unwrapGenerated(await apiClient.POST('/api/result-imports/refresh')) as MasterResultRefreshResponse
}
