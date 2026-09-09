import type { AuthUser, ProjectRole } from '../../auth'

export type Permission =
  | 'company.dashboard.view'
  | 'project.data.view'
  | 'report.export'
  | 'work.execute_assigned'
  | 'work.execute_any'
  | 'request.create'
  | 'request.edit'
  | 'workflow.edit'
  | 'result.import'
  | 'result.review'
  | 'dashboard.edit'
  | 'project.layout.edit'
  | 'project.threshold.manage'
  | 'project.variable.manage'
  | 'project.member.manage'
  | 'project.invitation.create'
  | 'system.catalog.manage'
  | 'system.user.approve'
  | 'system.menu_policy.manage'
  | 'audit.view'

export type MenuId = 'portfolio' | 'dashboard' | 'intake' | 'workbench' | 'data' | 'workbench_admin' | 'project_result_profiles' | 'variables' | 'templates' | 'schemas' | 'examples' | 'help' | 'access_admin' | 'menu_policy_admin' | 'audit_admin' | 'local_pc'
export type WorkspacePage = MenuId
export type MenuContext = 'company' | 'project' | 'system'
export type MenuPolicyItem = {
  id: MenuId
  label: string
  required_permission: Permission
  context_kind: MenuContext
  sequence_no: number
  is_policy_editable: boolean
  visibility: Record<ProjectRole, boolean>
}
export type MenuPolicy = { version: number; updated_by: string; updated_at: string; menus: MenuPolicyItem[] }

const companyPermissions = new Set<Permission>(['company.dashboard.view', 'project.data.view', 'report.export'])
const generalPermissions = new Set<Permission>([...companyPermissions, 'work.execute_assigned'])
const powerPermissions = new Set<Permission>([...generalPermissions, 'request.create', 'request.edit', 'workflow.edit', 'result.import', 'result.review'])
const adminPermissions = new Set<Permission>([...powerPermissions, 'dashboard.edit', 'project.layout.edit', 'project.threshold.manage', 'project.variable.manage', 'project.member.manage', 'project.invitation.create'])
const globalPermissions = new Set<Permission>([...adminPermissions, 'work.execute_any', 'system.catalog.manage', 'system.user.approve', 'system.menu_policy.manage', 'audit.view'])
const permissionsByRole: Record<ProjectRole, ReadonlySet<Permission>> = { general: generalPermissions, power: powerPermissions, admin: adminPermissions }

export function effectiveRole(user: AuthUser, projectId: string): ProjectRole | null {
  return user.memberships.find((item) => item.project_id === projectId)?.role ?? null
}

export function policyRole(user: AuthUser, projectId: string): ProjectRole {
  return effectiveRole(user, projectId) ?? 'general'
}

export function permissionsFor(user: AuthUser, projectId: string): ReadonlySet<Permission> {
  if (user.account_status !== 'ACTIVE') return new Set()
  if (user.is_global_admin) return globalPermissions
  const role = effectiveRole(user, projectId)
  const result = new Set<Permission>(user.company_permissions.filter((item): item is Permission => companyPermissions.has(item as Permission)))
  if (role) permissionsByRole[role].forEach((permission) => result.add(permission))
  return result
}

export function hasPermission(user: AuthUser | null, permission: Permission, projectId: string): boolean {
  return Boolean(user && permissionsFor(user, projectId).has(permission))
}

export function isMenuVisible(menu: MenuPolicyItem, user: AuthUser | null, projectId: string): boolean {
  if (!user || user.account_status !== 'ACTIVE') return false
  const permissions = permissionsFor(user, projectId)
  if (!permissions.has(menu.required_permission)) return false
  if (menu.context_kind === 'project' && !projectId && !user.is_global_admin) return false
  if (menu.context_kind === 'system' && !user.is_global_admin) return false
  return user.is_global_admin || menu.visibility[policyRole(user, projectId)] === true
}

export function visibleMenuItems(policy: MenuPolicy | null, user: AuthUser | null, projectId: string): MenuPolicyItem[] {
  if (!user || user.account_status !== 'ACTIVE') return []
  // Personal PC settings are a built-in account feature, independent of the
  // administrator's project/work menus and available before a project exists.
  const personal: MenuPolicyItem = { id: 'local_pc', label: '내 PC 설정', required_permission: 'company.dashboard.view', context_kind: 'company', sequence_no: 35, is_policy_editable: false, visibility: { general: true, power: true, admin: true } }
  return [...(policy?.menus ?? []).filter((menu) => menu.id !== 'local_pc' && isMenuVisible(menu, user, projectId)), personal].sort((left, right) => left.sequence_no - right.sequence_no)
}

export function firstAllowedWorkspacePage(policy: MenuPolicy | null, user: AuthUser | null, projectId: string): WorkspacePage | null {
  return visibleMenuItems(policy, user, projectId)[0]?.id ?? null
}
