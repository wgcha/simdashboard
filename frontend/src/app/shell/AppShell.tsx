import type { CSSProperties, ReactNode } from 'react'

type AppShellProps = {
  children: ReactNode
  className: string
  sidebar: ReactNode
  style: CSSProperties
  theme: 'dark' | 'light'
}

/**
 * The structural frame is deliberately feature-agnostic.  Feature screens own
 * their data and controls; the shell only owns the landmark hierarchy shared
 * by every authenticated workspace route.
 */
export function AppShell({ children, className, sidebar, style, theme }: AppShellProps) {
  return <div className={className} data-theme={theme} style={style}>
    {sidebar}
    {children}
  </div>
}

type AppShellMainProps = {
  children: ReactNode
  topbar: ReactNode
}

export function AppShellMain({ children, topbar }: AppShellMainProps) {
  return <main className="main-shell">
    {topbar}
    {children}
  </main>
}

type AppTopbarProps = {
  actions: ReactNode
  breadcrumb: ReactNode
}

export function AppTopbar({ actions, breadcrumb }: AppTopbarProps) {
  return <header className="topbar">
    <div className="breadcrumb">{breadcrumb}</div>
    <div className="top-actions">{actions}</div>
  </header>
}
