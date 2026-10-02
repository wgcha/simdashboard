import { useEffect, useRef } from 'react'

import { folderEnvironmentApi, type FolderDiscoveryResult } from '../api/folderEnvironment'
import { ApiError } from '../api/errors'

export const FOLDER_DISCOVERY_INTERVAL_MS = 60_000
// The first check waits until the workspace has loaded so a registration run
// never competes with the initial screen requests.
export const FOLDER_DISCOVERY_INITIAL_DELAY_MS = 15_000

type DiscoverCall = (body?: { force?: boolean }, signal?: AbortSignal) => Promise<FolderDiscoveryResult>

export type FolderDiscoveryOptions<P, R> = {
  enabled: boolean
  /** Only global administrators receive needs_review items; others never see that notice. */
  isAdmin: boolean
  /** The user's current project; only its request list is re-read, and the selection is never changed. */
  selectedProjectId: string
  loadProjects: () => Promise<P[]>
  loadRequests: (projectId: string) => Promise<R[]>
  setProjects: (projects: P[]) => void
  setRequests: (requests: R[]) => void
  onNotice: (message: string) => void
  intervalMs?: number
  initialDelayMs?: number
  /** Injectable for tests. */
  discover?: DiscoverCall
}

function pageVisible() {
  return typeof document === 'undefined' || document.visibilityState === 'visible'
}

/**
 * Lets the server register new SPDM project/request folders: one call when the
 * app starts, then every `intervalMs` while the page is visible. Calls never
 * overlap. When something new was registered only the project list and the
 * selected project's request list are re-read; the selection stays as it is.
 * Polling stops (until reload) after a 401/403. A created request is announced
 * once, and only after the lists were re-read successfully.
 */
export function useFolderDiscovery<P, R>(options: FolderDiscoveryOptions<P, R>) {
  const { enabled, intervalMs = FOLDER_DISCOVERY_INTERVAL_MS, initialDelayMs = FOLDER_DISCOVERY_INITIAL_DELAY_MS } = options
  const latest = useRef(options)
  latest.current = options
  const seenRequests = useRef(new Set<string>())
  const reviewKey = useRef('')
  const denied = useRef(false)

  useEffect(() => {
    if (!enabled || denied.current) return
    let inFlight: AbortController | null = null
    let lastRun: number | null = null
    let disposed = false
    let timer = 0
    let firstTimer = 0
    const stop = () => { window.clearTimeout(firstTimer); window.clearInterval(timer); document.removeEventListener('visibilitychange', onVisibility) }
    const refreshLists = async () => {
      const current = latest.current
      const projectId = current.selectedProjectId
      const [projects, requests] = await Promise.all([current.loadProjects(), projectId ? current.loadRequests(projectId) : Promise.resolve(null)])
      if (disposed) return
      current.setProjects(projects)
      // Ignore the request list if the user switched project meanwhile.
      if (requests && latest.current.selectedProjectId === projectId) current.setRequests(requests)
    }
    const run = () => {
      if (inFlight || disposed || !pageVisible()) return
      const discover = latest.current.discover ?? folderEnvironmentApi.discover
      const controller = new AbortController()
      inFlight = controller
      lastRun = Date.now()
      discover({}, controller.signal).then(async (result) => {
        if (disposed || controller.signal.aborted) return
        const fresh = result.created_requests.filter((item) => !seenRequests.current.has(item.id))
        const messages: string[] = []
        if (fresh.length || result.created_projects.length) {
          await refreshLists()
          fresh.forEach((item) => seenRequests.current.add(item.id))
          if (fresh.length) messages.push(`새 의뢰 ${fresh.length}건 확인`)
        }
        const nextReviewKey = latest.current.isAdmin ? result.needs_review.map((item) => `${item.relative_path}\u0000${item.code ?? ''}`).sort().join('\n') : ''
        if (nextReviewKey && nextReviewKey !== reviewKey.current) messages.push(`확인 필요 폴더 ${result.needs_review.length}건: ${result.needs_review[0].reason}`)
        reviewKey.current = nextReviewKey
        if (messages.length) latest.current.onNotice(messages.join(' · '))
      }).catch((reason: unknown) => {
        if (reason instanceof ApiError && (reason.status === 401 || reason.status === 403)) { denied.current = true; stop() }
        // Otherwise best-effort background work; the next poll retries.
      }).finally(() => { if (inFlight === controller) inFlight = null })
    }
    const onVisibility = () => { if (pageVisible() && (lastRun === null || Date.now() - lastRun >= intervalMs)) run() }
    firstTimer = window.setTimeout(() => {
      timer = window.setInterval(run, intervalMs)
      document.addEventListener('visibilitychange', onVisibility)
      run()
    }, initialDelayMs)
    return () => {
      disposed = true
      stop()
      inFlight?.abort()
    }
  }, [enabled, initialDelayMs, intervalMs])
}
