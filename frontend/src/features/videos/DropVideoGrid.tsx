import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, ChevronLeft, ChevronRight, LoaderCircle, Pause, Play, Repeat2, RotateCcw } from 'lucide-react'
import { api } from '../../api'
import type { DropVideoItem, DropVideoPage } from '../../types'

type PlaybackState = 'loading' | 'ready' | 'playing' | 'paused' | 'ended' | 'error'

const PLAYBACK_LABEL: Record<PlaybackState, string> = {
  loading: '로딩',
  ready: '재생 준비',
  playing: '재생 중',
  paused: '일시정지',
  ended: '재생 완료',
  error: '재생 불가',
}

function fileSizeLabel(bytes: number) {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function DropVideoGrid({ loadCaseId, pageSize = 20 }: { loadCaseId: string; pageSize?: number }) {
  const normalizedPageSize = Number.isFinite(pageSize) ? Math.min(20, Math.max(1, Math.round(pageSize))) : 20
  const [page, setPage] = useState(1)
  const [result, setResult] = useState<DropVideoPage | null>(null)
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
      })
      .catch((reason) => {
        if (controller.signal.aborted) return
        setResult(null)
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

  return <section className="drop-video-dashboard" data-testid="drop-video-grid">
    <header className="drop-video-controls">
      <div><span>DROP VIDEO COMPARISON</span><strong>{result.load_case.load_case_name}</strong><small>{result.demo_only ? 'DEMO ONLY · EXAMPLE ADAPTER' : result.source} · {result.pagination.total_items}개 영상</small></div>
      <nav aria-label="영상 일괄 재생 제어">
        <button onClick={() => void playVideos(false)}><Play /> 전체 재생</button>
        <button onClick={() => pauseVideos()}><Pause /> 전체 정지</button>
        <button onClick={() => void playVideos(true)}><RotateCcw /> 처음부터</button>
        <button className={loop ? 'active' : ''} aria-pressed={loop} onClick={() => setLoop((value) => !value)}><Repeat2 /> 반복 {loop ? '켜짐' : '꺼짐'}</button>
      </nav>
    </header>
    <div className="drop-video-grid">
      {result.videos.map((video) => <DropVideoCard
        key={video.video_id}
        video={video}
        loop={loop}
        state={playback[video.video_id] ?? 'loading'}
        error={videoErrors[video.video_id]}
        setRef={(element) => element ? videoRefs.current.set(video.video_id, element) : videoRefs.current.delete(video.video_id)}
        onState={(state) => setState(video.video_id, state)}
        onError={(message) => setVideoErrors((current) => ({ ...current, [video.video_id]: message }))}
      />)}
    </div>
    <footer className="drop-video-pagination">
      <button disabled={!result.pagination.has_previous} onClick={() => changePage(page - 1)}><ChevronLeft /> 이전</button>
      <span><strong>{result.pagination.page}</strong> / {result.pagination.total_pages} 페이지</span>
      <button disabled={!result.pagination.has_next} onClick={() => changePage(page + 1)}>다음 <ChevronRight /></button>
    </footer>
  </section>
}

function DropVideoCard({ video, loop, state, error, setRef, onState, onError }: {
  video: DropVideoItem
  loop: boolean
  state: PlaybackState
  error?: string
  setRef: (element: HTMLVideoElement | null) => void
  onState: (state: PlaybackState) => void
  onError: (message: string) => void
}) {
  return <article className={`drop-video-card ${state === 'error' ? 'error' : ''}`}>
    <header><span>{String(video.sort_order).padStart(2, '0')}</span><div><strong>{video.scene_name}</strong><small>{video.scene_id}</small></div><b className={state}>{PLAYBACK_LABEL[state]}</b></header>
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
    <footer><span>{video.format.toUpperCase()} · {video.codec ?? '코덱 미확인'}</span><span>{fileSizeLabel(video.file_size)}</span>{video.drop_direction && <span>{video.drop_direction}</span>}{video.drop_condition && <span>{video.drop_condition}</span>}{video.analysis_version && <span>{video.analysis_version}</span>}</footer>
  </article>
}
