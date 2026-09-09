import { Suspense } from 'react'
import type { AuthUser } from '../../auth'
import type { MenuId } from '../../features/auth/access'
import { LocalPcSettingsPage, preloadWorkspaceRouteModule } from '../routing/workspaceScreenModules'
import { WorkspaceShellLayout } from '../shell/WorkspaceShellLayout'

type Props = {
  user: AuthUser
  menus: readonly { id: MenuId; label: string }[]
  databaseBackend: 'duckdb' | 'postgresql'
  theme: 'dark' | 'light'
  fontSize: number
  onFontSizeChange: (size: number) => void
  onThemeChange: (theme: 'dark' | 'light') => void
  onLogout: () => void
  onNavigate: (page: MenuId) => void
}

/** Personal settings render independently of project/data bootstrap state. */
export function PersonalPcRoute({ user, menus, databaseBackend, theme, fontSize, onFontSizeChange, onThemeChange, onLogout, onNavigate }: Props) {
  return <WorkspaceShellLayout activePage="local_pc" user={user} menus={menus} databaseBackend={databaseBackend} theme={theme} fontSize={fontSize}
    onDecreaseFontSize={() => onFontSizeChange(Math.max(11, fontSize - 1))} onIncreaseFontSize={() => onFontSizeChange(Math.min(18, fontSize + 1))}
    onLogout={onLogout} onNavigate={onNavigate} onPreloadPage={preloadWorkspaceRouteModule}
    topbarBreadcrumb={<><span>개인 설정</span><b>/</b><strong>내 PC 설정</strong></>}
    actions={<div className="theme-switch" role="group" aria-label="화면 테마 선택"><button type="button" aria-pressed={theme === 'light'} onClick={() => onThemeChange('light')}>라이트</button><button type="button" aria-pressed={theme === 'dark'} onClick={() => onThemeChange('dark')}>다크</button></div>}>
    <Suspense fallback={<div className="full-state">내 PC 설정을 준비하고 있습니다.</div>}>
      <LocalPcSettingsPage key={user.id} currentUserId={user.id} currentUserName={user.display_name} />
    </Suspense>
  </WorkspaceShellLayout>
}
