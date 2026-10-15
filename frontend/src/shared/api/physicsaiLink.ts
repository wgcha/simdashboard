import { apiClient, unwrapGenerated } from './client'
import { normalizePhysicsAiLink, type PhysicsAiLink } from './physicsaiLinkModel'

export const physicsAiLinkApi = {
  /** Sidebar link flag; off unless the server sets SIMDASH_PHYSICSAI_ENABLED. */
  get: async (signal?: AbortSignal): Promise<PhysicsAiLink> => normalizePhysicsAiLink(unwrapGenerated(await apiClient.GET('/api/integrations/physicsai', { signal }))),
}
