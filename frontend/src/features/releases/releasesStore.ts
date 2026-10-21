import { useEffect, useSyncExternalStore } from 'react'

import { ApiError } from '../../shared/api/errors'
import { RELEASES_POLL_MS, releasesApi } from '../../shared/api/releases'
import { deploymentKey, newVersionDeployed, type ReleasesInfo } from '../../shared/api/releasesModel'

/**
 * Server version as this browser page knows it.  Module state, not component state: the top bar is
 * re-created on every workspace route, but "the version this page was loaded with" must survive that.
 */
type Snapshot = {
  info: ReleasesInfo | null
  /** Deployment key of the first successful read after the page was loaded. */
  loadedKey: string | null
  unavailable: boolean
  /** The server runs a different deployment than the one this page was loaded with. */
  stale: boolean
}

let snapshot: Snapshot = { info: null, loadedKey: null, unavailable: false, stale: false }
const listeners = new Set<() => void>()
let inflight: Promise<void> | null = null

function publish(next: Snapshot) {
  snapshot = next
  for (const listener of listeners) listener()
}

/** Read the server once; concurrent calls share one request.  Never throws. */
export function refreshReleases(): Promise<void> {
  if (inflight) return inflight
  inflight = (async () => {
    try {
      const info = await releasesApi.get()
      if (!info) return
      const loadedKey = snapshot.loadedKey ?? deploymentKey(info)
      publish({ info, loadedKey, unavailable: false, stale: newVersionDeployed(loadedKey, info) })
    } catch (reason) {
      // Signed out / pending account: no version button. Anything else (network, restart) keeps the last value.
      if (reason instanceof ApiError && (reason.status === 401 || reason.status === 403)) publish({ ...snapshot, unavailable: true })
    } finally {
      inflight = null
    }
  })()
  return inflight
}

/** Test helper: forget what this page was loaded with. */
export function resetReleasesStore() {
  snapshot = { info: null, loadedKey: null, unavailable: false, stale: false }
  inflight = null
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function useReleasesSnapshot(): Snapshot {
  return useSyncExternalStore(subscribe, () => snapshot, () => snapshot)
}

/** Poll the server version (60 s, on focus) so a deployment is noticed without a reload; mount once in the top bar. */
export function useReleasesPolling() {
  useEffect(() => {
    void refreshReleases()
    const timer = window.setInterval(() => { if (document.visibilityState !== 'hidden') void refreshReleases() }, RELEASES_POLL_MS)
    const onFocus = () => { void refreshReleases() }
    window.addEventListener('focus', onFocus)
    return () => { window.clearInterval(timer); window.removeEventListener('focus', onFocus) }
  }, [])
}
