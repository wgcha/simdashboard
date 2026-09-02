import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../../api'
import { clearSession, saveSession, type AuthUser } from '../../auth'
import type { MenuPolicy } from './access'

type AuthSessionOptions = {
  onAccessChanged: (user: AuthUser, policy: MenuPolicy) => void
  onAccessRefreshFailed: () => void
}

/** Authentication and policy-refresh lifecycle; workspace data stays outside. */
export function useAuthSession({ onAccessChanged, onAccessRefreshFailed }: AuthSessionOptions) {
  const [authReady, setAuthReady] = useState(false)
  const [authRequired, setAuthRequired] = useState(false)
  const [authMode, setAuthMode] = useState<'disabled' | 'password' | 'oidc'>('disabled')
  const [authUser, setAuthUser] = useState<AuthUser | null>(null)
  const [authError, setAuthError] = useState('')
  const onAccessChangedRef = useRef(onAccessChanged)
  onAccessChangedRef.current = onAccessChanged

  useEffect(() => {
    void (async () => {
      try {
        const status = await api.authStatus()
        setAuthMode(status.mode)
        setAuthRequired(status.authentication_required)
        try {
          setAuthUser(await api.me())
        } catch {
          if (!status.authentication_required) throw new Error('로컬 관리자 세션을 만들지 못했습니다.')
          setAuthUser(null)
        }
      } catch (reason) {
        clearSession()
        setAuthUser(null)
        setAuthError(reason instanceof Error ? reason.message : '인증 상태를 확인하지 못했습니다.')
      } finally {
        setAuthReady(true)
      }
    })()
  }, [])

  const refreshAccess = useCallback(async () => {
    if (!authUser) return
    const [verified, policy] = await Promise.all([api.me(), api.menuPolicy()])
    setAuthUser(verified)
    onAccessChangedRef.current(verified, policy)
  }, [authUser])

  useEffect(() => {
    const changed = () => { void refreshAccess().catch(onAccessRefreshFailed) }
    window.addEventListener('analysis-access-changed', changed)
    return () => window.removeEventListener('analysis-access-changed', changed)
  }, [onAccessRefreshFailed, refreshAccess])

  const login = useCallback(async (username: string, password: string) => {
    setAuthError('')
    try {
      const result = await api.login(username, password)
      const verified = await api.me()
      saveSession(result.access_token, verified)
      setAuthUser(verified)
    } catch (reason) {
      setAuthError(reason instanceof Error ? reason.message : '로그인하지 못했습니다.')
      throw reason
    }
  }, [])

  const logout = useCallback(async () => {
    const request = api.logout()
    setAuthUser(null)
    try { await request } catch { /* the browser session must still be cleared offline */ }
    clearSession()
  }, [])

  const expire = useCallback((message: string) => {
    setAuthUser(null)
    setAuthError(message)
  }, [])

  return { authError, authMode, authReady, authRequired, authUser, expire, login, logout, refreshAccess }
}
