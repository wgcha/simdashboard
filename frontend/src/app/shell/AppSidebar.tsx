import type { ComponentType, MouseEvent } from 'react'
import {
  Activity,
  BarChart3,
  BookOpen,
  ClipboardPlus,
  Database,
  FlaskConical,
  GripVertical,
  LayoutDashboard,
  LogOut,
  Minus,
  PanelLeftClose,
  PanelLeftOpen,
  Play,
  Plus,
  ScrollText,
  Settings2,
  ShieldCheck,
  Users,
} from 'lucide-react'

const MENU_ICONS = {
  portfolio: LayoutDashboard,
  dashboard: Activity,
  intake: ClipboardPlus,
  workbench: FlaskConical,
  data: Database,
  workbench_admin: Settings2,
  variables: BarChart3,
  templates: Settings2,
  schemas: GripVertical,
  examples: Play,
  help: BookOpen,
  access_admin: Users,
  menu_policy_admin: ShieldCheck,
  audit_admin: ScrollText,
} satisfies Record<string, ComponentType>

export type AppSidebarMenuId = keyof typeof MENU_ICONS

type AppSidebarMenu = {
  id: AppSidebarMenuId
  label: string
}

const MENU_GROUP_LABELS = {
  overview: '운영 개요',
  workflow: '의뢰 · 수행 흐름',
  configuration: '업무 구성',
  administration: '운영 관리',
  support: '지원',
} as const

type MenuGroupId = keyof typeof MENU_GROUP_LABELS

const MENU_GROUP_BY_ID: Record<AppSidebarMenuId, MenuGroupId> = {
  portfolio: 'overview',
  dashboard: 'workflow',
  intake: 'workflow',
  workbench: 'workflow',
  data: 'workflow',
  workbench_admin: 'configuration',
  schemas: 'configuration',
  variables: 'configuration',
  templates: 'configuration',
  access_admin: 'administration',
  menu_policy_admin: 'administration',
  audit_admin: 'administration',
  examples: 'support',
  help: 'support',
}

const FLOW_ORDER_BY_ID: Partial<Record<AppSidebarMenuId, number>> = {
  dashboard: 1,
  intake: 2,
  workbench: 3,
  data: 4,
}

type AppSidebarProps = {
  activePage: string
  collapsed: boolean
  databaseBackend: 'duckdb' | 'postgresql'
  fontSize: number
  menus: readonly AppSidebarMenu[]
  signedIn: boolean
  userBadge?: string
  userDisplayName?: string
  onDecreaseFontSize: () => void
  onIncreaseFontSize: () => void
  onLogout: () => void
  onNavigate: (id: AppSidebarMenuId) => void
  onPreloadPage: (id: AppSidebarMenuId) => void
  onToggleCollapsed: () => void
  workspacePathForMenu: (id: AppSidebarMenuId) => string
}

export function AppSidebar({
  activePage,
  collapsed,
  databaseBackend,
  fontSize,
  menus,
  signedIn,
  userBadge,
  userDisplayName,
  onDecreaseFontSize,
  onIncreaseFontSize,
  onLogout,
  onNavigate,
  onPreloadPage,
  onToggleCollapsed,
  workspacePathForMenu,
}: AppSidebarProps) {
  const groupedMenus = new Map<MenuGroupId, AppSidebarMenu[]>()
  menus.forEach((menu) => {
    const groupId = MENU_GROUP_BY_ID[menu.id]
    const group = groupedMenus.get(groupId)
    if (group) group.push(menu)
    else groupedMenus.set(groupId, [menu])
  })
  const visibleGroups = Array.from(groupedMenus, ([id, groupMenus]) => ({
    id,
    label: MENU_GROUP_LABELS[id],
    menus: groupMenus,
  }))

  return <aside className="sidebar" aria-label="주 메뉴">
    <div className="brand" title="VD simulation workbench"><span className="brand-mark"><Activity /></span><span>VD simulation<br /><strong>workbench</strong></span></div>
    <nav className="nav-main">
      {visibleGroups.map((group) => <div className={`nav-group nav-group--${group.id}`} key={group.id} role="group" aria-label={group.label}>
        <span className="nav-group-label" aria-hidden="true">{group.label}</span>
        <div className="nav-group-items">
          {group.menus.map((menu) => {
            const Icon = MENU_ICONS[menu.id]
            const flowOrder = FLOW_ORDER_BY_ID[menu.id]
            const navigate = (event: MouseEvent<HTMLAnchorElement>) => {
              if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
              event.preventDefault()
              onNavigate(menu.id)
            }
            return <a key={menu.id} aria-label={menu.label} aria-current={activePage === menu.id ? 'page' : undefined} title={menu.label} className={`nav-link ${activePage === menu.id ? 'active' : ''}`} href={workspacePathForMenu(menu.id)} onClick={navigate} onMouseEnter={() => onPreloadPage(menu.id)} onFocus={() => onPreloadPage(menu.id)}><Icon /><span>{menu.label}</span>{flowOrder && <small className="nav-flow-order" aria-hidden="true">{flowOrder}</small>}</a>
          })}
        </div>
      </div>)}
    </nav>
    <div className="sidebar-foot">
      <div className="global-font-control" aria-label="전체 글자 크기 조절">
        <span className="sidebar-label">글자 크기</span>
        <button type="button" aria-label="전체 글자 크기 줄이기" onClick={onDecreaseFontSize} disabled={fontSize <= 11}><Minus /></button>
        <output>{fontSize}pt</output>
        <button type="button" aria-label="전체 글자 크기 늘리기" onClick={onIncreaseFontSize} disabled={fontSize >= 18}><Plus /></button>
      </div>
      {signedIn && <div className="signed-user"><strong>{userDisplayName}</strong><span>{userBadge}</span></div>}
      <div className="system-pill"><span className="live-dot" /> {databaseBackend.toUpperCase()} · {databaseBackend === 'postgresql' ? 'SERVER' : 'LOCAL'}</div>
      <button type="button" className="sidebar-toggle" aria-label={collapsed ? '메뉴 펼치기' : '메뉴 접기'} aria-expanded={!collapsed} onClick={onToggleCollapsed}>{collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}<span>{collapsed ? '메뉴 펼치기' : '메뉴 접기'}</span></button>
      {signedIn && <button onClick={onLogout}><LogOut /><span>로그아웃</span></button>}
    </div>
  </aside>
}
