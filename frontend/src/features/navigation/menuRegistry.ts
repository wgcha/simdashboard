import type { MenuContext, MenuId, Permission, WorkspacePage } from '../auth/access'

export type MenuRegistryItem = {
  id: MenuId
  page: WorkspacePage
  label: string
  requiredPermission: Permission
  contextKind: MenuContext
}

export const MENU_REGISTRY: readonly MenuRegistryItem[] = [
  { id: 'portfolio', page: 'portfolio', label: '운영 대시보드', requiredPermission: 'company.dashboard.view', contextKind: 'company' },
  { id: 'dashboard', page: 'dashboard', label: '해석 의뢰 현황', requiredPermission: 'project.data.view', contextKind: 'project' },
  { id: 'intake', page: 'intake', label: '의뢰 접수', requiredPermission: 'request.create', contextKind: 'project' },
  { id: 'workbench', page: 'workbench', label: '해석 작업 실행', requiredPermission: 'project.data.view', contextKind: 'project' },
  { id: 'data', page: 'data', label: '해석 데이터 등록', requiredPermission: 'result.import', contextKind: 'project' },
  { id: 'workbench_admin', page: 'workbench_admin', label: '작업 유형 관리', requiredPermission: 'system.catalog.manage', contextKind: 'system' },
  { id: 'variables', page: 'variables', label: '변수 카탈로그', requiredPermission: 'project.variable.manage', contextKind: 'project' },
  { id: 'templates', page: 'templates', label: '자동화 템플릿', requiredPermission: 'system.catalog.manage', contextKind: 'system' },
  { id: 'schemas', page: 'schemas', label: '폴더 스키마', requiredPermission: 'system.catalog.manage', contextKind: 'system' },
  { id: 'examples', page: 'examples', label: '예제 갤러리', requiredPermission: 'project.data.view', contextKind: 'company' },
  { id: 'help', page: 'help', label: '도움말', requiredPermission: 'company.dashboard.view', contextKind: 'company' },
  { id: 'access_admin', page: 'access_admin', label: '사용자·프로젝트 권한', requiredPermission: 'project.member.manage', contextKind: 'project' },
  { id: 'menu_policy_admin', page: 'menu_policy_admin', label: '권한 및 메뉴 정책', requiredPermission: 'system.menu_policy.manage', contextKind: 'system' },
  { id: 'audit_admin', page: 'audit_admin', label: '감사로그', requiredPermission: 'audit.view', contextKind: 'system' },
] as const

export const MENU_REGISTRY_BY_ID = new Map(MENU_REGISTRY.map((item) => [item.id, item]))
