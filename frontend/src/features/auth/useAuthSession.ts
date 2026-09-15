import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../../api'
import { clearSession, saveSession, type AuthUser } from '../../auth'
import type { MenuPolicy } from './access'

type AuthSessionOptions = {
  onAccessChanged: (user: AuthUser, policy: MenuPolicy) => void
  onAccessRefreshFailed: () => void
}

export type AuthStatus = {
  mode: 'disabled' | 'password' | 'oidc'
  authentication_required: boolean
  registration_enabled: boolean
  setup_required: boolean
  setup_reason?: string
}

/** Authentication and policy-refresh lifecycle; workspace data stays outside. */
export function useAuthSession({ onAccessChanged, onAccessRefreshFailed }: AuthSessionOptions) {
  const [authReady, setAuthReady] = useState(false)
  const [authRequired, setAuthRequired] = useState(false)
  const [authMode, setAuthMode] = useState<'disabled' | 'password' | 'oidc'>('disabled')
  const [registrationEnabled, setRegistrationEnabled] = useState(false)
  const [setupRequired, setSetupRequired] = useState(false)
  const [setupReason, setSetupReason] = useState('')
  const [authUser, setAuthUser] = useState<AuthUser | null>(null)
  const [authError, setAuthError] = useState('')
  const [authCheckFailed, setAuthCheckFailed] = useState(false)
  const requestGeneration = useRef(0)
  const onAccessChangedRef = useRef(onAccessChanged)
  const onAccessRefreshFailedRef = useRef(onAccessRefreshFailed)
  onAccessChangedRef.current = onAccessChanged
  onAccessRefreshFailedRef.current = onAccessRefreshFailed

  const load = useCallback(async () => {
      const generation = ++requestGeneration.current
      setAuthReady(false)
      setAuthError('')
      setAuthCheckFailed(false)
      setSetupRequired(false)
      try {
        const status = await api.authStatus()
        if (generation !== requestGeneration.current) return
        setAuthMode(status.mode)
        setAuthRequired(status.authentication_required)
        setRegistrationEnabled(status.registration_enabled)
        setSetupRequired(status.setup_required === true)
        setSetupReason(status.setup_reason ?? '')
        if (status.setup_required === true) {
          clearSession()
          setAuthUser(null)
          return
        }
        try {
          const user = await api.me()
          if (generation === requestGeneration.current) setAuthUser(user)
        } catch (reason) {
          if (generation !== requestGeneration.current) return
          if (reason && typeof reason === 'object' && 'status' in reason && reason.status === 401) {
            setAuthUser(null)
          } else {
            throw reason
          }
        }
      } catch (reason) {
        if (generation !== requestGeneration.current) return
        clearSession()
        setAuthUser(null)
        setAuthCheckFailed(true)
        setAuthError(reason instanceof Error ? reason.message : '인증 상태를 확인하지 못했습니다.')
      } finally {
        if (generation === requestGeneration.current) setAuthReady(true)
      }
  }, [])

  useEffect(() => {
    void load()
    return () => { requestGeneration.current += 1 }
  }, [load])

  const refreshAccess = useCallback(async () => {
    if (!authUser) return
    const userId = authUser.id
    const generation = requestGeneration.current
    const [verified, policy] = await Promise.all([api.me(), api.menuPolicy()])
    if (generation !== requestGeneration.current || authUser?.id !== userId) return
    setAuthUser(verified)
    onAccessChangedRef.current(verified, policy)
  }, [authUser])

  useEffect(() => {
    const changed = () => { void refreshAccess().catch(() => onAccessRefreshFailedRef.current()) }
    window.addEventListener('analysis-access-changed', changed)
    return () => window.removeEventListener('analysis-access-changed', changed)
  }, [refreshAccess])

  const login = useCallback(async (username: string, password: string) => {
    const generation = ++requestGeneration.current
    setAuthError('')
    setAuthCheckFailed(false)
    try {
      const result = await api.login(username, password)
      const verified = await api.me()
      if (generation !== requestGeneration.current) return
      saveSession(result.access_token, verified)
      setAuthUser(verified)
    } catch (reason) {
      if (generation !== requestGeneration.current) return
      setAuthError(reason instanceof Error ? reason.message : '로그인하지 못했습니다.')
      throw reason
    }
  }, [])

  const logout = useCallback(async () => {
    requestGeneration.current += 1
    const request = api.logout()
    setAuthUser(null)
    try { await request } catch { /* the browser session must still be cleared offline */ }
    clearSession()
  }, [])

  const expire = useCallback((message: string) => {
    if (authUser) requestGeneration.current += 1
    setAuthUser(null)
    setAuthError(message)
  }, [authUser])

  return { authCheckFailed, authError, authMode, authReady, authRequired, authUser, expire, login, logout, refreshAccess, registrationEnabled, setupReason, setupRequired, retryAuth: load }
}
