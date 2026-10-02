import { useCallback, useState } from 'react'
import { simulationDashboardApi, type DashboardRunVideoPage } from '../../shared/api/simulationDashboard'
import './CaseVideoGrid.css'
import { VideoGridCard, type VideoGridItem } from './DropVideoParts'
import { VideoGridCore } from './VideoGridCore'

export type CaseVideoGridProps = {
  /** Exact capture id or the merged latest view `latest:<dashboard case id>`. */
  captureId: string
  runId: string
  runOptionId?: string | null
  mode?: string | null
  defaultColumns?: CaseVideoColumns
}
export type CaseVideoColumns = 2 | 3 | 4

const COLUMN_CHOICES: CaseVideoColumns[] = [2, 3, 4]
const ROWS = 2
type CaseVideoPage = DashboardRunVideoPage & { items: VideoGridItem[] }

const itemsOf = (page: CaseVideoPage) => page.items
const itemId = (item: VideoGridItem) => item.id

/** Every video of every Scene of the selected Run option, using the shared video grid playback. */
export function CaseVideoGrid({ captureId, runId, runOptionId, mode, defaultColumns = 2 }: CaseVideoGridProps) {
  const [columns, setColumns] = useState<CaseVideoColumns>(defaultColumns)
  const loadPage = useCallback(async (page: number, pageSize: number, signal: AbortSignal): Promise<CaseVideoPage> => {
    const result = await simulationDashboardApi.videos(runId, { capture_id: captureId, run_option_id: runOptionId ?? undefined, mode: runOptionId ? undefined : mode ?? undefined, page, page_size: pageSize }, signal)
    return { ...result, items: (result.videos ?? []).map((video) => ({ id: video.video_id, title: video.scene_label, subtitle: video.title, src: simulationDashboardApi.assetUrl(video.asset_id), status: video.status })) }
  }, [captureId, runId, runOptionId, mode])

  return <VideoGridCore<CaseVideoPage, VideoGridItem>
    resetKey={[captureId, runId, runOptionId ?? '', mode ?? ''].join('|')}
    loadPage={loadPage}
    itemsOf={itemsOf}
    itemId={itemId}
    columns={columns}
    rows={ROWS}
    testId="case-video-grid"
    className="case-video-grid"
    gridLabel="Scene 영상 목록"
    loadingLabel="영상 목록을 준비하고 있습니다."
    emptyState={<><strong>이 Run Option에는 영상이 없습니다.</strong><p>Scene 폴더에 MP4·WebM 영상이 등록되면 여기에 표시됩니다.</p></>}
    renderHeading={(result) => {
      const context = result.context
      const title = context.option_label || context.run_label || '선택한 Run'
      const path = [context.load_case_name, context.run_label].filter(Boolean).join(' · ')
      return <><strong>{title}</strong><small>{path ? `${path} · ` : ''}전체 {result.pagination.total_items}개 영상</small></>
    }}
    toolbar={<div className="case-video-columns" role="group" aria-label="영상 열 수"><span>열</span>{COLUMN_CHOICES.map((choice) => <button key={choice} type="button" className={choice === columns ? 'active' : ''} aria-pressed={choice === columns} onClick={() => setColumns(choice)}>{choice}열</button>)}</div>}
    renderCard={(video, slot) => <VideoGridCard video={video} {...slot} />}
  />
}
