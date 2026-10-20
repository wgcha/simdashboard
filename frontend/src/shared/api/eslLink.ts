import { apiClient, unwrapGenerated } from './client'
import { normalizeEslLink, type EslLink } from './eslLinkModel'

export const eslLinkApi = {
  /** Sidebar link flag; off unless the server sets SIMDASH_ESL_ENABLED. */
  get: async (signal?: AbortSignal): Promise<EslLink> => normalizeEslLink(unwrapGenerated(await apiClient.GET('/api/integrations/esl', { signal }))),
}
