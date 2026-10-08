import type { ReactNode } from 'react'

import type { AuthUser } from '../../auth'
import type { MenuId } from '../../features/auth/access'
import { WORKSPACE_ROUTES_BY_ID } from '../../features/navigation/workspaceRouteRegistry'
import { AppShell, AppShellMain, AppTopbar } from './AppShell'
import { AppSidebar, type AppSidebarMenuId } from './AppSidebar'
import { workspaceFontSizeStyle } from '../preferences/workspaceFontSizeStyle'

type WorkspaceShellLayoutProps = {
  authMode?: 'disabled' | 'password' | 'oidc'
  actions: ReactNode
  activePage: MenuId
  children: ReactNode
  databaseBackend: 'duckdb' | 'postgresql'
  fontSize: number
  menus: readonly { id: MenuId; label: string }[]
  theme: 'dark' | 'light'
  topbarBreadcrumb: ReactNode
  user: AuthUser | null
  userBadge?: string
  onLogout: () => void
  onNavigate: (id: MenuId) => void
  onPreloadPage: (id: MenuId) => void
}

/** Feature-agnostic authenticated chrome: navigation, top bar, and outlet. */
export function WorkspaceShellLayout({
  authMode,
  actions,
  activePage,
  children,
  databaseBackend,
  fontSize,
  menus,
  theme,
  topbarBreadcrumb,
  user,
  userBadge,
  onLogout,
  onNavigate,
  onPreloadPage,
}: WorkspaceShellLayoutProps) {
  return <AppShell
    className={`app-shell ${theme === 'light' ? 'light-theme' : 'dark-theme'}`}
    sidebar={<AppSidebar
      activePage={activePage}
      accountKey={user?.id}
      authMode={authMode}
      databaseBackend={databaseBackend}
      menus={menus}
      signedIn={Boolean(user)}
      theme={theme}
      userBadge={userBadge}
      userDisplayName={user?.display_name}
      onLogout={onLogout}
      onNavigate={onNavigate as (id: AppSidebarMenuId) => void}
      onPreloadPage={onPreloadPage as (id: AppSidebarMenuId) => void}
      workspacePathForMenu={(menuId) => WORKSPACE_ROUTES_BY_ID.get(menuId)?.path ?? '/workspace'}
    />}
    style={workspaceFontSizeStyle(fontSize)}
    theme={theme}
  >
    <AppShellMain topbar={<AppTopbar actions={actions} breadcrumb={topbarBreadcrumb} />}>
      {children}
    </AppShellMain>
  </AppShell>
}
