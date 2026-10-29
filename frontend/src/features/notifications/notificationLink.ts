const LEGACY_RUN_VIEWS = new Set(['open_cell', 'chassis', 'custom', 'compare'])

/**
 * Where a notification link opens. The legacy Run review has no entry point (user decision
 * 2026-10-29): a stored link into it (view=open_cell|chassis|custom|compare) opens the request's
 * Case 결과 instead. Every other link is returned unchanged.
 */
export function notificationTarget(link: string) {
  const [path, query = ''] = link.split('?', 2)
  if (!path.endsWith('/workspace/requests')) return link
  const params = new URLSearchParams(query)
  if (!LEGACY_RUN_VIEWS.has(params.get('view') ?? '')) return link
  const next = new URLSearchParams()
  for (const key of ['project', 'request']) { const value = params.get(key); if (value) next.set(key, value) }
  next.set('view', 'case_results')
  return `${path}?${next.toString()}`
}
