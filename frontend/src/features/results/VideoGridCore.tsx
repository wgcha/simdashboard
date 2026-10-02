import { Fragment, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { AlertTriangle, ChevronLeft, ChevronRight, LoaderCircle, Pause, Play, Repeat2, RotateCcw } from 'lucide-react'
import './DropVideoGrid.css'
import type { PlaybackState } from './DropVideoParts'
import { normalizeVideoGridLayout } from './videoGridLayout'

export type VideoGridPagination = { page: number; page_size: number; total_items: number; total_pages: number; has_previous: boolean; has_next: boolean }
export type VideoGridCardSlot = {
  ordinal: number
  selected: boolean
  loop: boolean
  state: PlaybackState
  error?: string
  setRef: (element: HTMLVideoElement | null) => void
  onSelect: () => void
  onState: (state: PlaybackState) => void
  onError: (message: string) => void
}

export type VideoGridCoreProps<P extends { pagination: VideoGridPagination }, T> = {
  /** Changing this key returns to the first server page (e.g. another Load Case or Run option). */
  resetKey: string
  loadPage: (page: number, pageSize: number, signal: AbortSignal) => Promise<P>
  itemsOf: (page: P) => T[]
  itemId: (item: T) => string
  renderCard: (item: T, slot: VideoGridCardSlot) => ReactNode
  renderHeading: (page: P) => ReactNode
  renderSummary?: (page: P, selected: T, onSelect: (id: string) => void) => ReactNode
  toolbar?: ReactNode
  emptyState: ReactNode
  loadingLabel: string
  gridLabel: string
  testId: string
  className?: string
  pageSize?: number
  columns?: number
  rows?: number
}

const PLAY_ERROR = '파일 형식 또는 브라우저 코덱 지원을 확인하세요.'

/** Grid, server/local paging and synchronized playback shared by every video widget. */
export function VideoGridCore<P extends { pagination: VideoGridPagination }, T>({ resetKey, loadPage, itemsOf, itemId, renderCard, renderHeading, renderSummary, toolbar, emptyState, loadingLabel, gridLabel, testId, className, pageSize = 20, columns = 2, rows = 2 }: VideoGridCoreProps<P, T>) {
  const layout = normalizeVideoGridLayout(columns, rows)
  const normalizedPageSize = Number.isFinite(pageSize) ? Math.min(20, Math.max(1, Math.round(pageSize))) : 20
  const [page, setPage] = useState(1)
  const [result, setResult] = useState<P | null>(null)
  const [selectedVideoId, setSelectedVideoId] = useState('')
  const [loading, setLoading] = useState(true)
  const [requestError, setRequestError] = useState('')
  const [loop, setLoop] = useState(false)
  const [reloadToken, setReloadToken] = useState(0)
  const [playback, setPlayback] = useState<Record<string, PlaybackState>>({})
  const [videoErrors, setVideoErrors] = useState<Record<string, string>>({})
  const videoRefs = useRef(new Map<string, HTMLVideoElement>())
  const loaderRef = useRef(loadPage)
  loaderRef.current = loadPage
  const itemsRef = useRef({ itemsOf, itemId })
  itemsRef.current = { itemsOf, itemId }

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
  }, [resetKey])

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
    loaderRef.current(page, normalizedPageSize, controller.signal)
      .then((payload) => {
        if (controller.signal.aborted) return
        const ids = itemsRef.current.itemsOf(payload).map(itemsRef.current.itemId)
        setResult(payload)
        setPlayback(Object.fromEntries(ids.map((id) => [id, 'loading'])))
        setSelectedVideoId(ids[0] ?? '')
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
  }, [resetKey, page, normalizedPageSize, reloadToken])

  const setState = (videoId: string, state: PlaybackState) => {
    setPlayback((current) => ({ ...current, [videoId]: state }))
  }

  const playVideos = async (restart: boolean) => {
    const attempts = [...videoRefs.current.entries()].map(async ([videoId, video]) => {
      video.muted = true
      if (restart) {
        try { video.currentTime = 0 } catch { /* metadata may not be available */ }
      }
      try {
        await video.play()
      } catch {
        setState(videoId, 'error')
        setVideoErrors((current) => ({ ...current, [videoId]: PLAY_ERROR }))
      }
    })
    await Promise.allSettled(attempts)
  }

  const items = result ? itemsOf(result) : []
  const indexOf = (videoId: string) => items.findIndex((item) => itemId(item) === videoId)

  const changePage = (nextPage: number) => {
    pauseVideos()
    setPage(nextPage)
  }

  const selectVideo = (videoId: string) => {
    setSelectedVideoId(videoId)
    const selectedIndex = indexOf(videoId)
    if (selectedIndex >= 0) {
      const currentLocalPage = Math.floor(Math.max(0, indexOf(selectedVideoId)) / layout.pageSize) + 1
      const nextLocalPage = Math.floor(selectedIndex / layout.pageSize) + 1
      if (nextLocalPage !== currentLocalPage) pauseVideos()
    }
  }

  const changeLocalCardPage = (nextPage: number) => {
    pauseVideos()
    const nextVideo = items[(nextPage - 1) * layout.pageSize]
    if (nextVideo) setSelectedVideoId(itemId(nextVideo))
  }

  if (loading) return <div className="drop-video-state" data-testid={`${testId}-loading`}><LoaderCircle className="spin" /><strong>{loadingLabel}</strong></div>
  if (requestError) return <div className="drop-video-state error"><AlertTriangle /><strong>영상 목록을 불러오지 못했습니다.</strong><p>{requestError}</p><button onClick={() => setReloadToken((value) => value + 1)}>다시 시도</button></div>
  if (!result || items.length === 0) return <div className="drop-video-state" data-testid={`${testId}-empty`}><Play />{emptyState}</div>

  const selectedIndex = Math.max(0, indexOf(selectedVideoId))
  const selectedVideo = items[selectedIndex]
  const localCardPage = Math.floor(selectedIndex / layout.pageSize) + 1
  const localCardTotalPages = Math.max(1, Math.ceil(items.length / layout.pageSize))
  const localCardStart = (localCardPage - 1) * layout.pageSize
  const visibleVideos = items.slice(localCardStart, localCardStart + layout.pageSize)
  const serverOffset = (result.pagination.page - 1) * result.pagination.page_size

  return <section className={`drop-video-dashboard${className ? ` ${className}` : ''}`} data-testid={testId} data-video-columns={layout.columns} data-video-rows={layout.rows} data-video-dense={layout.rows > 2 ? 'true' : 'false'} style={{ '--video-columns': layout.columns, '--video-rows': layout.rows } as CSSProperties}>
    <header className="drop-video-controls">
      <div>{renderHeading(result)}</div>
      <nav aria-label="영상 일괄 재생 제어">
        <button onClick={() => void playVideos(false)}><Play /> 표시 영상 전체 재생</button>
        <button onClick={() => pauseVideos()}><Pause /> 표시 영상 전체 정지</button>
        <button onClick={() => void playVideos(true)}><RotateCcw /> 표시 영상 처음부터</button>
        <button className={loop ? 'active' : ''} aria-pressed={loop} onClick={() => setLoop((value) => !value)}><Repeat2 /> 반복 {loop ? '켜짐' : '꺼짐'}</button>
        {toolbar}
      </nav>
    </header>
    <div className={`drop-video-layout${renderSummary ? '' : ' no-summary'}`}>
      {renderSummary?.(result, selectedVideo, selectVideo)}
      <div className="drop-video-media-panel">
        <div className="drop-video-media-heading">
          <div className="drop-video-media-heading-title"><strong>현재 표시 영상</strong><b role="status">가로 {layout.columns} × 세로 {layout.rows}</b></div>
          <div className="drop-video-media-heading-meta"><span>{localCardStart + 1}–{Math.min(localCardStart + layout.pageSize, items.length)} / {items.length} · 목록 {result.pagination.page} / {result.pagination.total_pages}</span></div>
        </div>
        <div className={`drop-video-grid drop-video-grid-${Math.min(4, visibleVideos.length)}`} aria-label={gridLabel}>
          {visibleVideos.map((video, index) => {
            const id = itemId(video)
            return <Fragment key={id}>{renderCard(video, {
              ordinal: serverOffset + localCardStart + index + 1,
              selected: id === itemId(selectedVideo),
              loop,
              state: playback[id] ?? 'loading',
              error: videoErrors[id],
              setRef: (element) => element ? videoRefs.current.set(id, element) : videoRefs.current.delete(id),
              onSelect: () => selectVideo(id),
              onState: (state) => setState(id, state),
              onError: (message) => setVideoErrors((current) => ({ ...current, [id]: message })),
            })}</Fragment>
          })}
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
