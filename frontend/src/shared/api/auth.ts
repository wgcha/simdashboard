import { clearSession } from '../../auth'
import { clearMemoryQueryCache, invalidateMemoryQueryCache, resumeMemoryQueryCache } from '../cache/useMemoryQuery'
import { apiUrl } from './url'

const authLoginPath = apiUrl('/api/auth/login')
const authMePath = apiUrl('/api/auth/me')

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const response = await globalThis.fetch(input, { ...init, credentials: 'same-origin' })
  const pathname = new URL(response.url, window.location.origin).pathname
  if (response.status === 401 && pathname !== authLoginPath) {
    clearMemoryQueryCache({ blockRevalidation: true })
    clearSession()
    window.dispatchEvent(new CustomEvent('analysis-auth-expired'))
  }
  if (response.status === 403) {
    clearMemoryQueryCache({ blockRevalidation: true })
    window.dispatchEvent(new CustomEvent('analysis-access-changed'))
  }
  if (response.ok && pathname === authMePath) resumeMemoryQueryCache()
  const method = (init.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase()
  if (response.ok && !['GET', 'HEAD', 'OPTIONS'].includes(method)) invalidateMemoryQueryCache()
  return response
}
