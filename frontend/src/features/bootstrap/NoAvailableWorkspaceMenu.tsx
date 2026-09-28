import type { ComponentProps, ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'
import { BootstrapWorkspaceShell } from './BootstrapWorkspaceShell'

type NoAvailableWorkspaceMenuProps = {
  accountSettings?: ReactNode
  databaseBackend: 'duckdb' | 'postgresql'
  displayName: string
  theme: 'dark' | 'light'
  onLogout: () => void
  onPageChange: ComponentProps<typeof BootstrapWorkspaceShell>['onPageChange']
  onThemeChange: (theme: 'dark' | 'light') => void
}

export function NoAvailableWorkspaceMenu({ accountSettings, databaseBackend, displayName, theme, onLogout, onPageChange, onThemeChange }: NoAvailableWorkspaceMenuProps) {
  return <BootstrapWorkspaceShell accountSettings={accountSettings} theme={theme} activePage="data" displayName={displayName} databaseBackend={databaseBackend}
    canOpenIntake={false} onPageChange={onPageChange} onThemeChange={onThemeChange} onLogout={onLogout}>
    <div className="bootstrap-empty-access" data-testid="workspace-no-available-menu"><AlertTriangle /><h1>사용할 수 있는 작업 화면이 없습니다.</h1><p>관리자에게 프로젝트 배정 또는 화면 권한을 요청하세요.</p></div>
  </BootstrapWorkspaceShell>
}
