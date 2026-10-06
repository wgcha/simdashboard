/**
 * W7 report A plan: shared choices of the 보고서 dialog and the Final dialog.
 * - The last PPTX layout used for a request is remembered per user (localStorage, best effort)
 *   and is the default of both dialogs; it falls back to the standard layout.
 * - Author (signed-in user's display name) and the optional 개발단계·검토조건·결론 fields.
 * Nothing here is sent to the server except inside the generated reports.
 */
import { storedUser } from '../../../auth'
import type { ReportLayout } from '../../../types'

import type { CaseReportMeta } from './reportMeta'

export type { CaseReportMeta } from './reportMeta'
export { reportMetaRows } from './reportMeta'

export const STANDARD_LAYOUT_ID = 'report-layout-standard'
export const TEMPLATE_NOT_APPLIED_TEXT = '업로드 PPTX 템플릿은 Case 보고서에 적용되지 않습니다. 화면 레이아웃(슬라이드 구성)만 사용합니다.'

const KEY_PREFIX = 'vdsim.caseReport.lastLayout.v1'

function layoutKey(requestId: string) {
  return `${KEY_PREFIX}:${storedUser()?.id ?? 'anonymous'}:${requestId}`
}

export function lastReportLayoutId(requestId: string): string | null {
  try {
    return window.localStorage.getItem(layoutKey(requestId))
  } catch {
    return null
  }
}

export function rememberReportLayout(requestId: string, layoutId: string) {
  try {
    window.localStorage.setItem(layoutKey(requestId), layoutId)
  } catch {
    // Storage may be unavailable (private mode, quota); the standard layout stays the default.
  }
}

/** The remembered layout of this request when it still exists, else standard, else the first. */
export function defaultReportLayout(layouts: ReportLayout[], requestId: string): ReportLayout | undefined {
  const remembered = lastReportLayoutId(requestId)
  return layouts.find((item) => item.id === remembered) ?? layouts.find((item) => item.id === STANDARD_LAYOUT_ID) ?? layouts[0]
}

export function usesUploadedTemplate(layout: { templateSource?: string } | null | undefined) {
  return layout?.templateSource === 'pptx_upload'
}

export function initialReportMeta(): CaseReportMeta {
  return { author: storedUser()?.display_name ?? '', developmentStage: '', reviewConditions: '', reviewConclusion: '' }
}

