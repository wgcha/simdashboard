/**
 * Builds the Final designation reports with the stage-5 builders. The scope is
 * the whole Case: the 사용환경 Case, or every Run Case · Run Option of a
 * 유통환경 Case (one section each; options without results show `결과 없음`).
 */
import { reportApi } from '../../../shared/api/reportLayouts'
import { defaultReportLayout } from './reportPreferences'
import type { CaseReportMeta } from './reportMeta'
import { buildCaseReportHtml, buildCaseReportPptx, CASE_REPORT_VIDEO_TOTAL_MAX_BYTES, loadCaseFinalReport, loadCaseReportImages, loadCaseReportVideos, skippedVideoNames, type CaseReportData, type CaseReportFinalScope, type CaseReportFormat } from './caseReport'

export type FinalReportFiles = { files: Partial<Record<CaseReportFormat, Blob>>; skippedVideos: string[]; skippedImages: string[]; data: CaseReportData }

/** Room kept for slides, charts and markup under the server's upload limit. */
const REPORT_MARGIN_BYTES = 16 * 1024 * 1024
/** base64 data URIs in the HTML report (4/3) plus line breaks and markup. */
const HTML_DATA_URI_FACTOR = 1.34

/**
 * Raw video bytes that still fit every chosen format under the Final upload limits
 * (`report_limits` of the preview) once the images are in; at most the 200 MB video cap.
 */
export function finalVideoBudget(limits: Partial<Record<CaseReportFormat, number>> | undefined, formats: CaseReportFormat[], imageBytes: number) {
  let budget = CASE_REPORT_VIDEO_TOTAL_MAX_BYTES
  for (const format of formats) {
    const limit = limits?.[format]
    if (!limit) continue
    const room = format === 'html' ? Math.floor((limit - REPORT_MARGIN_BYTES) / HTML_DATA_URI_FACTOR) - imageBytes : limit - REPORT_MARGIN_BYTES - imageBytes
    budget = Math.min(budget, Math.max(0, room))
  }
  return budget
}

/**
 * `layoutId`: the layout chosen in the Final dialog (W7); when absent or gone, the layout last used in
 * the 보고서 dialog for this request, then the standard layout. `meta`: author and optional fields.
 */
export async function buildFinalReports(scope: CaseReportFinalScope, options: { formats: CaseReportFormat[]; includeVideos: boolean; layoutId?: string | null; meta?: CaseReportMeta; signal?: AbortSignal; reportLimits?: Partial<Record<CaseReportFormat, number>> }): Promise<FinalReportFiles> {
  const data = await loadCaseFinalReport(scope, { signal: options.signal })
  // Images and videos are read once and shared by both formats; videos stay within the upload limits.
  const images = await loadCaseReportImages(data, { signal: options.signal, includeVideos: options.includeVideos })
  const videos = options.includeVideos ? await loadCaseReportVideos(data, { signal: options.signal, posters: options.formats.includes('pptx'), maxTotalVideoBytes: finalVideoBudget(options.reportLimits, options.formats, images.usedBytes ?? 0) }) : undefined
  const files: FinalReportFiles['files'] = {}
  const skippedVideos: string[] = []
  if (options.formats.includes('pptx')) {
    const [storedLayouts, reportModule] = await Promise.all([reportApi.layouts().catch(() => []), import('../../../reportExport')])
    const stored = (storedLayouts.find((item) => item.id === options.layoutId) ?? defaultReportLayout(storedLayouts, scope.source.requestId))?.definition
    const layout = stored ? reportModule.normalizeReportLayout(stored) : undefined
    const scopeValue = (label: string) => data.scopeRows.find((row) => row.label === label)?.value ?? ''
    files.pptx = await buildCaseReportPptx(data, { layout, images, videos, includeVideos: options.includeVideos, labels: { project: scopeValue('프로젝트'), request: scopeValue('의뢰'), loadCase: scopeValue('하중경우') }, meta: options.meta, signal: options.signal })
    if (videos) skippedVideos.push(...skippedVideoNames(data, videos, 'pptx'))
  }
  if (options.formats.includes('html')) {
    const html = await buildCaseReportHtml(data, { includeVideos: options.includeVideos, images, videos, meta: options.meta, signal: options.signal })
    files.html = html.blob
    for (const item of html.skippedVideos) if (!skippedVideos.includes(item)) skippedVideos.push(item)
  }
  return { files, skippedVideos, skippedImages: images.skipped, data }
}
