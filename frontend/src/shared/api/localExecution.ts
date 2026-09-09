import { apiFetch } from './auth'
import { apiUrl } from './url'
import type { LocalRun } from './localRunner'

/** The account-scoped device record returned by the central service. */
export type ManagedDevice = {
  id: string
  device_id: string
  host_name: string
  user_id: string
  created_at: string
  revoked_at?: string | null
}

export type LocalIdentity = { device_id: string; host_name: string; managed: boolean }
export type PairingGrant = { pairing_token: string; expires_at: string }
export type LocalBinding = { binding_id: string; user_id: string }
export type ManagedSession = { token: string; expires_at: string; binding_id: string; user_id: string }

export type CentralRun = LocalRun & {
  id: string
  binding_id: string
  device_id: string
  host_name: string
  actor_user_id: string
  synced_at?: string | null
}

const LOCAL_ORIGIN = 'http://127.0.0.1:8766/v1'

export class LocalExecutionError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message)
    this.name = 'LocalExecutionError'
  }
}

function messageFrom(value: unknown, fallback: string) {
  if (typeof value === 'string' && value) return value
  if (value && typeof value === 'object') {
    const body = value as Record<string, unknown>
    if (typeof body.detail === 'string') return body.detail
    if (body.detail && typeof body.detail === 'object' && 'message' in body.detail && typeof body.detail.message === 'string') return body.detail.message
    if (typeof body.message === 'string') return body.message
  }
  return fallback
}

async function readBody(response: Response) {
  const raw = await response.text()
  if (!raw) return undefined
  try { return JSON.parse(raw) as unknown } catch { return raw }
}

async function central<T>(path: string, init: RequestInit = {}) {
  let response: Response
  try { response = await apiFetch(path, { ...init, headers: { Accept: 'application/json', ...(init.headers ?? {}) } }) }
  catch (reason) { if (init.signal?.aborted) throw reason; throw new LocalExecutionError('중앙 실행 서비스를 확인할 수 없습니다.') }
  const body = await readBody(response)
  if (!response.ok) throw new LocalExecutionError(messageFrom(body, `중앙 실행 요청 실패 (${response.status})`), response.status)
  return body as T
}

async function local<T>(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  let response: Response
  try { response = await fetch(`${LOCAL_ORIGIN}${path}`, { ...init, headers, credentials: 'omit' }) }
  catch (reason) { if (init.signal?.aborted) throw reason; throw new LocalExecutionError('로컬 실행 도우미에 연결할 수 없습니다. 도우미가 실행 중인지 확인하세요.') }
  const body = await readBody(response)
  if (!response.ok) throw new LocalExecutionError(messageFrom(body, `로컬 도우미 요청 실패 (${response.status})`), response.status)
  return body as T
}

const json = (value: unknown) => JSON.stringify(value)

export const localExecutionApi = {
  identity: (signal?: AbortSignal) => local<LocalIdentity>('/identity', { signal }),
  devices: (signal?: AbortSignal) => central<ManagedDevice[]>(apiUrl('/api/local-execution/devices'), { signal }),
  createPairing: (deviceId: string, signal?: AbortSignal) => central<PairingGrant>(apiUrl('/api/local-execution/pairing'), { method: 'POST', body: json({ device_id: deviceId }), signal, headers: { 'Content-Type': 'application/json' } }),
  pairHelper: (pairingToken: string, signal?: AbortSignal) => local<LocalBinding>('/pair', { method: 'POST', body: json({ pairing_token: pairingToken }), signal }),
  session: (deviceId: string, signal?: AbortSignal) => central<ManagedSession>(apiUrl('/api/local-execution/devices/{binding_id}/session', { binding_id: deviceId }), { method: 'POST', body: json({}), signal, headers: { 'Content-Type': 'application/json' } }),
  revoke: (deviceId: string, signal?: AbortSignal) => central<{ ok: true }>(apiUrl('/api/local-execution/devices/{binding_id}/revoke', { binding_id: deviceId }), { method: 'POST', body: json({}), signal, headers: { 'Content-Type': 'application/json' } }),
  centralRuns: (requestId: string, workItemId: string, signal?: AbortSignal) => central<CentralRun[]>(apiUrl('/api/local-execution/runs', {}, { request_id: requestId, work_item_id: workItemId }), { signal }),
}

export async function localIdentity(signal?: AbortSignal) { return localExecutionApi.identity(signal) }
