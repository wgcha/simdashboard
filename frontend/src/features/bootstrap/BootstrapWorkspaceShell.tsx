import type { ReactNode } from 'react'

import type { WorkspacePage } from '../auth/access'
import './bootstrap-workspace.css'

type BootstrapWorkspaceShellProps = {
  activePage: 'data' | 'intake'
  canOpenIntake: boolean
  children: ReactNode
  databaseBackend: 'duckdb' | 'postgresql'
  displayName: string
  theme: 'dark' | 'light'
  onLogout: () => void
  onPageChange: (page: WorkspacePage) => void
  onThemeChange: (theme: 'dark' | 'light') => void
}

export function BootstrapWorkspaceShell({
  activePage,
  canOpenIntake,
  children,
  databaseBackend,
  displayName,
  theme,
  onLogout,
  onPageChange,
  onThemeChange,
}: BootstrapWorkspaceShellProps) {
  return <div className="bootstrap-workspace" data-theme={theme}>
    <header className="bootstrap-workspace-bar">
      <div><strong>Analysis Canvas</strong><span>초기 데이터 구성</span></div>
      <nav aria-label="초기 데이터 구성 단계">
        <button type="button" className={activePage === 'data' ? 'active' : ''} onClick={() => onPageChange('data')}>프로젝트 · 결과 등록</button>
        <button type="button" className={activePage === 'intake' ? 'active' : ''} disabled={!canOpenIntake} onClick={() => onPageChange('intake')}>해석 의뢰 접수</button>
      </nav>
      <div className="bootstrap-workspace-actions">
        <div className="theme-switch" role="group" aria-label="화면 테마 선택">
          <button type="button" className={theme === 'light' ? 'active' : ''} aria-pressed={theme === 'light'} onClick={() => onThemeChange('light')}>라이트</button>
          <button type="button" className={theme === 'dark' ? 'active' : ''} aria-pressed={theme === 'dark'} onClick={() => onThemeChange('dark')}>다크</button>
        </div>
        <span>{displayName} · {databaseBackend.toUpperCase()}</span>
        <button type="button" onClick={onLogout}>로그아웃</button>
      </div>
    </header>
    <aside className="bootstrap-workspace-guide"><strong>분석 전 준비 단계</strong><span>프로젝트 생성 → 해석 의뢰 접수 → 하중 경우 생성 → 결과 등록 순서로 진행하세요. 분석 결과가 준비되면 전체 작업공간이 자동으로 열립니다.</span></aside>
    <main>{children}</main>
  </div>
}
