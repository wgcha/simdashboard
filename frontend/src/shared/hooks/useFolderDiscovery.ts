import { useEffect, useRef } from 'react'

import { folderEnvironmentApi, type FolderDiscoveryResult } from '../api/folderEnvironment'

export const FOLDER_DISCOVERY_INTERVAL_MS = 60_000

type DiscoverCall = (body?: { force?: boolean }, signal?: AbortSignal) => Promise<FolderDiscoveryResult>

export type FolderDiscoveryOptions = {
  enabled: boolean
  /** Only global administrators receive needs_review items; others never see that notice. */
  isAdmin: boolean
  /** Re-read projects/requests after the server registered something new. */
  onCreated: () => void | Promise<void>
  onNotice: (message: string) => void
  intervalMs?: number
  /** Injectable for tests. */
  discover?: DiscoverCall
}

function pageVisible() {
  return typeof document === 'undefined' || document.visibilityState === 'visible'
}

/**
 * Lets the server register new SPDM project/request folders: one call when the
 * app starts, then every `intervalMs` while the page is visible. Calls never
 * overlap. A coalesced server answer may repeat the last result, so each
 * created request is announced once.
 */
export function useFolderDiscovery({ enabled, isAdmin, onCreated, onNotice, intervalMs = FOLDER_DISCOVERY_INTERVAL_MS, discover = folderEnvironmentApi.discover }: FolderDiscoveryOptions) {
  const callbacks = useRef({ onCreated, onNotice, discover, isAdmin })
  callbacks.current = { onCreated, onNotice, discover, isAdmin }
  const seenRequests = useRef(new Set<string>())
  const reviewKey = useRef('')

  useEffect(() => {
    if (!enabled) return
    let inFlight: AbortController | null = null
    let lastRun: number | null = null
    let disposed = false
    const run = () => {
      if (inFlight || !pageVisible()) return
      const controller = new AbortController()
      inFlight = controller
      lastRun = Date.now()
      callbacks.current.discover({}, controller.signal).then(async (result) => {
        if (disposed || controller.signal.aborted) return
        const fresh = result.created_requests.filter((item) => !seenRequests.current.has(item.id))
        fresh.forEach((item) => seenRequests.current.add(item.id))
        if (fresh.length || result.created_projects.length) {
          await callbacks.current.onCreated()
          if (fresh.length) callbacks.current.onNotice(`새 의뢰 ${fresh.length}건 확인`)
        }
        const nextReviewKey = callbacks.current.isAdmin ? result.needs_review.map((item) => item.relative_path).sort().join('\n') : ''
        if (nextReviewKey && nextReviewKey !== reviewKey.current && !fresh.length) {
          callbacks.current.onNotice(`확인 필요 폴더 ${result.needs_review.length}건: ${result.needs_review[0].reason}`)
        }
        reviewKey.current = nextReviewKey
      }).catch(() => {
        // Discovery is best-effort background work; the next poll retries.
      }).finally(() => { if (inFlight === controller) inFlight = null })
    }
    run()
    const timer = window.setInterval(run, intervalMs)
    const onVisibility = () => { if (pageVisible() && (lastRun === null || Date.now() - lastRun >= intervalMs)) run() }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      disposed = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibility)
      inFlight?.abort()
    }
  }, [enabled, intervalMs])
}
