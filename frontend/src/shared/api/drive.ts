// SCX drive admin/status API (docs/features/scx-drive.md, integration 02 §2/§4, 06 §2).
import { apiClient, unwrapGenerated } from './client'
import { ApiError, apiErrorMessage } from './errors'
import type { components } from './generated/openapi'

export type DriveAdminStatus = components['schemas']['DriveAdminStatus']
export type DriveUserStatus = components['schemas']['DriveUserStatus']
export type DriveTestResult = components['schemas']['DriveTestResult']
export type DriveCredentialsSaved = components['schemas']['DriveCredentialsSaved']
export type DriveCheckReport = components['schemas']['DriveCheckReport']
export type DriveCheckStep = components['schemas']['DriveCheckStep']
export type DriveSourceChanges = components['schemas']['DriveSourceChanges']
export type DriveSourceChangeItem = components['schemas']['DriveSourceChangeItem']
export type DriveSourceChangeResult = components['schemas']['DriveSourceChangeResult']
export type DriveHealthState = 'OK' | 'DEGRADED' | 'UNAVAILABLE' | 'AUTH_REQUIRED' | 'UNKNOWN'

/** Korean messages for drive error codes (integration 06 §2). */
export const DRIVE_ERROR_MESSAGES: Record<string, string> = {
  SPDM_NOT_FOUND: '드라이브에서 항목을 찾을 수 없습니다.',
  SPDM_FORBIDDEN: '공용 계정에 이 폴더 권한이 없습니다. 관리자에게 문의하세요.',
  SPDM_CONFLICT: '같은 이름의 파일이 이미 드라이브에 있습니다(덮어쓰지 않습니다).',
  SPDM_LOCKED: '드라이브에서 잠긴 항목입니다. 잠시 후 다시 시도하세요.',
  SPDM_FILE_BUSY: '파일이 사용 중입니다(쓰는 중). 잠시 후 다시 확인합니다.',
  SPDM_LIMIT: '크기 또는 항목 수 상한을 넘었습니다.',
  SPDM_INVALID_PATH: '이 서버에서 쓸 수 없는 이름입니다(Windows 금지 문자·대소문자 중복).',
  DRIVE_AUTH_REQUIRED: '드라이브 인증이 필요합니다. 관리자에게 문의하세요.',
  DRIVE_BUSY: '드라이브 요청이 많습니다. 잠시 후 다시 시도하세요.',
  DRIVE_TIMEOUT: '드라이브 응답이 늦습니다.',
  DRIVE_UNAVAILABLE: '드라이브에 연결할 수 없습니다.',
  DRIVE_INTERNAL: '드라이브 처리 중 오류가 발생했습니다.',
  DRIVE_WRITE_DISABLED: '드라이브 모드에서는 아직 SPDM 폴더에 쓸 수 없습니다(읽기 전용). 업로드·폴더 만들기·Final 지정은 드라이브 쓰기 단계 이후 사용할 수 있습니다.',
  DRIVE_READ_NOT_PREPARED: '드라이브 자료를 아직 준비하지 못했습니다. 잠시 후 다시 시도하세요.',
  DRIVE_SOURCE_CHANGED: '드라이브 원본이 바뀌어 등록된 버전을 다시 읽을 수 없습니다. 원본 변경을 확인하세요.',
  DRIVE_SOURCE_CHANGE_STALE: '확인할 변경 목록이 바뀌었습니다. 다시 불러오세요.',
  DRIVE_SOURCE_SCOPE_MISMATCH: '프로젝트와 의뢰 문맥이 일치하지 않습니다.',
  DRIVE_MODE_DISABLED: 'SCX 드라이브 모드가 아닙니다.',
  DRIVE_CREDENTIALS_INVALID: '토큰 JSON이 올바르지 않습니다.',
  DRIVE_CHECK_RUNNING: '드라이브 점검이 이미 실행 중입니다.',
  DRIVE_CHECK_FOLDER_INVALID: '시험 폴더 경로가 올바르지 않습니다.',
  DRIVE_ROOT_NOT_SET: 'SPDM 루트(SIMDASH_SCX_DRIVE_ROOT)가 설정되지 않았습니다.',
  GLOBAL_ADMIN_REQUIRED: '전체 관리자만 사용할 수 있습니다.',
  NOT_REJECTED: '같은 이름 쓰기가 거부되지 않았습니다(덮어쓰기 금지 확인 필요).',
}

const ADAPTER_CODE_TO_DRIVE: Record<string, string> = {
  NOT_FOUND: 'SPDM_NOT_FOUND', FORBIDDEN: 'SPDM_FORBIDDEN', CONFLICT: 'SPDM_CONFLICT', LOCKED: 'SPDM_LOCKED',
  BUSY: 'SPDM_FILE_BUSY', LIMIT: 'SPDM_LIMIT', INVALID_PATH: 'SPDM_INVALID_PATH', AUTH_REQUIRED: 'DRIVE_AUTH_REQUIRED',
  OVERLOADED: 'DRIVE_BUSY', TIMEOUT: 'DRIVE_TIMEOUT', UNAVAILABLE: 'DRIVE_UNAVAILABLE', INTERNAL: 'DRIVE_INTERNAL',
}

/** Message for a dashboard or adapter error code; unknown codes return the code itself. */
export function driveCodeMessage(code: string | null | undefined): string {
  if (!code) return ''
  return DRIVE_ERROR_MESSAGES[code] ?? DRIVE_ERROR_MESSAGES[ADAPTER_CODE_TO_DRIVE[code] ?? ''] ?? code
}

/** User-facing text for a failed drive API call: dictionary text plus the server detail. */
export function driveErrorText(reason: unknown, fallback: string): string {
  if (reason instanceof ApiError) {
    const detail = reason.detail && typeof reason.detail === 'object' && 'detail' in reason.detail ? (reason.detail as { detail: unknown }).detail : reason.detail
    if (detail && typeof detail === 'object' && 'code' in detail && typeof detail.code === 'string') {
      const message = 'message' in detail && typeof detail.message === 'string' ? detail.message : ''
      const known = DRIVE_ERROR_MESSAGES[detail.code]
      return known && message && message !== known ? `${known} ${message}` : known || message || detail.code
    }
    return detail == null ? reason.message || fallback : apiErrorMessage(detail, reason.message || fallback)
  }
  return reason instanceof Error && reason.message ? reason.message : fallback
}

export const DRIVE_STATE_LABELS: Record<DriveHealthState, string> = {
  OK: '연결됨',
  DEGRADED: '불안정',
  UNAVAILABLE: '연결 안 됨',
  AUTH_REQUIRED: '재로그인 필요',
  UNKNOWN: '확인 전',
}

export function driveStateLabel(state: string | null | undefined): string {
  return state && state in DRIVE_STATE_LABELS ? DRIVE_STATE_LABELS[state as DriveHealthState] : state ?? '—'
}

export const driveApi = {
  adminStatus: async () => unwrapGenerated(await apiClient.GET('/api/admin/drive/status')) as DriveAdminStatus,
  userStatus: async (signal?: AbortSignal) => unwrapGenerated(await apiClient.GET('/api/drive/status', { signal })) as DriveUserStatus,
  /** ``bundleText`` is the TokenBundle JSON printed by ``python -m scx_drive_adapter login``. */
  registerCredentials: async (bundleText: string) => {
    let body: unknown
    try { body = JSON.parse(bundleText) } catch { throw new ApiError('토큰 JSON을 해석할 수 없습니다. login 명령이 출력한 JSON 전체를 붙여 넣으세요.', 400, null) }
    if (!body || typeof body !== 'object' || Array.isArray(body)) throw new ApiError('토큰 JSON은 객체여야 합니다.', 400, null)
    return unwrapGenerated(await apiClient.PUT('/api/admin/drive/credentials', { body: body as { server_url: string; access_token: string; refresh_token: string } })) as DriveCredentialsSaved
  },
  deleteCredentials: async () => {
    const { response, error } = await apiClient.DELETE('/api/admin/drive/credentials')
    if (!response.ok) throw new ApiError(apiErrorMessage(error, `요청 실패 (${response.status})`), response.status, error)
  },
  test: async () => unwrapGenerated(await apiClient.POST('/api/admin/drive/test')) as DriveTestResult,
  /** Drive files of a request whose change or removal waits for confirmation (scx mode, plan D2). */
  sourceChanges: async (projectId: string, requestId: string, signal?: AbortSignal) => unwrapGenerated(await apiClient.GET('/api/drive/source-changes', { params: { query: { project_id: projectId, request_id: requestId } }, signal })) as DriveSourceChanges,
  /** [새 버전 등록]: all of the request's changes, or only ``ids``. */
  acceptSourceChanges: async (projectId: string, requestId: string, ids?: string[]) => unwrapGenerated(await apiClient.POST('/api/drive/source-changes/accept', { body: { project_id: projectId, request_id: requestId, ids: ids ?? null } })) as DriveSourceChangeResult,
  /** [무시]: keep the registered versions. */
  dismissSourceChanges: async (projectId: string, requestId: string, ids?: string[]) => unwrapGenerated(await apiClient.POST('/api/drive/source-changes/dismiss', { body: { project_id: projectId, request_id: requestId, ids: ids ?? null } })) as DriveSourceChangeResult,
  check: async (testFolder: string) => unwrapGenerated(await apiClient.POST('/api/admin/drive/check', { body: { test_folder: testFolder } })) as DriveCheckReport,
}

export type DriveWriteStatus = { scx: boolean; writesAvailable: boolean; writesEnabled: boolean }

let driveWriteStatusPromise: Promise<DriveWriteStatus> | null = null

/** Drive mode and write availability (D2: scx mode is read-only). One request per page load; mode changes need a restart. */
export function loadDriveWriteStatus(): Promise<DriveWriteStatus> {
  if (!driveWriteStatusPromise) {
    driveWriteStatusPromise = driveApi.userStatus().then(
      (status) => ({ scx: status.mode === 'scx', writesAvailable: status.mode !== 'scx' || Boolean(status.writes_available), writesEnabled: Boolean(status.writes_enabled) }),
      () => { driveWriteStatusPromise = null; return { scx: false, writesAvailable: true, writesEnabled: false } },
    )
  }
  return driveWriteStatusPromise
}

/** Shown where upload, folder creation or Final actions are disabled in drive read-only mode. */
export const DRIVE_READ_ONLY_NOTICE = '드라이브 읽기 전용: SCX 드라이브 모드에서는 아직 SPDM 폴더에 쓸 수 없어 업로드·폴더 만들기·Final 지정을 사용할 수 없습니다.'
