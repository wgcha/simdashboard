/**
 * Live progress text of the [저장 폴더 새로고침] full rescan (issue #55, 2026-10-21).
 * Pure functions over the GET /api/folder-discovery/environments/rescan state (tested in shared-api-self-test).
 */

type RescanProgressState = {
  status: string
  phase?: string | null
  rounds?: number
  progress?: { done: number; total: number; step?: string | null; current_path?: string | null; projects_done?: number; projects_total?: number }
  drive?: { calls: number; listings: number; errors: number; retries: number; last_error: { code: string; operation?: string | null; relative_path?: string | null } | null } | null
  elapsed_seconds?: number
  cancel_requested?: boolean
}

export function elapsedText(seconds: number | undefined): string {
  if (!seconds || seconds < 0) return ''
  const minutes = Math.floor(seconds / 60)
  return minutes ? `${minutes}분 ${seconds % 60}초` : `${seconds}초`
}

/** Second line under the button while a rescan runs: where it is, what the drive did, how long it has run. */
export function rescanProgressDetail(state: RescanProgressState | null): string {
  if (!state || state.status !== 'RUNNING') return ''
  const progress = state.progress ?? { done: 0, total: 0 }
  const parts: string[] = []
  if (state.phase === 'DISCOVER') {
    if (progress.projects_total) parts.push(`프로젝트 ${progress.projects_done ?? 0}/${progress.projects_total}`)
    else parts.push('폴더 목록 읽는 중')
    if (state.rounds) parts.push(`${state.rounds}회 확인 완료`)
  }
  if (progress.current_path) parts.push(progress.current_path)
  const drive = state.drive
  if (drive && drive.calls) parts.push(`드라이브 목록 ${drive.listings}건${drive.retries ? ` · 재시도 ${drive.retries}회` : ''}`)
  if (drive?.last_error) {
    const where = drive.last_error.relative_path ? ` (${drive.last_error.relative_path})` : ''
    parts.push(`최근 드라이브 오류 ${drive.last_error.code}${where}`)
  }
  const elapsed = elapsedText(state.elapsed_seconds)
  if (elapsed) parts.push(`경과 ${elapsed}`)
  if (state.cancel_requested) parts.push('중지 요청됨 · 다음 확인 지점에서 멈춥니다')
  return parts.join(' · ')
}
