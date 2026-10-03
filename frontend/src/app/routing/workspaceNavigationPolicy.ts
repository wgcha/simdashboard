import type { WorkspacePage } from '../../features/auth/access'
import type { WorkspaceContextQuery } from './useWorkspaceNavigation'
import { CASE_HIERARCHY_CHILDREN, CASE_HIERARCHY_KEYS } from '../../shared/hooks/caseHierarchyParams.ts'

/**
 * Workspace navigation contract (docs/contracts/workspace-navigation.md).
 *
 * N1: the bootstrap setup screen may stand in only for request-workspace pages.
 * With at least one project, every other page renders itself even when the
 * selected request has no legacy overview (folder-registered requests).
 * `workbench`/`data` were already exempt when projects exist and stay so; with no
 * projects (first-run setup) the previous behaviour is kept unchanged.
 */
export type BootstrapFallbackInput = {
  caseResultsMode: boolean
  hasDashboard: boolean
  hasOverview: boolean
  hasProjects: boolean
  isRequestMonitoring: boolean
  isWorkspaceIndex: boolean
  page: WorkspacePage
  pendingAnalysis: boolean
}

const ALWAYS_SELF_RENDERED: ReadonlySet<WorkspacePage> = new Set<WorkspacePage>(['access_admin', 'schemas'])

export function usesBootstrapFallback(input: BootstrapFallbackInput): boolean {
  if (ALWAYS_SELF_RENDERED.has(input.page)) return false
  if (input.hasOverview && input.hasDashboard) return false
  if (input.caseResultsMode || input.isRequestMonitoring || input.pendingAnalysis) return false
  // Only the request overview (`dashboard`) still needs the fallback once projects exist.
  return !(input.hasProjects && !input.isWorkspaceIndex && input.page !== 'dashboard')
}

/** Pages that share the selected request's screen state through the URL. */
const REQUEST_SCOPED_PAGES: ReadonlySet<WorkspacePage> = new Set<WorkspacePage>(['dashboard', 'workbench', 'data', 'materials'])

/** N3: request-screen-only query keys. `project` and `request` are never listed here. */
export const REQUEST_SCREEN_QUERY_KEYS: readonly string[] = Array.from(new Set([
  'view', 'capture', 'result_environment', 'resultTab', 'page', 'loadCase', 'run',
  ...CASE_HIERARCHY_KEYS, ...Object.values(CASE_HIERARCHY_CHILDREN).flat(),
]))

export function isRequestScopedPage(page: WorkspacePage): boolean {
  return REQUEST_SCOPED_PAGES.has(page)
}

/** N3 for the state→URL sync: pages outside the request workspace only keep project/request. */
export function scopeWorkspaceContext(page: WorkspacePage, context: WorkspaceContextQuery): WorkspaceContextQuery {
  return isRequestScopedPage(page) ? context : { projectId: context.projectId, requestId: context.requestId }
}

/**
 * Query string (with `?` or empty) to carry into a page change. Leaving the
 * request workspace drops request-screen-only state; moves between request
 * workspace pages keep today's behaviour (N4 covers same-page moves elsewhere).
 */
export function searchForPageChange(currentSearch: string, targetPage: WorkspacePage): string {
  if (REQUEST_SCOPED_PAGES.has(targetPage)) return currentSearch
  const query = new URLSearchParams(currentSearch)
  for (const key of REQUEST_SCREEN_QUERY_KEYS) query.delete(key)
  const search = query.toString()
  return search ? `?${search}` : ''
}
