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
  DRIVE_WRITE_DISABLED: '드라이브 모드에서는 이 화면에서 업로드할 수 없습니다. 결과 등록을 이용하세요.',
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
  check: async (testFolder: string) => unwrapGenerated(await apiClient.POST('/api/admin/drive/check', { body: { test_folder: testFolder } })) as DriveCheckReport,
}
