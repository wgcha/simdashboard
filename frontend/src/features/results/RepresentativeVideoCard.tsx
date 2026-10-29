import { AlertTriangle, Star } from 'lucide-react'
import type { KeyboardEvent } from 'react'
import { Button } from '../../shared/components/Button'
import type { PlaybackState } from './DropVideoParts'
import { isUnplayable, sourceLabel, sourceShort, unplayableText, type VideoTile } from './videoRepresentative'
import './VideoRepresentative.css'

const PLAYBACK_LABEL: Record<PlaybackState, string> = {
  loading: '로딩', ready: '재생 준비', playing: '재생 중', paused: '일시정지', ended: '재생 완료', error: '재생 불가',
}
const PLAY_ERROR = '파일 형식 또는 브라우저 코덱 지원을 확인하세요.'

export type RepresentativeVideoCardProps = {
  tile: VideoTile
  ordinal: number
  selected: boolean
  loop: boolean
  state: PlaybackState
  error?: string
  setRef: (element: HTMLVideoElement | null) => void
  onSelect: () => void
  onState: (state: PlaybackState) => void
  onError: (message: string) => void
  /** One tile per Scene (shows 「영상 N개」); false in the all-videos grid of one Scene. */
  representativeMode: boolean
  /** Opens the representative picker of this tile's Scene. */
  onChange: () => void
}

/** Video grid tile with the ★ representative mark, a 「대표 변경」 button and a 「재생 불가」 placeholder. */
export function RepresentativeVideoCard({ tile, ordinal, selected, loop, state, error, setRef, onSelect, onState, onError, representativeMode, onChange }: RepresentativeVideoCardProps) {
  const { video } = tile
  const blocked = isUnplayable(video)
  const handleKeyboard = (event: KeyboardEvent<HTMLElement>) => { if (event.target !== event.currentTarget || (event.key !== 'Enter' && event.key !== ' ')) return; event.preventDefault(); onSelect() }
  const label = tile.subtitle ? `${tile.title} · ${tile.subtitle}` : tile.title
  const unavailable = Boolean(tile.status && tile.status !== 'READY' && tile.status !== 'UNPLAYABLE')
  const shownState: PlaybackState = blocked ? 'error' : state
  return <article className={`drop-video-card video-grid-card rep-video-card${blocked ? ' is-unplayable' : ''}${shownState === 'error' ? ' media-error' : ''}${selected ? ' selected' : ''}`} tabIndex={0} aria-label={`영상 ${String(ordinal).padStart(2, '0')} ${label}`} aria-current={selected ? 'true' : undefined} onClick={onSelect} onKeyDown={handleKeyboard}>
    <header>
      <span>{String(ordinal).padStart(2, '0')}</span>
      <div><strong title={tile.title}>{tile.title}</strong>{tile.subtitle && <small title={tile.subtitle}>{tile.subtitle}</small>}</div>
      {video.is_representative && <b className="rep-video-star" title={sourceLabel(video.representative_source)}><Star aria-hidden="true" />대표</b>}
      {unavailable && <b className="drop-video-evaluation-badge">확인 필요</b>}
      <b className={shownState}>{PLAYBACK_LABEL[shownState]}</b>
    </header>
    <div className="drop-video-frame">
      {blocked
        ? <div className="rep-video-unplayable" role="note" data-testid="video-unplayable"><AlertTriangle aria-hidden="true" /><strong>{unplayableText(video)}</strong>{video.asset_id && <a href={tile.src} download={video.file_name} onClick={(event) => event.stopPropagation()}>내려받기</a>}</div>
        : <>
          <video ref={setRef} aria-label={`${label} 영상`} controls loop={loop} muted playsInline preload="metadata" src={tile.src} onCanPlay={() => onState('ready')} onPlaying={() => onState('playing')} onPause={() => onState('paused')} onEnded={() => onState('ended')} onError={() => { onState('error'); onError(PLAY_ERROR) }} />
          {state === 'error' && <div className="drop-video-error"><AlertTriangle /><strong>영상을 재생할 수 없습니다.</strong><small>{error ?? PLAY_ERROR}</small></div>}
        </>}
    </div>
    <footer className="rep-video-foot">
      <span>{[representativeMode ? `영상 ${video.scene_video_count ?? 1}개` : '', video.is_representative ? `★ ${sourceShort(video.representative_source)}` : ''].filter(Boolean).join(' · ')}</span>
      <Button size="sm" variant="ghost" onClick={(event) => { event.stopPropagation(); onChange() }} aria-label={`${tile.title} 대표 변경`}>대표 변경</Button>
    </footer>
    {!blocked && <details className="drop-video-file-details"><summary>파일 정보 · 다운로드</summary><footer><span>{tile.subtitle ?? tile.title}</span><a href={tile.src} download={video.file_name} onClick={(event) => event.stopPropagation()}>다운로드</a></footer></details>}
  </article>
}
