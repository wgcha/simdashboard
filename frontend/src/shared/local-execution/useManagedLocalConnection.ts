import { useCallback, useEffect, useRef, useState } from 'react'
import { localExecutionApi, type LocalIdentity, type ManagedDevice, type ManagedSession } from '../api/localExecution'
import { localRunnerApi, type LocalRunnerHealth } from '../api/localRunner'

type State = {
  identity: LocalIdentity | null
  device: ManagedDevice | null
  session: ManagedSession | null
  health: LocalRunnerHealth | null
  status: 'CHECKING' | 'NEEDS_PAIRING' | 'PAIRING' | 'CONNECTED' | 'HELPER_REQUIRED' | 'DISCONNECTED' | 'ERROR'
  error: string
}
const initial: State = { identity: null, device: null, session: null, health: null, status: 'CHECKING', error: '' }
const message = (reason: unknown) => reason instanceof Error ? reason.message : 'PC 연결을 확인하지 못했습니다.'

/** Connection credentials never depend on the selected work item or survive logout. */
export function useManagedLocalConnection(userId: string) {
  const [state, setState] = useState<State>(initial)
  const epoch = useRef(0)
  const pending = useRef<AbortController | null>(null)
  const invalidate = useCallback(() => { epoch.current += 1; pending.current?.abort(); pending.current = null }, [])
  const clearBinding = useCallback((status: 'DISCONNECTED' | 'HELPER_REQUIRED' = 'DISCONNECTED') => {
    invalidate()
    setState((old) => ({ ...old, session: null, health: null, status }))
  }, [invalidate])

  const check = useCallback(async (allowPairing = false) => {
    invalidate()
    const generation = epoch.current
    const controller = new AbortController()
    pending.current = controller
    const current = () => !controller.signal.aborted && epoch.current === generation
    setState((old) => ({ ...old, session: null, health: null, status: 'CHECKING', error: '' }))
    try {
      const [identityResult, devicesResult] = await Promise.allSettled([
        localExecutionApi.identity(controller.signal), localExecutionApi.devices(controller.signal),
      ])
      if (!current()) return
      const identity = identityResult.status === 'fulfilled' ? identityResult.value : null
      setState((old) => ({ ...old, identity }))
      if (devicesResult.status === 'rejected') throw devicesResult.reason
      if (!identity?.managed) {
        setState((old) => ({ ...old, status: 'HELPER_REQUIRED', error: '로컬 실행 도우미를 시작한 뒤 다시 확인하세요.' }))
        return
      }
      let device = devicesResult.value.find((item) => item.user_id === userId && item.device_id === identity.device_id && !item.revoked_at) ?? null
      if (!device && !allowPairing) {
        setState((old) => ({ ...old, device: null, status: 'NEEDS_PAIRING' }))
        return
      }
      if (!device) {
        setState((old) => ({ ...old, status: 'PAIRING' }))
        const grant = await localExecutionApi.createPairing(identity.device_id, controller.signal)
        if (!current()) return
        const binding = await localExecutionApi.pairHelper(grant.pairing_token, controller.signal)
        if (!current()) return
        if (binding.user_id !== userId) throw new Error('PC 연결 계정이 현재 로그인 계정과 다릅니다.')
        const devices = await localExecutionApi.devices(controller.signal)
        device = devices.find((item) => item.id === binding.binding_id && item.user_id === userId && !item.revoked_at) ?? null
      }
      if (!current()) return
      if (!device) throw new Error('PC 연결 정보를 다시 확인해 주세요.')
      const session = await localExecutionApi.session(device.id, controller.signal)
      if (!current()) return
      const health = await localRunnerApi.health(session.token, controller.signal)
      if (!current()) return
      if (session.user_id !== userId || health.user_id !== userId || health.binding_id !== device.id) throw new Error('PC 연결 계정 확인에 실패했습니다.')
      setState({ identity, device, session, health, status: 'CONNECTED', error: '' })
    } catch (reason) {
      if (current()) setState((old) => ({ ...old, session: null, health: null, status: 'ERROR', error: message(reason) }))
    }
  }, [invalidate, userId])

  useEffect(() => {
    setState(initial)
    void check()
    const expired = () => { clearBinding(); setState((old) => ({ ...old, error: '로그인이 만료되었습니다.' })) }
    window.addEventListener('analysis-auth-expired', expired)
    return () => { invalidate(); window.removeEventListener('analysis-auth-expired', expired) }
  }, [check, clearBinding, invalidate])

  useEffect(() => {
    if (!state.session || !state.device) return
    const generation = epoch.current
    const bindingId = state.device.id
    let controller: AbortController | null = null
    let stopped = false
    const refresh = async () => {
      if (controller || stopped) return
      controller = new AbortController()
      try {
        const session = await localExecutionApi.session(bindingId, controller.signal)
        const health = await localRunnerApi.health(session.token, controller.signal)
        if (stopped || controller.signal.aborted || epoch.current !== generation) return
        if (session.user_id !== userId || health.user_id !== userId || health.binding_id !== bindingId) throw new Error('PC 세션 계정이 변경되었습니다.')
        setState((old) => ({ ...old, session, health }))
      } catch (reason) {
        if (!stopped && !controller.signal.aborted && epoch.current === generation) {
          clearBinding()
          setState((old) => ({ ...old, status: 'ERROR', error: message(reason) }))
        }
      }
    }
    const remaining = Date.parse(state.session.expires_at) - Date.now()
    const timer = window.setTimeout(() => void refresh(), Math.max(1000, Math.min(300_000, remaining - 60_000)))
    const onFocus = () => { if (Date.parse(state.session!.expires_at) - Date.now() < 120_000) void refresh() }
    window.addEventListener('focus', onFocus)
    return () => { stopped = true; controller?.abort(); window.clearTimeout(timer); window.removeEventListener('focus', onFocus) }
  }, [state.session, state.device, clearBinding, userId])

  const disconnect = async () => {
    const device = state.device
    clearBinding()
    if (!device) return
    const generation = epoch.current
    const controller = new AbortController()
    pending.current = controller
    try {
      await localExecutionApi.revoke(device.id, controller.signal)
      if (!controller.signal.aborted && epoch.current === generation) setState((old) => ({ ...old, device: null, error: '' }))
    } catch (reason) {
      if (!controller.signal.aborted && epoch.current === generation) setState((old) => ({ ...old, status: 'ERROR', error: `연결 해제 요청 실패: ${message(reason)}` }))
    }
  }
  return { ...state, token: state.session?.token ?? '', clearBinding, refresh: () => void check(), connect: () => void check(true), disconnect }
}
