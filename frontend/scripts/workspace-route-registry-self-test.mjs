import {
  dashboardEntryForNavigation,
  WORKSPACE_ROUTES,
  workspacePathForPage,
  workspaceRouteForPathname,
} from '../src/features/navigation/workspaceRouteRegistry.ts'
import { resolveBlockedNavigation } from '../src/app/routing/navigationState.ts'
import { visibleMenuItems } from '../src/features/auth/access.ts'

const expectedPaths = {
  local_pc: '/workspace/settings/local-pc',
  portfolio: '/workspace/overview',
  dashboard: '/workspace/requests',
  intake: '/workspace/requests/new',
  workbench: '/workspace/execution',
  data: '/workspace/data',
  workbench_admin: '/workspace/admin/work-types',
  project_result_profiles: '/workspace/project/result-layouts',
  schemas: '/workspace/catalog/schemas',
  variables: '/workspace/catalog/variables',
  templates: '/workspace/catalog/templates',
  access_admin: '/workspace/admin/access',
  menu_policy_admin: '/workspace/admin/menu-policy',
  audit_admin: '/workspace/admin/audit',
  examples: '/workspace/examples',
  help: '/workspace/help',
  voc: '/workspace/voc',
}

const paths = new Set()
for (const route of WORKSPACE_ROUTES) {
  if (paths.has(route.path)) throw new Error(`duplicate workspace path: ${route.path}`)
  paths.add(route.path)
  if (expectedPaths[route.page] !== route.path) throw new Error(`unexpected canonical path for ${route.page}: ${route.path}`)
  if (workspacePathForPage(route.page) !== route.path) throw new Error(`page-to-path round trip failed for ${route.page}`)
  if (workspaceRouteForPathname(route.path)?.page !== route.page) throw new Error(`path-to-page round trip failed for ${route.path}`)
  if (workspaceRouteForPathname(`${route.path}/`)?.page !== route.page) throw new Error(`trailing slash normalization failed for ${route.path}`)
}

if (paths.size !== Object.keys(expectedPaths).length) throw new Error('canonical route registry is missing a menu')
if (workspaceRouteForPathname('/workspace/not-found')) throw new Error('unknown workspace paths must not resolve to a menu')
if (dashboardEntryForNavigation('dashboard') !== 'reset') throw new Error('sidebar dashboard navigation must reset to workflow after route commit')
if (dashboardEntryForNavigation('dashboard', 'preserve') !== 'preserve') throw new Error('imported analysis navigation must preserve its selected view')
if (dashboardEntryForNavigation('workbench') !== 'preserve') throw new Error('non-dashboard workspace routes must not reset an analysis view')
const pendingDashboardReset = { dashboardEntry: 'reset', pathname: '/workspace/overview' }
const cancelled = resolveBlockedNavigation(pendingDashboardReset, false)
if (cancelled.pending !== null || cancelled.shouldProceed) throw new Error('a cancelled blocker must discard its pending destination')
const confirmed = resolveBlockedNavigation(pendingDashboardReset, true)
if (confirmed.pending !== pendingDashboardReset || !confirmed.shouldProceed) throw new Error('a confirmed blocker must retain its pending destination')
console.log('Workspace route registry self-test passed.')

const personalUser = { id: 'personal-user', username: 'personal-user', display_name: 'Personal user', employee_id: null, account_status: 'ACTIVE', is_global_admin: false, memberships: [], company_permissions: [] }
if (!visibleMenuItems(null, personalUser, '').some((item) => item.id === 'local_pc')) throw new Error('personal settings must be available without project or policy')
const hiddenPersonalPolicy = { version: 1, updated_by: '', updated_at: '', menus: [{ id: 'local_pc', label: 'hidden', required_permission: 'system.menu_policy.manage', context_kind: 'system', sequence_no: 1, is_policy_editable: true, visibility: { general: false, power: false, admin: false } }] }
if (visibleMenuItems(hiddenPersonalPolicy, personalUser, '').filter((item) => item.id === 'local_pc').length !== 1) throw new Error('personal settings must remain a single built-in menu')
for (const account_status of ['PENDING', 'SUSPENDED']) {
  if (visibleMenuItems(null, { ...personalUser, account_status }, '').length) throw new Error('inactive accounts must not get personal menu access')
}
console.log('Personal PC default menu self-test passed.')
