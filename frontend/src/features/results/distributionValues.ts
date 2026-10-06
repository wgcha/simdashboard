/**
 * Per-Scene values of a 유통환경 distribution result, as the 요약 tab shows
 * them (selected-edge envelope of the Case's member). Shared by the Case
 * report and the Case 비교 view so both read the same numbers.
 */
import type { DashboardDistribution, DashboardMember, DashboardScene } from '../../shared/api/simulationDashboard'

export type SceneEnvelope = { scene: DashboardScene; value: number | null; unit: string | null }

/** The member a single-Case distribution result belongs to. */
export function distributionMember(distribution: DashboardDistribution, caseId = distribution.context.simulation_case_id ?? ''): DashboardMember | undefined {
  return distribution.members.find((member) => member.simulation_case_id === caseId) ?? distribution.members[0]
}

/** True when no edge is selected (same rule as the 요약 tab's "선택 없음"). */
export function noEdgeSelected(distribution: DashboardDistribution, edgeKeys: string) {
  return !edgeKeys.trim() || distribution.status === 'NO_SELECTION' || distribution.series.some((point) => point.status === 'NO_SELECTION')
}

/** Selected-edge envelope per Scene, in the server's Scene order (null without a value or edge selection). */
export function sceneEnvelopes(distribution: DashboardDistribution, edgeKeys: string): SceneEnvelope[] {
  const memberId = distributionMember(distribution)?.id
  const noEdge = noEdgeSelected(distribution, edgeKeys)
  const byScene = new Map(distribution.series.filter((point) => point.member_id === memberId).map((point) => [point.scene_id, point]))
  return distribution.scenes.map((scene) => {
    const point = byScene.get(scene.id)
    return { scene, value: noEdge ? null : point?.selected_edge_envelope ?? point?.value ?? null, unit: point?.unit ?? null }
  })
}
