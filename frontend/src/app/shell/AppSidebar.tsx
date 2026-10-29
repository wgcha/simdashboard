import { useEffect, useMemo, useState, type ComponentType, type MouseEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  BarChart3,
  Bell,
  BookOpen,
  Box,
  ClipboardPlus,
  Database,
  FlaskConical,
  GripVertical,
  LayoutDashboard,
  LogOut,
  Monitor,
  ChevronsLeft,
  ChevronsRight,
  PanelLeftClose,
  PanelLeftOpen,
  Play,
  Plus,
  ScrollText,
  Settings2,
  ShieldCheck,
  Tag,
  Users,
  MessageSquare,
  BrainCircuit,
  Weight,
} from 'lucide-react'
import { ChangePasswordDialog } from '../../features/auth/ChangePasswordDialog'
import './AppSidebar.css'
import { physicsAiNavHref } from '../../shared/api/physicsaiLinkModel'
import { usePhysicsAiLink } from '../../shared/hooks/usePhysicsAiLink'
import { eslNavHref } from '../../shared/api/eslLinkModel'
import { useEslLink } from '../../shared/hooks/useEslLink'

const MENU_ICONS = {
  local_pc: Monitor,
  portfolio: LayoutDashboard,
  dashboard: Activity,
  intake: ClipboardPlus,
  workbench: FlaskConical,
  data: Database,
  materials: Box,
  workbench_admin: Settings2,
  project_result_profiles: LayoutDashboard,
  variables: BarChart3,
  templates: Settings2,
  schemas: GripVertical,
  examples: Play,
  help: BookOpen,
  voc: MessageSquare,
  access_admin: Users,
  menu_policy_admin: ShieldCheck,
  audit_admin: ScrollText,
  notifications: Bell,
  releases: Tag,
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
  materials: 'workflow',
  workbench_admin: 'administration',
  project_result_profiles: 'administration',
  schemas: 'administration',
  variables: 'administration',
  templates: 'administration',
  access_admin: 'administration',
  menu_policy_admin: 'administration',
  audit_admin: 'administration',
  notifications: 'overview',
  releases: 'support',
  examples: 'support',
  help: 'support',
  voc: 'support',
}

const USER_MENU_LABELS: Partial<Record<AppSidebarMenuId, string>> = {
  dashboard: '내 작업',
  portfolio: '결과 대시보드',
  intake: '새 의뢰',
  workbench: '해석 작업 실행',
  data: '해석 데이터 등록',
}

// Hidden from the sidebar for the current usage scenario (user decision 2026-10-06).
// Pages and permissions are unchanged; direct URLs still work. Remove an id to show it again.
export const SIDEBAR_HIDDEN_MENUS: ReadonlySet<AppSidebarMenuId> = new Set<AppSidebarMenuId>([
  'intake',
  'examples',
  'variables',
  'project_result_profiles',
  'workbench_admin',
  // Opened from the top bar bell (2026-10-08), never a sidebar entry.
  'notifications',
  // Opened from the top bar version button (2026-10-21), never a sidebar entry.
  'releases',
  // 해석 작업 실행 is a demo page not used in the current scenario (user decision 2026-10-14).
  'workbench',
  // 모델 소재·물성 is not a navigator-level item (user decision 2026-10-29); the result screen's 소재·물성 tab and the route stay.
  'materials',
])

// Collapsed icon rail (2026-10-29): remembered per browser; the default stays expanded.
export const SIDEBAR_RAIL_STORAGE_KEY = 'simdashboard.sidebar.rail.v1'
function readSidebarRail() { try { return window.localStorage.getItem(SIDEBAR_RAIL_STORAGE_KEY) === '1' } catch { return false } }
function writeSidebarRail(rail: boolean) { try { window.localStorage.setItem(SIDEBAR_RAIL_STORAGE_KEY, rail ? '1' : '0') } catch { /* the state still applies until reload */ } }

function groupForMenu(id: string): MenuGroupId | undefined {
  return MENU_GROUP_BY_ID[id as AppSidebarMenuId]
}

type AppSidebarProps = {
  activePage: string
  accountKey?: string
  authMode?: 'disabled' | 'password' | 'oidc'
  databaseBackend: 'duckdb' | 'postgresql'
  menus: readonly AppSidebarMenu[]
  signedIn: boolean
  theme: 'dark' | 'light'
  userBadge?: string
  userDisplayName?: string
  onLogout: () => void
  onNavigate: (id: AppSidebarMenuId) => void
  onPreloadPage: (id: AppSidebarMenuId) => void
  workspacePathForMenu: (id: AppSidebarMenuId) => string
}

export function AppSidebar({
  activePage,
  accountKey,
  authMode = 'disabled',
  databaseBackend,
  menus,
  signedIn,
  theme,
  userBadge,
  userDisplayName,
  onLogout,
  onNavigate,
  onPreloadPage,
  workspacePathForMenu,
}: AppSidebarProps) {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [rail, setRail] = useState(readSidebarRail)
  const toggleRail = () => setRail((current) => { writeSidebarRail(!current); return !current })
  const activeGroup = groupForMenu(activePage)
  const initialOpen = useMemo(() => new Set<MenuGroupId>(['overview', ...(activeGroup && activeGroup !== 'overview' ? [activeGroup] : [])]), [activeGroup])
  const [openGroups, setOpenGroups] = useState<Set<MenuGroupId>>(initialOpen)
  useEffect(() => {
    if (!activeGroup) return
    setOpenGroups((current) => current.has(activeGroup) ? current : new Set([...current, activeGroup]))
  }, [activeGroup])
  const groupedMenus = new Map<MenuGroupId, AppSidebarMenu[]>()
  menus.forEach((menu) => {
    if (menu.id === 'workbench' || menu.id === 'data' || menu.id === 'help' || menu.id === 'voc' || SIDEBAR_HIDDEN_MENUS.has(menu.id)) return
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
  // External "AI 예측" link (PhysicsAI at same-origin /physicsai/), shown only when SIMDASH_PHYSICSAI_ENABLED is on.
  const physicsAiHref = physicsAiNavHref(usePhysicsAiLink(signedIn), signedIn)
  // External "등가정적하중" link (ESL platform at same-origin /esl/), shown only when SIMDASH_ESL_ENABLED is on.
  const eslHref = eslNavHref(useEslLink(signedIn), signedIn)
  const renderMenuLink = (menu: AppSidebarMenu, extraClass = '') => {
    const Icon = MENU_ICONS[menu.id]
    const label = USER_MENU_LABELS[menu.id] ?? menu.label
    const navigate = (event: MouseEvent<HTMLAnchorElement>) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      event.preventDefault()
      setMobileMenuOpen(false)
      onNavigate(menu.id)
    }
    return <Link key={menu.id} aria-label={label} aria-current={activePage === menu.id ? 'page' : undefined} title={label} className={'nav-link ' + extraClass + (activePage === menu.id ? ' active' : '')} to={workspacePathForMenu(menu.id)} onClick={navigate} onMouseEnter={() => onPreloadPage(menu.id)} onFocus={() => onPreloadPage(menu.id)}><Icon /><span>{label}</span></Link>
  }

  return <aside className={`sidebar${rail ? ' sidebar--rail' : ''}${mobileMenuOpen ? ' mobile-menu-open' : ''}`} aria-label="주 메뉴">
    <div className="brand" title="VD simulation workbench"><span className="brand-mark"><Activity /></span><span>VD <strong>workbench</strong></span><button type="button" className="mobile-menu-trigger" aria-label={mobileMenuOpen ? '메뉴 닫기' : '메뉴 열기'} aria-expanded={mobileMenuOpen} onClick={() => setMobileMenuOpen((value) => !value)}>{mobileMenuOpen ? <PanelLeftClose /> : <PanelLeftOpen />}<span>{mobileMenuOpen ? '메뉴 닫기' : '메뉴 열기'}</span></button><button type="button" className="sidebar-rail-toggle" aria-label={rail ? '메뉴 펼치기' : '메뉴 접기'} title={rail ? '메뉴 펼치기' : '메뉴 접기'} aria-expanded={!rail} onClick={toggleRail}>{rail ? <ChevronsRight aria-hidden="true" /> : <ChevronsLeft aria-hidden="true" />}</button></div>
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
        <summary className="nav-group-label" aria-label={`${group.label} 메뉴`} title={group.label}><span className="nav-group-icon" aria-hidden="true">{group.id === 'workflow' ? <FlaskConical /> : group.id === 'administration' ? <Settings2 /> : group.id === 'support' ? <BookOpen /> : <LayoutDashboard />}</span><span>{group.label}</span><Plus className="nav-group-caret" aria-hidden="true" /></summary>
        <div className="nav-group-items">
          {group.menus.map((menu) => renderMenuLink(menu))}
        </div>
      </details></div>)}
      {physicsAiHref ? <a className="nav-link nav-link-standalone" href={physicsAiHref} target="_blank" rel="noopener noreferrer" aria-label="AI 예측 (새 창)" title="AI 예측 (PhysicsAI, 새 창)"><BrainCircuit /><span>AI 예측</span></a> : null}
      {eslHref ? <a className="nav-link nav-link-standalone" href={eslHref} target="_blank" rel="noopener noreferrer" aria-label="등가정적하중 (새 창)" title="등가정적하중 (ESL, 새 창)"><Weight /><span>등가정적하중</span></a> : null}
      {helpMenu && renderMenuLink(helpMenu, 'nav-link-standalone nav-link-help')}
      {menus.find((menu) => menu.id === 'voc') && renderMenuLink(menus.find((menu) => menu.id === 'voc')!, 'nav-link-standalone nav-link-help')}
    </nav>
    <div className="sidebar-foot">
      {/* Global font-size buttons are hidden (user decision 2026-10-08); the stored size still applies. */}
      {signedIn && <div className="signed-user"><strong>{userDisplayName}</strong><span>{userBadge}</span></div>}
      <details className="sidebar-settings"><summary aria-label="환경설정" title="환경설정"><Settings2 /> <span>환경설정</span><Plus className="nav-group-caret" aria-hidden="true" /></summary><div className="sidebar-settings-body"><span className="system-pill"><span className="live-dot" /> {databaseBackend === 'postgresql' ? '서버 연결' : '로컬 연결'}</span><small>계정과 연결 상태를 확인합니다.</small>{signedIn && authMode === 'password' && <ChangePasswordDialog key={accountKey ?? 'account'} theme={theme} />}</div></details>
      {signedIn && <button onClick={onLogout} aria-label="로그아웃" title="로그아웃"><LogOut /><span>로그아웃</span></button>}
    </div>
  </aside>
}
