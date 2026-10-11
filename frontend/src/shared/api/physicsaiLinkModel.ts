// Pure helpers for the PhysicsAI link (docs/physicsai-integration.md).
// Kept import-free for the node self-test. The dashboard only hands an SPDM
// path to PhysicsAI by link; it never moves or writes files.
export type PhysicsAiLink = { enabled: boolean; basePath: string; spdmRoot: string | null }

export const PHYSICSAI_BASE_PATH = '/physicsai/'
export const PHYSICSAI_LINK_OFF: PhysicsAiLink = { enabled: false, basePath: PHYSICSAI_BASE_PATH, spdmRoot: null }

const CONTROL = /[\u0000-\u001f]/

/** Same-origin path only; anything else falls back to `/physicsai/`. */
function safeBasePath(value: unknown): string {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || value.includes('\\') || value.includes('..') || CONTROL.test(value)) return PHYSICSAI_BASE_PATH
  return value.endsWith('/') ? value : `${value}/`
}

/** Defensive parse of GET /api/integrations/physicsai; unknown shapes mean off. */
export function normalizePhysicsAiLink(value: unknown): PhysicsAiLink {
  if (!value || typeof value !== 'object') return PHYSICSAI_LINK_OFF
  const raw = value as { enabled?: unknown; base_path?: unknown; spdm_root?: unknown }
  if (raw.enabled !== true) return PHYSICSAI_LINK_OFF
  const root = typeof raw.spdm_root === 'string' && raw.spdm_root.trim() && !CONTROL.test(raw.spdm_root) ? raw.spdm_root.trim() : null
  return { enabled: true, basePath: safeBasePath(raw.base_path), spdmRoot: root }
}

/**
 * Absolute SPDM path from the server root and a root-relative catalog path
 * (`Project/Request/Working/...`). Windows roots (drive letter or UNC) get `\`.
 * Returns null for an unsafe or empty relative path.
 */
export function joinSpdmPath(root: string | null | undefined, relativePath: string | null | undefined): string | null {
  if (!root || !relativePath || CONTROL.test(relativePath)) return null
  const parts = relativePath.split(/[\\/]+/).filter(Boolean)
  if (!parts.length || parts.some((part) => part === '.' || part === '..' || part.includes(':')) || /^[\\/]/.test(relativePath)) return null
  const windows = /^[A-Za-z]:[\\/]?/.test(root) || root.startsWith('\\\\')
  const sep = windows ? '\\' : '/'
  const base = root.replace(/[\\/]+$/, '')
  return `${base}${sep}${parts.join(sep)}`
}

/** `/physicsai/import?spdm_path=<encoded absolute path>&project_id=<id>` (PhysicsAI phase2 §12.7, D8). */
export function physicsAiImportHref(link: PhysicsAiLink, relativePath: string | null | undefined, projectId?: string | null): string | null {
  if (!link.enabled) return null
  const spdmPath = joinSpdmPath(link.spdmRoot, relativePath)
  if (!spdmPath) return null
  const project = projectId ? `&project_id=${encodeURIComponent(projectId)}` : ''
  return `${link.basePath}import?spdm_path=${encodeURIComponent(spdmPath)}${project}`
}
