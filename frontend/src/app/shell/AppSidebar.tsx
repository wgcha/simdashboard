import type { ComponentType } from 'react'
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
  onToggleCollapsed: () => void
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
  onToggleCollapsed,
}: AppSidebarProps) {
  return <aside className="sidebar" aria-label="주 메뉴">
    <div className="brand" title="VD simulation workbench"><span className="brand-mark"><Activity /></span><span>VD simulation<br /><strong>workbench</strong></span></div>
    <nav className="nav-main">
      {menus.map((menu) => {
        const Icon = MENU_ICONS[menu.id]
        return <button key={menu.id} aria-label={menu.label} title={menu.label} className={activePage === menu.id ? 'active' : ''} onClick={() => onNavigate(menu.id)}><Icon /><span>{menu.label}</span></button>
      })}
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
