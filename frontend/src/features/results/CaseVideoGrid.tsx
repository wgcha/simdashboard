import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link2 } from 'lucide-react'
import { simulationDashboardApi, type DashboardRunVideoPage, type DashboardVideoRule, type DashboardVideoScene } from '../../shared/api/simulationDashboard'
import { Select } from '../../shared/components/Select'
import './CaseVideoGrid.css'
import { RepresentativeVideoCard } from './RepresentativeVideoCard'
import { VideoGridCore } from './VideoGridCore'
import { VideoRepresentativePanel, type RepresentativeTarget } from './VideoRepresentativePanel'
import { VideoRuleEditor } from './VideoRuleEditor'
import { toTile, type VideoTile } from './videoRepresentative'

export type CaseVideoGridProps = {
  /** Exact capture id or the merged latest view `latest:<dashboard case id>`. */
  captureId: string
  runId: string
  runOptionId?: string | null
  mode?: string | null
  defaultColumns?: CaseVideoColumns
  /** 「Case 전체 · Scene별 대표 영상」 (default) or 「Scene 하나 · 영상 전체 격자」. */
  defaultView?: CaseVideoView
  /** 동시 재생 맞춤 initially on. */
  defaultSyncPlayback?: boolean
}
export type CaseVideoColumns = 2 | 3 | 4
export type CaseVideoView = 'representatives' | 'scene' | 'all'

const COLUMN_CHOICES: CaseVideoColumns[] = [2, 3, 4]
const ROWS = 2
type CaseVideoPage = DashboardRunVideoPage & { items: VideoTile[] }

const itemsOf = (page: CaseVideoPage) => page.items
const itemId = (item: VideoTile) => item.id

/** Videos of the selected Run option: one representative per Scene, or every video of one Scene, in the shared video grid. */
export function CaseVideoGrid({ captureId, runId, runOptionId, mode, defaultColumns = 2, defaultView = 'representatives', defaultSyncPlayback = false }: CaseVideoGridProps) {
  const [columns, setColumns] = useState<CaseVideoColumns>(defaultColumns)
  const [view, setView] = useState<CaseVideoView>(defaultView)
  const [sync, setSync] = useState(defaultSyncPlayback)
  const [sceneId, setSceneId] = useState('')
  const [scenes, setScenes] = useState<DashboardVideoScene[]>([])
  const [rule, setRule] = useState<DashboardVideoRule | null>(null)
  const [caseId, setCaseId] = useState('')
  const [panel, setPanel] = useState<RepresentativeTarget | null>(null)
  const [refresh, setRefresh] = useState(0)

  const scope = useMemo(() => ({ capture_id: captureId, run_option_id: runOptionId ?? undefined, mode: runOptionId ? undefined : mode ?? undefined }), [captureId, runOptionId, mode])
  // Scene list (selector, totals): independent of the paged grid; a failure only hides the selector.
  useEffect(() => {
    const controller = new AbortController()
    simulationDashboardApi.videoScenes(runId, scope, controller.signal)
      .then((result) => { setScenes(result.scenes ?? []); if (result.video_rule) setRule((current) => current ?? result.video_rule!) })
      .catch(() => { if (!controller.signal.aborted) setScenes([]) })
    return () => controller.abort()
  }, [runId, scope, refresh])
  const withVideos = useMemo(() => scenes.filter((scene) => scene.video_count + scene.unplayable_count > 0), [scenes])
  const activeScene = withVideos.find((scene) => scene.scene_id === sceneId) ?? withVideos[0]
  const totalVideos = scenes.length ? scenes.reduce((sum, scene) => sum + scene.video_count + scene.unplayable_count, 0) : null

  const loadPage = useCallback(async (page: number, pageSize: number, signal: AbortSignal): Promise<CaseVideoPage> => {
    const result = await simulationDashboardApi.videos(runId, { ...scope, page, page_size: pageSize, ...(view === 'representatives' ? { representative_only: true } : view === 'scene' && activeScene ? { scene_id: activeScene.scene_id } : {}) }, signal)
    if (!signal.aborted) {
      setCaseId(result.case_id ?? '')
      if (result.video_rule) setRule(result.video_rule)
    }
    return { ...result, items: (result.videos ?? []).map((video) => toTile(video, view === 'representatives', simulationDashboardApi.assetUrl)) }
  }, [runId, scope, view, activeScene])

  const onChanged = useCallback(() => setRefresh((value) => value + 1), [])
  const modeButton = (value: CaseVideoView, label: string) => <button key={value} type="button" className={view === value ? 'active' : ''} aria-pressed={view === value} onClick={() => { setView(value); setPanel(null) }}>{label}</button>

  return <div className="case-video-view" data-testid="case-video-view">
    <VideoGridCore<CaseVideoPage, VideoTile>
      resetKey={[captureId, runId, runOptionId ?? '', mode ?? '', view, view === 'scene' ? activeScene?.scene_id ?? '' : '', refresh].join('|')}
      loadPage={loadPage}
      itemsOf={itemsOf}
      itemId={itemId}
      columns={columns}
      rows={ROWS}
      syncPlayback={sync}
      testId="case-video-grid"
      className="case-video-grid"
      gridLabel="Scene 영상 목록"
      loadingLabel="영상 목록을 준비하고 있습니다."
      emptyState={<><strong>이 Run Option에는 영상이 없습니다.</strong><p>Scene 폴더에 MP4·WebM 영상이 등록되면 여기에 표시됩니다.</p></>}
      renderHeading={(result) => {
        const context = result.context
        const title = context.option_label || context.run_label || '선택한 Run'
        const path = [context.load_case_name, context.run_label].filter(Boolean).join(' · ')
        const total = totalVideos ?? result.pagination.total_items
        const extra = view === 'representatives' ? (scenes.length ? ` · Scene ${scenes.length}개` : '') : view === 'scene' && activeScene ? ` · ${activeScene.label}` : ''
        return <><strong>{title}</strong><small>{path ? `${path} · ` : ''}전체 {total}개 영상{extra}</small></>
      }}
      toolbar={<>
        <div className="case-video-columns" role="group" aria-label="영상 열 수"><span>열</span>{COLUMN_CHOICES.map((choice) => <button key={choice} type="button" className={choice === columns ? 'active' : ''} aria-pressed={choice === columns} onClick={() => setColumns(choice)}>{choice}열</button>)}</div>
        <div className="case-video-modes" role="group" aria-label="영상 보기 방식">{modeButton('representatives', 'Case 전체 · Scene별 대표 영상')}{modeButton('scene', 'Scene 하나 · 영상 전체 격자')}{modeButton('all', '모든 영상')}</div>
        {view === 'scene' && withVideos.length > 0 && <label className="case-video-scene"><span>Scene</span><Select controlSize="sm" value={activeScene?.scene_id ?? ''} onChange={(event) => { setSceneId(event.target.value); setPanel(null) }} aria-label="영상을 볼 Scene">{withVideos.map((scene) => <option key={scene.scene_id} value={scene.scene_id}>{scene.scene_sequence_label ? `${scene.scene_sequence_label} ` : ''}{scene.label} ({scene.video_count + scene.unplayable_count})</option>)}</Select></label>}
        <button type="button" className={`case-video-sync${sync ? ' active' : ''}`} aria-pressed={sync} title="표시한 영상의 재생·정지·위치를 함께 맞춥니다." onClick={() => setSync((value) => !value)}><Link2 aria-hidden="true" /> 동시 재생 맞춤 {sync ? '켜짐' : '꺼짐'}</button>
      </>}
      renderCard={(tile, slot) => <RepresentativeVideoCard tile={tile} {...slot} representativeMode={view === 'representatives'}
        onChange={() => setPanel({ sceneId: tile.video.scene_id, sceneKey: tile.video.scene_key ?? '', sceneLabel: tile.video.scene_label })} />}
    />
    {panel?.sceneKey && caseId ? <VideoRepresentativePanel key={panel.sceneKey} target={panel} caseId={caseId} captureId={captureId} runId={runId} runOptionId={runOptionId} mode={mode} rule={rule ?? undefined}
      canChoose={Boolean(rule?.can_choose)} onClose={() => setPanel(null)} onChanged={onChanged} /> : null}
    {rule?.project_id ? <VideoRuleEditor projectId={rule.project_id} rule={rule} onSaved={(saved) => { setRule(saved); onChanged() }} /> : null}
  </div>
}
