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
export type DriveUploadBatch = components['schemas']['DriveUploadBatch']
export type DriveQueueList = components['schemas']['DriveQueueList']
export type DriveQueueItem = components['schemas']['DriveQueueItem']
export type DriveQueueBulkResult = components['schemas']['DriveQueueBulkResult']
export type DriveAuthStatus = components['schemas']['DriveAuthStatus']
export type DriveReloginResult = components['schemas']['DriveReloginResult']

/** Korean messages for drive error codes (integration 06 §2). */
export const DRIVE_ERROR_MESSAGES: Record<string, string> = {
  SPDM_NOT_FOUND: '드라이브에서 항목을 찾을 수 없습니다.',
  SPDM_FORBIDDEN: '공용 계정에 이 폴더 권한이 없습니다. 관리자에게 문의하세요.',
  SPDM_CONFLICT: '같은 이름의 파일이 이미 드라이브에 있습니다(덮어쓰지 않습니다).',
  SPDM_CONFLICT_UNVERIFIED: '같은 이름의 큰 파일이 드라이브에 있어 내용을 비교하지 않았습니다(덮어쓰지 않습니다). 관리자가 확인하세요.',
  SPDM_LOCKED: '드라이브에서 잠긴 항목입니다. 잠시 후 다시 시도하세요.',
  SPDM_FILE_BUSY: '파일이 사용 중입니다(쓰는 중). 잠시 후 다시 확인합니다.',
  SPDM_LIMIT: '크기 또는 항목 수 상한을 넘었습니다.',
  SPDM_INVALID_PATH: '이 서버에서 쓸 수 없는 이름입니다(Windows 금지 문자·대소문자 중복).',
  DRIVE_AUTH_REQUIRED: '드라이브 인증이 필요합니다. 관리자에게 문의하세요.',
  DRIVE_BUSY: '드라이브 요청이 많습니다. 잠시 후 다시 시도하세요.',
  DRIVE_TIMEOUT: '드라이브 응답이 늦습니다.',
  DRIVE_UNAVAILABLE: '드라이브에 연결할 수 없습니다.',
  DRIVE_INTERNAL: '드라이브 처리 중 오류가 발생했습니다.',
  DRIVE_WRITE_DISABLED: '드라이브 쓰기가 꺼져 있거나 드라이브 모드에서 쓸 수 없는 기능입니다. 결과는 결과 등록(끌어놓기)으로 올리세요.',
  DRIVE_STAGING_MISSING: '서버 임시 파일이 없어 올릴 수 없습니다. 다시 올리세요.',
  DRIVE_STAGING_FULL: '서버 임시 저장 공간이 부족해 기다립니다. 공간이 생기면 이어서 진행합니다.',
  DRIVE_ROOT_CHANGED: 'SPDM 루트가 바뀌어 이 항목을 쓰지 않았습니다.',
  DRIVE_UPLOAD_MISMATCH: '드라이브에 올라간 파일 크기가 다릅니다. 자동으로 다시 확인합니다.',
  DRIVE_QUEUE_CANCELLED: '관리자가 취소했습니다(드라이브 내용은 그대로입니다).',
  DRIVE_BATCH_NOT_FOUND: '드라이브 업로드 묶음을 찾을 수 없습니다.',
  DRIVE_QUEUE_ITEM_NOT_FOUND: '대기열 항목을 찾을 수 없습니다.',
  DRIVE_QUEUE_ITEM_STATE: '이 상태의 항목에는 할 수 없는 동작입니다.',
  DRIVE_QUEUE_BATCH_OPEN: '같은 묶음에 아직 진행 중인 항목이 있어 지우지 않았습니다.',
  DRIVE_QUEUE_FINAL_ACTIVE: '완료되지 않은 Final 지정의 항목이라 지우지 않았습니다(다시 시도에 필요).',
  DRIVE_QUEUE_SELECTION_INVALID: '선택한 항목 또는 목록 조건 중 하나만 보내야 합니다.',
  DRIVE_QUEUE_PURGE_STATE: '완료·취소·실패·충돌 상태만 목록에서 지울 수 있습니다.',
  FINALIZATION_LOCKED: '같은 의뢰의 다른 Final 지정이 드라이브에 반영되고 있습니다. 끝난 뒤 다시 시도하세요.',
  FINALIZATION_SOURCE_STALE: 'Final 계획 이후 드라이브 원본이 바뀌었습니다. 원본 변경을 확인하고 다시 지정하세요.',
  FINALIZATION_SOURCE_MISSING: '드라이브 원본 파일을 찾을 수 없습니다.',
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
  // Re-login (issue #50, docs/plans/scx-drive-relogin-spec.md §2.4·§3.2)
  AUTH_INVALID_CREDENTIALS: '아이디 또는 비밀번호가 맞지 않습니다.',
  AUTH_ACCOUNT_LOCKED: 'HPC 계정이 잠겼거나 비활성 상태입니다. HPC 관리자에게 문의하세요.',
  AUTH_MFA_REQUIRED: '이 계정은 추가 인증이 필요해 대시보드에서 로그인할 수 없습니다.',
  AUTH_METHOD_UNSUPPORTED: '이 방식의 재연결을 지원하지 않습니다. 토큰 붙여넣기를 사용하세요.',
  AUTH_REFRESH_EXPIRED: '저장된 토큰이 만료되었습니다. 계정으로 로그인하세요.',
  RATE_LIMITED: 'HPC SSO가 시도 횟수를 제한했습니다. 잠시 후 다시 시도하세요.',
  DRIVE_LOGIN_LOCKED: '로그인 시도가 너무 많아 잠시 잠갔습니다(HPC 계정 잠금 방지).',
  DRIVE_LOGIN_INVALID: '아이디(128자 이하)와 비밀번호(256자 이하)를 입력하세요.',
  DRIVE_PASSWORD_LOGIN_DISABLED: '이 접속에서는 계정 로그인을 사용할 수 없습니다.',
  DRIVE_ADAPTER_UPDATE_REQUIRED: '설치된 드라이브 어댑터가 재연결을 지원하지 않습니다(어댑터 업데이트 필요).',
  DRIVE_ACCOUNT_CHANGE_CONFIRM: '연결 계정이 바뀝니다.',
  DRIVE_AUTH_BUSY: '다른 재연결이 진행 중입니다. 잠시 후 다시 시도하세요.',
  DRIVE_CREDENTIALS_MISSING: '저장된 토큰이 없습니다. 계정으로 로그인하거나 토큰을 붙여 넣으세요.',
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

/** The ``detail`` object of a failed drive API call (``{code, message, ...}``), when there is one. */
export function driveErrorDetail(reason: unknown): Record<string, unknown> | null {
  if (!(reason instanceof ApiError)) return null
  const outer = reason.detail
  const detail = outer && typeof outer === 'object' && 'detail' in outer ? (outer as { detail: unknown }).detail : outer
  return detail && typeof detail === 'object' && !Array.isArray(detail) ? detail as Record<string, unknown> : null
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
  registerCredentials: async (bundleText: string, confirmAccountChange = false) => {
    let body: unknown
    try { body = JSON.parse(bundleText) } catch { throw new ApiError('토큰 JSON을 해석할 수 없습니다. login 명령이 출력한 JSON 전체를 붙여 넣으세요.', 400, null) }
    if (!body || typeof body !== 'object' || Array.isArray(body)) throw new ApiError('토큰 JSON은 객체여야 합니다.', 400, null)
    return unwrapGenerated(await apiClient.PUT('/api/admin/drive/credentials', {
      params: { query: confirmAccountChange ? { confirm_account_change: true } : {} },
      body: body as { server_url: string; access_token: string; refresh_token: string },
    })) as DriveCredentialsSaved
  },
  /** Re-login status (issue #50): adapter capabilities, connected account, whether account login is allowed here. */
  authStatus: async () => unwrapGenerated(await apiClient.GET('/api/admin/drive/auth')) as DriveAuthStatus,
  /** [저장된 토큰으로 다시 연결]: refresh with the stored refresh token. */
  reconnect: async (confirmAccountChange = false) => unwrapGenerated(await apiClient.POST('/api/admin/drive/reconnect', {
    body: { confirm_account_change: confirmAccountChange },
  })) as DriveReloginResult,
  /** [계정으로 로그인]: the password goes to the server once and is never kept by this module. */
  login: async (username: string, password: string, confirmAccountChange = false) => unwrapGenerated(await apiClient.POST('/api/admin/drive/login', {
    body: { username, password, confirm_account_change: confirmAccountChange },
  })) as DriveReloginResult,
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
  /** D3: progress of one drive upload batch (result drop publish, folder, Final, summary file). */
  uploadBatch: async (batchId: string, signal?: AbortSignal) => unwrapGenerated(await apiClient.GET('/api/drive/upload-batches/{batch_id}', { params: { path: { batch_id: batchId } }, signal })) as DriveUploadBatch,
  /** D3 admin: the drive upload queue (open items and those finished in the last day). */
  queue: async (state?: string) => unwrapGenerated(await apiClient.GET('/api/admin/drive/queue', { params: { query: state ? { state } : {} } })) as DriveQueueList,
  retryQueueItem: async (itemId: string) => unwrapGenerated(await apiClient.POST('/api/admin/drive/queue/{item_id}/retry', { params: { path: { item_id: itemId } } })) as DriveQueueItem,
  /** Marks the item CANCELLED; nothing on the drive is deleted. */
  cancelQueueItem: async (itemId: string) => unwrapGenerated(await apiClient.POST('/api/admin/drive/queue/{item_id}/cancel', { params: { path: { item_id: itemId } } })) as DriveQueueItem,
  resumeQueue: async () => unwrapGenerated(await apiClient.POST('/api/admin/drive/queue/resume')) as DriveQueueList,
  /** Cancel the selected open items, or every open item of the list filter (``allFilter``). Drive files stay. */
  bulkCancelQueue: async (selection: { ids: string[] } | { allFilter: string | null }) => unwrapGenerated(await apiClient.POST('/api/admin/drive/queue/bulk-cancel', {
    body: 'ids' in selection ? { ids: selection.ids } : { all_filter: { state: selection.allFilter } },
  })) as DriveQueueBulkResult,
  /** Remove finished rows (and their server staging) from the queue list; drive files are never deleted. */
  purgeQueue: async (selection: { ids: string[] } | { states: string[] }) => unwrapGenerated(await apiClient.POST('/api/admin/drive/queue/purge', {
    body: 'ids' in selection ? { ids: selection.ids } : { states: selection.states },
  })) as DriveQueueBulkResult,
}

export const DRIVE_BATCH_STATE_LABELS: Record<string, string> = {
  QUEUED: '드라이브 반영 대기', RUNNING: '드라이브 반영 중', PAUSED: '일시 정지(드라이브 인증 필요)', DONE: '드라이브 반영 완료',
  PARTIAL: '일부만 반영(충돌·실패 있음)', CONFLICT: '충돌(같은 이름의 다른 파일, 덮어쓰지 않음)', FAILED: '실패', CANCELLED: '취소됨',
}

export const DRIVE_QUEUE_ITEM_STATE_LABELS: Record<string, string> = {
  PENDING: '대기', RUNNING: '진행', DONE: '완료', CONFLICT: '충돌', FAILED: '실패', BLOCKED: '인증 대기', CANCELLED: '취소',
}

export type DriveWriteStatus = { scx: boolean; writesAvailable: boolean; writesEnabled: boolean; queuePaused: boolean }

let driveWriteStatusPromise: Promise<DriveWriteStatus> | null = null

/** Drive mode and write availability (scx: only with "드라이브 쓰기 허용"). One request per page load; mode changes need a restart. */
export function loadDriveWriteStatus(): Promise<DriveWriteStatus> {
  if (!driveWriteStatusPromise) {
    driveWriteStatusPromise = driveApi.userStatus().then(
      (status) => ({ scx: status.mode === 'scx', writesAvailable: status.mode !== 'scx' || Boolean(status.writes_available), writesEnabled: Boolean(status.writes_enabled), queuePaused: Boolean(status.queue_paused) }),
      () => { driveWriteStatusPromise = null; return { scx: false, writesAvailable: true, writesEnabled: false, queuePaused: false } },
    )
  }
  return driveWriteStatusPromise
}

/** Shown where upload, folder creation or Final actions are disabled in drive read-only mode. */
export const DRIVE_READ_ONLY_NOTICE = '드라이브 읽기 전용: 관리자가 드라이브 쓰기 허용(SIMDASH_DRIVE_WRITES_ENABLED)을 켜기 전에는 업로드·폴더 만들기·Final 지정을 사용할 수 없습니다.'

/** Storage panel uploads are never available in drive mode (integration 03 8-6: use result registration). */
export const DRIVE_PANEL_NOTICE = '드라이브 모드에서는 이 화면에서 업로드할 수 없습니다. 결과는 결과 등록(끌어놓기)으로 올리세요.'
