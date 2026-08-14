import { clearSession } from '../../auth'

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const response = await globalThis.fetch(input, { ...init, credentials: 'same-origin' })
  const pathname = new URL(response.url, window.location.origin).pathname
  if (response.status === 401 && pathname !== '/api/auth/login') {
    clearSession()
    window.dispatchEvent(new CustomEvent('analysis-auth-expired'))
  }
  if (response.status === 403) window.dispatchEvent(new CustomEvent('analysis-access-changed'))
  return response
}
