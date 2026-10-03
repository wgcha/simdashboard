// Server timestamps are UTC. Older responses (and caches) send naive ISO
// strings without an offset; JavaScript would read those as local time, so
// they are treated as UTC here (depth-schema.md §14.3).
const OFFSET = /(Z|[+-]\d{2}:?\d{2})$/i
const ISO_DATE_TIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/

/** Parses an API timestamp; a value without a UTC offset is read as UTC. */
export function parseServerTime(value: string | null | undefined): Date | null {
  if (!value) return null
  const text = value.trim()
  const normalized = ISO_DATE_TIME.test(text) && !OFFSET.test(text) ? `${text.replace(' ', 'T')}Z` : text
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

/** Korean local display of an API timestamp; unreadable values are shown as given. */
export function formatServerTime(value: string | null | undefined, options?: Intl.DateTimeFormatOptions, timeZone?: string): string {
  const date = parseServerTime(value)
  if (!date) return value ?? ''
  return date.toLocaleString('ko-KR', timeZone ? { ...options, timeZone } : options)
}
