// Pure helpers for the version button and 버전 게시판 (docs/features/version-board.md).
// Kept import-free for the node self-test.
export type ReleaseNote = { version: string; date: string; notes: string[] }
export type BuildInfo = { commit: string | null; builtAt: string | null; source: 'bundle' | 'git' | null }
export type ReleasesInfo = {
  current: string
  releasedAt: string | null
  build: BuildInfo
  releases: ReleaseNote[]
}

const text = (value: unknown): string => (typeof value === 'string' ? value : '')
const optionalText = (value: unknown): string | null => (typeof value === 'string' && value.trim() ? value : null)

/** Defensive parse of GET /api/releases; null when the payload carries no current version. */
export function normalizeReleases(value: unknown): ReleasesInfo | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as { version?: { current?: unknown; released_at?: unknown }; build?: { commit?: unknown; built_at?: unknown; source?: unknown }; releases?: unknown }
  const current = text(raw.version?.current).trim()
  if (!current) return null
  const source = raw.build?.source === 'bundle' || raw.build?.source === 'git' ? raw.build.source : null
  const releases = Array.isArray(raw.releases)
    ? raw.releases.flatMap((entry): ReleaseNote[] => {
      if (!entry || typeof entry !== 'object') return []
      const item = entry as { version?: unknown; date?: unknown; notes?: unknown }
      const version = text(item.version).trim()
      if (!version) return []
      return [{ version, date: text(item.date), notes: Array.isArray(item.notes) ? item.notes.filter((note): note is string => typeof note === 'string') : [] }]
    })
    : []
  return {
    current,
    releasedAt: optionalText(raw.version?.released_at),
    build: { commit: optionalText(raw.build?.commit), builtAt: optionalText(raw.build?.built_at), source },
    releases,
  }
}

/** `0.3.0` -> `v0.3.0`; `0.3.0.1` -> `v0.3.0.1`. */
export function versionLabel(version: string): string {
  return version.startsWith('v') ? version : `v${version}`
}

/**
 * What identifies one deployment: the version alone.  Every deployment changes the version number
 * (`x.y.z`, or `x.y.z.n` for a small deployment), so the commit and build date are display info only and
 * a redeploy of the same version is, by the release rule, not a new deployment.
 */
export function deploymentKey(info: Pick<ReleasesInfo, 'current'>): string {
  return info.current.trim().replace(/^v/, '')
}

/** True when the server now runs a different version than the one this page was loaded with. */
export function newVersionDeployed(loadedKey: string | null, info: Pick<ReleasesInfo, 'current'> | null): boolean {
  return loadedKey !== null && info !== null && deploymentKey(info) !== loadedKey
}

/**
 * "빌드 2026. 10. 21. · 0c09b07abcde"; a source deployment (Git) has no build time, so its date is the
 * commit date and is labelled that way.  Empty when the server knows neither.
 */
export function buildLine(build: BuildInfo, formatDate: (iso: string) => string = (iso) => iso.slice(0, 10)): string {
  const parts: string[] = []
  if (build.builtAt) parts.push(`${build.source === 'git' ? '커밋 날짜' : '빌드'} ${formatDate(build.builtAt)}`)
  if (build.commit) parts.push(build.commit)
  return parts.join(' · ')
}

/** Heading date of one release entry (`2026-10-21` is already a date; shown as is). */
export function releaseDateText(date: string): string {
  return date || '날짜 미상'
}

/** x.y.z[.n] comparison (as in PhysicsAI): positive when a > b; a missing part counts as 0 (`0.3.0.1 > 0.3.0`), non-numeric as 0. */
export function compareVersion(a: string, b: string): number {
  const pa = a.replace(/^v/, '').split('.').map((part) => Number.parseInt(part, 10) || 0)
  const pb = b.replace(/^v/, '').split('.').map((part) => Number.parseInt(part, 10) || 0)
  for (let i = 0; i < Math.max(pa.length, pb.length); i += 1) {
    const diff = (pa[i] ?? 0) - (pb[i] ?? 0)
    if (diff) return diff
  }
  return 0
}

/** Newer than the version this browser last saw on the board (nothing seen yet = new). */
export function isNewVersion(version: string, lastSeen: string | null): boolean {
  return lastSeen === null || compareVersion(version, lastSeen) > 0
}
