/** Typed client for the per-machine local execution helper.
 *
 * This client intentionally does not use the main API client or login token.
 * The helper receives only a short-lived account/device session and listens on
 * the user's loopback interface.
 */

export type LocalProgram = {
  id: string
  name: string
  version: string
  keywords: string[]
  executable_path: string
  arguments: string[]
  host_id: string
  available: boolean
}

export type LocalProgramInput = Omit<LocalProgram, 'id' | 'host_id' | 'available'> & { available?: boolean }

export type LocalProgramCandidate = Omit<LocalProgram, 'id' | 'host_id' | 'available'>

export type LocalRunnerHealth = { host_id: string; host_name: string; platform: string; binding_id?: string | null; user_id?: string | null; sync_pending?: number; sync_error?: string | null }
export type PickerKind = 'program' | 'files' | 'directory'

export type LocalRunMode = 'DIRECT' | 'BATCH'
export type LocalRunStatus = 'QUEUED' | 'RUNNING' | 'AWAITING_COMPLETION' | 'SUCCEEDED' | 'FAILED' | 'COMPLETED' | 'INTERRUPTED'
export type LocalRunItem = { input_path: string; working_directory: string }
export type LocalRunContext = { request_id: string; work_item_id: string; task_name: string; actor: string }

export type LocalRun = {
  id: string
  source_run_id?: string | null
  batch_id?: string | null
  mode: LocalRunMode
  status: LocalRunStatus
  program_name: string
  program_version: string
  input_path: string
  working_directory: string
  created_at: string
  started_at?: string | null
  completed_at?: string | null
  exit_code?: number | null
  note?: string | null
  error?: string | null
  context: LocalRunContext
  program_snapshot: LocalProgramCandidate & { id?: string; host_id?: string; available?: boolean }
}

export type LocalBatch = { id: string; name: string; program_id: string; items: LocalRunItem[] }

export type LocalRunRequest = {
  program_id: string
  mode: LocalRunMode
  items: LocalRunItem[]
  context: LocalRunContext
  idempotency_key: string
}

export type LocalBatchRequest = { name: string; program_id: string; items: LocalRunItem[] }

const LOCAL_RUNNER_ORIGIN = 'http://127.0.0.1:8766/v1'

export class LocalRunnerError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message)
    this.name = 'LocalRunnerError'
  }
}

function errorMessage(value: unknown, fallback: string) {
  if (typeof value === 'string' && value) return value
  if (value && typeof value === 'object') {
    const item = value as Record<string, unknown>
    if (typeof item.detail === 'string') return item.detail
    if (typeof item.message === 'string') return item.message
  }
  return fallback
}

async function request<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  let response: Response
  try {
    response = await fetch(`${LOCAL_RUNNER_ORIGIN}${path}`, { ...init, headers, credentials: 'omit' })
  } catch {
    throw new LocalRunnerError('로컬 실행 도우미에 연결할 수 없습니다. 도우미가 127.0.0.1:8766에서 실행 중인지 확인하세요.')
  }
  const raw = await response.text()
  let body: unknown = undefined
  try { body = raw ? JSON.parse(raw) : undefined } catch { body = raw }
  if (!response.ok) throw new LocalRunnerError(errorMessage(body, `로컬 실행 요청 실패 (${response.status})`), response.status)
  return body as T
}

const json = (value: unknown) => JSON.stringify(value)

export const localRunnerApi = {
  health: (token: string, signal?: AbortSignal) => request<LocalRunnerHealth>('/health', token, { signal }),
  programs: (token: string, query = '', signal?: AbortSignal) => request<LocalProgram[]>(`/programs${query ? `?q=${encodeURIComponent(query)}` : ''}`, token, { signal }),
  registerProgram: (token: string, input: LocalProgramInput, signal?: AbortSignal) => request<LocalProgram>('/programs', token, { method: 'POST', body: json(input), signal }),
  updateProgram: (token: string, id: string, input: LocalProgramInput, signal?: AbortSignal) => request<LocalProgram>(`/programs/${encodeURIComponent(id)}`, token, { method: 'PUT', body: json(input), signal }),
  removeProgram: (token: string, id: string, signal?: AbortSignal) => request<{ ok: true }>(`/programs/${encodeURIComponent(id)}`, token, { method: 'DELETE', signal }),
  discover: (token: string, signal?: AbortSignal) => request<LocalProgramCandidate[]>('/discover', token, { method: 'POST', body: json({}), signal }),
  pick: (token: string, kind: PickerKind, signal?: AbortSignal) => request<{ paths: string[] }>('/pick', token, { method: 'POST', body: json({ kind }), signal }),
  createRuns: (token: string, input: LocalRunRequest, signal?: AbortSignal) => request<LocalRun[]>('/runs', token, { method: 'POST', body: json(input), signal }),
  retryRun: (token: string, id: string, idempotencyKey: string, signal?: AbortSignal) => request<LocalRun[]>(`/runs/${encodeURIComponent(id)}/retry`, token, { method: 'POST', body: json({ idempotency_key: idempotencyKey }), signal }),
  runs: (token: string, context: Pick<LocalRunContext, 'request_id' | 'work_item_id'>, signal?: AbortSignal) => {
    const query = new URLSearchParams({ request_id: context.request_id, work_item_id: context.work_item_id })
    return request<LocalRun[]>(`/runs?${query.toString()}`, token, { signal })
  },
  complete: (token: string, id: string, note: string, signal?: AbortSignal) => request<LocalRun>(`/runs/${encodeURIComponent(id)}/complete`, token, { method: 'POST', body: json({ note }), signal }),
  batches: (token: string, signal?: AbortSignal) => request<LocalBatch[]>('/batches', token, { signal }),
  saveBatch: (token: string, input: LocalBatchRequest, signal?: AbortSignal) => request<LocalBatch>('/batches', token, { method: 'POST', body: json(input), signal }),
}

export function localRunnerPairingKey(userId: string) {
  return `local-runner:pairing:${userId}`
}

export function newIdempotencyKey() {
  return `local-${crypto.randomUUID()}`
}
