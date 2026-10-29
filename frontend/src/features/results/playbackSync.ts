/**
 * 「동시 재생 맞춤」: play, pause, seek and rate of one video are mirrored to the others (shared time).
 * The mirrored calls raise the same events again; every handler only acts when the other video differs
 * (paused / time beyond the tolerance / rate), so the echo ends after one round.
 */
export type SyncableVideo = Pick<HTMLVideoElement, 'paused' | 'ended' | 'currentTime' | 'playbackRate' | 'duration' | 'play' | 'pause' | 'addEventListener' | 'removeEventListener'>

export const SYNC_TOLERANCE_SECONDS = 0.25
const EVENTS = ['play', 'pause', 'seeked', 'ratechange'] as const

function seekTo(video: SyncableVideo, time: number) {
  const target = Number.isFinite(video.duration) && video.duration > 0 ? Math.min(time, video.duration) : time
  if (Math.abs(video.currentTime - target) > SYNC_TOLERANCE_SECONDS) {
    try { video.currentTime = target } catch { /* metadata may not be available yet */ }
  }
}

function startPlaying(video: SyncableVideo) {
  if (!video.paused) return
  try { void Promise.resolve(video.play()).catch(() => undefined) } catch { /* autoplay refused */ }
}

/** Mirrors the state of whichever video the user touches to the other `videos`; returns the cleanup. */
export function attachPlaybackSync(videos: SyncableVideo[]): () => void {
  const handlers = new Map<SyncableVideo, Record<string, () => void>>()
  const others = (source: SyncableVideo) => videos.filter((video) => video !== source)
  for (const source of videos) {
    const handler = {
      play: () => others(source).forEach((video) => { seekTo(video, source.currentTime); startPlaying(video) }),
      // A video that reached its end pauses itself; the longer ones keep playing.
      pause: () => { if (!source.ended) others(source).forEach((video) => { if (!video.paused) video.pause() }) },
      seeked: () => others(source).forEach((video) => seekTo(video, source.currentTime)),
      ratechange: () => others(source).forEach((video) => { if (video.playbackRate !== source.playbackRate) video.playbackRate = source.playbackRate }),
    }
    handlers.set(source, handler)
    for (const name of EVENTS) source.addEventListener(name, handler[name])
  }
  // Turning the option on while a video plays brings the others to its time.
  const playing = videos.find((video) => !video.paused)
  if (playing) { others(playing).forEach((video) => { seekTo(video, playing.currentTime); startPlaying(video) }) }
  return () => {
    for (const [source, handler] of handlers) for (const name of EVENTS) source.removeEventListener(name, handler[name])
  }
}
