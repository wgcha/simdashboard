import type { WorkspacePage } from '../../features/auth/access'
import { preloadableScreen } from './preloadableScreen'

export const DataWorkspace = preloadableScreen(() => import('../../features/data/DataWorkspace').then((m) => ({ default: m.DataWorkspace })))
export const FolderSchemaWorkspace = preloadableScreen(() => import('../../features/data/FolderSchemaWorkspace').then((m) => ({ default: m.FolderSchemaWorkspace })))
export const VariableCatalogPage = preloadableScreen(() => import('../../features/data/VariableCatalogPage').then((m) => ({ default: m.VariableCatalogPage })))
export const AutomationTemplatesPage = preloadableScreen(() => import('../../features/workbench/AutomationTemplatesPage').then((m) => ({ default: m.AutomationTemplatesPage })))
export const FeatureExampleGallery = preloadableScreen(() => import('../../features/examples/FeatureExampleGallery').then((m) => ({ default: m.FeatureExampleGallery })))
export const HelpCenter = preloadableScreen(() => import('../../features/help/HelpCenter').then((m) => ({ default: m.HelpCenter })))
export const LocalPcSettingsPage = preloadableScreen(() => import('../../features/local-pc/LocalPcSettingsPage').then((m) => ({ default: m.LocalPcSettingsPage })))
export const WorkflowView = preloadableScreen(() => import('../../features/requests/WorkflowView').then((m) => ({ default: m.WorkflowView })))
export const ResultsWorkspace = preloadableScreen(() => import('../../features/results/ResultsWorkspace').then((m) => ({ default: m.ResultsWorkspace })))
export const PendingAnalysisWorkspace = preloadableScreen(() => import('../../features/results/PendingAnalysisWorkspace').then((m) => ({ default: m.PendingAnalysisWorkspace })))
export const AnalysisPageManager = preloadableScreen(() => import('../../features/analysis/AnalysisPageManager').then((m) => ({ default: m.AnalysisPageManager })))
export const AccessAdminPage = preloadableScreen(() => import('../../features/access/AccessAdministration').then((m) => ({ default: m.AccessAdminPage })))
export const MenuPolicyAdminPage = preloadableScreen(() => import('../../features/access/AccessAdministration').then((m) => ({ default: m.MenuPolicyAdminPage })))
export const AuditAdminPage = preloadableScreen(() => import('../../features/access/AccessAdministration').then((m) => ({ default: m.AuditAdminPage })))
export const SimulationWorkbench = preloadableScreen(() => import('../../features/workbench/SimulationWorkbench').then((m) => ({ default: m.SimulationWorkbench })))
export const WorkbenchTypeAdmin = preloadableScreen(() => import('../../features/workbench/SimulationWorkbench').then((m) => ({ default: m.WorkbenchTypeAdmin })))
export const ProjectResultProfileBinding = preloadableScreen(() => import('../../features/workbench/ProjectResultProfileBinding').then((m) => ({ default: m.ProjectResultProfileBinding })))

const modulesByPage: Partial<Record<WorkspacePage, { preload: () => Promise<unknown> }[]>> = {
  local_pc: [LocalPcSettingsPage],
  dashboard: [WorkflowView, PendingAnalysisWorkspace, ResultsWorkspace],
  data: [DataWorkspace], workbench: [SimulationWorkbench],
  schemas: [FolderSchemaWorkspace], variables: [VariableCatalogPage], templates: [AutomationTemplatesPage],
  examples: [FeatureExampleGallery], help: [HelpCenter],
  access_admin: [AccessAdminPage], menu_policy_admin: [MenuPolicyAdminPage], audit_admin: [AuditAdminPage],
  workbench_admin: [WorkbenchTypeAdmin], project_result_profiles: [ProjectResultProfileBinding],
}

export function preloadWorkspaceRouteModule(page: WorkspacePage) {
  for (const screen of modulesByPage[page] ?? []) void screen.preload().catch(() => undefined)
}
