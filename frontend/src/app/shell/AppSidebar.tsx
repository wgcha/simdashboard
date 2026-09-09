import { useEffect, useMemo, useState, type ComponentType, type MouseEvent } from 'react'
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
  Monitor,
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
  local_pc: Monitor,
  portfolio: LayoutDashboard,
  dashboard: Activity,
  intake: ClipboardPlus,
  workbench: FlaskConical,
  data: Database,
  workbench_admin: Settings2,
  project_result_profiles: LayoutDashboard,
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
  overview: '기본 메뉴',
  workflow: '전체 작업',
  configuration: '설정 및 관리',
  administration: '설정 및 관리',
  support: '예제 및 참고',
} as const

type MenuGroupId = keyof typeof MENU_GROUP_LABELS

const MENU_GROUP_BY_ID: Record<AppSidebarMenuId, MenuGroupId> = {
  local_pc: 'overview',
  portfolio: 'overview',
  dashboard: 'overview',
  intake: 'overview',
  workbench: 'workflow',
  data: 'workflow',
  workbench_admin: 'administration',
  project_result_profiles: 'administration',
  schemas: 'administration',
  variables: 'administration',
  templates: 'administration',
  access_admin: 'administration',
  menu_policy_admin: 'administration',
  audit_admin: 'administration',
  examples: 'support',
  help: 'support',
}

const USER_MENU_LABELS: Partial<Record<AppSidebarMenuId, string>> = {
  dashboard: '내 작업',
  portfolio: '결과 대시보드',
  intake: '새 의뢰',
  workbench: '해석 작업 실행',
  data: '해석 데이터 등록',
}

function groupForMenu(id: string): MenuGroupId | undefined {
  return MENU_GROUP_BY_ID[id as AppSidebarMenuId]
}

type AppSidebarProps = {
  activePage: string
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
  workspacePathForMenu: (id: AppSidebarMenuId) => string
}

export function AppSidebar({
  activePage,
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
  workspacePathForMenu,
}: AppSidebarProps) {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const activeGroup = groupForMenu(activePage)
  const initialOpen = useMemo(() => new Set<MenuGroupId>(['overview', ...(activeGroup && activeGroup !== 'overview' ? [activeGroup] : [])]), [activeGroup])
  const [openGroups, setOpenGroups] = useState<Set<MenuGroupId>>(initialOpen)
  useEffect(() => {
    if (!activeGroup) return
    setOpenGroups((current) => current.has(activeGroup) ? current : new Set([...current, activeGroup]))
  }, [activeGroup])
  const groupedMenus = new Map<MenuGroupId, AppSidebarMenu[]>()
  menus.forEach((menu) => {
    if (menu.id === 'workbench' || menu.id === 'data' || menu.id === 'help') return
    const groupId = MENU_GROUP_BY_ID[menu.id]
    const group = groupedMenus.get(groupId)
    if (group) group.push(menu)
    else groupedMenus.set(groupId, [menu])
  })
  const visibleGroups = Array.from(groupedMenus, ([id, groupMenus]) => ({
    id,
    label: MENU_GROUP_LABELS[id],
    menus: id === 'overview' ? [...groupMenus].sort((a, b) => ['portfolio', 'dashboard', 'intake', 'local_pc'].indexOf(a.id) - ['portfolio', 'dashboard', 'intake', 'local_pc'].indexOf(b.id)) : groupMenus,
  }))
  const helpMenu = menus.find((menu) => menu.id === 'help')
  const renderMenuLink = (menu: AppSidebarMenu, extraClass = '') => {
    const Icon = MENU_ICONS[menu.id]
    const label = USER_MENU_LABELS[menu.id] ?? menu.label
    const navigate = (event: MouseEvent<HTMLAnchorElement>) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      event.preventDefault()
      setMobileMenuOpen(false)
      onNavigate(menu.id)
    }
    return <a key={menu.id} aria-label={label} aria-current={activePage === menu.id ? 'page' : undefined} title={label} className={'nav-link ' + extraClass + (activePage === menu.id ? ' active' : '')} href={workspacePathForMenu(menu.id)} onClick={navigate} onMouseEnter={() => onPreloadPage(menu.id)} onFocus={() => onPreloadPage(menu.id)}><Icon /><span>{label}</span></a>
  }

  return <aside className={`sidebar ${mobileMenuOpen ? 'mobile-menu-open' : ''}`} aria-label="주 메뉴">
    <div className="brand" title="VD simulation workbench"><span className="brand-mark"><Activity /></span><span>VD <strong>workbench</strong></span><button type="button" className="mobile-menu-trigger" aria-label={mobileMenuOpen ? '메뉴 닫기' : '메뉴 열기'} aria-expanded={mobileMenuOpen} onClick={() => setMobileMenuOpen((value) => !value)}>{mobileMenuOpen ? <PanelLeftClose /> : <PanelLeftOpen />}<span>{mobileMenuOpen ? '메뉴 닫기' : '메뉴 열기'}</span></button></div>
    <nav className="nav-main">
      {visibleGroups.map((group) => group.id === 'overview' ? <div key={group.id} className="nav-primary" aria-label="주요 업무">{group.menus.map((menu) => renderMenuLink(menu))}</div> : <div key={group.id}><details className={`nav-group nav-group--${group.id}`} role="group" aria-label={group.label} open={openGroups.has(group.id)} onToggle={(event) => {
        const isOpen = event.currentTarget.open
        setOpenGroups((current) => {
          const next = new Set(current)
          if (isOpen) next.add(group.id)
          else next.delete(group.id)
          return next
        })
      }}>
        <summary className="nav-group-label" aria-label={`${group.label} 메뉴`}><span className="nav-group-icon" aria-hidden="true">{group.id === 'workflow' ? <FlaskConical /> : group.id === 'administration' ? <Settings2 /> : group.id === 'support' ? <BookOpen /> : <LayoutDashboard />}</span><span>{group.label}</span><Plus className="nav-group-caret" aria-hidden="true" /></summary>
        <div className="nav-group-items">
          {group.menus.map((menu) => renderMenuLink(menu))}
        </div>
      </details></div>)}
      {helpMenu && renderMenuLink(helpMenu, 'nav-link-standalone nav-link-help')}
    </nav>
    <div className="sidebar-foot">
      <div className="global-font-control" aria-label="전체 글자 크기 조절">
        <span className="sidebar-label">글자 크기</span>
        <button type="button" aria-label="전체 글자 크기 줄이기" onClick={onDecreaseFontSize} disabled={fontSize <= 11}><Minus /></button>
        <output>{fontSize}pt</output>
        <button type="button" aria-label="전체 글자 크기 늘리기" onClick={onIncreaseFontSize} disabled={fontSize >= 18}><Plus /></button>
      </div>
      {signedIn && <div className="signed-user"><strong>{userDisplayName}</strong><span>{userBadge}</span></div>}
      <details className="sidebar-settings"><summary><Settings2 /> <span>환경설정</span><Plus className="nav-group-caret" aria-hidden="true" /></summary><div className="sidebar-settings-body"><span className="system-pill"><span className="live-dot" /> {databaseBackend === 'postgresql' ? '서버 연결' : '로컬 연결'}</span><small>화면 글자 크기와 메뉴를 조정합니다.</small></div></details>
      {signedIn && <button onClick={onLogout}><LogOut /><span>로그아웃</span></button>}
    </div>
  </aside>
}
