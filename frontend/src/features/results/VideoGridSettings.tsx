import type { DashboardWidget } from '../../types'
import { MAX_VIDEO_CELLS, normalizeVideoGridLayout } from './videoGridLayout'
import './VideoGridSettings.css'

export function VideoGridSettings({ settings, onChange }: {
  settings: DashboardWidget['settings']
  onChange: (patch: Partial<DashboardWidget>) => void
}) {
  const layout = normalizeVideoGridLayout(settings?.videoColumns, settings?.videoRows)
  const update = (columns: number, rows: number) => {
    onChange({ settings: { videoColumns: columns, videoRows: rows } })
  }
  return <fieldset className="video-grid-settings">
    <legend>영상 배치</legend>
    <label><span>가로 열 수</span><select aria-label="영상 가로 열 수" value={layout.columns} onChange={(event) => update(Number(event.target.value), layout.rows)}>
      {Array.from({ length: MAX_VIDEO_CELLS }, (_, index) => index + 1).map((value) => <option key={value} value={value} disabled={value * layout.rows > MAX_VIDEO_CELLS}>{value}열</option>)}
    </select></label>
    <label><span>세로 행 수</span><select aria-label="영상 세로 행 수" value={layout.rows} onChange={(event) => update(layout.columns, Number(event.target.value))}>
      {Array.from({ length: MAX_VIDEO_CELLS }, (_, index) => index + 1).map((value) => <option key={value} value={value} disabled={value * layout.columns > MAX_VIDEO_CELLS}>{value}행</option>)}
    </select></label>
    <p role="status">{layout.columns}열 × {layout.rows}행 · 한 번에 최대 {layout.pageSize}개</p>
    <small>최대 20개까지 표시합니다. 작은 화면에서는 열 수를 줄여 표시하며, 영상이 많으면 스크롤합니다. 설정 완료 후 레이아웃 저장을 누르세요.</small>
  </fieldset>
}
