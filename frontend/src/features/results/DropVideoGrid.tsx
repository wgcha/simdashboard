import { api } from '../../api'
import type { DropVideoItem, DropVideoPage } from '../../types'
import { DropVideoCard, DropVideoSummary } from './DropVideoParts'
import { VideoGridCore } from './VideoGridCore'

const dropVideos = (page: DropVideoPage) => page.videos
const dropVideoId = (video: DropVideoItem) => video.video_id

/** Run dashboard `video_grid` widget: legacy demo drop videos with a synthetic PASS/FAIL summary. */
export function DropVideoGrid({ loadCaseId, pageSize = 20, columns = 2, rows = 2 }: { loadCaseId: string; pageSize?: number; columns?: number; rows?: number }) {
  return <VideoGridCore<DropVideoPage, DropVideoItem>
    resetKey={loadCaseId}
    loadPage={(page, size, signal) => api.dropVideos(loadCaseId, page, size, signal)}
    itemsOf={dropVideos}
    itemId={dropVideoId}
    pageSize={pageSize}
    columns={columns}
    rows={rows}
    testId="drop-video-grid"
    gridLabel="낙하 영상 Scene 목록"
    loadingLabel="낙하 영상 목록을 준비하고 있습니다."
    emptyState={<><strong>이 하중 경우에 연결된 예제 영상이 없습니다.</strong><p>예제 영상은 지정된 데모 낙하 하중 경우에서만 표시됩니다.</p></>}
    renderHeading={(result) => <><strong>{result.load_case.load_case_name}</strong><small>{result.evaluation_source === 'SYNTHETIC_DEMO' ? '합성 예제 · 실제 해석 결과 아님' : result.evaluation_source} · 전체 {result.pagination.total_items}개 영상</small></>}
    renderSummary={(result, selected, onSelect) => <DropVideoSummary result={result} selected={selected} onSelect={onSelect} />}
    renderCard={(video, slot) => <DropVideoCard video={video} selected={slot.selected} loop={slot.loop} state={slot.state} error={slot.error} setRef={slot.setRef} onSelect={slot.onSelect} onState={slot.onState} onError={slot.onError} />}
  />
}
