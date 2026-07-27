export type UserRole = 'viewer' | 'editor' | 'admin'
export type AuthUser = { id: string; username: string; display_name: string; role: UserRole }

const USER_KEY = 'analysis-canvas-user'

export function storedUser(): AuthUser | null {
  try {
    const value = sessionStorage.getItem(USER_KEY)
    return value ? JSON.parse(value) as AuthUser : null
  } catch {
    return null
  }
}

export function saveSession(_token: string, user: AuthUser) {
  // Browser API calls use the HttpOnly cookie. The bearer token remains in
  // the login response for non-browser API clients and is never persisted by the SPA.
  sessionStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function clearSession() {
  sessionStorage.removeItem('analysis-canvas-access-token')
  sessionStorage.removeItem(USER_KEY)
}

export function authenticatedFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  return globalThis.fetch(input, { ...init, credentials: 'same-origin' })
}
