// Pure helpers for GET /api/projects/{p}/requests/{r}/result-environments
// (case-results-environment.md §2). Kept import-free for the node self-test.
export type ResultEnvironmentName = 'USAGE' | 'DISTRIBUTION'
export type RequestResultEnvironments = { environments: ResultEnvironmentName[]; case_counts: Record<ResultEnvironmentName, number>; folder_environments?: ResultEnvironmentName[] }

export const resultEnvironmentsPath = (projectId: string, requestId: string) => `/${['api', 'projects', encodeURIComponent(projectId), 'requests', encodeURIComponent(requestId), 'result-environments'].join('/')}`

/** Known environments only, in USAGE, DISTRIBUTION order. */
export function normalizeResultEnvironments(value: Partial<RequestResultEnvironments> | null | undefined): RequestResultEnvironments {
  const listed = new Set<string>(Array.isArray(value?.environments) ? value.environments : [])
  const counts = { USAGE: Number(value?.case_counts?.USAGE ?? 0) || 0, DISTRIBUTION: Number(value?.case_counts?.DISTRIBUTION ?? 0) || 0 }
  const folder = new Set<string>(Array.isArray(value?.folder_environments) ? value.folder_environments : [])
  return { environments: (['USAGE', 'DISTRIBUTION'] as const).filter((environment) => listed.has(environment)), case_counts: counts,
    folder_environments: (['USAGE', 'DISTRIBUTION'] as const).filter((environment) => folder.has(environment)) }
}

/** Default environment for a request without Cases: its single folder-registered environment, else null. */
export function folderDefaultEnvironment(value: RequestResultEnvironments | null | undefined): ResultEnvironmentName | null {
  return value?.folder_environments?.length === 1 ? value.folder_environments[0] : null
}

/** The single registered environment, or null when there are none or both (E1–E4). */
export function resolvedResultEnvironment(value: RequestResultEnvironments | null | undefined): ResultEnvironmentName | null {
  return value?.environments.length === 1 ? value.environments[0] : null
}

/**
 * Environments to keep in sync for a request without Cases: the request folder-name
 * keyword (사용 → USAGE, 유통 → DISTRIBUTION, depth-schema D4); both when it is missing or ambiguous.
 */
export function keywordEnvironments(requestName: string | null | undefined): ResultEnvironmentName[] {
  const name = requestName ?? ''
  const usage = name.includes('사용'); const distribution = name.includes('유통')
  return usage !== distribution ? [usage ? 'USAGE' : 'DISTRIBUTION'] : ['USAGE', 'DISTRIBUTION']
}
