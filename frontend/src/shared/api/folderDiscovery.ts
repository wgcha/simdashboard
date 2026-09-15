import { apiClient, unwrapGenerated } from './client'

export type FolderRole = string
export type FolderRoleKind = 'PROJECT' | 'REQUEST' | 'LOAD_CASE' | 'RESULTS' | 'INPUT'
export type FolderRoleOption = { key: string; label: string; kind: FolderRoleKind; active: boolean }
export type FolderAnalysisTypeOption = { key: string; label: string; active: boolean }
export type FolderDiscoveryCatalog = { revision: number; roles: FolderRoleOption[]; analysis_types: FolderAnalysisTypeOption[] }
export type FolderDiscoveryRule = {
  depth: number
  role: FolderRole
  keyword?: string
  prefix?: string
  delimiter?: string
  code_token?: number
  name_from_token?: number
  analysis_type?: string
}

export type FolderDiscoveryEntry = { name: string; relative_path: string; is_directory: boolean }
export type FolderDiscoveryBrowse = { configured: boolean; root_path?: string | null; relative_path: string; entries: FolderDiscoveryEntry[] }
export type FolderDiscoveryNode = { relative_path: string; parent_path: string; name: string; depth: number; file_count: number; extensions: string[] }
export type FolderDiscoveryScan = { id: string; status: 'COMPLETE' | 'INCOMPLETE'; relative_path: string; folder_count: number; file_count: number; nodes: FolderDiscoveryNode[]; issues: unknown[] }
export type FolderDiscoveryPreviewRow = { relative_path: string; role: FolderRole; code: string; name: string; status: 'CREATE' | 'KEEP' | 'CONFLICT'; message?: string | null }
export type FolderDiscoveryPreview = { id: string; scan_id: string; can_apply: boolean; rows: (FolderDiscoveryPreviewRow & { role_kind?: FolderRoleKind; role_label?: string; project_id?: string | null; request_id?: string | null; load_case_id?: string | null })[]; unmatched_count: number; summary: { projects: number; requests: number; load_cases: number; conflicts: number; folders?: number } }
export type FolderDiscoveryApply = { status: 'APPLIED'; created: { projects: number; requests: number; load_cases: number }; kept_count: number }
export type FolderDiscoveryRules = { rules: FolderDiscoveryRule[]; revision: number }

const completeRule = (rule: FolderDiscoveryRule) => {
  const { prefix: legacyPrefix, ...rest } = rule
  return { ...rest, keyword: rule.keyword ?? legacyPrefix ?? '', prefix: '', delimiter: rule.delimiter ?? '_', code_token: rule.code_token ?? 1, name_from_token: rule.name_from_token ?? 2, analysis_type: rule.analysis_type ?? '' }
}

export const folderDiscoveryApi = {
  browse: async (relativePath = '', signal?: AbortSignal) => unwrapGenerated(await apiClient.GET('/api/folder-discovery/browse', { params: { query: { relative_path: relativePath } }, signal })) as FolderDiscoveryBrowse,
  scan: async (relativePath: string, signal?: AbortSignal) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/scan', { body: { relative_path: relativePath }, signal })) as FolderDiscoveryScan,
  preview: async (scanId: string, rules: FolderDiscoveryRule[], signal?: AbortSignal) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/preview', { body: { scan_id: scanId, rules: rules.map(completeRule) }, signal })) as FolderDiscoveryPreview,
  apply: async (previewId: string, signal?: AbortSignal) => unwrapGenerated(await apiClient.POST('/api/folder-discovery/apply', { body: { preview_id: previewId }, signal })) as FolderDiscoveryApply,
  rules: async (relativePath = '', signal?: AbortSignal) => unwrapGenerated(await apiClient.GET('/api/folder-discovery/rules', { params: { query: { relative_path: relativePath } }, signal })) as FolderDiscoveryRules,
  saveRules: async (relativePath: string, rules: FolderDiscoveryRule[], expectedRevision: number, signal?: AbortSignal) => unwrapGenerated(await apiClient.PUT('/api/folder-discovery/rules', { body: { relative_path: relativePath, rules: rules.map(completeRule), expected_revision: expectedRevision }, signal })) as FolderDiscoveryRules,
  catalog: async (signal?: AbortSignal) => unwrapGenerated(await apiClient.GET('/api/folder-discovery/catalog', { signal })) as FolderDiscoveryCatalog,
  saveCatalog: async (catalog: Omit<FolderDiscoveryCatalog, 'revision'>, expectedRevision: number, signal?: AbortSignal) => unwrapGenerated(await apiClient.PUT('/api/folder-discovery/catalog', { body: { ...catalog, expected_revision: expectedRevision }, signal })) as FolderDiscoveryCatalog,
}
