import { useCallback, useMemo, useState, type Dispatch, type SetStateAction } from 'react'
import type { DashboardDefinition, DashboardPageSummary, Overview } from '../../types'
import { resultLayoutApi } from '../../shared/api/resultLayouts'

type ActiveView = 'open_cell' | 'chassis' | 'custom' | 'workflow' | 'compare'
type UseRequestResultSnapshotArgs = {
  activeDashboardId: string
  selectedProjectId: string
  selectedRequestId: string
  selectedLoadCaseId: string
  overview: Overview | null
  analysisPages: DashboardPageSummary[]
  dashboard: DashboardDefinition | null
  dashboardReady: boolean
  shouldPrepareDashboard: boolean
  visiblePages: (pages: DashboardPageSummary[], overview: Overview) => DashboardPageSummary[]
  canEditActiveDashboard: boolean
  setDashboard: (value: DashboardDefinition | null) => void
  setDashboardLoading: (value: boolean) => void
  setAnalysisPages: Dispatch<SetStateAction<DashboardPageSummary[]>>
  setActiveDashboardId: (value: string) => void
  setActiveView: (value: ActiveView) => void
  setAssistantOpen: (value: boolean) => void
  setError: (value: string) => void
}

export function useRequestResultSnapshot({ activeDashboardId, selectedProjectId, selectedRequestId, selectedLoadCaseId, overview, analysisPages, visiblePages, dashboard, dashboardReady, shouldPrepareDashboard, canEditActiveDashboard, setDashboard, setDashboardLoading, setAnalysisPages, setActiveDashboardId, setActiveView, setAssistantOpen, setError }: UseRequestResultSnapshotArgs) {
  const [snapshotDashboard, setSnapshotDashboard] = useState<DashboardDefinition | null>(null)
  const [snapshotSourcePageId, setSnapshotSourcePageId] = useState<string | null>(null)
  const snapshotReportPage = useMemo<DashboardPageSummary | null>(() => snapshotDashboard ? { id: snapshotDashboard.id, project_id: selectedProjectId, request_id: selectedRequestId, load_case_id: selectedLoadCaseId, name: snapshotDashboard.name, description: snapshotDashboard.description, version: snapshotDashboard.version ?? 1, updated_at: snapshotDashboard.updated_at ?? '', page: snapshotDashboard.page ?? { kind: 'analysis_page', analysis_key: 'custom', status: 'draft', display_order: 0, is_system: false } } : null, [selectedLoadCaseId, selectedProjectId, selectedRequestId, snapshotDashboard])
  const standardReportablePages = overview ? visiblePages(analysisPages, overview).filter((page) => page.page.analysis_key !== 'run_comparison' && (page.page.is_system || page.page.status === 'published' || (page.id === activeDashboardId && canEditActiveDashboard))) : []
  const reportablePages = activeDashboardId === 'request-result-layout' && snapshotReportPage ? [snapshotReportPage, ...standardReportablePages.filter((page) => page.id !== snapshotReportPage.id)] : standardReportablePages
  const hydrateSnapshotDashboard = useCallback((page: DashboardDefinition) => {
    const sourcePageId = page.id
    const virtual: DashboardDefinition = { ...page, id: 'request-result-layout', widgets: page.widgets.map((widget) => { const variableKey = widget.settings?.variable_key; return variableKey && widget.settings?.variableId == null ? { ...widget, settings: { ...widget.settings, variableId: variableKey } } : widget }), page: { kind: 'analysis_page', analysis_key: 'custom', status: 'draft', display_order: 0, is_system: false, ...page.page } }
    setSnapshotSourcePageId(sourcePageId)
    setSnapshotDashboard(virtual)
    if (activeDashboardId === 'request-result-layout') { setDashboard(virtual); setDashboardLoading(false) }
  }, [activeDashboardId, setDashboard, setDashboardLoading])
  const materializeSnapshotDashboard = async () => {
    if (!selectedLoadCaseId) throw new Error('하중 경우를 선택한 뒤 대시보드를 편집할 수 있습니다.')
    const materialized = await resultLayoutApi.materializeRequestResultLayout(selectedRequestId, selectedLoadCaseId, snapshotSourcePageId ?? undefined)
    setSnapshotDashboard(null)
    setSnapshotSourcePageId(null)
    setDashboard(materialized)
    setDashboardLoading(false)
    setActiveDashboardId(materialized.id)
    setActiveView('custom')
    const materializedPage = { id: materialized.id, project_id: selectedProjectId, request_id: selectedRequestId, load_case_id: selectedLoadCaseId, name: materialized.name, description: materialized.description, version: materialized.version ?? 1, updated_at: materialized.updated_at ?? '', page: materialized.page ?? { kind: 'analysis_page' as const, analysis_key: 'custom' as const, status: 'draft' as const, display_order: 0, is_system: false } }
    setAnalysisPages((items) => [materializedPage, ...items.filter((item) => item.id !== materializedPage.id)])
    return materialized
  }
  const prepareEditableDashboard = async (): Promise<DashboardDefinition | null> => {
    if (activeDashboardId === 'request-result-layout') {
      try { return await materializeSnapshotDashboard() } catch (reason) { setError(reason instanceof Error ? reason.message : '대시보드 편집 준비에 실패했습니다.'); return null }
    }
    if (shouldPrepareDashboard && !dashboardReady) { setError('현재 상세 분석 페이지를 불러온 뒤 편집해 주세요.'); return null }
    return dashboard
  }
  const openAssistant = async () => {
    try { if (activeDashboardId === 'request-result-layout') await materializeSnapshotDashboard(); setAssistantOpen(true) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '대시보드 편집 준비에 실패했습니다.') }
  }
  const clearSnapshotDashboard = useCallback(() => { setSnapshotDashboard(null); setSnapshotSourcePageId(null); setDashboard(null); setDashboardLoading(true); setActiveDashboardId('request-result-layout'); setActiveView('custom') }, [setActiveDashboardId, setActiveView, setDashboard, setDashboardLoading])
  return { snapshotDashboard, reportablePages, hydrateSnapshotDashboard, prepareEditableDashboard, openAssistant, clearSnapshotDashboard }
}
