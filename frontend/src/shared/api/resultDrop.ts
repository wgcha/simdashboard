import { apiFetch } from './auth'
import { apiErrorFromResponse } from './errors'
import { apiUrl } from './url'

// W8 결과 등록: drag & drop of files or whole folders into the request's Working tree.

export type DropEnvironment = 'USAGE' | 'DISTRIBUTION'
type Scope = { project_id: string; request_id: string; environment: DropEnvironment }

export type DropTreeNode = { relative_path: string; name: string; parent_path: string; level: number; role: string; display_path: string }
export type DropTree = Scope & {
  request_relative_path: string
  working_relative_path: string
  working_display_path: string
  display_root: string
  levels: Array<{ level: number; role: string; label: string }>
  nodes: DropTreeNode[]
  truncated: boolean
  chunk_bytes: number
  blocked_extensions: string[]
  active_uploads: number
}

export type DropIssue = { code: string; severity: 'error' | 'warning'; message: string; paths: string[]; count: number }
export type DropPlanFile = { client_index: number; relative_path: string; destination_relative_path: string; size: number; role: string }
export type DropSkipped = { client_index: number; relative_path: string; size: number; reason: string }
export type DropConflict = { relative_path: string; destination_relative_path: string; reason: string }
export type DropPlan = Scope & {
  target_relative_path: string
  target_display_path: string
  target_level: number
  target_role: string
  target_role_label: string
  files: DropPlanFile[]
  folders_to_create: Array<{ relative_path: string; client_path: string; level: number; role: string; role_label: string }>
  skipped: DropSkipped[]
  conflicts: DropConflict[]
  issues: DropIssue[]
  file_count: number
  folder_count: number
  total_bytes: number
  free_bytes: number | null
  required_bytes: number
  can_upload: boolean
}
export type DropSessionFile = { index: number; client_index: number; relative_path: string; destination_relative_path: string; size: number; received: number; complete: boolean; published: boolean }
export type DropSession = Scope & {
  session_id: string
  state: 'UPLOADING' | 'PUBLISHING' | 'PUBLISHED' | 'PARTIAL' | 'ABORTED'
  target_relative_path: string
  chunk_bytes: number
  file_count: number
  total_bytes: number
  received_bytes: number
  completed_files: number
  files: DropSessionFile[]
  folders_to_create: string[]
  skipped: DropSkipped[]
  conflicts: DropConflict[]
}
export type DropCompletion = DropSession & {
  published_files: number
  published_bytes: number
  created_folders: string[]
  busy: string[]
  sync: { status?: string | null; changed?: boolean | null; code?: string | null; message?: string | null }
  cases: Array<{ case_relative_path: string; case_name: string; case_id: string | null }>
}
export type DropChunkResult = { index: number; received: number; size: number; complete: boolean; sha256: string | null }
export type DropFolderCreated = { relative_path: string; display_path: string; level: number; role: string; role_label: string; warnings: string[] }
export type DropPlanInput = Scope & { target_relative_path: string; files: Array<{ relative_path: string; size: number; sha256?: string | null }>; folders: string[] }
export type LegacyDraft = {
  draft_id: string; status: string; case_relative_path: string; result_relative_path: string; file_count: number; total_bytes: number
  case_id: string | null; capture_id: string | null; mirror_status: string | null; created_by: string | null; approved_by: string | null
  published_at: string | null; created_at: string | null; updated_at: string | null
}

async function json<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await apiFetch(url, init)
  if (!response.ok) throw await apiErrorFromResponse(response)
  return await response.json() as T
}
const post = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const resultDropApi = {
  tree(scope: Scope, signal?: AbortSignal) {
    return json<DropTree>(apiUrl('/api/result-registration/drop-target', {}, scope), { signal })
  },
  createFolder(input: Scope & { parent_relative_path: string; name: string; confirm: boolean }) {
    return json<DropFolderCreated>(apiUrl('/api/result-registration/drop-target/folders'), post(input))
  },
  plan(input: DropPlanInput, signal?: AbortSignal) {
    return json<DropPlan>(apiUrl('/api/result-registration/drop-uploads/plan'), { ...post(input), signal })
  },
  start(input: DropPlanInput) {
    return json<DropSession & { plan: DropPlan }>(apiUrl('/api/result-registration/drop-uploads'), post(input))
  },
  session(sessionId: string) {
    return json<DropSession>(apiUrl('/api/result-registration/drop-uploads/{session_id}', { session_id: sessionId }))
  },
  chunk(sessionId: string, index: number, offset: number, body: Blob | ArrayBuffer, sha256?: string | null) {
    const headers: Record<string, string> = { 'Content-Type': 'application/octet-stream' }
    if (sha256) headers['X-Chunk-SHA256'] = sha256
    return json<DropChunkResult>(apiUrl('/api/result-registration/drop-uploads/{session_id}/files/{index}', { session_id: sessionId, index }, { offset }), { method: 'PUT', headers, body })
  },
  complete(sessionId: string) {
    return json<DropCompletion>(apiUrl('/api/result-registration/drop-uploads/{session_id}/complete', { session_id: sessionId }), { method: 'POST' })
  },
  abort(sessionId: string) {
    return json<DropSession>(apiUrl('/api/result-registration/drop-uploads/{session_id}', { session_id: sessionId }), { method: 'DELETE' })
  },
  legacyDrafts(scope: Scope, signal?: AbortSignal) {
    return json<{ drafts: LegacyDraft[]; truncated: boolean }>(apiUrl('/api/result-registration/drafts', {}, scope), { signal })
  },
}

/** SHA-256 of one chunk, or ``null`` where WebCrypto is unavailable (plain HTTP on a LAN host). */
export async function chunkSha256(data: ArrayBuffer): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null
  const digest = await crypto.subtle.digest('SHA-256', data)
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}
