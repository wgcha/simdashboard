import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { AlertTriangle, ChevronLeft, ChevronRight, LoaderCircle, Pause, Play, Repeat2, RotateCcw } from 'lucide-react'
import { api } from '../../api'
import type { DropVideoPage } from '../../types'
import './DropVideoGrid.css'
import { DropVideoCard, DropVideoSummary, type PlaybackState } from './DropVideoParts'
import { normalizeVideoGridLayout } from './videoGridLayout'

export function DropVideoGrid({ loadCaseId, pageSize = 20, columns = 2, rows = 2 }: { loadCaseId: string; pageSize?: number; columns?: number; rows?: number }) {
  const layout = normalizeVideoGridLayout(columns, rows)
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
    const outgoingIds = new Set(videoRefs.current.keys())
    setPlayback((current) => Object.fromEntries(Object.entries(current).map(([id, state]) => [id, outgoingIds.has(id) && state === 'playing' ? 'paused' : state])))
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
    pauseVideos()
  }, [layout.pageSize])

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
        if (controller.signal.aborted) return
        setResult(payload)
        setPlayback(Object.fromEntries(payload.videos.map((video) => [video.video_id, 'loading'])))
        setSelectedVideoId(payload.videos[0]?.video_id ?? '')
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

  const selectVideo = (videoId: string) => {
    setSelectedVideoId(videoId)
    const selectedIndex = result?.videos.findIndex((video) => video.video_id === videoId) ?? -1
    if (selectedIndex >= 0) {
      const currentLocalPage = Math.floor((result?.videos.findIndex((video) => video.video_id === selectedVideoId) ?? 0) / layout.pageSize) + 1
      const nextLocalPage = Math.floor(selectedIndex / layout.pageSize) + 1
      if (nextLocalPage !== currentLocalPage) pauseVideos()
    }
  }

  const changeLocalCardPage = (nextPage: number) => {
    pauseVideos()
    const nextVideo = result?.videos[(nextPage - 1) * layout.pageSize]
    if (nextVideo) setSelectedVideoId(nextVideo.video_id)
  }

  if (loading) return <div className="drop-video-state"><LoaderCircle className="spin" /><strong>낙하 영상 목록을 준비하고 있습니다.</strong></div>
  if (requestError) return <div className="drop-video-state error"><AlertTriangle /><strong>영상 목록을 불러오지 못했습니다.</strong><p>{requestError}</p><button onClick={() => setReloadToken((value) => value + 1)}>다시 시도</button></div>
  if (!result || result.videos.length === 0) return <div className="drop-video-state"><Play /><strong>이 하중 경우에 연결된 예제 영상이 없습니다.</strong><p>예제 영상은 지정된 데모 낙하 하중 경우에서만 표시됩니다.</p></div>

  const selectedVideo = result.videos.find((video) => video.video_id === selectedVideoId) ?? result.videos[0]
  const selectedIndex = Math.max(0, result.videos.findIndex((video) => video.video_id === selectedVideo.video_id))
  const localCardPage = Math.floor(selectedIndex / layout.pageSize) + 1
  const localCardTotalPages = Math.max(1, Math.ceil(result.videos.length / layout.pageSize))
  const localCardStart = (localCardPage - 1) * layout.pageSize
  const visibleVideos = result.videos.slice(localCardStart, localCardStart + layout.pageSize)

  return <section className="drop-video-dashboard" data-testid="drop-video-grid" data-video-columns={layout.columns} data-video-rows={layout.rows} data-video-dense={layout.rows > 2 ? 'true' : 'false'} style={{ '--video-columns': layout.columns, '--video-rows': layout.rows } as CSSProperties}>
    <header className="drop-video-controls">
      <div><strong>{result.load_case.load_case_name}</strong><small>{result.evaluation_source === 'SYNTHETIC_DEMO' ? '합성 예제 · 실제 해석 결과 아님' : result.evaluation_source} · 전체 {result.pagination.total_items}개 영상</small></div>
      <nav aria-label="영상 일괄 재생 제어">
        <button onClick={() => void playVideos(false)}><Play /> 표시 영상 전체 재생</button>
        <button onClick={() => pauseVideos()}><Pause /> 표시 영상 전체 정지</button>
        <button onClick={() => void playVideos(true)}><RotateCcw /> 표시 영상 처음부터</button>
        <button className={loop ? 'active' : ''} aria-pressed={loop} onClick={() => setLoop((value) => !value)}><Repeat2 /> 반복 {loop ? '켜짐' : '꺼짐'}</button>
      </nav>
    </header>
    <div className="drop-video-layout">
      <DropVideoSummary result={result} selected={selectedVideo} onSelect={selectVideo} />
      <div className="drop-video-media-panel">
        <div className="drop-video-media-heading">
          <strong>현재 표시 영상</strong>
          <span>{localCardStart + 1}–{Math.min(localCardStart + layout.pageSize, result.videos.length)} / {result.videos.length} · 배치 {layout.columns}×{layout.rows} · 목록 {result.pagination.page} / {result.pagination.total_pages}</span>
        </div>
        <div className={`drop-video-grid drop-video-grid-${Math.min(4, visibleVideos.length)}`} aria-label="낙하 영상 Scene 목록">
        {visibleVideos.map((video) => <DropVideoCard
          key={video.video_id}
          video={video}
          selected={video.video_id === selectedVideo.video_id}
          loop={loop}
          state={playback[video.video_id] ?? 'loading'}
          error={videoErrors[video.video_id]}
          setRef={(element) => element ? videoRefs.current.set(video.video_id, element) : videoRefs.current.delete(video.video_id)}
          onSelect={() => selectVideo(video.video_id)}
          onState={(state) => setState(video.video_id, state)}
          onError={(message) => setVideoErrors((current) => ({ ...current, [video.video_id]: message }))}
        />)}
        </div>
        {localCardTotalPages > 1 && <nav className="drop-video-local-pagination" aria-label="현재 서버 페이지 영상 이동">
          <button disabled={localCardPage <= 1} onClick={() => changeLocalCardPage(localCardPage - 1)}><ChevronLeft /> 이전 영상</button>
          <span><strong>{localCardPage}</strong> / {localCardTotalPages}</span>
          <button disabled={localCardPage >= localCardTotalPages} onClick={() => changeLocalCardPage(localCardPage + 1)}>다음 영상 <ChevronRight /></button>
        </nav>}
      </div>
    </div>
    {result.pagination.total_pages > 1 && <footer className="drop-video-pagination">
      <button disabled={!result.pagination.has_previous} onClick={() => changePage(page - 1)}><ChevronLeft /> 이전</button>
      <span><strong>{result.pagination.page}</strong> / {result.pagination.total_pages} 페이지</span>
      <button disabled={!result.pagination.has_next} onClick={() => changePage(page + 1)}>다음 <ChevronRight /></button>
    </footer>}
  </section>
}
