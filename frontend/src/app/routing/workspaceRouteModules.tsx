import { lazy } from 'react'
import type { WorkspacePage } from '../../features/auth/access'

// Keep one importer per physical module so lazy rendering and intent preloading
// resolve the same Vite module promise without loading an inactive screen.
const importAccessAdministration = () => import('../../features/access/AccessAdministration')
const importSimulationWorkbench = () => import('../../features/workbench/SimulationWorkbench')

export const AccessAdminPage = lazy(() => importAccessAdministration().then(({ AccessAdminPage }) => ({ default: AccessAdminPage })))
export const MenuPolicyAdminPage = lazy(() => importAccessAdministration().then(({ MenuPolicyAdminPage }) => ({ default: MenuPolicyAdminPage })))
export const AuditAdminPage = lazy(() => importAccessAdministration().then(({ AuditAdminPage }) => ({ default: AuditAdminPage })))
export const SimulationWorkbench = lazy(() => importSimulationWorkbench().then(({ SimulationWorkbench }) => ({ default: SimulationWorkbench })))
export const WorkbenchTypeAdmin = lazy(() => importSimulationWorkbench().then(({ WorkbenchTypeAdmin }) => ({ default: WorkbenchTypeAdmin })))

const preloadByPage: Partial<Record<WorkspacePage, () => Promise<unknown>>> = {
  access_admin: importAccessAdministration,
  menu_policy_admin: importAccessAdministration,
  audit_admin: importAccessAdministration,
  workbench: importSimulationWorkbench,
  workbench_admin: importSimulationWorkbench,
}

export function preloadWorkspaceRouteModule(page: WorkspacePage) {
  const load = preloadByPage[page]
  if (load) void load().catch(() => undefined)
}
