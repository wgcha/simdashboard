// Pure helpers for the PhysicsAI "AI 예측" sidebar link (docs/windows-lan-access.md).
// Kept import-free for the node self-test. PhysicsAI is a separate app served on the
// same origin under the root path `/physicsai/` (not under VITE_APP_BASE_PATH).
export type PhysicsAiLink = { enabled: boolean; basePath: string }

export const PHYSICSAI_BASE_PATH = '/physicsai/'
export const PHYSICSAI_LINK_OFF: PhysicsAiLink = { enabled: false, basePath: PHYSICSAI_BASE_PATH }

const CONTROL = /[\u0000-\u001f]/

/** Same-origin path only; anything else falls back to `/physicsai/`. */
function safeBasePath(value: unknown): string {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || value.includes('\\') || value.includes('..') || CONTROL.test(value)) return PHYSICSAI_BASE_PATH
  return value.endsWith('/') ? value : `${value}/`
}

/** Defensive parse of GET /api/integrations/physicsai; unknown shapes mean off. */
export function normalizePhysicsAiLink(value: unknown): PhysicsAiLink {
  if (!value || typeof value !== 'object') return PHYSICSAI_LINK_OFF
  const raw = value as { enabled?: unknown; base_path?: unknown }
  if (raw.enabled !== true) return PHYSICSAI_LINK_OFF
  return { enabled: true, basePath: safeBasePath(raw.base_path) }
}

/** Sidebar link target, or null when the link must be hidden (signed out or flag off). */
export function physicsAiNavHref(link: PhysicsAiLink, signedIn: boolean): string | null {
  return signedIn && link.enabled ? link.basePath : null
}
