import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api } from '../../api'
import type { WorkspaceEditorMode } from '../../editorState'
import type { InitialWorkspace } from '../../features/bootstrap/loadInitialWorkspace'
import { pageView, preferredPage, type ActiveView } from '../../features/analysis/pageSelection'
import { DEFAULT_WORKFLOW_DASHBOARD_LAYOUT, loadWorkflowDashboardLayout } from '../../features/layouts/layoutDefaults'
import { useResultVersionSelection } from '../../features/results/useResultVersionSelection'
import type { AnalysisRequest, DashboardPageSummary, LoadCase, Overview, Project, QualityThreshold, Workflow, WorkflowDashboardLayout, WorkflowStep } from '../../types'

type Editor = {
  close: () => void
  mode: WorkspaceEditorMode
  open: (mode: Exclude<WorkspaceEditorMode, null>) => void
}

type Options = {
  initialWorkspace: InitialWorkspace | null
  resultAccessEnabled: boolean
  onError: (message: string) => void
  onNotice: (message: string) => void
  workspaceEditor: Editor
}

const workflowSignature = (steps: WorkflowStep[]) => JSON.stringify(steps.map((step) => ({
  id: step.id,
  name: step.name,
  status: step.status,
  owner_user_id: step.owner_user_id,
  progress: step.progress,
  is_optional: step.is_optional,
  note: step.note ?? '',
})))

/**
 * Owns request selection, request/workflow context loading, and the two
 * workflow editing drafts. Dashboard-widget state deliberately lives in the
 * results feature, so this controller remains usable for monitoring-only UI.
 */
export function useRequestWorkspaceController({ initialWorkspace, resultAccessEnabled, onError, onNotice, workspaceEditor }: Options) {
  const [databaseBackend, setDatabaseBackend] = useState<'duckdb' | 'postgresql'>('duckdb')
  const [projects, setProjects] = useState<Project[]>([])
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [requests, setRequests] = useState<AnalysisRequest[]>([])
  const [loadCases, setLoadCases] = useState<LoadCase[]>([])
  const [thresholds, setThresholds] = useState<QualityThreshold[]>([])
  const [selectedProjectId, setSelectedProjectId] = useState('')
  const [selectedRequestId, setSelectedRequestId] = useState('')
  const [selectedLoadCaseId, setSelectedLoadCaseId] = useState('')
  const [overview, setOverview] = useState<Overview | null>(null)
  const [analysisPages, setAnalysisPages] = useState<DashboardPageSummary[]>([])
  const [activeDashboardId, setActiveDashboardId] = useState('dashboard-drop-default')
  const [activeView, setActiveView] = useState<ActiveView>('workflow')
  const [workflowDashboardLayout, setWorkflowDashboardLayout] = useState<WorkflowDashboardLayout>(loadWorkflowDashboardLayout)
  const [workflowLayoutVersion, setWorkflowLayoutVersion] = useState(1)
  const [operationalRefreshToken, setOperationalRefreshToken] = useState(0)
  const contextLoadSequence = useRef(0)
  const workflowsBeforeEdit = useRef<Workflow[] | null>(null)
  const workflowLayoutBeforeEdit = useRef<WorkflowDashboardLayout | null>(null)

  useEffect(() => {
    if (!initialWorkspace) return
    setDatabaseBackend(initialWorkspace.databaseBackend)
    setProjects(initialWorkspace.projects)
    setWorkflows(initialWorkspace.workflows)
    setRequests([])
    setLoadCases([])
    setThresholds([])
    setSelectedProjectId('')
    setSelectedRequestId('')
    setSelectedLoadCaseId('')
    setOverview(null)
    setAnalysisPages([])
    setActiveView('workflow')
    if (initialWorkspace.kind === 'empty') return
    setSelectedProjectId(initialWorkspace.selectedProjectId)
    setRequests(initialWorkspace.requests)
    setSelectedRequestId(initialWorkspace.selectedRequestId)
    if (initialWorkspace.kind === 'setup') return
    setLoadCases(initialWorkspace.loadCases)
    setThresholds(initialWorkspace.thresholds)
    setSelectedLoadCaseId(initialWorkspace.selectedLoadCaseId)
    setOverview(initialWorkspace.overview)
    setAnalysisPages(initialWorkspace.analysisPages)
    setActiveDashboardId(initialWorkspace.dashboardId)
    setWorkflowDashboardLayout(initialWorkspace.workflowLayout.definition)
    setWorkflowLayoutVersion(initialWorkspace.workflowLayout.version)
  }, [initialWorkspace])

  const { analysisRuns, selectedAnalysisRunId, analysisRunsLoading, analysisRunChanging, analysisRunError, selectAnalysisRun } = useResultVersionSelection({
    loadCaseId: selectedLoadCaseId,
    enabled: resultAccessEnabled,
    overview,
    setOverview,
  })

  const loadContext = useCallback(async (projectId: string, requestId?: string, preferredView?: ActiveView) => {
    const sequence = ++contextLoadSequence.current
    const requestData = await api.requests(projectId)
    if (sequence !== contextLoadSequence.current) return
    const request = requestData.find((item) => item.id === requestId) ?? requestData[0]
    const [caseData, thresholdData, storedWorkflowLayout] = await Promise.all([
      request ? api.loadCases(request.id) : Promise.resolve([] as LoadCase[]),
      api.qualityThresholds(projectId),
      api.workspaceLayout(projectId, 'workflow'),
    ])
    if (sequence !== contextLoadSequence.current) return
    if (!request) {
      setRequests([])
      setLoadCases([])
      setThresholds(thresholdData)
      setSelectedProjectId(projectId)
      setSelectedRequestId('')
      setSelectedLoadCaseId('')
      setOverview(null)
      setAnalysisPages([])
      setActiveDashboardId('pending-open-cell')
      setActiveView('workflow')
      setWorkflowDashboardLayout(storedWorkflowLayout.definition)
      setWorkflowLayoutVersion(storedWorkflowLayout.version)
      return
    }
    const loadCase = caseData[0]
    if (!loadCase) throw new Error('선택한 의뢰에 하중 경우가 없습니다.')
    const [overviewData, pageData] = await Promise.all([api.overview(loadCase.id), api.dashboardPages(loadCase.id)])
    if (sequence !== contextLoadSequence.current) return
    const selectedPage = preferredPage(pageData, overviewData, preferredView)
    setRequests(requestData)
    setLoadCases(caseData)
    setThresholds(thresholdData)
    setSelectedProjectId(projectId)
    setSelectedRequestId(request.id)
    setSelectedLoadCaseId(loadCase.id)
    setOverview(overviewData)
    setAnalysisPages(pageData)
    setWorkflowDashboardLayout(storedWorkflowLayout.definition)
    setWorkflowLayoutVersion(storedWorkflowLayout.version)
    if (selectedPage) {
      setActiveDashboardId(selectedPage.id)
      setActiveView(pageView(selectedPage))
    }
  }, [])

  const loadMonitoringContext = useCallback(async (projectId: string, requestId?: string) => {
    const sequence = ++contextLoadSequence.current
    const requestData = await api.requests(projectId)
    if (sequence !== contextLoadSequence.current) return
    const request = requestData.find((item) => item.id === requestId) ?? requestData[0]
    const [caseData, thresholdData, storedWorkflowLayout] = await Promise.all([
      request ? api.loadCases(request.id) : Promise.resolve([] as LoadCase[]),
      api.qualityThresholds(projectId),
      api.workspaceLayout(projectId, 'workflow'),
    ])
    if (sequence !== contextLoadSequence.current) return
    if (!request) {
      setRequests([])
      setLoadCases([])
      setThresholds(thresholdData)
      setSelectedProjectId(projectId)
      setSelectedRequestId('')
      setSelectedLoadCaseId('')
      setOverview(null)
      setAnalysisPages([])
      setActiveDashboardId('pending-open-cell')
      setActiveView('workflow')
      setWorkflowDashboardLayout(storedWorkflowLayout.definition)
      setWorkflowLayoutVersion(storedWorkflowLayout.version)
      return
    }
    setRequests(requestData)
    setLoadCases(caseData)
    setThresholds(thresholdData)
    setSelectedProjectId(projectId)
    setSelectedRequestId(request.id)
    setSelectedLoadCaseId(caseData[0]?.id ?? '')
    setWorkflowDashboardLayout(storedWorkflowLayout.definition)
    setWorkflowLayoutVersion(storedWorkflowLayout.version)
    if (caseData[0]) {
      const [overviewData, pageData] = await Promise.all([api.overview(caseData[0].id), api.dashboardPages(caseData[0].id)])
      if (sequence !== contextLoadSequence.current) return
      const selectedPage = preferredPage(pageData, overviewData)
      setOverview(overviewData)
      setAnalysisPages(pageData)
      if (selectedPage) setActiveDashboardId(selectedPage.id)
    }
    setActiveView('workflow')
  }, [])

  const selectProject = useCallback(async (projectId: string) => {
    try {
      if (activeView === 'workflow') await loadMonitoringContext(projectId)
      else await loadContext(projectId)
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : '프로젝트를 변경하지 못했습니다.')
    }
  }, [activeView, loadContext, loadMonitoringContext, onError])

  const selectRequest = useCallback(async (requestId: string) => {
    try {
      if (activeView === 'workflow') await loadMonitoringContext(selectedProjectId, requestId)
      else await loadContext(selectedProjectId, requestId)
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : '의뢰를 변경하지 못했습니다.')
    }
  }, [activeView, loadContext, loadMonitoringContext, onError, selectedProjectId])

  const selectLoadCase = useCallback(async (loadCaseId: string) => {
    const sequence = ++contextLoadSequence.current
    try {
      const [overviewData, pageData] = await Promise.all([api.overview(loadCaseId), api.dashboardPages(loadCaseId)])
      if (sequence !== contextLoadSequence.current) return
      const selectedPage = preferredPage(pageData, overviewData)
      setSelectedLoadCaseId(loadCaseId)
      setOverview(overviewData)
      setAnalysisPages(pageData)
      if (selectedPage) {
        setActiveDashboardId(selectedPage.id)
        setActiveView(pageView(selectedPage))
      }
    } catch (reason) {
      if (sequence === contextLoadSequence.current) onError(reason instanceof Error ? reason.message : '하중 경우를 변경하지 못했습니다.')
    }
  }, [onError])

  const beginWorkflowStageEditing = useCallback(() => {
    workflowsBeforeEdit.current = structuredClone(workflows)
    workspaceEditor.open('workflow-stages')
  }, [workflows, workspaceEditor])

  const beginWorkflowLayoutEditing = useCallback(() => {
    workflowLayoutBeforeEdit.current = structuredClone(workflowDashboardLayout)
    workspaceEditor.open('workflow-layout')
  }, [workflowDashboardLayout, workspaceEditor])

  const cancelWorkflowEditing = useCallback(() => {
    if (workflowsBeforeEdit.current) setWorkflows(workflowsBeforeEdit.current)
    if (workflowLayoutBeforeEdit.current) setWorkflowDashboardLayout(workflowLayoutBeforeEdit.current)
    workflowsBeforeEdit.current = null
    workflowLayoutBeforeEdit.current = null
    workspaceEditor.close()
  }, [workspaceEditor])

  const resetWorkflowDashboardLayout = useCallback(() => {
    setWorkflowDashboardLayout({ ...DEFAULT_WORKFLOW_DASHBOARD_LAYOUT, items: [] })
    onNotice('진행 현황 기본 레이아웃을 미리 적용했습니다. 저장하거나 취소할 수 있습니다.')
  }, [onNotice])

  const saveWorkflowEditing = useCallback(async () => {
    if (workspaceEditor.mode === 'workflow-layout') {
      try {
        const stored = await api.saveWorkspaceLayout(selectedProjectId, 'workflow', workflowDashboardLayout)
        setWorkflowDashboardLayout(stored.definition)
        setWorkflowLayoutVersion(stored.version)
        workflowLayoutBeforeEdit.current = null
        workspaceEditor.close()
        onNotice(`진행 현황 대시보드 레이아웃 v${stored.version}을 저장했습니다.`)
      } catch (reason) {
        onError(reason instanceof Error ? reason.message : '진행 현황 레이아웃을 저장하지 못했습니다.')
      }
      return
    }
    const original = workflowsBeforeEdit.current
    if (!original) { workspaceEditor.close(); return }
    const originalByRequest = new Map(original.map((workflow) => [workflow.request.id, workflow]))
    const changed = workflows.filter((workflow) => workflowSignature(workflow.steps) !== workflowSignature(originalByRequest.get(workflow.request.id)?.steps ?? []))
    const invalid = changed.flatMap((workflow) => workflow.steps).find((step) => step.name.trim().length < 2 || !step.owner.trim() || step.progress < 0 || step.progress > 100)
    if (invalid) { onError('단계 이름은 두 글자 이상, 담당자는 필수이며 진행률은 0~100이어야 합니다.'); return }
    try {
      await Promise.all(changed.map((workflow) => api.replaceWorkflowSteps(workflow.request.id, workflow.steps.map((step) => ({
        id: step.id.startsWith('draft-step-') ? null : step.id,
        name: step.name.trim(),
        status: step.status,
        owner_user_id: step.owner_user_id ?? workflow.request.owner_user_id ?? '',
        progress: step.progress,
        is_optional: step.is_optional,
        note: step.note ?? '',
      })))))
      setWorkflows(await api.workflows())
      workflowsBeforeEdit.current = null
      workspaceEditor.close()
      onNotice(changed.length ? `의뢰 ${changed.length}건의 진행 단계를 저장했습니다.` : '변경된 작업 단계가 없습니다.')
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : '작업 단계를 저장하지 못했습니다.')
    }
  }, [onError, onNotice, selectedProjectId, workflowDashboardLayout, workflows, workspaceEditor])

  const updateWorkflowStepDraft = useCallback((stepId: string, patch: Partial<WorkflowStep>) => {
    setWorkflows((items) => items.map((workflow) => {
      if (!workflow.steps.some((step) => step.id === stepId)) return workflow
      const steps = workflow.steps.map((step) => step.id === stepId ? { ...step, ...patch } : step)
      return { ...workflow, steps, progress: Math.round(steps.reduce((sum, step) => sum + step.progress, 0) / Math.max(steps.length, 1)) }
    }))
  }, [])

  const addWorkflowStepDraft = useCallback((requestId: string) => {
    setWorkflows((items) => items.map((workflow) => {
      if (workflow.request.id !== requestId) return workflow
      const steps = [...workflow.steps, {
        id: `draft-step-${crypto.randomUUID()}`,
        sequence_no: workflow.steps.length + 1,
        name: '새 진행 단계',
        status: 'WAITING' as const,
        owner: workflow.request.owner || '미지정',
        owner_user_id: workflow.request.owner_user_id ?? null,
        progress: 0,
        planned_end: workflow.request.due_at,
        is_optional: false,
        note: '',
      }]
      return { ...workflow, steps, progress: Math.round(steps.reduce((sum, step) => sum + step.progress, 0) / steps.length) }
    }))
  }, [])

  const deleteWorkflowStepDraft = useCallback((requestId: string, stepId: string) => {
    setWorkflows((items) => items.map((workflow) => {
      if (workflow.request.id !== requestId) return workflow
      if (workflow.steps.length <= 1) { onNotice('의뢰에는 최소 한 개의 진행 단계가 필요합니다.'); return workflow }
      const steps = workflow.steps.filter((step) => step.id !== stepId).map((step, index) => ({ ...step, sequence_no: index + 1 }))
      return { ...workflow, steps, progress: Math.round(steps.reduce((sum, step) => sum + step.progress, 0) / steps.length) }
    }))
  }, [onNotice])

  const moveWorkflowStepDraft = useCallback((requestId: string, stepId: string, offset: -1 | 1) => {
    setWorkflows((items) => items.map((workflow) => {
      if (workflow.request.id !== requestId) return workflow
      const index = workflow.steps.findIndex((step) => step.id === stepId)
      const target = index + offset
      if (index < 0 || target < 0 || target >= workflow.steps.length) return workflow
      const steps = [...workflow.steps]
      ;[steps[index], steps[target]] = [steps[target], steps[index]]
      return { ...workflow, steps: steps.map((step, stepIndex) => ({ ...step, sequence_no: stepIndex + 1 })) }
    }))
  }, [])

  const refreshOperationalData = useCallback(async () => {
    const [projectData, workflowData] = await Promise.all([api.projects(), api.workflows()])
    setProjects(projectData)
    setWorkflows(workflowData)
    setSelectedProjectId((current) => current || projectData[0]?.id || '')
    setOperationalRefreshToken((value) => value + 1)
  }, [])

  const handleIntakeCreated = useCallback(async (projectId: string, request: AnalysisRequest) => {
    const [projectData, workflowData, requestData] = await Promise.all([api.projects(), api.workflows(), api.requests(projectId)])
    setProjects(projectData)
    setWorkflows(workflowData)
    setRequests(requestData)
    setSelectedProjectId(projectId)
    setSelectedRequestId(request.id)
    setLoadCases([])
    setSelectedLoadCaseId('')
    setOperationalRefreshToken((value) => value + 1)
    onNotice(`${request.title} 의뢰를 접수했습니다.`)
  }, [onNotice])

  const loadImportedContext = useCallback(async (projectId: string, requestId: string, loadCaseId: string) => {
    const [requestData, caseData, thresholdData, overviewData, pageData] = await Promise.all([
      api.requests(projectId), api.loadCases(requestId), api.qualityThresholds(projectId), api.overview(loadCaseId), api.dashboardPages(loadCaseId),
    ])
    const selectedPage = preferredPage(pageData, overviewData)
    setRequests(requestData)
    setLoadCases(caseData)
    setThresholds(thresholdData)
    setSelectedProjectId(projectId)
    setSelectedRequestId(requestId)
    setSelectedLoadCaseId(loadCaseId)
    setOverview(overviewData)
    setAnalysisPages(pageData)
    if (selectedPage) {
      setActiveDashboardId(selectedPage.id)
      setActiveView(pageView(selectedPage))
    }
  }, [])

  const projectWorkflows = useMemo(() => workflows.filter((workflow) => workflow.request.project_id === selectedProjectId), [selectedProjectId, workflows])
  const selectedWorkflow = useMemo(() => workflows.find((workflow) => workflow.request.id === selectedRequestId), [selectedRequestId, workflows])

  return {
    activeDashboardId, activeView, addWorkflowStepDraft, analysisPages, beginWorkflowLayoutEditing,
    beginWorkflowStageEditing, cancelWorkflowEditing, databaseBackend, deleteWorkflowStepDraft,
    handleIntakeCreated, loadContext, loadImportedContext, loadMonitoringContext, loadCases, moveWorkflowStepDraft,
    operationalRefreshToken, overview, projectWorkflows, projects, refreshOperationalData, requests,
    resetWorkflowDashboardLayout, saveWorkflowEditing, selectLoadCase, selectProject, selectRequest,
    selectedLoadCaseId, selectedProjectId, selectedRequestId, selectedWorkflow, setActiveDashboardId,
    setActiveView, setAnalysisPages, setOverview, setRequests, setSelectedRequestId, setThresholds,
    setWorkflows, setWorkflowDashboardLayout, thresholds, updateWorkflowStepDraft, workflowDashboardLayout,
    analysisRuns, selectedAnalysisRunId, analysisRunsLoading, analysisRunChanging, analysisRunError, selectAnalysisRun,
    workflowLayoutVersion, workflows,
  }
}
