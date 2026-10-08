import {
  dashboardEntryForNavigation,
  WORKSPACE_ROUTES,
  workspacePathForPage,
  workspaceRouteForPathname,
} from '../src/features/navigation/workspaceRouteRegistry.ts'
import { resolveBlockedNavigation } from '../src/app/routing/navigationState.ts'
import { visibleMenuItems } from '../src/features/auth/access.ts'
import { applySearchPatch, withClearedChildren } from '../src/shared/hooks/caseHierarchyParams.ts'

const expectedPaths = {
  local_pc: '/workspace/settings/local-pc',
  portfolio: '/workspace/overview',
  dashboard: '/workspace/requests',
  intake: '/workspace/requests/new',
  workbench: '/workspace/execution',
  data: '/workspace/data',
  materials: '/workspace/materials',
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
  notifications: '/workspace/notifications',
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
if (visibleMenuItems(null, personalUser, '').some((item) => item.id === 'local_pc')) throw new Error('legacy personal PC settings must stay out of visible menus')
const hiddenPersonalPolicy = { version: 1, updated_by: '', updated_at: '', menus: [{ id: 'local_pc', label: 'hidden', required_permission: 'system.menu_policy.manage', context_kind: 'system', sequence_no: 1, is_policy_editable: true, visibility: { general: false, power: false, admin: false } }] }
if (visibleMenuItems(hiddenPersonalPolicy, personalUser, '').some((item) => item.id === 'local_pc')) throw new Error('legacy menu-policy entries must not restore the hidden personal PC menu')
for (const account_status of ['PENDING', 'SUSPENDED']) {
  if (visibleMenuItems(null, { ...personalUser, account_status }, '').length) throw new Error('inactive accounts must not get personal menu access')
}
console.log('Personal PC default menu self-test passed.')

// Shared Case hierarchy URL contract (Case results <-> materials tabs).
const hierarchySearch = 'project=p&request=r&view=case_results&resultTab=materials&case=c1&case_load=l1&case_run=r1&case_option=o1&scene=s1&part=7&filter=x'
const caseChange = applySearchPatch(`${hierarchySearch}&capture=cap1`, withClearedChildren({ case: 'c2' }))
if (caseChange !== 'project=p&request=r&view=case_results&resultTab=materials&case=c2&filter=x') throw new Error(`case change must clear its children only: ${caseChange}`)
const optionChange = applySearchPatch(hierarchySearch, withClearedChildren({ case_option: 'o2' }))
if (optionChange !== 'project=p&request=r&view=case_results&resultTab=materials&case=c1&case_load=l1&case_run=r1&case_option=o2&filter=x') throw new Error(`option change must clear scene/part: ${optionChange}`)
const restored = applySearchPatch('scene=s1', withClearedChildren({ case: 'c1', case_load: 'l1', case_run: 'r1', case_option: 'o1' }))
if (new URLSearchParams(restored).has('scene')) throw new Error('setting parents without scene clears the scene child')
const restoredWithScene = applySearchPatch('scene=s1', withClearedChildren({ case: 'c1', case_option: 'o1', scene: 's1' }))
if (new URLSearchParams(restoredWithScene).get('scene') !== 's1') throw new Error('an explicit child in the patch must be kept')
console.log('Case hierarchy URL self-test passed.')

// Workspace navigation contract (docs/contracts/workspace-navigation.md N1, N3).
const { scopeWorkspaceContext, searchForPageChange, usesBootstrapFallback } = await import('../src/app/routing/workspaceNavigationPolicy.ts')
const noOverview = { hasOverview: false, hasDashboard: false, caseResultsMode: false, isRequestMonitoring: false, pendingAnalysis: false, hasProjects: true, isWorkspaceIndex: false }
for (const page of ['variables', 'templates', 'examples', 'help', 'menu_policy_admin', 'audit_admin', 'workbench_admin', 'project_result_profiles', 'access_admin', 'schemas', 'portfolio', 'intake', 'workbench', 'data', 'materials', 'voc', 'local_pc']) {
  if (usesBootstrapFallback({ ...noOverview, page })) throw new Error(`${page} must render itself without an overview`)
}
if (!usesBootstrapFallback({ ...noOverview, page: 'dashboard' })) throw new Error('dashboard without overview keeps the bootstrap fallback')
if (usesBootstrapFallback({ ...noOverview, page: 'dashboard', caseResultsMode: true })) throw new Error('case results never use the fallback')
if (usesBootstrapFallback({ ...noOverview, page: 'dashboard', hasOverview: true, hasDashboard: true })) throw new Error('a loaded legacy request never uses the fallback')
if (!usesBootstrapFallback({ ...noOverview, page: 'variables', hasProjects: false })) throw new Error('first-run setup without projects keeps the fallback')
if (usesBootstrapFallback({ ...noOverview, page: 'schemas', hasProjects: false })) throw new Error('schemas stays self-rendered during first-run setup')
const caseResultsSearch = '?project=p&request=r&view=case_results&result_environment=DISTRIBUTION&capture=cap&case=c&case_load=l&case_run=u&case_option=o&scene=s&part=7&loadCase=lc&run=rn&page=pg&resultTab=materials'
for (const page of ['variables', 'templates', 'examples', 'help', 'access_admin', 'schemas', 'portfolio']) {
  const cleaned = searchForPageChange(caseResultsSearch, page)
  if (cleaned !== '?project=p&request=r') throw new Error(`${page} must keep only project/request: ${cleaned}`)
}
if (searchForPageChange('?view=case_results', 'help') !== '') throw new Error('an emptied query yields no ?')
for (const page of ['dashboard', 'workbench', 'data', 'materials']) {
  if (searchForPageChange(caseResultsSearch, page) !== caseResultsSearch) throw new Error(`${page} keeps request-workspace query as before`)
}
const fullContext = { projectId: 'p', requestId: 'r', loadCaseId: 'l', runId: 'u', view: 'workflow', pageId: 'pg', resultTab: 'materials' }
if (JSON.stringify(scopeWorkspaceContext('variables', fullContext)) !== JSON.stringify({ projectId: 'p', requestId: 'r' })) throw new Error('non-request pages sync only project/request to the URL')
if (scopeWorkspaceContext('dashboard', fullContext) !== fullContext) throw new Error('request workspace pages sync their full context as before')
console.log('Workspace navigation contract self-test passed.')
