import type { DashboardChoice } from './simulationDashboard'

export type ResultStateChoice = { id: string; label: string; title: string; disabled?: boolean; reason?: string | null }

export const CAPTURE_PENDING_LABEL = '결과 받는 중'
export const NO_RESULT_LABEL = '결과 없음'
export const CAPTURE_PENDING_REASON = '드라이브에서 결과 파일을 받는 중입니다. 받는 대로 자동으로 표시됩니다.'
export const CAPTURE_FAILED_REASON = '결과 파일을 받지 못했습니다. 등록 화면에서 다시 시도하세요.'
export const NO_RESULT_REASON = '이 Case에는 아직 결과 파일이 없습니다.'

/** What a Case without results waits for: `결과 받는 중` (pending job) or `결과 없음` (failed or no result files). */
export function caseResultState(item: DashboardChoice | undefined): { label: string; reason: string } | null {
  if (!item || item.capture_count) return null
  if (item.capture_pending) return { label: CAPTURE_PENDING_LABEL, reason: CAPTURE_PENDING_REASON }
  if (item.capture_failed) return { label: NO_RESULT_LABEL, reason: CAPTURE_FAILED_REASON }
  return { label: NO_RESULT_LABEL, reason: NO_RESULT_REASON }
}

/**
 * Path choices of one hierarchy level. Cases (and the levels known from the registration under a Case)
 * without results stay selectable and are labelled, so the page shows the matching empty state instead
 * of hiding them. `owner` is the Case whose levels these are; Case choices use their own state.
 */
export function resultStateChoices(choices: readonly DashboardChoice[], owner?: DashboardChoice): ResultStateChoice[] {
  return choices.map((item) => {
    const state = caseResultState(owner ?? item)
    const uncaptured = owner ? item.capture_id == null : true
    const mark = state && uncaptured ? state : null
    return {
      id: item.id,
      label: mark ? `${item.label} · ${mark.label}` : item.label,
      title: mark?.reason ?? item.relative_path ?? item.label,
      disabled: item.disabled,
      reason: item.reason ?? mark?.reason ?? null,
    }
  })
}
