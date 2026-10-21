import { apiClient, unwrapGenerated } from './client'
import { normalizeReleases, type ReleasesInfo } from './releasesModel'

/** Same cadence as the notification bell and the drive status poll. */
export const RELEASES_POLL_MS = 60_000

export const releasesApi = {
  /** Running server version, build info and RELEASES.md entries (any signed-in account). */
  get: async (signal?: AbortSignal): Promise<ReleasesInfo | null> => normalizeReleases(unwrapGenerated(await apiClient.GET('/api/releases', { signal }))),
}
