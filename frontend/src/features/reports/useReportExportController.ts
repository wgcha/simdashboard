import { useCallback, useState } from 'react'
import { api } from '../../api'
import { reportApi } from './api'
import { withReportVariables } from './reportLayoutUtils'
import type { AnalysisRunSummary, DashboardDefinition, DashboardPageSummary, Overview, ReportContentItem, ReportLayout, ReportLayoutDefinition, ReportLayoutVersion, ReportSource, ReportTemplateAsset } from '../../types'
import type { ReportExportOptions } from '../../reportExport'

type ComparisonReportContext = {
  baselineRunId: string
  comparison: Awaited<ReturnType<typeof api.runComparison>>
  loadCaseId: string
  reviews: Awaited<ReturnType<typeof api.reviewItems>>
  targetRunId: string
  trust: Awaited<ReturnType<typeof api.runTrust>>
}

type ReportExportContext = {
  activeDashboardId: string
  comparisonReportContext: ComparisonReportContext | null
  dashboard: DashboardDefinition | null
  dashboardReady: boolean
  mode: 'analysis' | 'comparison'
  overview: Overview | null
  reportablePages: DashboardPageSummary[]
  selectedLoadCaseId: string
  onComparisonReportContextChanged: (context: ComparisonReportContext) => void
  onError: (message: string) => void
  onNotice: (message: string) => void
}

type ReportModule = typeof import('../../reportExport')

export function useReportExportController(context: ReportExportContext) {
  const [reportDraft, setReportDraft] = useState<ReportExportOptions | null>(null)
  const [reportOverview, setReportOverview] = useState<Overview | null>(null)
  const [reportPageId, setReportPageId] = useState('')
  const [reportRuns, setReportRuns] = useState<AnalysisRunSummary[]>([])
  const [reportRunId, setReportRunId] = useState('')
  const [reportContents, setReportContents] = useState<ReportContentItem[]>([])
  const [reportSource, setReportSource] = useState<ReportSource | null>(null)
  const [reportExporting, setReportExporting] = useState(false)
  const [reportError, setReportError] = useState('')
  const [reportLayouts, setReportLayouts] = useState<ReportLayout[]>([])
  const [reportLayoutDraft, setReportLayoutDraft] = useState<ReportLayoutDefinition | null>(null)
  const [reportLayoutVersions, setReportLayoutVersions] = useState<ReportLayoutVersion[]>([])
  const [reportTemplates, setReportTemplates] = useState<ReportTemplateAsset[]>([])
  const [isLayoutEditing, setIsLayoutEditing] = useState(false)

  const close = useCallback(() => {
    setReportDraft(null); setReportOverview(null); setReportLayoutDraft(null); setReportContents([]); setReportSource(null)
  }, [])
  const cancel = useCallback(() => {
    setReportDraft(null); setReportOverview(null); setReportLayoutDraft(null)
  }, [])

  const reportDataForPage = useCallback(async (pageId: string, sourceOverview: Overview | null | undefined, reportModule: ReportModule, definition?: DashboardDefinition) => {
    const dataOverview = sourceOverview ?? context.overview
    if (!context.overview || !dataOverview) throw new Error('선택한 하중 경우의 결과가 없습니다.')
    const page = context.reportablePages.find((item) => item.id === pageId)
    if (!page) throw new Error('내보낼 분석 페이지를 찾을 수 없습니다.')
    const resolvedDefinition = definition ?? (context.dashboard?.id === pageId ? context.dashboard : await api.dashboard(pageId))
    if (page.page.analysis_key === 'open_cell') {
      const scopedOverview = reportModule.filterOverviewForReport(dataOverview, 'open_cell')
      return { scopedOverview, contentOverview: scopedOverview, definition: resolvedDefinition, page }
    }
    if (page.page.analysis_key === 'chassis_rear') {
      const scopedOverview = reportModule.filterOverviewForReport(dataOverview, 'chassis')
      return { scopedOverview, contentOverview: scopedOverview, definition: resolvedDefinition, page }
    }
    const variableKeys = [...new Set(resolvedDefinition.widgets.filter((widget) => widget.settings?.includeInReport !== false && typeof widget.settings?.variableId === 'string').map((widget) => String(widget.settings?.variableId)))]
    return { scopedOverview: reportModule.filterOverviewForVariables(dataOverview, variableKeys, page.name), contentOverview: dataOverview, definition: resolvedDefinition, page }
  }, [context.dashboard, context.overview, context.reportablePages])

  const open = useCallback(async () => {
    if (!context.overview) return
    if (!context.dashboardReady) { context.onError('현재 상세 분석 페이지를 불러온 뒤 보고서를 내보내 주세요.'); return }
    setReportError('')
    try {
      const comparisonContext = context.mode === 'comparison' ? context.comparisonReportContext : null
      if (context.mode === 'comparison' && (!comparisonContext || comparisonContext.loadCaseId !== context.selectedLoadCaseId)) throw new Error('현재 하중 경우의 기준 Run과 대상 Run 비교가 준비된 뒤 보고서를 내보낼 수 있습니다.')
      const comparisonPromise = comparisonContext ? Promise.all([
        api.runComparison(comparisonContext.loadCaseId, comparisonContext.baselineRunId, comparisonContext.targetRunId), api.runTrust(comparisonContext.targetRunId), api.reviewItems(comparisonContext.targetRunId),
      ]) : null
      const selectedPage = context.mode === 'analysis' ? context.reportablePages.find((page) => page.id === context.activeDashboardId) ?? context.reportablePages[0] : null
      if (context.mode === 'analysis' && !selectedPage) throw new Error('내보낼 분석 페이지가 없습니다.')
      const currentDashboard = context.dashboard
      const selectedPageDefinitionPromise = selectedPage ? currentDashboard?.id === selectedPage.id ? Promise.resolve(currentDashboard) : api.dashboard(selectedPage.id) : Promise.resolve(null)
      const [reportModule, layouts, templates, availableRuns, selectedPageDefinition] = await Promise.all([
        import('../../reportExport'), reportApi.layouts(), api.reportTemplates(), api.analysisRuns(context.selectedLoadCaseId), selectedPageDefinitionPromise,
      ])
      const selected = layouts.find((item) => item.id === 'report-layout-standard')?.definition ?? layouts[0]?.definition ?? reportModule.DEFAULT_REPORT_LAYOUT
      const layoutVersionsPromise = layouts.length ? reportApi.versions(selected.id) : Promise.resolve([])
      let scopedOverview = context.overview
      let contents: ReportContentItem[]
      let source: ReportSource
      let pageId: string
      if (context.mode === 'comparison') {
        if (!comparisonContext || !comparisonPromise) throw new Error('현재 하중 경우의 기준 Run과 대상 Run 비교가 준비된 뒤 보고서를 내보낼 수 있습니다.')
        const [comparison, trust, reviews] = await comparisonPromise
        contents = reportModule.createRunComparisonReportContent(comparison, trust, reviews)
        source = { kind: 'run_compare_review', loadCaseId: comparisonContext.loadCaseId, baselineRunId: comparisonContext.baselineRunId, targetRunId: comparisonContext.targetRunId }
        pageId = context.activeDashboardId
      } else {
        if (!selectedPage || !selectedPageDefinition) throw new Error('내보낼 분석 페이지를 불러오지 못했습니다.')
        const reportData = await reportDataForPage(selectedPage.id, context.overview, reportModule, selectedPageDefinition)
        scopedOverview = reportData.scopedOverview
        if (!scopedOverview.run) throw new Error('완료된 Run이 없어 보고서를 내보낼 수 없습니다.')
        contents = reportModule.createDashboardReportContent(reportData.definition, reportData.contentOverview)
        source = { kind: 'analysis_page', dashboardId: selectedPage.id, loadCaseId: context.selectedLoadCaseId, runId: scopedOverview.run }
        pageId = selectedPage.id
      }
      const layoutVersions = await layoutVersionsPromise
      setReportPageId(pageId); setReportRuns(availableRuns); setReportRunId(scopedOverview.run ?? ''); setReportOverview(scopedOverview)
      setReportDraft(reportModule.createDefaultReportOptions(scopedOverview)); setReportContents(contents); setReportSource(source); setReportLayouts(layouts)
      setReportLayoutDraft(reportModule.prepareContentReportLayout(withReportVariables(reportModule.normalizeReportLayout(selected), scopedOverview), source, contents, true))
      setReportLayoutVersions(layoutVersions); setReportTemplates(templates); setIsLayoutEditing(false)
    } catch (reason) { context.onError(reason instanceof Error ? reason.message : '보고서 레이아웃을 불러오지 못했습니다.') }
  }, [context, reportDataForPage])

  const updateForAnalysisPage = useCallback(async (pageId: string, runId: string) => {
    const currentDashboard = context.dashboard
    const dashboardPromise = currentDashboard?.id === pageId ? Promise.resolve(currentDashboard) : api.dashboard(pageId)
    const [reportModule, selectedOverview, definition] = await Promise.all([import('../../reportExport'), api.overview(context.selectedLoadCaseId, runId), dashboardPromise])
    const { scopedOverview, contentOverview } = await reportDataForPage(pageId, selectedOverview, reportModule, definition)
    const contents = reportModule.createDashboardReportContent(definition, contentOverview)
    const source: ReportSource = { kind: 'analysis_page', dashboardId: pageId, loadCaseId: context.selectedLoadCaseId, runId }
    setReportOverview(scopedOverview); setReportDraft(reportModule.createDefaultReportOptions(scopedOverview)); setReportContents(contents); setReportSource(source)
    setReportLayoutDraft((current) => current ? reportModule.prepareContentReportLayout(withReportVariables(current, scopedOverview), source, contents, true) : current)
  }, [context.dashboard, context.selectedLoadCaseId, reportDataForPage])

  const selectReportPage = useCallback(async (pageId: string) => {
    setReportError('')
    try {
      if (!reportRunId) throw new Error('보고서에 사용할 Run을 먼저 선택해 주세요.')
      await updateForAnalysisPage(pageId, reportRunId); setReportPageId(pageId)
    } catch (reason) { setReportError(reason instanceof Error ? reason.message : '분석 페이지를 보고서에 연결하지 못했습니다.') }
  }, [reportRunId, updateForAnalysisPage])

  const selectReportRun = useCallback(async (runId: string) => {
    if (!reportPageId || !runId) return
    setReportError('')
    try { await updateForAnalysisPage(reportPageId, runId); setReportRunId(runId) } catch (reason) { setReportError(reason instanceof Error ? reason.message : '선택한 Run을 보고서에 연결하지 못했습니다.') }
  }, [reportPageId, updateForAnalysisPage])

  const selectComparisonReportRun = useCallback(async (side: 'baseline' | 'target', runId: string) => {
    if (reportSource?.kind !== 'run_compare_review' || !runId) return
    const baselineRunId = side === 'baseline' ? runId : reportSource.baselineRunId
    const targetRunId = side === 'target' ? runId : reportSource.targetRunId
    if (baselineRunId === targetRunId) return
    setReportError('')
    try {
      const [reportModule, comparison, trust, reviews, targetOverview] = await Promise.all([
        import('../../reportExport'), api.runComparison(reportSource.loadCaseId, baselineRunId, targetRunId), api.runTrust(targetRunId), api.reviewItems(targetRunId), api.overview(reportSource.loadCaseId, targetRunId),
      ])
      const contents = reportModule.createRunComparisonReportContent(comparison, trust, reviews)
      const source: ReportSource = { kind: 'run_compare_review', loadCaseId: reportSource.loadCaseId, baselineRunId, targetRunId }
      context.onComparisonReportContextChanged({ loadCaseId: reportSource.loadCaseId, baselineRunId, targetRunId, comparison, trust, reviews })
      setReportOverview(targetOverview); setReportDraft(reportModule.createDefaultReportOptions(targetOverview)); setReportContents(contents); setReportSource(source)
      setReportLayoutDraft((current) => current ? reportModule.prepareContentReportLayout(withReportVariables(current, targetOverview), source, contents, true) : current)
    } catch (reason) { setReportError(reason instanceof Error ? reason.message : '선택한 Run 비교를 보고서에 연결하지 못했습니다.') }
  }, [context, reportSource])

  const selectReportLayout = useCallback(async (layoutId: string) => {
    const selected = reportLayouts.find((item) => item.id === layoutId)
    if (!selected || !reportOverview || !reportSource) return
    const [reportModule, versions] = await Promise.all([import('../../reportExport'), reportApi.versions(layoutId)])
    setReportLayoutDraft(reportModule.prepareContentReportLayout(withReportVariables(reportModule.normalizeReportLayout(selected.definition), reportOverview), reportSource, reportContents)); setReportLayoutVersions(versions)
  }, [reportContents, reportLayouts, reportOverview, reportSource])

  const selectReportLayoutVersion = useCallback(async (version: number) => {
    if (!reportLayoutDraft || !reportOverview || !reportSource) return
    try {
      const [reportModule, stored] = await Promise.all([import('../../reportExport'), reportApi.version(reportLayoutDraft.id, version)])
      setReportLayoutDraft(reportModule.prepareContentReportLayout(withReportVariables(reportModule.normalizeReportLayout(stored.definition), reportOverview), reportSource, reportContents))
    } catch (reason) { setReportError(reason instanceof Error ? reason.message : '보고서 레이아웃 버전을 불러오지 못했습니다.') }
  }, [reportContents, reportLayoutDraft, reportOverview, reportSource])

  const saveReportLayout = useCallback(async (asNew: boolean) => {
    if (!reportLayoutDraft || !reportOverview) return
    setReportError('')
    try {
      const payload = { name: reportLayoutDraft.name, description: reportLayoutDraft.description, definition: reportLayoutDraft, updated_by: '보고서 편집자' }
      const saved = asNew ? await reportApi.createLayout(payload) : await reportApi.updateLayout(reportLayoutDraft.id, payload)
      const [layouts, versions, reportModule] = await Promise.all([reportApi.layouts(), reportApi.versions(saved.id), import('../../reportExport')])
      setReportLayouts(layouts); setReportLayoutDraft(withReportVariables(reportModule.normalizeReportLayout(saved.definition), reportOverview)); setReportLayoutVersions(versions); context.onNotice(`${saved.name} v${saved.version} 저장 완료`)
    } catch (reason) { setReportError(reason instanceof Error ? reason.message : '보고서 레이아웃을 저장하지 못했습니다.') }
  }, [context, reportLayoutDraft, reportOverview])

  const uploadReportTemplate = useCallback(async (file: File) => {
    if (!reportLayoutDraft) return
    setReportError('')
    try {
      const created = await api.uploadReportTemplate(file.name.replace(/\.pptx$/i, ''), file)
      setReportTemplates((items) => [created, ...items]); setReportLayoutDraft({ ...reportLayoutDraft, templateSource: 'pptx_upload', templateAssetId: created.id, templateBindings: {} }); context.onNotice(`${created.name} 템플릿의 플레이스홀더 ${created.definition.placeholders.length}개를 인식했습니다.`)
    } catch (reason) { setReportError(reason instanceof Error ? reason.message : 'PPTX 템플릿을 업로드하지 못했습니다.') }
  }, [context, reportLayoutDraft])

  const removeReportTemplate = useCallback(async (templateId: string) => {
    try {
      await api.deleteReportTemplate(templateId); setReportTemplates((items) => items.filter((item) => item.id !== templateId))
      if (reportLayoutDraft?.templateAssetId === templateId) setReportLayoutDraft({ ...reportLayoutDraft, templateSource: 'native', templateAssetId: undefined, templateBindings: {} })
    } catch (reason) { setReportError(reason instanceof Error ? reason.message : 'PPTX 템플릿을 삭제하지 못했습니다.') }
  }, [reportLayoutDraft])

  const removeReportLayout = useCallback(async () => {
    if (!reportLayoutDraft || reportLayoutDraft.id.startsWith('report-layout-standard') || reportLayouts.find((item) => item.id === reportLayoutDraft.id)?.is_system) return
    try {
      await reportApi.deleteLayout(reportLayoutDraft.id); const layouts = await reportApi.layouts(); setReportLayouts(layouts)
      if (reportOverview && layouts[0]) setReportLayoutDraft(withReportVariables(layouts[0].definition, reportOverview))
    } catch (reason) { setReportError(reason instanceof Error ? reason.message : '보고서 레이아웃을 삭제하지 못했습니다.') }
  }, [reportLayoutDraft, reportLayouts, reportOverview])

  const downloadReport = useCallback(async () => {
    if (!reportDraft || !reportOverview || !reportLayoutDraft) return
    setReportExporting(true); setReportError('')
    try {
      const reportModule = await import('../../reportExport')
      let filename: string
      if (reportLayoutDraft.templateSource === 'pptx_upload' && reportLayoutDraft.templateAssetId) {
        if (reportContents.length) throw new Error('페이지별 위젯·Run 비교 콘텐츠는 시각적 레이아웃에서 내보내 주세요. 업로드 PPTX 바인딩은 아직 이 콘텐츠 형식을 지원하지 않습니다.')
        filename = reportModule.reportFilename(reportOverview, reportDraft)
        const blob = await api.renderReportTemplate(reportLayoutDraft.templateAssetId, reportModule.createReportTemplateReplacements(reportOverview, reportDraft, reportLayoutDraft, reportTemplates.find((item) => item.id === reportLayoutDraft.templateAssetId)), filename)
        const href = URL.createObjectURL(blob); const anchor = document.createElement('a')
        anchor.href = href; anchor.download = filename; document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(href), 1000)
      } else filename = await reportModule.exportAnalysisReport(reportOverview, reportDraft, reportLayoutDraft, reportContents)
      close(); context.onNotice(`${filename} 생성 완료`)
    } catch (reason) { setReportError(reason instanceof Error ? reason.message : 'PPTX 보고서를 생성하지 못했습니다.') } finally { setReportExporting(false) }
  }, [close, context, reportContents, reportDraft, reportLayoutDraft, reportOverview, reportTemplates])

  const updateDraft = useCallback((field: keyof ReportExportOptions, value: string) => setReportDraft((current) => current ? { ...current, [field]: value } : current), [])
  const handlePageDeleted = useCallback((pageId: string) => {
    if (reportPageId !== pageId) return
    setReportDraft(null); setReportOverview(null); setReportLayoutDraft(null); setReportPageId(''); setIsLayoutEditing(false)
  }, [reportPageId])

  const reportPageOptions = context.reportablePages
  return { close, cancel, downloadReport, handlePageDeleted, isLayoutEditing, isOpen: Boolean(reportDraft && reportOverview && reportLayoutDraft && reportSource), open, removeReportLayout, removeReportTemplate, reportContents, reportDraft, reportError, reportExporting, reportLayoutDraft, reportLayoutVersions, reportLayouts, reportOverview, reportPageId, reportPageOptions, reportRunId, reportRuns, reportSource, reportTemplates, saveReportLayout, selectComparisonReportRun, selectReportLayout, selectReportLayoutVersion, selectReportPage, selectReportRun, setIsLayoutEditing, setReportLayoutDraft, updateDraft, uploadReportTemplate }
}

export type ReportExportController = ReturnType<typeof useReportExportController>
