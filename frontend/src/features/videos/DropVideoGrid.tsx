import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react'
import { AlertTriangle, ChevronLeft, ChevronRight, LoaderCircle, Pause, Play, Repeat2, RotateCcw } from 'lucide-react'
import { api } from '../../api'
import type { DropVideoItem, DropVideoPage, DropVideoSubsystemEvaluation } from '../../types'

type PlaybackState = 'loading' | 'ready' | 'playing' | 'paused' | 'ended' | 'error'

const PLAYBACK_LABEL: Record<PlaybackState, string> = {
  loading: '로딩',
  ready: '재생 준비',
  playing: '재생 중',
  paused: '일시정지',
  ended: '재생 완료',
  error: '재생 불가',
}

const METRIC_LABEL: Record<string, string> = {
  top_edge: '상단 엣지',
  bottom_edge: '하단 엣지',
  left_edge: '좌측 엣지',
  right_edge: '우측 엣지',
  top_gap: '상단 이격',
  bottom_gap: '하단 이격',
  top_left_corner: '좌상단 코너',
  top_right_corner: '우상단 코너',
  bottom_left_corner: '좌하단 코너',
  bottom_right_corner: '우하단 코너',
}

function fileSizeLabel(bytes: number) {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function metricBarStyle(metric: DropVideoSubsystemEvaluation): CSSProperties {
  return { '--metric-fill': `${Math.min(100, metric.critical_value / (metric.threshold * 1.25) * 100)}%` } as CSSProperties
}

export function DropVideoGrid({ loadCaseId, pageSize = 20 }: { loadCaseId: string; pageSize?: number }) {
  const normalizedPageSize = Number.isFinite(pageSize) ? Math.min(20, Math.max(1, Math.round(pageSize))) : 20
  const [page, setPage] = useState(1)
  const [result, setResult] = useState<DropVideoPage | null>(null)
  const [selectedVideoId, setSelectedVideoId] = useState('')
  const [loading, setLoading] = useState(true)
  const [requestError, setRequestError] = useState('')
  const [loop, setLoop] = useState(false)
  const [reloadToken, setReloadToken] = useState(0)
  const [playback, setPlayback] = useState<Record<string, PlaybackState>>({})
  const [videoErrors, setVideoErrors] = useState<Record<string, string>>({})
  const videoRefs = useRef(new Map<string, HTMLVideoElement>())

  const pauseVideos = (reset = false) => {
    videoRefs.current.forEach((video) => {
      video.pause()
      if (reset) {
        try { video.currentTime = 0 } catch { /* metadata may not be available */ }
      }
    })
  }

  useEffect(() => {
    setPage(1)
    setSelectedVideoId('')
  }, [loadCaseId])

  useEffect(() => {
    const controller = new AbortController()
    pauseVideos()
    videoRefs.current.clear()
    setLoading(true)
    setRequestError('')
    setPlayback({})
    setVideoErrors({})
    api.dropVideos(loadCaseId, page, normalizedPageSize, controller.signal)
      .then((payload) => {
        setResult(payload)
        setPlayback(Object.fromEntries(payload.videos.map((video) => [video.video_id, 'loading'])))
        setSelectedVideoId((current) => payload.videos.some((video) => video.video_id === current) ? current : payload.videos[0]?.video_id ?? '')
      })
      .catch((reason) => {
        if (controller.signal.aborted) return
        setResult(null)
        setSelectedVideoId('')
        setRequestError(reason instanceof Error ? reason.message : '영상 목록을 불러오지 못했습니다.')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => {
      controller.abort()
      pauseVideos()
      videoRefs.current.clear()
    }
  }, [loadCaseId, page, normalizedPageSize, reloadToken])

  const setState = (videoId: string, state: PlaybackState) => {
    setPlayback((current) => ({ ...current, [videoId]: state }))
  }

  const playVideos = async (restart: boolean) => {
    const entries = [...videoRefs.current.entries()]
    const attempts = entries.map(async ([videoId, video]) => {
      video.muted = true
      if (restart) {
        try { video.currentTime = 0 } catch { /* metadata may not be available */ }
      }
      try {
        await video.play()
      } catch {
        setState(videoId, 'error')
        setVideoErrors((current) => ({ ...current, [videoId]: '파일 형식 또는 브라우저 코덱 지원을 확인하세요.' }))
      }
    })
    await Promise.allSettled(attempts)
  }

  const changePage = (nextPage: number) => {
    pauseVideos()
    setPage(nextPage)
  }

  if (loading) return <div className="drop-video-state"><LoaderCircle className="spin" /><strong>낙하 영상 목록을 준비하고 있습니다.</strong></div>
  if (requestError) return <div className="drop-video-state error"><AlertTriangle /><strong>영상 목록을 불러오지 못했습니다.</strong><p>{requestError}</p><button onClick={() => setReloadToken((value) => value + 1)}>다시 시도</button></div>
  if (!result || result.videos.length === 0) return <div className="drop-video-state"><Play /><strong>이 하중 경우에 연결된 예제 영상이 없습니다.</strong><p>예제 영상은 지정된 데모 낙하 하중 경우에서만 표시됩니다.</p></div>

  const selectedVideo = result.videos.find((video) => video.video_id === selectedVideoId) ?? result.videos[0]

  return <section className="drop-video-dashboard" data-testid="drop-video-grid">
    <header className="drop-video-controls">
      <div><span>DROP VIDEO COMPARISON</span><strong>{result.load_case.load_case_name}</strong><small>{result.evaluation_source.replace('_', ' ')} · CONTRACT v{result.contract_version} · {result.pagination.total_items}개 영상</small></div>
      <nav aria-label="영상 일괄 재생 제어">
        <button onClick={() => void playVideos(false)}><Play /> 전체 재생</button>
        <button onClick={() => pauseVideos()}><Pause /> 전체 정지</button>
        <button onClick={() => void playVideos(true)}><RotateCcw /> 처음부터</button>
        <button className={loop ? 'active' : ''} aria-pressed={loop} onClick={() => setLoop((value) => !value)}><Repeat2 /> 반복 {loop ? '켜짐' : '꺼짐'}</button>
      </nav>
    </header>
    <div className="drop-video-layout">
      <DropVideoSummary result={result} selected={selectedVideo} onSelect={setSelectedVideoId} />
      <div className={`drop-video-grid drop-video-grid-${Math.min(4, result.videos.length)}`} aria-label="낙하 영상 Scene 목록">
        {result.videos.map((video) => <DropVideoCard
          key={video.video_id}
          video={video}
          selected={video.video_id === selectedVideo.video_id}
          loop={loop}
          state={playback[video.video_id] ?? 'loading'}
          error={videoErrors[video.video_id]}
          setRef={(element) => element ? videoRefs.current.set(video.video_id, element) : videoRefs.current.delete(video.video_id)}
          onSelect={() => setSelectedVideoId(video.video_id)}
          onState={(state) => setState(video.video_id, state)}
          onError={(message) => setVideoErrors((current) => ({ ...current, [video.video_id]: message }))}
        />)}
      </div>
    </div>
    <footer className="drop-video-pagination">
      <button disabled={!result.pagination.has_previous} onClick={() => changePage(page - 1)}><ChevronLeft /> 이전</button>
      <span><strong>{result.pagination.page}</strong> / {result.pagination.total_pages} 페이지</span>
      <button disabled={!result.pagination.has_next} onClick={() => changePage(page + 1)}>다음 <ChevronRight /></button>
    </footer>
  </section>
}

function DropVideoSummary({ result, selected, onSelect }: { result: DropVideoPage; selected: DropVideoItem; onSelect: (videoId: string) => void }) {
  return <aside className="drop-video-summary" data-testid="drop-video-summary">
    <header><span>SYNTHETIC EVALUATION</span><strong>{result.summary.total_scenes} Scene 판정 요약</strong><small>실제 Solver 결과가 아닌 고정 데모 평가값입니다.</small></header>
    <div className="drop-video-summary-counts">
      <div><span>TOTAL</span><strong>{result.summary.total_scenes}</strong></div>
      <div className="pass"><span>PASS</span><strong>{result.summary.pass_count}</strong></div>
      <div className="fail"><span>FAIL</span><strong>{result.summary.fail_count}</strong></div>
    </div>
    <div className="drop-video-summary-limits">
      <p><span>Open Cell</span><strong>{result.summary.open_cell.threshold} {result.summary.open_cell.unit}</strong><small>초과 시 FAIL</small></p>
      <p><span>Chassis Rear</span><strong>{result.summary.chassis_rear.threshold} {result.summary.chassis_rear.unit}</strong><small>이상 시 FAIL</small></p>
    </div>
    <section className="drop-video-scene-chart" aria-label="20 Scene compact verdict graph">
      <header><span>SCENE</span><span>OPEN CELL / CHASSIS</span><span>판정</span></header>
      {result.videos.map((video) => <button key={video.video_id} className={`${video.video_id === selected.video_id ? 'selected' : ''} evaluation-${video.evaluation.overall_verdict.toLowerCase()}`} onClick={() => onSelect(video.video_id)} aria-pressed={video.video_id === selected.video_id}>
        <span>{String(video.sort_order).padStart(2, '0')}</span>
        <i><b style={metricBarStyle(video.evaluation.open_cell)} /><b style={metricBarStyle(video.evaluation.chassis_rear)} /></i>
        <strong>{video.evaluation.overall_verdict}</strong>
      </button>)}
    </section>
    <section className={`drop-video-selected evaluation-${selected.evaluation.overall_verdict.toLowerCase()}`} aria-live="polite">
      <header><span>SELECTED SCENE {String(selected.sort_order).padStart(2, '0')}</span><strong>{selected.scene_name}</strong><b>{selected.evaluation.overall_verdict}</b></header>
      <SelectedMetric title="Open Cell" metric={selected.evaluation.open_cell} />
      <SelectedMetric title="Chassis Rear" metric={selected.evaluation.chassis_rear} />
    </section>
  </aside>
}

function SelectedMetric({ title, metric }: { title: string; metric: DropVideoSubsystemEvaluation }) {
  return <div className="drop-video-selected-metric">
    <header><span>{title}</span><strong>{metric.critical_value.toFixed(1)} / {metric.threshold.toFixed(1)} {metric.unit}</strong><b className={metric.verdict.toLowerCase()}>{metric.verdict}</b></header>
    <div>{Object.entries(metric.metrics).map(([key, value]) => <p key={key}><span>{METRIC_LABEL[key] ?? key}</span><strong>{value.toFixed(1)} {metric.unit}</strong></p>)}</div>
  </div>
}

function MiniMetricBar({ label, metric }: { label: string; metric: DropVideoSubsystemEvaluation }) {
  return <div className={`drop-video-mini-bar ${metric.verdict.toLowerCase()}`} aria-label={`${label} ${metric.critical_value} ${metric.unit}, 판정 ${metric.verdict}`}>
    <header><span>{label}</span><strong>{metric.critical_value.toFixed(1)} / {metric.threshold.toFixed(1)} {metric.unit}</strong><b>{metric.verdict}</b></header>
    <i style={metricBarStyle(metric)}><span /></i>
  </div>
}

function DropVideoCard({ video, selected, loop, state, error, setRef, onSelect, onState, onError }: {
  video: DropVideoItem
  selected: boolean
  loop: boolean
  state: PlaybackState
  error?: string
  setRef: (element: HTMLVideoElement | null) => void
  onSelect: () => void
  onState: (state: PlaybackState) => void
  onError: (message: string) => void
}) {
  const handleKeyboard = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    onSelect()
  }
  return <article
    className={`drop-video-card evaluation-${video.evaluation.overall_verdict.toLowerCase()} ${state === 'error' ? 'media-error' : ''} ${selected ? 'selected' : ''}`}
    tabIndex={0}
    aria-label={`Scene ${String(video.sort_order).padStart(2, '0')} ${video.scene_name}, 평가 ${video.evaluation.overall_verdict}`}
    aria-current={selected ? 'true' : undefined}
    onClick={onSelect}
    onKeyDown={handleKeyboard}
  >
    <header><span>{String(video.sort_order).padStart(2, '0')}</span><div><strong>{video.scene_name}</strong><small>{video.scene_id}</small></div><b className={`drop-video-evaluation-badge ${video.evaluation.overall_verdict.toLowerCase()}`}>{video.evaluation.overall_verdict}</b><b className={state}>{PLAYBACK_LABEL[state]}</b></header>
    <div className="drop-video-frame">
      <video
        ref={setRef}
        aria-label={`${video.scene_name} 영상`}
        controls
        loop={loop}
        muted
        playsInline
        preload="metadata"
        poster={video.thumbnail_url ?? undefined}
        src={video.video_url}
        onCanPlay={() => onState('ready')}
        onPlaying={() => onState('playing')}
        onPause={() => onState('paused')}
        onEnded={() => onState('ended')}
        onError={() => {
          onState('error')
          onError('파일 형식 또는 브라우저 코덱 지원을 확인하세요.')
        }}
      />
      {state === 'error' && <div className="drop-video-error"><AlertTriangle /><strong>영상을 재생할 수 없습니다.</strong><small>{error ?? '파일 또는 브라우저 코덱 지원을 확인하세요.'}</small></div>}
    </div>
    <div className="drop-video-mini-evaluations">
      <MiniMetricBar label="OPEN CELL" metric={video.evaluation.open_cell} />
      <MiniMetricBar label="CHASSIS" metric={video.evaluation.chassis_rear} />
    </div>
    <footer><span>{video.format.toUpperCase()} · {video.codec?.toUpperCase() ?? '코덱 미확인'}{video.fast_start ? ' · FAST START' : ''}</span><span>{fileSizeLabel(video.file_size)}</span>{video.drop_direction && <span>{video.drop_direction}</span>}{video.drop_condition && <span>{video.drop_condition}</span>}{video.analysis_version && <span>{video.analysis_version}</span>}</footer>
  </article>
}
