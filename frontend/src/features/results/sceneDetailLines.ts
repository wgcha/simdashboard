/**
 * Points of one Scene 상세 line (L1–L4) as drawn by the screen and the reports (issue #59).
 *
 * The csv rows are in node order, not along `ref_coord`, and several nodes can share a
 * `ref_coord`. Drawn as they come, the line jumps back from the largest to the smallest
 * coordinate (screen) and stacks vertical segments at shared coordinates (HTML). So the
 * points are sorted by `ref_coord` and a shared coordinate keeps its largest value.
 */
import type { DashboardLinePoint } from '../../shared/api/simulationDashboard'

export type SceneLine = { points: Array<[number, number]>; source: number; merged: number }

export function sceneDetailLine(points: DashboardLinePoint[], line: number): SceneLine {
  const byCoord = new Map<number, number>()
  let source = 0
  for (const point of points) {
    if (point.line_index !== line || point.ref_coord == null || point.value == null) continue
    if (!Number.isFinite(point.ref_coord) || !Number.isFinite(point.value)) continue
    source += 1
    const known = byCoord.get(point.ref_coord)
    if (known === undefined || point.value > known) byCoord.set(point.ref_coord, point.value)
  }
  const sorted = [...byCoord.entries()].sort((a, b) => a[0] - b[0])
  return { points: sorted, source, merged: source - sorted.length }
}
