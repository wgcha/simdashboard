/**
 * Builds the Final designation reports with the stage-5 builders. The scope is
 * the whole Case: the 사용환경 Case, or every Run Case · Run Option of a
 * 유통환경 Case (one section each; options without results show `결과 없음`).
 */
import { reportApi } from '../../../shared/api/reportLayouts'
import { defaultReportLayout } from './reportPreferences'
import type { CaseReportMeta } from './reportMeta'
import { buildCaseReportHtml, buildCaseReportPptx, loadCaseFinalReport, loadCaseReportImages, type CaseReportData, type CaseReportFinalScope, type CaseReportFormat } from './caseReport'

export type FinalReportFiles = { files: Partial<Record<CaseReportFormat, Blob>>; skippedVideos: string[]; skippedImages: string[]; data: CaseReportData }

/**
 * `layoutId`: the layout chosen in the Final dialog (W7); when absent or gone, the layout last used in
 * the 보고서 dialog for this request, then the standard layout. `meta`: author and optional fields.
 */
export async function buildFinalReports(scope: CaseReportFinalScope, options: { formats: CaseReportFormat[]; includeVideos: boolean; layoutId?: string | null; meta?: CaseReportMeta; signal?: AbortSignal }): Promise<FinalReportFiles> {
  const data = await loadCaseFinalReport(scope, { signal: options.signal })
  // Images are read once and shared by both formats.
  const images = await loadCaseReportImages(data, { signal: options.signal })
  const files: FinalReportFiles['files'] = {}
  let skippedVideos: string[] = []
  if (options.formats.includes('pptx')) {
    const [storedLayouts, reportModule] = await Promise.all([reportApi.layouts().catch(() => []), import('../../../reportExport')])
    const stored = (storedLayouts.find((item) => item.id === options.layoutId) ?? defaultReportLayout(storedLayouts, scope.source.requestId))?.definition
    const layout = stored ? reportModule.normalizeReportLayout(stored) : undefined
    const scopeValue = (label: string) => data.scopeRows.find((row) => row.label === label)?.value ?? ''
    files.pptx = await buildCaseReportPptx(data, { layout, images, labels: { project: scopeValue('프로젝트'), request: scopeValue('의뢰'), loadCase: scopeValue('하중경우') }, meta: options.meta, signal: options.signal })
  }
  if (options.formats.includes('html')) {
    const html = await buildCaseReportHtml(data, { includeVideos: options.includeVideos, images, meta: options.meta, signal: options.signal })
    files.html = html.blob
    skippedVideos = html.skippedVideos
  }
  return { files, skippedVideos, skippedImages: images.skipped, data }
}
