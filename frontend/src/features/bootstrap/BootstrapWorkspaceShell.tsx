import type { ReactNode } from 'react'

import type { WorkspacePage } from '../auth/access'
import './bootstrap-workspace.css'

type BootstrapWorkspaceShellProps = {
  activePage: 'data' | 'intake'
  canOpenIntake: boolean
  canOpenFolders?: boolean
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
  canOpenFolders = false,
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
        {canOpenFolders && <button type="button" onClick={() => onPageChange('schemas')}>폴더 연결·규칙</button>}
      </nav>
      <div className="bootstrap-workspace-actions">
        <button type="button" onClick={() => onPageChange('local_pc')}>내 PC 설정</button>
        <div className="theme-switch" role="group" aria-label="화면 테마 선택">
          <button type="button" className={theme === 'light' ? 'active' : ''} aria-pressed={theme === 'light'} onClick={() => onThemeChange('light')}>라이트</button>
          <button type="button" className={theme === 'dark' ? 'active' : ''} aria-pressed={theme === 'dark'} onClick={() => onThemeChange('dark')}>다크</button>
        </div>
        <span>{displayName} · {databaseBackend.toUpperCase()}</span>
        <button type="button" onClick={onLogout}>로그아웃</button>
      </div>
    </header>
    <aside className="bootstrap-workspace-guide"><strong>분석 전 준비 단계</strong><span>{canOpenFolders ? '폴더 연결·규칙에서 사용환경 또는 유통환경을 선택해 업무와 Case 결과를 등록할 수 있습니다. 기존 업무는 프로젝트·결과 등록에서 관리하세요.' : '프로젝트와 해석 의뢰를 선택해 결과를 등록하세요. 등록된 Case 결과는 의뢰의 Case 결과 화면에서 확인할 수 있습니다.'}</span></aside>
    <main>{children}</main>
  </div>
}
