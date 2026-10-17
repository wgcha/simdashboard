// Server time policy (docs/features/server-time.md, depth-schema.md §14.3).
//
// * Server timestamps are UTC. Older responses (and caches) send naive ISO
//   strings without an offset; JavaScript would read those as local time, so
//   they are treated as UTC here.
// * "Now" is the server clock: every API response carries X-Server-Time, and
//   apiFetch feeds it to noteServerClock(). Relative times ("3분 전") use
//   serverNow(), so a PC whose clock or time zone is wrong still shows the
//   right age.
// * Absolute times are shown in the server's zone (X-Server-Timezone, else
//   its UTC offset), falling back to the browser zone before the first response.
const OFFSET = /(Z|[+-]\d{2}:?\d{2})$/i
const ISO_DATE_TIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/

export const SERVER_TIME_HEADER = 'X-Server-Time'
export const SERVER_TIMEZONE_HEADER = 'X-Server-Timezone'
export const SERVER_OFFSET_HEADER = 'X-Server-UTC-Offset-Minutes'
/** Offset samples are kept this long; older ones are dropped so a corrected server clock is followed. */
export const SERVER_CLOCK_WINDOW_MS = 10 * 60_000

type ClockSample = { at: number; offset: number }
const clock: { samples: ClockSample[]; timeZone: string | null; utcOffsetMinutes: number | null } = { samples: [], timeZone: null, utcOffsetMinutes: null }

/** Parses an API timestamp; a value without a UTC offset is read as UTC. */
export function parseServerTime(value: string | null | undefined): Date | null {
  if (!value) return null
  const text = value.trim()
  const normalized = ISO_DATE_TIME.test(text) && !OFFSET.test(text) ? `${text.replace(' ', 'T')}Z` : text
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

function validTimeZone(name: string): boolean {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: name })
    return true
  } catch {
    return false
  }
}

/**
 * Records the server clock of one response. `receivedAt` is the client time the
 * response headers arrived; the server stamped them just before, so
 * `server - receivedAt` underestimates the offset by the one-way latency only
 * (independent of how long the request ran). The largest sample in the window wins.
 */
export function noteServerClock(headers: Pick<Headers, 'get'> | null | undefined, receivedAt = Date.now()): void {
  if (!headers || typeof headers.get !== 'function') return   // test doubles may omit headers
  const stamp = parseServerTime(headers.get(SERVER_TIME_HEADER))
  if (stamp) {
    clock.samples = clock.samples.filter((sample) => receivedAt - sample.at < SERVER_CLOCK_WINDOW_MS && sample.at <= receivedAt)
    clock.samples.push({ at: receivedAt, offset: stamp.getTime() - receivedAt })
    if (clock.samples.length > 32) clock.samples.splice(0, clock.samples.length - 32)
  }
  const zone = headers.get(SERVER_TIMEZONE_HEADER)?.trim()
  if (zone && validTimeZone(zone)) clock.timeZone = zone
  const minutes = Number(headers.get(SERVER_OFFSET_HEADER))
  if (headers.get(SERVER_OFFSET_HEADER) !== null && Number.isFinite(minutes)) clock.utcOffsetMinutes = Math.trunc(minutes)
}

/** Server clock offset (server − client, ms); 0 until a response has been seen. */
export function serverClockOffsetMs(): number {
  if (!clock.samples.length) return 0
  return Math.max(...clock.samples.map((sample) => sample.offset))
}

/** Current time on the server clock (epoch ms). */
export function serverNow(): number {
  return Date.now() + serverClockOffsetMs()
}

/**
 * IANA zone used for absolute times: the server zone, else an Etc/GMT zone for
 * a whole-hour server offset, else undefined (browser zone).
 */
export function serverTimeZone(): string | undefined {
  if (clock.timeZone) return clock.timeZone
  const minutes = clock.utcOffsetMinutes
  if (minutes === null || minutes % 60 !== 0) return undefined
  const hours = minutes / 60
  if (hours === 0) return 'UTC'
  const zone = `Etc/GMT${hours > 0 ? '-' : '+'}${Math.abs(hours)}`   // Etc/GMT signs are inverted
  return validTimeZone(zone) ? zone : undefined
}

/** Test hook: forget every sample and the server zone. */
export function resetServerClock(): void {
  clock.samples = []
  clock.timeZone = null
  clock.utcOffsetMinutes = null
}

/** Korean display of an API timestamp in the server zone; unreadable values are shown as given. */
export function formatServerTime(value: string | null | undefined, options?: Intl.DateTimeFormatOptions, timeZone?: string): string {
  const date = parseServerTime(value)
  if (!date) return value ?? ''
  const zone = timeZone ?? serverTimeZone()
  return date.toLocaleString('ko-KR', zone ? { ...options, timeZone: zone } : options)
}

/** Korean date (no time) of an API timestamp in the server zone. */
export function formatServerDate(value: string | null | undefined, timeZone?: string): string {
  const date = parseServerTime(value)
  if (!date) return value ?? ''
  const zone = timeZone ?? serverTimeZone()
  return date.toLocaleDateString('ko-KR', zone ? { timeZone: zone } : undefined)
}

/** "3분 전" style age on the server clock; older than a week shows the date (server zone). */
export function relativeServerTime(value: string | null | undefined, now = serverNow()): string {
  const date = parseServerTime(value)
  if (!date) return ''
  const minutes = Math.max(0, Math.round((now - date.getTime()) / 60_000))
  if (minutes < 1) return '방금'
  if (minutes < 60) return `${minutes}분 전`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}시간 전`
  const days = Math.round(hours / 24)
  if (days < 7) return `${days}일 전`
  return formatServerDate(value)
}
