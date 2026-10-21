// The version this browser last saw on the 버전 게시판 (per-viewer convenience; storage may be unavailable).
export const LAST_SEEN_KEY = 'simdash.lastSeenVersion'
export const LAST_SEEN_EVENT = 'simdash:last-seen-version'

export function readLastSeen(): string | null {
  try { return window.localStorage.getItem(LAST_SEEN_KEY) } catch { return null }
}

export function writeLastSeen(version: string): void {
  try { window.localStorage.setItem(LAST_SEEN_KEY, version) } catch { /* private mode: the dot simply stays */ }
  window.dispatchEvent(new Event(LAST_SEEN_EVENT))
}
