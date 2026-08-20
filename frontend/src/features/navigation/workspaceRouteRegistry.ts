import type { MenuContext, MenuId, Permission, WorkspacePage } from '../auth/access'

export type WorkspaceNavigationKind = 'select' | 'open-dashboard'
export type DashboardEntry = 'preserve' | 'reset'

export type WorkspaceRouteRegistryItem = {
  breadcrumb: {
    section: string
    title: string
  }
  id: MenuId
  page: WorkspacePage
  label: string
  /** Stable, shareable browser location for this workspace menu. */
  path: string
  requiredPermission: Permission
  contextKind: MenuContext
  navigationKind: WorkspaceNavigationKind
}

export const WORKSPACE_ROUTES: readonly WorkspaceRouteRegistryItem[] = [
  { id: 'portfolio', page: 'portfolio', path: '/workspace/overview', label: '운영 대시보드', breadcrumb: { section: '운영', title: '해석 운영 현황' }, requiredPermission: 'company.dashboard.view', contextKind: 'company', navigationKind: 'select' },
  { id: 'dashboard', page: 'dashboard', path: '/workspace/requests', label: '해석 의뢰 현황', breadcrumb: { section: '프로젝트', title: '해석 의뢰 현황' }, requiredPermission: 'project.data.view', contextKind: 'project', navigationKind: 'open-dashboard' },
  { id: 'intake', page: 'intake', path: '/workspace/requests/new', label: '의뢰 접수', breadcrumb: { section: '의뢰', title: '해석 의뢰 접수' }, requiredPermission: 'request.create', contextKind: 'project', navigationKind: 'select' },
  { id: 'workbench', page: 'workbench', path: '/workspace/execution', label: '해석 작업 실행', breadcrumb: { section: '실행', title: '해석 작업 실행 · DEMO ONLY' }, requiredPermission: 'project.data.view', contextKind: 'project', navigationKind: 'select' },
  { id: 'data', page: 'data', path: '/workspace/data', label: '해석 데이터 등록', breadcrumb: { section: '운영', title: '해석 데이터 등록' }, requiredPermission: 'result.import', contextKind: 'project', navigationKind: 'select' },
  { id: 'workbench_admin', page: 'workbench_admin', path: '/workspace/admin/work-types', label: '작업 유형 관리', breadcrumb: { section: '관리', title: '작업 유형 관리' }, requiredPermission: 'system.catalog.manage', contextKind: 'system', navigationKind: 'select' },
  { id: 'project_result_profiles', page: 'project_result_profiles', path: '/workspace/project/result-layouts', label: '프로젝트 결과 구성', breadcrumb: { section: '프로젝트 관리', title: '프로젝트 결과 구성' }, requiredPermission: 'dashboard.edit', contextKind: 'project', navigationKind: 'select' },
  { id: 'schemas', page: 'schemas', path: '/workspace/catalog/schemas', label: '폴더 스키마', breadcrumb: { section: '데이터 설계', title: '폴더 스키마' }, requiredPermission: 'system.catalog.manage', contextKind: 'system', navigationKind: 'select' },
  { id: 'variables', page: 'variables', path: '/workspace/catalog/variables', label: '변수 카탈로그', breadcrumb: { section: '설계', title: '변수 카탈로그' }, requiredPermission: 'project.variable.manage', contextKind: 'project', navigationKind: 'select' },
  { id: 'templates', page: 'templates', path: '/workspace/catalog/templates', label: '자동화 템플릿', breadcrumb: { section: '자동화', title: '모델링 템플릿' }, requiredPermission: 'system.catalog.manage', contextKind: 'system', navigationKind: 'select' },
  { id: 'access_admin', page: 'access_admin', path: '/workspace/admin/access', label: '사용자·프로젝트 권한', breadcrumb: { section: '관리', title: '사용자·프로젝트 권한' }, requiredPermission: 'project.member.manage', contextKind: 'project', navigationKind: 'select' },
  { id: 'menu_policy_admin', page: 'menu_policy_admin', path: '/workspace/admin/menu-policy', label: '권한 및 메뉴 정책', breadcrumb: { section: '관리', title: '권한 및 메뉴 정책' }, requiredPermission: 'system.menu_policy.manage', contextKind: 'system', navigationKind: 'select' },
  { id: 'audit_admin', page: 'audit_admin', path: '/workspace/admin/audit', label: '감사로그', breadcrumb: { section: '관리', title: '감사로그' }, requiredPermission: 'audit.view', contextKind: 'system', navigationKind: 'select' },
  { id: 'examples', page: 'examples', path: '/workspace/examples', label: '기능 예제 갤러리', breadcrumb: { section: '지원', title: '기능 예제 갤러리' }, requiredPermission: 'project.data.view', contextKind: 'company', navigationKind: 'select' },
  { id: 'help', page: 'help', path: '/workspace/help', label: '도움말', breadcrumb: { section: '지원', title: '사용 시나리오 도움말' }, requiredPermission: 'company.dashboard.view', contextKind: 'company', navigationKind: 'select' },
] as const

export const WORKSPACE_ROUTES_BY_ID = new Map(WORKSPACE_ROUTES.map((route) => [route.id, route]))
export const WORKSPACE_ROUTES_BY_PATH = new Map(WORKSPACE_ROUTES.map((route) => [route.path, route]))

export function normalizeWorkspacePathname(pathname: string) {
  return pathname.length > 1 && pathname.endsWith('/') ? pathname.slice(0, -1) : pathname
}

export function workspaceRouteForPathname(pathname: string) {
  return WORKSPACE_ROUTES_BY_PATH.get(normalizeWorkspacePathname(pathname))
}

export function workspacePathForPage(page: WorkspacePage) {
  return WORKSPACE_ROUTES_BY_ID.get(page)?.path
}

export function dashboardEntryForNavigation(menuId: MenuId, requested?: DashboardEntry): DashboardEntry {
  if (requested) return requested
  return WORKSPACE_ROUTES_BY_ID.get(menuId)?.navigationKind === 'open-dashboard' ? 'reset' : 'preserve'
}
