import { lazy } from 'react'
import type { WorkspacePage } from '../../features/auth/access'

const importAccessAdministration = () => import('../../features/access/AccessAdministration')
const importSimulationWorkbench = () => import('../../features/workbench/SimulationWorkbench')
const importProjectResultProfileBinding = () => import('../../features/workbench/ProjectResultProfileBinding')

export const AccessAdminPage = lazy(() => importAccessAdministration().then(({ AccessAdminPage }) => ({ default: AccessAdminPage })))
export const MenuPolicyAdminPage = lazy(() => importAccessAdministration().then(({ MenuPolicyAdminPage }) => ({ default: MenuPolicyAdminPage })))
export const AuditAdminPage = lazy(() => importAccessAdministration().then(({ AuditAdminPage }) => ({ default: AuditAdminPage })))
export const SimulationWorkbench = lazy(() => importSimulationWorkbench().then(({ SimulationWorkbench }) => ({ default: SimulationWorkbench })))
export const ProjectResultProfileBinding = lazy(() => importProjectResultProfileBinding().then(({ ProjectResultProfileBinding }) => ({ default: ProjectResultProfileBinding })))
export const WorkbenchTypeAdmin = lazy(() => importSimulationWorkbench().then(({ WorkbenchTypeAdmin }) => ({ default: WorkbenchTypeAdmin })))

const preloadByPage: Partial<Record<WorkspacePage, () => Promise<unknown>>> = {
  access_admin: importAccessAdministration,
  menu_policy_admin: importAccessAdministration,
  audit_admin: importAccessAdministration,
  workbench: importSimulationWorkbench,
  workbench_admin: importSimulationWorkbench,
  project_result_profiles: importProjectResultProfileBinding,
}

export function preloadWorkspaceRouteModule(page: WorkspacePage) { void preloadByPage[page]?.().catch(() => undefined) }
