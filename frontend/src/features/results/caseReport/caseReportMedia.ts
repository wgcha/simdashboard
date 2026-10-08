/**
 * Browser-side still frame of a video (first frame drawn on a canvas, PNG).
 * Used as the PPTX poster of embedded videos and as the still image of an
 * animated contour when videos are not included. Fails soft (null): unknown
 * codecs, broken files and slow decoders (timeout) simply give no frame.
 */
const FRAME_TIMEOUT_MS = 8000
const FRAME_MAX_WIDTH = 1920

export type VideoFrame = { dataUri: string; width: number; height: number }

export async function captureFirstFrame(blob: Blob, options: { signal?: AbortSignal; timeoutMs?: number } = {}): Promise<VideoFrame | null> {
  if (typeof document === 'undefined' || options.signal?.aborted) return null
  const url = URL.createObjectURL(blob)
  const video = document.createElement('video')
  video.muted = true
  video.playsInline = true
  video.preload = 'auto'
  let timer = 0
  const cleanup = () => { window.clearTimeout(timer); video.removeAttribute('src'); video.load(); URL.revokeObjectURL(url) }
  try {
    await new Promise<void>((resolve, reject) => {
      timer = window.setTimeout(() => reject(new Error('timeout')), options.timeoutMs ?? FRAME_TIMEOUT_MS)
      options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
      video.onerror = () => reject(new Error('decode'))
      video.onloadeddata = () => {
        // A tiny seek makes browsers paint the first decoded frame reliably.
        video.onseeked = () => resolve()
        try { video.currentTime = Math.min(0.001, Number.isFinite(video.duration) ? video.duration / 2 : 0.001) } catch { resolve() }
      }
      video.src = url
    })
    const sourceWidth = video.videoWidth
    const sourceHeight = video.videoHeight
    if (!sourceWidth || !sourceHeight) return null
    const scale = Math.min(1, FRAME_MAX_WIDTH / sourceWidth)
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(sourceWidth * scale)
    canvas.height = Math.round(sourceHeight * scale)
    const context = canvas.getContext('2d')
    if (!context) return null
    context.drawImage(video, 0, 0, canvas.width, canvas.height)
    return { dataUri: canvas.toDataURL('image/png'), width: canvas.width, height: canvas.height }
  } catch (reason) {
    if (options.signal?.aborted) throw reason
    return null
  } finally {
    cleanup()
  }
}
