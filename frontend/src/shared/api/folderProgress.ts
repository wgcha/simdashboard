// Pure helpers for GET /api/projects/{p}/requests/{r}/folder-progress
// (folder-request-progress.md §3). Kept import-free for the node self-test.
export type FolderProgressStepKey = 'REGISTERED' | 'MODELING' | 'RESULTS' | 'FINAL' | 'REPORT'
export type FolderProgressStatus = 'DONE' | 'IN_PROGRESS' | 'WAITING'
export type FolderProgressStep = { key: string; label: string; status: FolderProgressStatus; detail: string | null }
export type RequestFolderProgress = {
  applicable: boolean
  environment: string | null
  completed: number
  total: number
  current_key: string | null
  next_action: string
  steps: FolderProgressStep[]
  checked_at: string | null
}

export const folderProgressPath = (projectId: string, requestId: string) => `/${['api', 'projects', encodeURIComponent(projectId), 'requests', encodeURIComponent(requestId), 'folder-progress'].join('/')}`

const STATUSES = new Set<string>(['DONE', 'IN_PROGRESS', 'WAITING'])

/** Defensive parse: unknown statuses become WAITING; missing steps make the response not applicable. */
export function normalizeFolderProgress(value: Partial<RequestFolderProgress> | null | undefined): RequestFolderProgress {
  const steps: FolderProgressStep[] = (Array.isArray(value?.steps) ? value.steps : [])
    .filter((step): step is FolderProgressStep => Boolean(step) && typeof step.key === 'string' && step.key.length > 0)
    .map((step) => ({ key: step.key, label: typeof step.label === 'string' && step.label ? step.label : step.key, status: STATUSES.has(step.status) ? step.status : 'WAITING', detail: typeof step.detail === 'string' && step.detail ? step.detail : null }))
  const completed = steps.filter((step) => step.status === 'DONE').length
  const current = typeof value?.current_key === 'string' && value.current_key ? value.current_key : steps.find((step) => step.status !== 'DONE')?.key ?? null
  return {
    applicable: value?.applicable === true && steps.length > 0,
    environment: typeof value?.environment === 'string' ? value.environment : null,
    completed,
    total: steps.length,
    current_key: current,
    next_action: typeof value?.next_action === 'string' && value.next_action ? value.next_action : current ? '' : '완료',
    steps,
    checked_at: typeof value?.checked_at === 'string' ? value.checked_at : null,
  }
}

/** Overview label for a folder step status (§4). */
export function folderProgressStatusLabel(status: FolderProgressStatus): string {
  return status === 'DONE' ? '완료' : status === 'IN_PROGRESS' ? '진행 중' : '대기'
}
