import { Suspense } from 'react'

import { api } from '../../api'
import type { AuthUser } from '../../auth'
import type { Layout, Layouts } from 'react-grid-layout'
import { hasPermission, type MenuPolicy } from '../../features/auth/access'
import { PortfolioRoute } from '../../features/portfolio/PortfolioRoute'
import { AccessAdminPage, AuditAdminPage, MenuPolicyAdminPage, ProjectResultProfileBinding, SimulationWorkbench, WorkbenchTypeAdmin } from '../routing/workspaceRouteModules'
import { RequestIntakePage } from '../../features/workbench/RequestIntakePage'
import type { ActiveView } from '../../features/analysis/pageSelection'
import type { DashboardDefinition, DashboardPageSummary, FeatureExample, VariableDefinition, Workflow } from '../../types'
import type { usePortfolioController } from './usePortfolioController'
import type { useRequestWorkspaceController } from './useRequestWorkspaceController'
import { RequestWorkspaceRoute } from './RequestWorkspaceRoute'
import { StorageWorkspacePanel } from '../../features/storage/StorageWorkspacePanel'

import { DataWorkspace, FolderSchemaWorkspace, VariableCatalogPage, AutomationTemplatesPage, FeatureExampleGallery, HelpCenter } from '../routing/workspaceScreenModules'

export function FeatureScreenFallback() {
  return <div className="full-state">화면을 준비하고 있습니다.</div>
}

type Props = {
  authUser: AuthUser | null
  dashboard: DashboardDefinition
  editMode: boolean
  menuPolicy: MenuPolicy | null
  onBeginEditing: () => void
  onCancelEditing: () => void
  onDashboardLayoutChange: (layout: Layout[], layouts: Layouts) => void
  onFeatureExample: (example: FeatureExample) => void
  onImportedResult: (projectId: string, requestId: string, loadCaseId: string) => void
  onMenuPolicyChanged: (policy: MenuPolicy) => void
  onIntakeWorkbench: (requestId: string) => void
  onOpenPortfolioRequest: (projectId: string, requestId: string) => void
  onOpenWorkflowAnalysis: (workflow: Workflow) => void
  onRemoveWidget: (id: string) => void
  onRefreshAccess: () => void
  onSaveChassisThreshold: (value: number) => void
  onSaveOpenCellThreshold: (value: number) => void
  onSetAssistantOpen: (open: boolean) => void
  onSetComparisonContext: (context: import('../../types').RunComparisonReportContext | null) => void
  onSetPageManagerOpen: (open: boolean) => void
  onSetSelectedWidgetId: (id: string | null) => void
  onSetVariables: (items: VariableDefinition[]) => void
  onSetWorkflowDashboardLayout: ReturnType<typeof useRequestWorkspaceController>['setWorkflowDashboardLayout']
  onSwitchAnalysisPage: (page: DashboardPageSummary) => void
  onSwitchDashboardView: (view: ActiveView) => void
  onWidgetCatalogVariablesChanged: (items: VariableDefinition[]) => void
  onWorkspaceNavigate: (page: import('../../features/auth/access').MenuId, options?: { dashboardEntry?: 'preserve' | 'reset' }) => void
  portfolio: ReturnType<typeof usePortfolioController>
  requestWorkspace: ReturnType<typeof useRequestWorkspaceController>
  selectedEdges: string[]
  setSelectedEdges: (edges: string[]) => void
  variables: VariableDefinition[]
  workspacePage: import('../../features/auth/access').MenuId
  workflowEditorMode: 'layout' | 'stages' | null
  canEdit: boolean
  canManagePages: boolean
  chassisThreshold?: import('../../types').QualityThreshold
  openCellThreshold?: import('../../types').QualityThreshold
}

/** Explicit app-level adapter: maps a canonical menu route to its feature boundary. */
export function WorkspaceRouteRenderer({
  authUser, canEdit, canManagePages, chassisThreshold, dashboard, editMode, menuPolicy, onBeginEditing,
  onCancelEditing, onDashboardLayoutChange, onFeatureExample, onImportedResult, onIntakeWorkbench,
  onMenuPolicyChanged, onOpenPortfolioRequest, onOpenWorkflowAnalysis, onRefreshAccess, onRemoveWidget,
  onSaveChassisThreshold, onSaveOpenCellThreshold, onSetAssistantOpen,
  onSetComparisonContext, onSetPageManagerOpen, onSetSelectedWidgetId, onSetVariables,
  onSetWorkflowDashboardLayout, onSwitchAnalysisPage, onSwitchDashboardView, onWorkspaceNavigate,
  onWidgetCatalogVariablesChanged, openCellThreshold, portfolio, requestWorkspace, selectedEdges, setSelectedEdges, variables,
  workspacePage, workflowEditorMode,
}: Props) {
  const { activeDashboardId, activeView, addWorkflowStepDraft, analysisPages, analysisRuns, analysisRunsLoading, analysisRunChanging, analysisRunError, selectedAnalysisRunId, databaseBackend, deleteWorkflowStepDraft,
    handleIntakeCreated, loadCases, moveWorkflowStepDraft, operationalRefreshToken, overview, projectWorkflows,
    projects, refreshOperationalData, requests, resetWorkflowDashboardLayout, selectedLoadCaseId, selectedProjectId,
    selectedRequestId, selectedWorkflow, selectLoadCase, selectAnalysisRun, selectProject, selectRequest, setAnalysisPages,
    setSelectedRequestId, setWorkflows, thresholds, updateWorkflowStepDraft, workflowDashboardLayout,
    workflowLayoutVersion, workflows } = requestWorkspace

  if (workspacePage === 'portfolio') return <PortfolioRoute refreshToken={operationalRefreshToken} editMode={editMode} layout={portfolio.layout} layoutVersion={portfolio.layoutVersion} onLayoutChange={portfolio.setLayout} onCancelEdit={onCancelEditing} onResetLayout={portfolio.reset} onOpen={(projectId, requestId) => void onOpenPortfolioRequest(projectId, requestId)} />
  if (workspacePage === 'intake') return <RequestIntakePage projects={projects} createdBy={authUser?.display_name ?? '데모 사용자'} canCreate={hasPermission(authUser, 'request.create', selectedProjectId)} onCreated={handleIntakeCreated} onOpenWorkbench={onIntakeWorkbench} />
  if (workspacePage === 'workbench') return <Suspense fallback={<FeatureScreenFallback />}><SimulationWorkbench workflows={workflows} initialRequestId={selectedRequestId} currentUserId={authUser?.id ?? ''} createdBy={authUser?.display_name ?? '데모 사용자'} canExecute={hasPermission(authUser, 'work.execute_assigned', selectedProjectId) || hasPermission(authUser, 'work.execute_any', selectedProjectId)} isAdmin={hasPermission(authUser, 'work.execute_any', selectedProjectId)} onRequestSelected={setSelectedRequestId} onChanged={async (message) => { setWorkflows(await api.workflows()); }} /></Suspense>
  if (workspacePage === 'workbench_admin') return <Suspense fallback={<FeatureScreenFallback />}><WorkbenchTypeAdmin /></Suspense>
  if (workspacePage === 'project_result_profiles') return <Suspense fallback={<FeatureScreenFallback />}><ProjectResultProfileBinding projectId={selectedProjectId} /></Suspense>
  if (workspacePage === 'schemas') return <Suspense fallback={<FeatureScreenFallback />}><FolderSchemaWorkspace /></Suspense>
  if (workspacePage === 'variables') return <Suspense fallback={<FeatureScreenFallback />}><VariableCatalogPage variables={variables} overview={overview!} loadCaseId={selectedLoadCaseId} onChanged={onWidgetCatalogVariablesChanged} /></Suspense>
  if (workspacePage === 'templates') return <Suspense fallback={<FeatureScreenFallback />}><AutomationTemplatesPage /></Suspense>
  if (workspacePage === 'examples') return <Suspense fallback={<FeatureScreenFallback />}><FeatureExampleGallery onOpen={async (example) => onFeatureExample(example)} /></Suspense>
  if (workspacePage === 'help') return <Suspense fallback={<FeatureScreenFallback />}><HelpCenter onNavigate={onWorkspaceNavigate} /></Suspense>
  if (workspacePage === 'access_admin') return <Suspense fallback={<FeatureScreenFallback />}><AccessAdminPage projectId={selectedProjectId} projects={projects.filter((project) => hasPermission(authUser, 'project.member.manage', project.id))} onProjectChange={(projectId) => void selectProject(projectId)} canApproveUsers={hasPermission(authUser, 'system.user.approve', selectedProjectId)} onAccessChanged={async () => onRefreshAccess()} /></Suspense>
  if (workspacePage === 'menu_policy_admin' && menuPolicy) return <Suspense fallback={<FeatureScreenFallback />}><MenuPolicyAdminPage policy={menuPolicy} onPolicyChanged={onMenuPolicyChanged} /></Suspense>
  if (workspacePage === 'audit_admin') return <Suspense fallback={<FeatureScreenFallback />}><AuditAdminPage /></Suspense>
  if (workspacePage === 'data') return <Suspense fallback={<FeatureScreenFallback />}><DataWorkspace storagePanel={StorageWorkspacePanel} refreshToken={operationalRefreshToken} canCreateProject={hasPermission(authUser, 'system.user.approve', selectedProjectId)} canRetryImports={hasPermission(authUser, 'system.catalog.manage', selectedProjectId)} canUploadStorage={hasPermission(authUser, 'result.import', selectedProjectId)} canBindStorage={hasPermission(authUser, 'result.import', selectedProjectId)} canManageStorageRoot={Boolean(authUser?.is_global_admin)} projects={projects} initialProjectId={selectedProjectId} initialRequestId={selectedRequestId} initialLoadCaseId={selectedLoadCaseId} onDataChanged={refreshOperationalData} onOpenAnalysis={async (projectId, requestId, loadCaseId) => onImportedResult(projectId, requestId, loadCaseId)} onOpenIntake={() => onWorkspaceNavigate('intake')} /></Suspense>

  const projectSetup = Boolean(selectedProjectId && requests.length === 0)
  return <RequestWorkspaceRoute activeDashboardId={activeDashboardId} activeView={activeView} analysisPages={analysisPages} analysisRuns={analysisRuns} analysisRunsLoading={analysisRunsLoading} analysisRunChanging={analysisRunChanging} analysisRunError={analysisRunError} selectedAnalysisRunId={selectedAnalysisRunId} canEdit={projectSetup ? false : canEdit} canManagePages={projectSetup ? false : canManagePages} canCreateRequest={hasPermission(authUser, 'request.create', selectedProjectId)} chassisThreshold={chassisThreshold} dashboard={dashboard} editMode={editMode} loadCases={loadCases} onAddStep={addWorkflowStepDraft} onBeginEditing={onBeginEditing} onCancelEditing={onCancelEditing} onComparisonContext={onSetComparisonContext} onConfigureWidget={onSetSelectedWidgetId} onDashboardLayoutChange={onSetWorkflowDashboardLayout} onDeleteStep={deleteWorkflowStepDraft} onLayoutChange={onDashboardLayoutChange} onMoveStep={moveWorkflowStepDraft} onOpenAnalysis={onOpenWorkflowAnalysis} onOpenAssistant={() => onSetAssistantOpen(true)} onOpenIntake={() => onWorkspaceNavigate('intake')} onOpenPageManager={() => onSetPageManagerOpen(true)} onRemoveWidget={onRemoveWidget} onSaveChassisThreshold={onSaveChassisThreshold} onSaveOpenCellThreshold={onSaveOpenCellThreshold} onSelectAnalysisRun={selectAnalysisRun} onSelectLoadCase={selectLoadCase} onSelectProject={selectProject} onSelectRequest={selectRequest} onStepChange={updateWorkflowStepDraft} onSwitchAnalysisPage={onSwitchAnalysisPage} onSwitchView={onSwitchDashboardView} onUpdateWorkflowLayout={onSetWorkflowDashboardLayout} openCellThreshold={openCellThreshold} overview={overview!} projectName={projects.find((project) => project.id === selectedProjectId)?.name} projectSetup={projectSetup} projectWorkflows={projectWorkflows} projects={projects} requests={requests} resetWorkflowDashboardLayout={resetWorkflowDashboardLayout} selectedEdges={selectedEdges} selectedLoadCaseId={selectedLoadCaseId} selectedProjectId={selectedProjectId} selectedRequestId={selectedRequestId} selectedWorkflow={selectedWorkflow} setSelectedEdges={setSelectedEdges} workflowDashboardLayout={workflowDashboardLayout} workflowEditorMode={workflowEditorMode} workflowLayoutVersion={workflowLayoutVersion} />
}
