import { apiClient, unwrapGenerated } from './client'
import type { components } from './generated/openapi'

export type ImpactResponse = components['schemas']['BundleImpactResponse']
export type ImpactCheck = components['schemas']['ImpactPairCheck']

export const semanticImpactApi = {
  previewBundle: async (payload: components['schemas']['BundleActivation'], signal?: AbortSignal) => unwrapGenerated(await apiClient.POST('/api/semantic-mapping/impact-bundle', { body: payload, signal })) as ImpactResponse,
}
