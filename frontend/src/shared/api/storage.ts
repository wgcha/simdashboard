import { apiClient, unwrapGenerated } from './client'
import { apiUrl } from './url'
import type { components } from './generated/openapi'

export type StorageFileKind = 'ORIGINAL' | 'STRUCTURED_RESULT' | 'MANIFEST' | 'OTHER' | 'results' | 'solver' | 'media' | 'inputs' | 'reports' | string
export type StorageFileStatus = 'DISCOVERED' | 'STORED' | 'IMPORTED' | 'FAILED' | 'PENDING' | string

export type StorageFile = {
  id: string
  filename: string
  relative_path: string
  kind: StorageFileKind
  status: StorageFileStatus
  run_id?: string | null
  run_no?: number | null
  message?: string | null
  mime_type?: string | null
  size_bytes?: number | null
  created_at?: string | null
  download_url?: string | null
}

export type StorageRule = {
  kind: string
  folder?: string
  extensions?: string[]
  label?: string
  description?: string
  allowed?: boolean
}

export type StorageBinding = {
  load_case_id: string
  relative_path: string
  absolute_path?: string | null
  exists?: boolean
  status?: string
  message?: string | null
}

export type StorageConfig = {
  root_path?: string | null
  root?: string | null
  configured?: boolean
  locked?: boolean
  writable?: boolean
  status?: string
  message?: string | null
}

export type StorageWorkspaceState = {
  config?: StorageConfig | null
  binding?: StorageBinding | null
  rules: StorageRule[]
  files: StorageFile[]
  candidate_folders: string[]
}

export type StoredFile = StorageFile & { kind: StorageFileKind }
export type StorageUploadResult = Record<string, unknown> & {
  status?: string
  run_id?: string | null
  run_no?: number | null
  relative_path?: string
  message?: string | null
}
type GeneratedStorageUploadResponse = Omit<components['schemas']['StructuredUploadResponse'], 'stored_file'>
export type StorageUploadResponse = Partial<GeneratedStorageUploadResponse> & Record<string, unknown> & { stored_file?: StoredFile | null; result?: StorageUploadResult | null }
export type StoragePanelProps = {
  refreshToken?: number
  loadCaseId?: string
  projectLabel?: string
  requestLabel?: string
  loadCaseLabel?: string
  canManageRoot?: boolean
  canBindFolder?: boolean
  canUpload?: boolean
  canRefresh?: boolean
  allowStructuredUpload?: boolean
  compact?: boolean
  readOnly?: boolean
  onBindingStateChange?: (bound: boolean) => void
  onChanged?: () => Promise<void> | void
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {}
}

function text(value: unknown, fallback = '') {
  return typeof value === 'string' ? value : fallback
}

function normalizeFile(value: unknown): StorageFile {
  const item = record(value)
  return {
    id: text(item.id),
    filename: text(item.filename ?? item.name, '알 수 없는 파일'),
    relative_path: text(item.relative_path ?? item.path),
    kind: text(item.kind, 'OTHER'),
    status: text(item.status, 'UNKNOWN') === 'READY' ? 'STORED' : text(item.status, 'UNKNOWN'),
    run_id: text(item.run_id) || null,
    run_no: typeof item.run_no === 'number' ? item.run_no : null,
    message: text(item.message) || null,
    mime_type: text(item.mime_type) || null,
    size_bytes: typeof item.size_bytes === 'number' ? item.size_bytes : typeof item.size === 'number' ? item.size : null,
    created_at: text(item.created_at) || null,
    download_url: text(item.download_url) || null,
  }
}

function normalizeUpload(value: unknown): StorageUploadResponse {
  const item = record(value)
  return { ...item, stored_file: item.stored_file == null ? null : normalizeFile(item.stored_file) }
}

export function normalizeStorageState(value: unknown): StorageWorkspaceState {
  const item = record(value)
  const rawBinding = record(item.binding)
  const rawConfig = record(item.config ?? item)
  const rawCandidates = Array.isArray(item.candidate_folders) ? item.candidate_folders : Array.isArray(item.candidates) ? item.candidates : Array.isArray(item.folders) ? item.folders : []
  return {
    config: item.config == null && item.root == null && item.root_path == null && item.configured == null ? null : {
      root_path: text(rawConfig.root_path ?? rawConfig.root) || null,
      root: text(rawConfig.root ?? rawConfig.root_path) || null,
      configured: Boolean(rawConfig.configured),
      locked: rawConfig.locked == null ? undefined : Boolean(rawConfig.locked),
      writable: rawConfig.writable == null ? undefined : Boolean(rawConfig.writable),
      status: text(rawConfig.status) || undefined,
      message: text(rawConfig.message) || null,
    },
    binding: item.binding == null ? null : {
      load_case_id: text(rawBinding.load_case_id),
      relative_path: text(rawBinding.relative_path),
      absolute_path: text(rawBinding.absolute_path) || null,
      exists: rawBinding.exists == null ? undefined : Boolean(rawBinding.exists),
      status: text(rawBinding.status) || undefined,
      message: text(rawBinding.message) || null,
    },
    rules: (Array.isArray(item.rules) ? item.rules : []).map((rule) => {
      const raw = record(rule)
      return { kind: text(raw.kind), folder: text(raw.folder) || undefined, label: text(raw.label) || undefined, extensions: Array.isArray(raw.extensions) ? raw.extensions.filter((extension): extension is string => typeof extension === 'string') : undefined, description: text(raw.description) || undefined, allowed: raw.allowed == null ? undefined : Boolean(raw.allowed) }
    }),
    files: (Array.isArray(item.files) ? item.files : []).map(normalizeFile),
    candidate_folders: rawCandidates.map((candidate) => typeof candidate === 'string' ? candidate : text(record(candidate).relative_path)),
  }
}

export async function fetchStorageConfig(signal?: AbortSignal): Promise<StorageWorkspaceState> {
  return normalizeStorageState(unwrapGenerated(await apiClient.GET('/api/storage/config', { signal })))
}

export async function fetchStorageFolders(signal?: AbortSignal): Promise<string[]> {
  const body = record(unwrapGenerated(await apiClient.GET('/api/storage/folders', { signal })))
  const folders = Array.isArray(body.folders) ? body.folders : []
  return folders.map((folder) => typeof folder === 'string' ? folder : text(record(folder).relative_path)).filter(Boolean)
}

export async function saveStorageConfig(rootPath: string, signal?: AbortSignal): Promise<StorageWorkspaceState> {
  return normalizeStorageState(unwrapGenerated(await apiClient.PUT('/api/storage/config', { body: { root: rootPath }, signal })))
}

export async function refreshStorage(signal?: AbortSignal): Promise<unknown> {
  return unwrapGenerated(await apiClient.POST('/api/storage/refresh', { signal }))
}

export async function fetchStorageState(loadCaseId: string, signal?: AbortSignal): Promise<StorageWorkspaceState> {
  return normalizeStorageState(unwrapGenerated(await apiClient.GET('/api/load-cases/{load_case_id}/storage', { params: { path: { load_case_id: loadCaseId } }, signal })))
}

export async function bindStorageFolder(loadCaseId: string, relativePath: string, signal?: AbortSignal): Promise<StorageWorkspaceState> {
  unwrapGenerated(await apiClient.PUT('/api/load-cases/{load_case_id}/storage', { params: { path: { load_case_id: loadCaseId } }, body: { relative_path: relativePath }, signal }))
  return fetchStorageState(loadCaseId, signal)
}

export async function refreshStorageFolder(loadCaseId: string, signal?: AbortSignal): Promise<StorageWorkspaceState> {
  unwrapGenerated(await apiClient.POST('/api/load-cases/{load_case_id}/storage/refresh', { params: { path: { load_case_id: loadCaseId } }, signal }))
  return fetchStorageState(loadCaseId, signal)
}

export async function uploadStructuredResult(loadCaseId: string, payload: { filename: string; content: string; author: string }, signal?: AbortSignal): Promise<StorageUploadResponse> {
  return normalizeUpload(unwrapGenerated(await apiClient.POST('/api/load-cases/{load_case_id}/storage/upload', { params: { path: { load_case_id: loadCaseId } }, body: payload, signal })))
}

export async function uploadOriginalFile(loadCaseId: string, file: File, kind = 'ORIGINAL', signal?: AbortSignal): Promise<StorageUploadResponse> {
  return normalizeUpload(unwrapGenerated(await apiClient.PUT('/api/load-cases/{load_case_id}/storage/files', { params: { path: { load_case_id: loadCaseId }, query: { filename: file.name, kind: kind as 'solver' | 'media' | 'inputs' | 'reports' } }, headers: { 'Content-Type': file.type || 'application/octet-stream' }, body: file as unknown as string, bodySerializer: (value) => value as unknown as BodyInit, signal })))
}

export function storageFileDownloadUrl(fileId: string) {
  return apiUrl('/api/storage/files/{file_id}/download', { file_id: fileId })
}
