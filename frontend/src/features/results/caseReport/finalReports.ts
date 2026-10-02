/**
 * Builds the Final designation reports with the stage-5 builders (same recipe
 * as the 보고서 dialog) for the scope fixed when the Final dialog opened.
 */
import { api } from '../../../api'
import { reportApi } from '../../../shared/api/reportLayouts'
import { buildCaseReportData, buildCaseReportHtml, buildCaseReportPptx, loadCaseReportSources, type CaseReportFormat, type CaseReportScope } from './caseReport'

export type FinalReportFiles = { files: Partial<Record<CaseReportFormat, Blob>>; skippedVideos: string[] }

export async function buildFinalReports(scope: CaseReportScope, options: { formats: CaseReportFormat[]; includeVideos: boolean; signal?: AbortSignal }): Promise<FinalReportFiles> {
  const { source } = scope
  const [sources, project, request] = await Promise.all([
    loadCaseReportSources(source, options.signal),
    api.projects().then((items) => items.find((item) => item.id === source.projectId)?.name).catch(() => undefined),
    api.requests(source.projectId).then((items) => items.find((item) => item.id === source.requestId)?.title).catch(() => undefined),
  ])
  const labels = { ...scope.labels, project: project || scope.labels.project, request: request || scope.labels.request }
  const data = buildCaseReportData({ scope: { source, labels }, distribution: sources.distribution, videos: sources.videos, generatedAt: new Date() })
  const files: FinalReportFiles['files'] = {}
  let skippedVideos: string[] = []
  if (options.formats.includes('pptx')) {
    const [storedLayouts, reportModule] = await Promise.all([reportApi.layouts().catch(() => []), import('../../../reportExport')])
    const stored = storedLayouts.find((item) => item.id === 'report-layout-standard')?.definition ?? storedLayouts[0]?.definition
    const layout = stored ? reportModule.normalizeReportLayout(stored) : undefined
    files.pptx = await buildCaseReportPptx(data, { layout, labels: { project: labels.project, request: labels.request, loadCase: labels.loadCase }, signal: options.signal })
  }
  if (options.formats.includes('html')) {
    const html = await buildCaseReportHtml(data, { includeVideos: options.includeVideos, signal: options.signal })
    files.html = html.blob
    skippedVideos = html.skippedVideos
  }
  return { files, skippedVideos }
}
