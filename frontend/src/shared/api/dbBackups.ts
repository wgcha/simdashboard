// Administrator DB backups (docs/deployment-security-backup-guide.md §5.1; backend app/services/db_backup.py).
import { apiClient, unwrapGenerated } from './client'
import { ApiError, apiErrorMessage } from './errors'
import type { components } from './generated/openapi'
import { apiUrl } from './url'

export type DbBackupOverview = components['schemas']['DbBackupOverview']
export type DbBackupItem = components['schemas']['DbBackupItem']
export type DbBackupJob = components['schemas']['DbBackupJob']

export const DB_BACKUP_ERROR_MESSAGES: Record<string, string> = {
  GLOBAL_ADMIN_REQUIRED: 'DB 백업은 전체 관리자만 사용할 수 있습니다.',
  DB_BACKUP_RUNNING: '이미 백업이 진행 중입니다. 끝난 뒤 다시 시도하세요.',
  DB_BACKUP_NOT_FOUND: '백업 파일을 찾을 수 없습니다.',
  DB_BACKUP_TOOL_MISSING: 'pg_dump / pg_restore를 찾지 못했습니다. PostgreSQL bin 폴더를 POSTGRES_BIN에 지정하세요.',
  DB_BACKUP_VERSION_MISMATCH: 'pg_dump 버전이 DB 서버 버전과 맞지 않습니다. 서버와 같은 메이저 버전의 bin 폴더를 POSTGRES_BIN에 지정하세요.',
  DB_BACKUP_AUTH_FAILED: 'DB 계정 인증에 실패했습니다. DATABASE_URL·POSTGRES_OWNER_URL을 확인하세요.',
  DB_BACKUP_CONNECTION_FAILED: 'PostgreSQL에 연결하지 못했습니다. 서비스 상태와 포트를 확인하세요.',
  DB_BACKUP_DB_PRIVILEGE: 'DB 읽기 권한이 부족합니다. POSTGRES_OWNER_URL(소유자 계정)을 설정하세요.',
  DB_BACKUP_STORAGE_FAILED: '백업 파일을 쓰지 못했습니다. 백업 폴더 권한과 남은 디스크 공간을 확인하세요.',
  DB_BACKUP_DIR_IN_WEB_ROOT: '백업 폴더가 웹 공개 폴더 안에 있습니다. SIMDASH_DB_BACKUP_DIR을 바꾸세요.',
  DB_BACKUP_VERIFY_FAILED: '백업 파일 검증(pg_restore --list)에 실패해 보관하지 않았습니다.',
  DB_BACKUP_DUMP_FAILED: 'pg_dump가 실패했습니다.',
  DB_BACKUP_TIMEOUT: '백업이 제한 시간 안에 끝나지 않았습니다.',
}

export function dbBackupCodeMessage(code: string | null | undefined, fallback = ''): string {
  return code ? DB_BACKUP_ERROR_MESSAGES[code] ?? fallback ?? code : fallback
}

/** User-facing text for a failed backup API call. */
export function dbBackupErrorText(reason: unknown, fallback: string): string {
  if (reason instanceof ApiError) {
    const outer = reason.detail
    const detail = outer && typeof outer === 'object' && 'detail' in outer ? (outer as { detail: unknown }).detail : outer
    if (detail && typeof detail === 'object' && 'code' in detail && typeof detail.code === 'string') {
      const message = 'message' in detail && typeof detail.message === 'string' ? detail.message : ''
      return DB_BACKUP_ERROR_MESSAGES[detail.code] ?? (message || detail.code)
    }
    return detail == null ? reason.message || fallback : apiErrorMessage(detail, reason.message || fallback)
  }
  return reason instanceof Error && reason.message ? reason.message : fallback
}

export const dbBackupApi = {
  overview: async () => unwrapGenerated(await apiClient.GET('/api/admin/db-backups')) as DbBackupOverview,
  start: async (memo: string) => unwrapGenerated(await apiClient.POST('/api/admin/db-backups', { body: { memo: memo.trim() || null } })) as DbBackupJob,
  remove: async (id: string) => {
    const { response, error } = await apiClient.DELETE('/api/admin/db-backups/{backup_id}', { params: { path: { backup_id: id } } })
    if (!response.ok) throw new ApiError(apiErrorMessage(error, `요청 실패 (${response.status})`), response.status, error)
  },
  /** Plain link: the session cookie authorizes it and the browser streams the file to disk. */
  downloadUrl: (id: string) => apiUrl('/api/admin/db-backups/{backup_id}/download', { backup_id: id }),
}
