import { useCallback, useEffect, useReducer } from 'react'

const MAX_ENTRIES = 64
const MAX_AGE_MS = 5 * 60 * 1000

type Entry<T> = {
  data?: T
  hasData: boolean
  error?: Error
  updatedAt: number
  inFlight?: Promise<void>
  generation: number
  staleToken: number
  maxAgeMs: number
  forbidden: boolean
  listeners: Set<() => void>
  lastAccessedAt: number
}

type QueryOptions<T> = {
  key: string
  query: () => Promise<T>
  enabled?: boolean
  /** Retention TTL for an inactive entry; mounted consumers still revalidate. */
  maxAgeMs?: number
}

type MemoryQuery<T> = {
  data: T | undefined
  error: Error | undefined
  isLoading: boolean
  isRefreshing: boolean
  isBlocked: boolean
  isForbidden: boolean
  retry: () => void
}

const entries = new Map<string, Entry<unknown>>()
let cacheGeneration = 0
let revalidationBlocked = false

function notify(entry: Entry<unknown>) {
  entry.listeners.forEach((listener) => listener())
}

function entryFor<T>(key: string): Entry<T> {
  const existing = entries.get(key) as Entry<T> | undefined
  if (existing) {
    if (!existing.inFlight && existing.listeners.size === 0 && existing.updatedAt > 0 && Date.now() - existing.updatedAt > existing.maxAgeMs) {
      entries.delete(key)
      return entryFor<T>(key)
    }
    existing.lastAccessedAt = Date.now()
    return existing
  }
  const entry: Entry<T> = { hasData: false, updatedAt: 0, generation: 0, staleToken: 0, maxAgeMs: MAX_AGE_MS, forbidden: false, listeners: new Set(), lastAccessedAt: Date.now() }
  entries.set(key, entry as Entry<unknown>)
  evict()
  return entry
}

function evict() {
  const now = Date.now()
  for (const [key, entry] of entries) {
    if (!entry.inFlight && entry.listeners.size === 0 && entry.updatedAt > 0 && now - entry.updatedAt > entry.maxAgeMs) entries.delete(key)
  }
  while (entries.size > MAX_ENTRIES) {
    const candidate = [...entries.entries()]
      .filter(([, entry]) => !entry.inFlight && entry.listeners.size === 0)
      .sort(([, left], [, right]) => left.lastAccessedAt - right.lastAccessedAt)[0]
    if (!candidate) return
    entries.delete(candidate[0])
  }
}

function load<T>(key: string, query: () => Promise<T>): Promise<void> {
  const entry = entryFor<T>(key)
  if (entry.inFlight) return entry.inFlight
  const requestGeneration = entry.generation
  const requestCacheGeneration = cacheGeneration
  entry.inFlight = query()
    .then((data) => {
      if (cacheGeneration !== requestCacheGeneration || entry.generation !== requestGeneration || entries.get(key) !== entry) return
      entry.data = data
      entry.hasData = true
      entry.error = undefined
      entry.updatedAt = Date.now()
    })
    .catch((reason: unknown) => {
      if (isForbiddenError(reason) && entries.get(key) === entry) {
        entry.forbidden = true
        entry.error = reason instanceof Error ? reason : new Error('접근 권한이 변경되었습니다.')
        entry.inFlight = undefined
        notify(entry as Entry<unknown>)
        return
      }
      if (cacheGeneration !== requestCacheGeneration || entry.generation !== requestGeneration || entries.get(key) !== entry) return
      entry.error = reason instanceof Error ? reason : new Error('데이터를 불러오지 못했습니다.')
    })
    .finally(() => {
      if (entries.get(key) === entry && entry.generation === requestGeneration) {
        entry.inFlight = undefined
        entry.lastAccessedAt = Date.now()
        notify(entry as Entry<unknown>)
        evict()
      }
    })
  notify(entry as Entry<unknown>)
  return entry.inFlight
}

function isForbiddenError(reason: unknown) {
  return typeof reason === 'object' && reason !== null && 'status' in reason && (reason as { status?: unknown }).status === 403
}

/** Removes every response and makes late in-flight responses unable to commit. */
export function clearMemoryQueryCache({ blockRevalidation = false }: { blockRevalidation?: boolean } = {}) {
  cacheGeneration += 1
  revalidationBlocked ||= blockRevalidation
  for (const entry of entries.values()) {
    entry.generation += 1
    entry.inFlight = undefined
    entry.staleToken += 1
    entry.data = undefined
    entry.hasData = false
    // A second 403 clear can arrive before `/auth/me` resumes. Keep the
    // original forbidden key blocked through that authentication cycle.
    if (!blockRevalidation) {
      entry.error = undefined
      entry.forbidden = false
    }
    entry.updatedAt = 0
    notify(entry)
  }
}

/** Re-enable explicit consumers after a successful login or user transition. */
export function resumeMemoryQueryCache() {
  if (!revalidationBlocked) return
  revalidationBlocked = false
  cacheGeneration += 1
  for (const entry of entries.values()) notify(entry)
}

/** Marks cached queries stale; their next mounted consumer revalidates. */
export function invalidateMemoryQueryCache(match: (key: string) => boolean = () => true) {
  for (const [key, entry] of entries) {
    if (!match(key)) continue
    entry.generation += 1
    entry.inFlight = undefined
    entry.staleToken += 1
    // A successful mutation makes data stale but is not an authorization
    // grant. Preserve a forbidden key and its completed-data age.
    if (!entry.forbidden) entry.error = undefined
    notify(entry)
  }
}

/**
 * Explicit opt-in stale-while-revalidate query cache. Keys must contain every
 * data scope input (filters, refresh token, user/context identity). The hook
 * never shares an old key's data while a new key is resolving.
 */
export function useMemoryQuery<T>({ key, query, enabled = true, maxAgeMs = MAX_AGE_MS }: QueryOptions<T>): MemoryQuery<T> {
  const [, rerender] = useReducer((value) => value + 1, 0)
  const retry = useCallback(() => {
    const entry = entryFor<T>(key)
    entry.generation += 1
    entry.error = undefined
    entry.inFlight = undefined
    entry.staleToken += 1
    entry.forbidden = false
    void load(key, query)
  }, [key, query])

  const current = enabled ? entryFor<T>(key) : undefined
  if (current) current.maxAgeMs = maxAgeMs
  const staleToken = current?.staleToken ?? 0
  const observedCacheGeneration = cacheGeneration

  useEffect(() => {
    if (!enabled) return
    const entry = entryFor<T>(key)
    entry.maxAgeMs = maxAgeMs
    const listener = () => rerender()
    entry.listeners.add(listener)
    // Cached data renders synchronously, then this effect always revalidates
    // on a revisit. Entries themselves expire after five minutes/LRU eviction.
    if (!revalidationBlocked && !entry.forbidden && !entry.inFlight) void load(key, query)
    return () => { entry.listeners.delete(listener) }
  }, [enabled, key, maxAgeMs, query, observedCacheGeneration, staleToken])

  const entry = enabled ? entryFor<T>(key) : undefined
  return {
    data: entry?.data,
    error: entry?.error,
    isLoading: Boolean(enabled && !revalidationBlocked && !entry?.hasData && !entry?.error),
    isRefreshing: Boolean(enabled && entry?.hasData && entry?.inFlight),
    isBlocked: Boolean(enabled && (revalidationBlocked || entry?.forbidden)),
    isForbidden: Boolean(enabled && entry?.forbidden),
    retry,
  }
}
