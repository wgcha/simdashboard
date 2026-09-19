import { apiClient, unwrapGenerated } from './client'

export type FolderEnvironment = 'USAGE' | 'DISTRIBUTION'
export type FolderEnvironmentRole = 'PROJECT' | 'REQUEST' | 'SIMULATION_CASE' | 'EVALUATION' | 'LOAD_CASE' | 'EXECUTION_RUN' | 'RUN_OPTION' | 'INPUT' | 'RESULTS' | 'SCENE' | 'CONTAINER' | 'EXCLUDE'
export type FolderAssignment = { node_id: string; role_kind: string; confirm: boolean; target_mode: 'CREATE' | 'LINK'; target_id?: string | null }
export type FolderEnvironmentProfileRule = { role_kind: string; parent_role?: string | null; pattern: string; match_mode: 'glob' | 'contains'; depth?: number }
export type FolderEnvironmentProfile = { id: string; environment: FolderEnvironment; name: string; revision: number; rules: { rules?: FolderEnvironmentProfileRule[] }; message?: string }
export type FolderEnvironmentNode = { id: string; relative_path: string; parent_path: string | null; name: string; depth: number; file_count?: number; allowed_roles: string[]; role_kind: string | null; status: string; target_id?: string; target_mode?: string; project_id?: string; request_id?: string; message?: string }
export type FolderEnvironmentScan = { id: string; environment: FolderEnvironment; profile_id: string; relative_path: string; nodes: FolderEnvironmentNode[]; status: string; issues: Array<{ message: string; code?: string }> }
export type FolderEnvironmentPreview = { id: string; scan_id: string; environment: FolderEnvironment; can_apply: boolean; message?: string | null; rows: FolderEnvironmentNode[]; unresolved_count: number; summary: { new: number; existing: number; conflicts?: number } }
export type FolderCaptureJob = { id: string; status: string; case_id: string; capture_id: string | null; error_code: string | null; project_id?: string; request_id?: string }
export type FolderEnvironmentRegistration = { registration_id: string; preview_id: string; environment: FolderEnvironment; project_id: string; request_id: string; status: string; created_at: string; relative_path?: string; capture_jobs: FolderCaptureJob[] }
type ProfileInput = { environment: FolderEnvironment; name: string; rules: { rules: FolderEnvironmentProfileRule[] } }

export const folderEnvironmentApi = {
  profiles: async (signal?: AbortSignal) => (unwrapGenerated(await apiClient.GET('/api/folder-discovery/environments', { signal })) as { items: FolderEnvironmentProfile[] }).items,
  createProfile: async (body: ProfileInput) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/profiles', { body })) as FolderEnvironmentProfile,
  updateProfile: async (id: string, body: ProfileInput & { expected_revision: number }) => unwrapGenerated(await apiClient.PUT('/api/folder-discovery/environments/profiles/{profile_id}', { params: { path: { profile_id: id } }, body })) as FolderEnvironmentProfile,
  profileFromLegacy: async (body: { environment: FolderEnvironment; name: string; relative_path: string }) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/profiles/from-legacy', { body })) as FolderEnvironmentProfile,
  history: async (offset = 0, signal?: AbortSignal) => unwrapGenerated(await apiClient.GET('/api/folder-discovery/environments/history', { params: { query: { offset, limit: 50 } }, signal })) as { items: FolderEnvironmentRegistration[]; total: number },
  scan: async (body: { environment: FolderEnvironment; relative_path: string; profile_id?: string; project_id?: string; request_id?: string }, signal?: AbortSignal) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/scan', { body, signal })) as FolderEnvironmentScan,
  preview: async (body: { scan_id: string; assignments: FolderAssignment[] }, signal?: AbortSignal) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/previews', { body, signal })) as FolderEnvironmentPreview,
  register: async (body: { preview_id: string; idempotency_key: string; capture: boolean }) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/registrations', { body })) as FolderEnvironmentRegistration,
  registration: async (id: string) => unwrapGenerated(await apiClient.GET('/api/folder-discovery/environments/registrations/{registration_id}', { params: { path: { registration_id: id } } })) as FolderEnvironmentRegistration,
  retryCapture: async (id: string) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/environments/registrations/{registration_id}/capture/retry', { params: { path: { registration_id: id } }, body: {} })) as FolderEnvironmentRegistration,
}
