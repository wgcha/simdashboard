import { useCallback, useEffect, useRef, useState } from 'react'

import { folderEnvironmentApi, type FolderEnvironment, type FolderEnvironmentSync, type FolderEnvironmentSyncStatus } from '../api/folderEnvironment'

export const FOLDER_AUTO_SYNC_INTERVAL_MS = 30_000
/** Server code for folders that are still being copied. Normal; retried on the next poll. */
export const FOLDER_SCHEMA_FILE_BUSY = 'FOLDER_SCHEMA_FILE_BUSY'

type SyncCall = (body: { project_id: string; request_id: string; environment: FolderEnvironment; force?: boolean }, signal?: AbortSignal) => Promise<FolderEnvironmentSync>

export type FolderAutoSyncOptions = {
  projectId: string
  requestId: string
  environment: FolderEnvironment
  enabled?: boolean
  /** Poll period while the page is visible. Injectable for tests. */
  intervalMs?: number
  /** Injectable for tests. */
  sync?: SyncCall
}

export type FolderAutoSyncState = {
  status: FolderEnvironmentSyncStatus | null
  code: string | null
  message: string | null
  /** Client time (ms) of the last completed check in the current scope. */
  lastCheckedAt: number | null
  busy: boolean
  /** Transport/API error text of the last check; the last good data stays on screen. */
  error: string
  /** Increments only when a check reports changed=true (REFRESHED). */
  revision: number
  lastResult: FolderEnvironmentSync | null
}

const initialState = (revision: number): FolderAutoSyncState => ({ status: null, code: null, message: null, lastCheckedAt: null, busy: false, error: '', revision, lastResult: null })

function pageVisible() {
  return typeof document === 'undefined' || document.visibilityState === 'visible'
}

/**
 * Keeps the viewed request in step with its SPDM folders: one check when the
 * scope becomes active, then every `intervalMs` while the page is visible.
 * Requests never overlap; a response from a previous scope is ignored.
 */
export function useFolderAutoSync({ projectId, requestId, environment, enabled = true, intervalMs = FOLDER_AUTO_SYNC_INTERVAL_MS, sync = folderEnvironmentApi.sync }: FolderAutoSyncOptions) {
  const scopeKey = enabled && projectId && requestId ? `${projectId}\u0000${requestId}\u0000${environment}` : ''
  const [state, setState] = useState<FolderAutoSyncState>(() => initialState(0))
  const scopeRef = useRef(scopeKey)
  const inFlight = useRef<{ key: string; controller: AbortController } | null>(null)
  const forceQueued = useRef(false)
  const lastCheckedRef = useRef<number | null>(null)
  const syncRef = useRef(sync)
  syncRef.current = sync

  const run = useCallback((force: boolean) => {
    const key = scopeRef.current
    if (!key) return
    if (inFlight.current) {
      // Never overlap: a manual check waits for the running one.
      if (force) forceQueued.current = true
      return
    }
    const [project_id, request_id, scopeEnvironment] = key.split('\u0000')
    const controller = new AbortController()
    inFlight.current = { key, controller }
    setState((current) => ({ ...current, busy: true }))
    const finish = () => {
      if (inFlight.current?.controller === controller) inFlight.current = null
      if (scopeRef.current !== key) return
      if (forceQueued.current) { forceQueued.current = false; run(true) }
    }
    syncRef.current({ project_id, request_id, environment: scopeEnvironment as FolderEnvironment, ...(force ? { force: true } : {}) }, controller.signal).then((result) => {
      if (controller.signal.aborted || scopeRef.current !== key) return
      const now = Date.now()
      lastCheckedRef.current = now
      setState((current) => ({
        status: result.status, code: result.code ?? null, message: result.message ?? null, lastCheckedAt: now, busy: false, error: '',
        revision: result.changed ? current.revision + 1 : current.revision, lastResult: result,
      }))
    }).catch((reason: unknown) => {
      if (controller.signal.aborted || scopeRef.current !== key) return
      const now = Date.now()
      lastCheckedRef.current = now
      setState((current) => ({ ...current, busy: false, lastCheckedAt: current.lastCheckedAt, error: reason instanceof Error ? reason.message : '폴더 상태를 확인하지 못했습니다.' }))
    }).finally(finish)
  }, [])

  useEffect(() => {
    scopeRef.current = scopeKey
    inFlight.current?.controller.abort()
    inFlight.current = null
    forceQueued.current = false
    lastCheckedRef.current = null
    setState((current) => initialState(current.revision))
    if (!scopeKey) return
    if (pageVisible()) run(false)
    const timer = window.setInterval(() => { if (pageVisible()) run(false) }, intervalMs)
    const onVisibility = () => {
      if (!pageVisible()) return
      const last = lastCheckedRef.current
      if (last === null || Date.now() - last >= intervalMs) run(false)
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibility)
      inFlight.current?.controller.abort()
      inFlight.current = null
    }
  }, [intervalMs, run, scopeKey])

  const checkNow = useCallback(() => run(true), [run])
  return { ...state, checkNow }
}
