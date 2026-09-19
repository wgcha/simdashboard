import type { WorkspaceContextQuery } from './useWorkspaceNavigation'

export const CASE_RESULTS_VIEW = 'case_results'

/** The SPDM Case dashboard belongs to a project/request, never a legacy load case or Run. */
export function isCaseResultsView(page: string, view?: string) {
  return page === 'dashboard' && view === CASE_RESULTS_VIEW
}

export function caseResultsContext(projectId: string, requestId: string): WorkspaceContextQuery {
  return { projectId, requestId, view: CASE_RESULTS_VIEW }
}
