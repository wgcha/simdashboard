// Pure helpers for the ESL (등가정적하중) sidebar link, mirroring physicsaiLinkModel.ts.
// Kept import-free for the node self-test. ESL is a separate app served on the same
// origin under the root path `/esl/` (API `/esl/api`, not under VITE_APP_BASE_PATH).
export type EslLink = { enabled: boolean; basePath: string }

export const ESL_BASE_PATH = '/esl/'
export const ESL_LINK_OFF: EslLink = { enabled: false, basePath: ESL_BASE_PATH }

const CONTROL = /[\u0000-\u001f]/

/** Same-origin path only; anything else falls back to `/esl/`. */
function safeBasePath(value: unknown): string {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || value.includes('\\') || value.includes('..') || CONTROL.test(value)) return ESL_BASE_PATH
  return value.endsWith('/') ? value : `${value}/`
}

/** Defensive parse of GET /api/integrations/esl; unknown shapes mean off. */
export function normalizeEslLink(value: unknown): EslLink {
  if (!value || typeof value !== 'object') return ESL_LINK_OFF
  const raw = value as { enabled?: unknown; base_path?: unknown }
  if (raw.enabled !== true) return ESL_LINK_OFF
  return { enabled: true, basePath: safeBasePath(raw.base_path) }
}

/** Sidebar link target, or null when the link must be hidden (signed out or flag off). */
export function eslNavHref(link: EslLink, signedIn: boolean): string | null {
  return signedIn && link.enabled ? link.basePath : null
}
