import { Suspense } from 'react'
import type { AuthUser } from '../../auth'
import type { MenuId } from '../../features/auth/access'
import { NotificationsPage, preloadWorkspaceRouteModule } from '../routing/workspaceScreenModules'
import { WorkspaceShellLayout } from '../shell/WorkspaceShellLayout'

type Props = {
  authMode: 'disabled' | 'password' | 'oidc'
  user: AuthUser
  menus: readonly { id: MenuId; label: string }[]
  databaseBackend: 'duckdb' | 'postgresql'
  theme: 'dark' | 'light'
  fontSize: number
  onThemeChange: (theme: 'dark' | 'light') => void
  onLogout: () => void
  onNavigate: (page: MenuId) => void
}

/** Personal notifications (top bar bell → 모두 보기): independent of project and result bootstrap state. */
export function NotificationsRoute({ authMode, user, menus, databaseBackend, theme, fontSize, onThemeChange, onLogout, onNavigate }: Props) {
  return <WorkspaceShellLayout activePage="notifications" user={user} menus={menus} databaseBackend={databaseBackend} theme={theme} fontSize={fontSize} authMode={authMode}
    onLogout={onLogout} onNavigate={onNavigate} onPreloadPage={preloadWorkspaceRouteModule}
    topbarBreadcrumb={<><span>개인</span><b>/</b><strong>알림</strong></>}
    actions={<div className="theme-switch" role="group" aria-label="화면 테마 선택"><button type="button" aria-label="라이트" aria-pressed={theme === 'light'} onClick={() => onThemeChange('light')}>라이트</button><button type="button" aria-label="다크" aria-pressed={theme === 'dark'} onClick={() => onThemeChange('dark')}>다크</button></div>}>
    <Suspense fallback={<div className="full-state">알림을 준비하고 있습니다.</div>}><NotificationsPage key={user.id} /></Suspense>
  </WorkspaceShellLayout>
}
