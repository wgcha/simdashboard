// Pure rules of the folder-structure review (폴더 연결·규칙 → 구조 확인): which nodes need a review and how
// "이번 등록에서 제외" is applied in bulk.  Exclusions are preview assignments `role_kind: 'EXCLUDE'`; the
// server excludes everything below an excluded folder too (folder_discovery_environment._preview_depth), so
// the screen treats a descendant of an excluded folder as excluded as well.  No runtime imports (node self-test).
import type { FolderAssignment } from './folderEnvironment'

export type ReviewNode = { id: string; relative_path: string; status: string; deviation?: unknown }
export type Assignments = Record<string, FolderAssignment>

const norm = (path: string) => path.replace(/\/+$/, '').toLowerCase()

/** The scan-root node (empty path) has no descendants to cascade to: excluding it excludes nothing below. */
export const cascades = (node: Pick<ReviewNode, 'relative_path'>): boolean => norm(node.relative_path) !== ''

export const isUnder = (path: string, parent: string): boolean => {
  const base = norm(parent)
  return base !== '' && norm(path).startsWith(`${base}/`)
}

export const exclusion = (nodeId: string): FolderAssignment =>
  ({ node_id: nodeId, role_kind: 'EXCLUDE', target_mode: 'CREATE', target_id: null, propagate_same_level: false, confirm: true })

export const ownExcluded = (node: Pick<ReviewNode, 'id'>, assignments: Assignments): boolean => assignments[node.id]?.role_kind === 'EXCLUDE'

/** Paths of the folders the administrator excluded (by an explicit assignment). */
export function excludedPaths(nodes: ReviewNode[], assignments: Assignments): string[] {
  return nodes.filter((node) => ownExcluded(node, assignments) && cascades(node)).map((node) => node.relative_path)
}

/** Excluded by its own assignment or because a folder above it is excluded. */
export function effectivelyExcluded(node: ReviewNode, assignments: Assignments, paths: string[] = []): boolean {
  return ownExcluded(node, assignments) || paths.some((path) => isUnder(node.relative_path, path))
}

export const flagged = (node: ReviewNode): boolean => ['UNRESOLVED', 'ERROR'].includes(node.status) || Boolean(node.deviation)

/** Nodes that still need a review: flagged and not (effectively) excluded. */
export function reviewNodes<T extends ReviewNode>(nodes: T[], assignments: Assignments): T[] {
  const paths = excludedPaths(nodes, assignments)
  return nodes.filter((node) => flagged(node) && !effectivelyExcluded(node, assignments, paths))
}

/** Bulk: exclude every node that needs a review (its descendants follow on the server). */
export function excludeReviewNodes(nodes: ReviewNode[], assignments: Assignments): Assignments {
  const next = { ...assignments }
  for (const node of reviewNodes(nodes, assignments)) next[node.id] = exclusion(node.id)
  return next
}

/** Descendants of a node by path (the scan root has no path prefix, so all other nodes are below it). */
export function descendants<T extends ReviewNode>(nodes: T[], node: ReviewNode): T[] {
  return nodes.filter((item) => item.id !== node.id && (norm(node.relative_path) === '' ? true : isUnder(item.relative_path, node.relative_path)))
}

/** Exclude a node together with everything below it. */
export function excludeSubtree(nodes: ReviewNode[], assignments: Assignments, node: ReviewNode): Assignments {
  const next = { ...assignments, [node.id]: exclusion(node.id) }
  // The scan root cannot cascade on the server: list its descendants explicitly.
  if (!cascades(node)) for (const item of descendants(nodes, node)) next[item.id] = exclusion(item.id)
  return next
}

/** Undo the exclusion of a node and of every descendant that was excluded explicitly. */
export function includeSubtree(nodes: ReviewNode[], assignments: Assignments, node: ReviewNode): Assignments {
  const drop = new Set([node.id, ...descendants(nodes, node).map((item) => item.id)])
  return Object.fromEntries(Object.entries(assignments).filter(([id, value]) => !(drop.has(id) && value.role_kind === 'EXCLUDE')))
}

/** "선택 해제": drop every exclusion; project/request LINK assignments stay. */
export function clearExclusions(assignments: Assignments): Assignments {
  return Object.fromEntries(Object.entries(assignments).filter(([, value]) => value.role_kind !== 'EXCLUDE'))
}

export const exclusionCount = (assignments: Assignments): number => Object.values(assignments).filter((value) => value.role_kind === 'EXCLUDE').length
