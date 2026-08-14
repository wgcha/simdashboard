export type UserRole = 'viewer' | 'editor' | 'admin'
export type AccountStatus = 'PENDING' | 'ACTIVE' | 'SUSPENDED'
export type ProjectRole = 'general' | 'power' | 'admin'
export type ProjectMembership = { project_id: string; role: ProjectRole }
export type AuthUser = {
  id: string
  username: string
  display_name: string
  employee_id: string | null
  account_status: AccountStatus
  is_global_admin: boolean
  memberships: ProjectMembership[]
  company_permissions: string[]
  role?: UserRole
}

const USER_KEY = 'analysis-canvas-user'

export function storedUser(): Pick<AuthUser, 'id' | 'display_name'> | null {
  try {
    const value = sessionStorage.getItem(USER_KEY)
    return value ? JSON.parse(value) as Pick<AuthUser, 'id' | 'display_name'> : null
  } catch {
    return null
  }
}

export function saveSession(_token: string, user: AuthUser) {
  // Browser API calls use the HttpOnly cookie. The bearer token remains in
  // the login response for non-browser API clients and is never persisted by the SPA.
  sessionStorage.setItem(USER_KEY, JSON.stringify({ id: user.id, display_name: user.display_name }))
}

export function clearSession() {
  sessionStorage.removeItem('analysis-canvas-access-token')
  sessionStorage.removeItem(USER_KEY)
}
