import type { DashboardWidget } from '../../types'
import { compact } from 'react-grid-layout/build/utils.js'

const GRID_COLUMNS = 12
const MAX_LAYOUT_Y = 10_000
const MAX_WIDGET_HEIGHT = 1_000

type Suggestion = {
  widgets: DashboardWidget[]
  changedCount: number
  reason: string
  available: boolean
}

type PositionedWidget = DashboardWidget & { originalIndex: number }

const summaryTypes = new Set<DashboardWidget['type']>([
  'kpi', 'verdict', 'gauge', 'summary', 'open_cell_summary', 'chassis_summary',
])

const evidenceTypes = new Set<DashboardWidget['type']>([
  'edge_bar', 'time_series', 'scatter', 'result_table', 'contour', 'open_cell_map',
  'chassis_diagram', 'chassis_bar', 'chassis_table', 'run_comparison', 'model3d',
])

const videoTypes = new Set<DashboardWidget['type']>(['video', 'video_grid'])

function isWholeNonNegative(value: number) {
  return Number.isSafeInteger(value) && value >= 0
}

function geometryProblem(widget: DashboardWidget): string | null {
  if (!isWholeNonNegative(widget.x) || !isWholeNonNegative(widget.y)) return `위젯 "${widget.id}"의 위치는 음수가 아닌 안전한 정수여야 합니다.`
  if (!Number.isSafeInteger(widget.w) || !Number.isSafeInteger(widget.h) || widget.w < 1 || widget.h < 1) return `위젯 "${widget.id}"의 크기가 올바르지 않습니다.`
  if (widget.y > MAX_LAYOUT_Y || widget.h > MAX_WIDGET_HEIGHT) return `위젯 "${widget.id}"의 높이 또는 세로 위치가 안전한 레이아웃 범위를 벗어났습니다.`
  if (widget.w > GRID_COLUMNS || widget.x + widget.w > GRID_COLUMNS) return `위젯 "${widget.id}"이 12열 그리드를 벗어났습니다.`
  return null
}

function intersects(first: DashboardWidget, second: DashboardWidget) {
  return first.x < second.x + second.w
    && first.x + first.w > second.x
    && first.y < second.y + second.h
    && first.y + first.h > second.y
}

function groupRank(widget: DashboardWidget) {
  if (summaryTypes.has(widget.type)) return 0
  if (evidenceTypes.has(widget.type)) return 1
  if (videoTypes.has(widget.type)) return 3
  return 2
}

function compareVisualOrder(first: PositionedWidget, second: PositionedWidget) {
  return first.y - second.y || first.x - second.x || first.originalIndex - second.originalIndex
}

function compactWithGridLayout(widgets: PositionedWidget[]) {
  const layout = compact(
    widgets.map((widget) => ({ i: widget.id, x: widget.x, y: widget.y, w: widget.w, h: widget.h })),
    'vertical',
    GRID_COLUMNS,
  ) as Array<{ i: string; x: number; y: number }>
  const positions = new Map(layout.map((item) => [item.i, item]))
  return widgets.map((widget) => ({ ...widget, x: positions.get(widget.id)!.x, y: positions.get(widget.id)!.y }))
}

function canonicalPositions(widgets: DashboardWidget[]) {
  const ordered = widgets
    .map((widget, originalIndex) => ({ ...widget, originalIndex }))
    .sort((first, second) => groupRank(first) - groupRank(second) || compareVisualOrder(first, second))

  let x = 0
  let y = 0
  let rowHeight = 0
  let previousRank = -1
  const packed = ordered.map((widget) => {
    const rank = groupRank(widget)
    if (rank !== previousRank && x > 0) {
      y += rowHeight
      x = 0
      rowHeight = 0
    }
    if (x > 0 && x + widget.w > GRID_COLUMNS) {
      y += rowHeight
      x = 0
      rowHeight = 0
    }
    const positioned = { ...widget, x, y }
    x += widget.w
    rowHeight = Math.max(rowHeight, widget.h)
    previousRank = rank
    return positioned
  })

  return new Map(compactWithGridLayout(packed).map((widget) => [widget.id, widget]))
}

function isVerticallyCompact(widgets: DashboardWidget[]) {
  const compacted = compactWithGridLayout(widgets.map((widget, originalIndex) => ({ ...widget, originalIndex })))
  return compacted.every((widget, index) => widget.x === widgets[index].x && widget.y === widgets[index].y)
}

function preservesGroupBoundaries(widgets: DashboardWidget[]) {
  const verticalRange = (rank: number) => {
    const group = widgets.filter((widget) => groupRank(widget) === rank)
    return group.length ? { top: Math.min(...group.map((widget) => widget.y)), bottom: Math.max(...group.map((widget) => widget.y + widget.h)) } : null
  }
  const summaries = verticalRange(0)
  const videos = verticalRange(3)
  const nonSummaryTop = widgets.filter((widget) => !summaryTypes.has(widget.type))
    .reduce((top, widget) => Math.min(top, widget.y), Infinity)
  const nonVideoBottom = widgets
    .filter((widget) => !videoTypes.has(widget.type))
    .reduce((bottom, widget) => Math.max(bottom, widget.y + widget.h), 0)
  return (!summaries || summaries.bottom <= nonSummaryTop)
    && (!videos || videos.top >= nonVideoBottom)
}

/**
 * Proposes a 12-column, vertically compact layout using only widget types and
 * their existing visual order. The returned array retains the caller's order
 * and every field except proposed x/y coordinates.
 */
export function suggestLayout(widgets: DashboardWidget[]): Suggestion {
  const ids = new Set<string>()
  for (const widget of widgets) {
    if (typeof widget.id !== 'string' || !widget.id.trim()) {
      return { widgets, changedCount: 0, available: false, reason: '유효한 ID가 없는 위젯이 있습니다.' }
    }
    if (ids.has(widget.id)) {
      return { widgets, changedCount: 0, available: false, reason: `중복된 위젯 ID "${widget.id}" 때문에 안전한 레이아웃을 제안할 수 없습니다.` }
    }
    ids.add(widget.id)
    const problem = geometryProblem(widget)
    if (problem) return { widgets, changedCount: 0, available: false, reason: problem }
  }

  for (let index = 0; index < widgets.length; index += 1) {
    for (let otherIndex = 0; otherIndex < index; otherIndex += 1) {
      if (intersects(widgets[index], widgets[otherIndex])) {
        return { widgets, changedCount: 0, available: false, reason: `위젯 "${widgets[otherIndex].id}"과(와) "${widgets[index].id}"이 겹칩니다.` }
      }
    }
  }

  if (widgets.length === 0) return { widgets, changedCount: 0, available: true, reason: '정렬할 위젯이 없습니다.' }
  if (widgets.length === 1) return { widgets, changedCount: 0, available: true, reason: '위젯이 하나라서 레이아웃 변경이 필요하지 않습니다.' }
  if (isVerticallyCompact(widgets) && preservesGroupBoundaries(widgets)) {
    return { widgets, changedCount: 0, available: true, reason: '현재 레이아웃은 이미 압축되어 있고 검토 순서에 맞습니다.' }
  }

  const positions = canonicalPositions(widgets)
  const compacted = widgets.map((widget) => ({ ...widget, x: positions.get(widget.id)!.x, y: positions.get(widget.id)!.y }))
  if (!preservesGroupBoundaries(compacted)) {
    return {
      widgets,
      changedCount: 0,
      available: false,
      reason: '고정된 위젯 크기로는 세로 압축 후 요약·근거·영상의 순서를 안전하게 유지할 수 없습니다.',
    }
  }
  let changedCount = 0
  const proposed = widgets.map((widget) => {
    const position = positions.get(widget.id)!
    if (widget.x === position.x && widget.y === position.y) return widget
    changedCount += 1
    return { ...widget, x: position.x, y: position.y }
  })

  return changedCount === 0
    ? { widgets: proposed, changedCount, available: true, reason: '현재 레이아웃은 이미 압축되어 있고 검토 순서에 맞습니다.' }
    : { widgets: proposed, changedCount, available: true, reason: '요약 위젯을 먼저 배치하고, 근거 위젯과 영상 위젯을 그 뒤에 배치했습니다.' }
}
