import { useCallback, type Dispatch, type MutableRefObject, type SetStateAction } from 'react'

import { api } from '../../api'
import type { DashboardDefinition, DashboardVersion, DashboardWidget, VariableDefinition, WidgetCatalogItem } from '../../types'

type AssistantProposal = Awaited<ReturnType<typeof api.previewCommand>> | null

type Options = {
  catalogVariable: string
  dashboard: DashboardDefinition | null
  dashboardBeforeEdit: MutableRefObject<DashboardDefinition | null>
  dashboardReady: boolean
  setAssistantOpen: (open: boolean) => void
  setCommand: (value: string) => void
  setDashboard: Dispatch<SetStateAction<DashboardDefinition | null>>
  setError: (message: string) => void
  setNotice: (message: string) => void
  setProposal: (proposal: AssistantProposal) => void
  setSelectedWidgetId: (id: string | null) => void
  setVersions: (versions: DashboardVersion[]) => void
  variables: VariableDefinition[]
  workspaceEditor: { open: (mode: 'analysis-dashboard') => void }
}

/** Dashboard-only assistant, widget, and version actions. */
export function useDashboardAssistantController({
  catalogVariable, dashboard, dashboardBeforeEdit, dashboardReady, setAssistantOpen,
  setCommand, setDashboard, setError, setNotice, setProposal, setSelectedWidgetId,
  setVersions, variables, workspaceEditor,
}: Options) {
  const previewCommand = useCallback(async (command: string) => {
    if (!command.trim()) return
    if (!dashboardReady) { setError('현재 상세 분석 페이지를 불러온 뒤 자연어 개선을 실행해 주세요.'); return }
    try { setProposal(await api.previewCommand(command)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '요청을 해석하지 못했습니다.') }
  }, [dashboardReady, setError, setProposal])

  const applyProposal = useCallback((proposal: AssistantProposal) => {
    if (!dashboard || !dashboardReady || !proposal?.proposal) return
    if (!dashboardBeforeEdit.current) dashboardBeforeEdit.current = structuredClone(dashboard)
    const proposedChange = proposal.proposal
    if (proposedChange.action === 'add_widget') setDashboard({ ...dashboard, widgets: [...dashboard.widgets, proposedChange.widget] })
    else setDashboard({ ...dashboard, widgets: dashboard.widgets.map((widget) => {
      const update = proposedChange.updates.find((item) => item.widget_type === widget.type)
      return update ? { ...widget, ...update, settings: { ...widget.settings, ...update.settings } } : widget
    }) })
    setProposal(null); setCommand(''); setAssistantOpen(false); workspaceEditor.open('analysis-dashboard')
    setNotice('변경안을 적용했습니다. 저장하면 새 버전이 생성됩니다.')
  }, [dashboard, dashboardBeforeEdit, dashboardReady, setAssistantOpen, setCommand, setDashboard, setNotice, setProposal, workspaceEditor])

  const removeWidget = useCallback((id: string) => {
    if (dashboard) setDashboard({ ...dashboard, widgets: dashboard.widgets.filter((item) => item.id !== id) })
  }, [dashboard, setDashboard])

  const updateWidget = useCallback((id: string, patch: Partial<DashboardWidget>) => {
    if (dashboard) setDashboard({ ...dashboard, widgets: dashboard.widgets.map((item) => item.id === id ? { ...item, ...patch, settings: { ...item.settings, ...patch.settings } } : item) })
  }, [dashboard, setDashboard])

  const addCatalogWidget = useCallback((item: WidgetCatalogItem) => {
    if (!dashboard) return
    if (!dashboardBeforeEdit.current) dashboardBeforeEdit.current = structuredClone(dashboard)
    const variable = item.allowed_data_types.length === 0 || item.type === 'video_grid' ? undefined : variables.find((entry) => entry.id === catalogVariable)
    if (variable && !item.allowed_data_types.includes(variable.data_type)) { setNotice(`${variable.display_name}에는 ${item.label}을 사용할 수 없습니다.`); return }
    const [w, h] = item.default_size
    setDashboard({ ...dashboard, widgets: [...dashboard.widgets, { id: `${item.type}-${Date.now()}`, type: item.type, title: variable ? `${variable.display_name} · ${item.label}` : item.label, x: 0, y: 30, w, h, settings: { variableId: variable?.id } }] })
    workspaceEditor.open('analysis-dashboard'); setNotice('위젯을 추가했습니다. 위치를 조정한 뒤 저장하세요.')
  }, [catalogVariable, dashboard, dashboardBeforeEdit, setDashboard, setNotice, variables, workspaceEditor])

  const cloneLayout = useCallback(async () => {
    if (!dashboard) return
    const result = await api.cloneDashboard(dashboard.id, `${dashboard.name} 복제본`, dashboard.description)
    setNotice(`복제 레이아웃 ${result.id}을 저장했습니다.`)
  }, [dashboard, setNotice])

  const restorePrevious = useCallback(async (versions: DashboardVersion[]) => {
    if (!dashboard) return
    const previous = versions.find((item) => item.version < (dashboard.version ?? 1) && item.is_valid)
    if (!previous) { setNotice('복구할 이전 정상 버전이 없습니다.'); return }
    await api.restoreDashboard(dashboard.id, previous.version)
    setDashboard(await api.dashboard(dashboard.id)); setVersions(await api.dashboardVersions(dashboard.id)); setNotice(`v${previous.version} 내용을 새 버전으로 복구했습니다.`)
  }, [dashboard, setDashboard, setNotice, setVersions])

  const loadDashboardVersionDraft = useCallback(async (version: number) => {
    if (!dashboard) return
    try {
      const stored = await api.dashboardVersion(dashboard.id, version)
      if (!dashboardBeforeEdit.current) dashboardBeforeEdit.current = structuredClone(dashboard)
      const draft = dashboard.page ? { ...stored.definition, id: dashboard.id, name: dashboard.name, description: dashboard.description, page: dashboard.page } : { ...stored.definition, id: dashboard.id }
      setDashboard({ ...draft, version: dashboard.version, updated_at: dashboard.updated_at })
      setSelectedWidgetId(null); workspaceEditor.open('analysis-dashboard')
      setNotice(`v${version} 내용을 현재 편집 초안으로 불러왔습니다. 저장하면 새 버전이 생성됩니다.`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '과거 버전을 불러오지 못했습니다.') }
  }, [dashboard, dashboardBeforeEdit, setDashboard, setError, setNotice, setSelectedWidgetId, workspaceEditor])

  const removeDashboardVersion = useCallback(async (version: number) => {
    if (!dashboard) return
    try { await api.deleteDashboardVersion(dashboard.id, version); setVersions(await api.dashboardVersions(dashboard.id)); setNotice(`v${version} 과거 이력을 삭제했습니다.`) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '과거 버전을 삭제하지 못했습니다.') }
  }, [dashboard, setError, setNotice, setVersions])

  const loadSavedDashboard = useCallback(async (id: string) => {
    setDashboard(await api.dashboard(id)); setNotice('저장된 레이아웃을 불러왔습니다.')
  }, [setDashboard, setNotice])

  return { addCatalogWidget, applyProposal, cloneLayout, loadDashboardVersionDraft, loadSavedDashboard, previewCommand, removeDashboardVersion, removeWidget, restorePrevious, updateWidget }
}
