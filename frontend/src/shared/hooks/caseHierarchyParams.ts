/**
 * URL contract shared by the request-level Case results and materials tabs.
 * Both tabs address the same Folder Schema hierarchy with the same keys, so a
 * selection survives tab switches and browser history.
 */
export const CASE_HIERARCHY_KEYS = ['case', 'case_load', 'case_run', 'case_option'] as const
export type CaseHierarchyKey = typeof CASE_HIERARCHY_KEYS[number]

/** Keys owned by a parent level. Changing the parent invalidates all of them. */
export const CASE_HIERARCHY_CHILDREN: Readonly<Record<CaseHierarchyKey, readonly string[]>> = {
  case: ['case_load', 'case_run', 'case_option', 'scene', 'part'],
  case_load: ['case_run', 'case_option', 'scene', 'part'],
  case_run: ['case_option', 'scene', 'part'],
  case_option: ['scene', 'part'],
}

export type SearchParamPatch = Record<string, string | null | undefined>

function isHierarchyKey(key: string): key is CaseHierarchyKey {
  return (CASE_HIERARCHY_KEYS as readonly string[]).includes(key)
}

/**
 * Adds `null` for every child of each hierarchy key present in the patch.
 * Children the patch sets explicitly are kept, so callers can restore a whole
 * path (for example from an old `scene` link) in one write.
 */
export function withClearedChildren(patch: SearchParamPatch): SearchParamPatch {
  const next: SearchParamPatch = { ...patch }
  for (const key of Object.keys(patch)) {
    if (!isHierarchyKey(key)) continue
    for (const child of CASE_HIERARCHY_CHILDREN[key]) {
      if (!(child in patch)) next[child] = null
    }
  }
  return next
}

/** Merges a patch into a query string; empty values delete the key. Returns the query without `?`. */
export function applySearchPatch(search: string, patch: SearchParamPatch): string {
  const next = new URLSearchParams(search)
  for (const [key, value] of Object.entries(patch)) {
    if (value) next.set(key, value)
    else next.delete(key)
  }
  return next.toString()
}
