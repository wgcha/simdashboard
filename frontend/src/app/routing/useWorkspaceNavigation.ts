import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useBlocker, useLocation, useNavigate } from 'react-router-dom'

import type { AuthUser } from '../../auth'
import type { MenuId, WorkspacePage } from '../../features/auth/access'
import { dashboardEntryForNavigation, WORKSPACE_ROUTES_BY_ID, workspacePathForPage, workspaceRouteForPathname, type DashboardEntry } from '../../features/navigation/workspaceRouteRegistry'
import { resolveBlockedNavigation } from './navigationState'
import { preloadWorkspaceRouteModule } from './workspaceRouteModules'

type WorkspaceNavigationOptions = {
  allowedPages: ReadonlySet<WorkspacePage>
  authUser: AuthUser | null
  editMode: boolean
  menuPolicyReady: boolean
  onCancelEditing: () => void
  onDashboardRoute: () => void
  onNotice: (message: string) => void
  personalOnly: boolean
  visibleMenus: readonly { id: MenuId }[]
}

export type WorkspaceNavigationRequest = {
  dashboardEntry?: DashboardEntry
  replace?: boolean
}

export type WorkspaceContextQuery = {
  loadCaseId?: string
  pageId?: string
  projectId?: string
  requestId?: string
  runId?: string
  view?: string
}

type PendingNavigation = {
  dashboardEntry: DashboardEntry
  pathname: string
}

/**
 * URL-derived workspace state and the single navigation contract. The data
 * router blocker protects sidebar clicks, feature hand-offs, and browser
 * history with one confirmation prompt.
 */
export function useWorkspaceNavigation({
  allowedPages,
  authUser,
  editMode,
  menuPolicyReady,
  onCancelEditing,
  onDashboardRoute,
  onNotice,
  personalOnly,
  visibleMenus,
}: WorkspaceNavigationOptions) {
  const location = useLocation()
  const navigate = useNavigate()
  const matchedWorkspaceRoute = workspaceRouteForPathname(location.pathname)
  const workspacePage = matchedWorkspaceRoute?.page ?? 'portfolio'
  const isWorkspaceIndex = location.pathname === '/' || location.pathname === '/workspace' || location.pathname === '/workspace/'
  const workspaceContext = useMemo<WorkspaceContextQuery>(() => {
    const query = new URLSearchParams(location.search)
    return {
      projectId: query.get('project') || undefined,
      requestId: query.get('request') || undefined,
      loadCaseId: query.get('loadCase') || undefined,
      runId: query.get('run') || undefined,
      view: query.get('view') || undefined,
      pageId: query.get('page') || undefined,
    }
  }, [location.search])
  const navigationBlocker = useBlocker(editMode)
  const pendingNavigationRef = useRef<PendingNavigation | null>(null)
  const [workspaceNavigationPending, setWorkspaceNavigationPending] = useState(false)

  useEffect(() => {
    if (!authUser || authUser.account_status !== 'ACTIVE' || personalOnly || !menuPolicyReady) return
    // Load only permitted, frequently used screens after the first paint. No data requests.
    const timers = (['dashboard', 'data', 'workbench'] as const)
      .filter((page) => allowedPages.has(page))
      .map((page, index) => window.setTimeout(() => preloadWorkspaceRouteModule(page), 600 + index * 250))
    return () => timers.forEach(window.clearTimeout)
  }, [authUser?.id, authUser?.account_status, menuPolicyReady, allowedPages, personalOnly])

  useEffect(() => {
    if (!authUser || authUser.account_status !== 'ACTIVE') return
    if (personalOnly) {
      if (workspacePage !== 'local_pc') navigate(workspacePathForPage('local_pc')!, { replace: true })
      return
    }
    if (!menuPolicyReady) return
    const fallbackPath = visibleMenus[0] ? workspacePathForPage(visibleMenus[0].id) : undefined
    if (isWorkspaceIndex) {
      if (fallbackPath) navigate(fallbackPath, { replace: true })
      return
    }
    if (!matchedWorkspaceRoute) return
    if (allowedPages.has(matchedWorkspaceRoute.page) || !fallbackPath) return
    onNotice('현재 권한으로 열 수 없는 화면입니다. 허용된 첫 화면으로 이동했습니다.')
    navigate(fallbackPath, { replace: true })
  }, [allowedPages, authUser?.account_status, authUser?.id, isWorkspaceIndex, matchedWorkspaceRoute, menuPolicyReady, navigate, onNotice, personalOnly, visibleMenus, workspacePage])

  useEffect(() => {
    if (!editMode) return
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', beforeUnload)
    return () => window.removeEventListener('beforeunload', beforeUnload)
  }, [editMode])

  useEffect(() => {
    if (navigationBlocker.state !== 'blocked') return
    const decision = resolveBlockedNavigation(
      pendingNavigationRef.current,
      window.confirm('저장하지 않은 변경 사항이 있습니다. 변경을 취소하고 이동할까요?'),
    )
    pendingNavigationRef.current = decision.pending
    if (decision.shouldProceed) {
      onCancelEditing()
      navigationBlocker.proceed()
    } else {
      setWorkspaceNavigationPending(false)
      navigationBlocker.reset()
    }
  }, [navigationBlocker, onCancelEditing])

  useEffect(() => {
    const pending = pendingNavigationRef.current
    if (!pending || pending.pathname !== location.pathname) return
    pendingNavigationRef.current = null
    setWorkspaceNavigationPending(false)
    if (matchedWorkspaceRoute?.page === 'dashboard' && pending.dashboardEntry === 'reset') onDashboardRoute()
  }, [location.pathname, matchedWorkspaceRoute?.page, onDashboardRoute])

  const navigateWorkspace = useCallback((menuId: MenuId, options: WorkspaceNavigationRequest = {}) => {
    const route = WORKSPACE_ROUTES_BY_ID.get(menuId)
    if (!route) return
    preloadWorkspaceRouteModule(route.page)
    const dashboardEntry = dashboardEntryForNavigation(menuId, options.dashboardEntry)
    if (route.path === location.pathname) {
      // A same-route menu click must not silently abandon an active editor.
      // There is no browser transition for useBlocker to intercept here.
      if (route.page === 'dashboard' && dashboardEntry === 'reset' && !editMode) onDashboardRoute()
      return
    }
    pendingNavigationRef.current = { dashboardEntry, pathname: route.path }
    setWorkspaceNavigationPending(true)
    navigate({ pathname: route.path, search: location.search }, { replace: options.replace })
  }, [editMode, location.pathname, location.search, navigate, onDashboardRoute])

  const updateWorkspaceContext = useCallback((next: WorkspaceContextQuery, options: { replace?: boolean } = {}) => {
    const query = new URLSearchParams(location.search)
    const values: Array<[string, string | undefined]> = [
      ['project', next.projectId], ['request', next.requestId], ['loadCase', next.loadCaseId],
      ['run', next.runId], ['view', next.view], ['page', next.pageId],
    ]
    for (const [key, value] of values) {
      if (value) query.set(key, value)
      else query.delete(key)
    }
    const search = query.toString()
    if (search === location.search.slice(1)) return
    const targetSearch = search ? `?${search}` : ''
    navigate({ pathname: location.pathname, search: targetSearch }, { replace: options.replace ?? true })
  }, [location.pathname, location.search, navigate])

  return { isWorkspaceIndex, matchedWorkspaceRoute, navigateWorkspace, updateWorkspaceContext, workspaceContext, workspaceNavigationPending, workspacePage }
}
