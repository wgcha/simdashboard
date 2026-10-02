// Pure DEPTH_V1 editor rules (docs/contracts/depth-schema.md §4.3). No runtime imports so the
// node self-test can load it directly.
import type { DepthLevel, DepthLowerRole, DepthSampleLevel, DepthUpperRole, FolderEnvironment } from './folderEnvironment'

export const UPPER_MAX_LEVELS = 8
export const UPPER_ROLES: DepthUpperRole[] = ['CONTAINER', 'PROJECT', 'REQUEST']
const LOWER_MIDDLE_ROLES: Record<FolderEnvironment, DepthLowerRole[]> = {
  USAGE: ['CONTAINER', 'SCENE'],
  DISTRIBUTION: ['LOAD_CASE', 'EXECUTION_RUN', 'RUN_OPTION', 'CONTAINER', 'SCENE'],
}

/** Role choices for one lower level; L1 and L2 are fixed by the contract. */
export function lowerRoleOptions(environment: FolderEnvironment, level: number): DepthLowerRole[] {
  if (level === 1) return ['WORKING']
  if (level === 2) return ['SIMULATION_CASE']
  return LOWER_MIDDLE_ROLES[environment]
}

export function validateUpperLevels(levels: DepthLevel<DepthUpperRole>[]): string[] {
  const errors: string[] = []
  if (!levels.length) return ['상위 구조에 깊이가 없습니다.']
  if (levels.length > UPPER_MAX_LEVELS) errors.push(`상위 구조는 최대 ${UPPER_MAX_LEVELS}단계입니다.`)
  if (levels.some((item, index) => item.level !== index + 1)) errors.push('깊이는 1부터 연속이어야 합니다.')
  const projects = levels.filter((item) => item.role === 'PROJECT')
  const requests = levels.filter((item) => item.role === 'REQUEST')
  if (projects.length !== 1) errors.push('프로젝트 깊이는 정확히 1개여야 합니다.')
  if (requests.length !== 1) errors.push('의뢰 깊이는 정확히 1개여야 합니다.')
  if (levels[levels.length - 1]?.role !== 'REQUEST') errors.push('의뢰는 마지막 깊이여야 합니다.')
  if (projects.length === 1 && requests.length === 1 && projects[0].level >= requests[0].level) errors.push('프로젝트는 의뢰보다 얕아야 합니다.')
  return errors
}

export function validateLowerLevels(environment: FolderEnvironment, levels: DepthLevel<DepthLowerRole>[]): string[] {
  const errors: string[] = []
  if (levels.some((item, index) => item.level !== index + 1)) errors.push('깊이는 1부터 연속이어야 합니다.')
  if (levels[0]?.role !== 'WORKING') errors.push('1단계는 Working이어야 합니다.')
  if (levels[1]?.role !== 'SIMULATION_CASE') errors.push('2단계는 해석 Case여야 합니다.')
  if (levels.length < 3 || levels[levels.length - 1].role !== 'SCENE') errors.push('마지막 깊이는 Scene이어야 합니다.')
  if (levels.slice(2).some((item) => !lowerRoleOptions(environment, item.level).includes(item.role))) errors.push('이 환경에서 쓸 수 없는 역할이 있습니다.')
  if (environment === 'DISTRIBUTION' && !levels.some((item) => item.role === 'RUN_OPTION')) errors.push('유통환경에는 Run Option 깊이가 필요합니다.')
  return errors
}

export type DepthRow<Role extends string> = { level: number; role: Role | ''; sample?: DepthSampleLevel; inSchema: boolean }

/**
 * Table rows = schema levels ∪ sampled depths. Rows past the schema are shown as "내용물" (CONTENT);
 * only the first such row can receive a role (extends the schema) and only the last schema row can be
 * cleared (shrinks it), so the schema stays contiguous.
 */
export function depthRows<Role extends string>(levels: DepthLevel<Role>[], samples: DepthSampleLevel[]): DepthRow<Role>[] {
  const sampleByLevel = new Map(samples.filter((item) => item.level > 0).map((item) => [item.level, item]))
  const depth = Math.max(levels.length, ...sampleByLevel.keys(), 0)
  return Array.from({ length: depth }, (_, index) => {
    const level = index + 1
    const defined = levels[index]
    return { level, role: defined?.role ?? '', sample: sampleByLevel.get(level), inSchema: Boolean(defined) }
  })
}

/** Applies a role choice to one row; '' clears the last schema level. Returns the unchanged list when not allowed. */
export function setDepthRole<Role extends string>(levels: DepthLevel<Role>[], level: number, role: Role | ''): DepthLevel<Role>[] {
  if (role === '') return level === levels.length && level > 0 ? levels.slice(0, -1) : levels
  if (level <= levels.length) return levels.map((item) => item.level === level ? { ...item, role } : item)
  if (level === levels.length + 1) return [...levels, { level, role }]
  return levels
}

/** Whether the role select of a row may be changed at all. */
export function depthRowEditable(levelCount: number, level: number, locked = false): boolean {
  return !locked && level <= levelCount + 1
}

/** Deviation code → admin-facing label (contract §5). */
export const DEPTH_DEVIATION_LABELS: Record<string, string> = {
  ENV_KEYWORD_BOTH: '의뢰명에 사용·유통 모두 있음',
  ENV_KEYWORD_NONE: '의뢰명에 사용·유통 없음',
  WORKING_MISSING: 'Working 폴더 없음',
  UNEXPECTED_REQUEST_CHILD: '의뢰 아래 Working·Final 외 폴더',
  UNEXPECTED_FINAL_CHILD: 'Final 아래 CAE·Reports·CAD 외 폴더',
  FINAL_VERSION_INVALID: 'Final 버전 폴더 이름 형식 아님',
}
export const BLOCKING_DEVIATIONS = new Set(['ENV_KEYWORD_BOTH', 'ENV_KEYWORD_NONE', 'WORKING_MISSING', 'UNEXPECTED_REQUEST_CHILD'])
export const deviationLabel = (code: string) => DEPTH_DEVIATION_LABELS[code] ?? code

/** Sums delete-preview counts for the confirmation dialog. */
export function sumDeleteCounts(items: Array<{ counts: Record<string, number> }>): Record<string, number> {
  const total: Record<string, number> = {}
  for (const item of items) for (const [key, value] of Object.entries(item.counts ?? {})) total[key] = (total[key] ?? 0) + (Number(value) || 0)
  return total
}

/** Reads `code` from an ApiError detail (or any error-like value). */
export function errorCode(error: unknown): string | null {
  const detail = error && typeof error === 'object' && 'detail' in error ? (error as { detail: unknown }).detail : error
  if (detail && typeof detail === 'object' && 'code' in detail && typeof (detail as { code: unknown }).code === 'string') return (detail as { code: string }).code
  return null
}

/** Pulls preview-style items out of a 409 REGISTRATION_DELETE_BLOCKED detail. */
export function errorItems<T>(error: unknown): T[] {
  const detail = error && typeof error === 'object' && 'detail' in error ? (error as { detail: unknown }).detail : null
  return detail && typeof detail === 'object' && Array.isArray((detail as { items?: unknown }).items) ? (detail as { items: T[] }).items : []
}
