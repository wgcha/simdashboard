import { useCallback, useEffect, useReducer, useRef } from 'react'

import { loadInitialWorkspace, type InitialWorkspace } from './loadInitialWorkspace'
import { initialWorkspaceBootstrapState, workspaceBootstrapReducer } from './bootstrapMachine'

type WorkspaceBootstrapOptions = {
  userKey: string | null
  onStart: () => void
  onResolved: (initial: InitialWorkspace) => void
}

export function useWorkspaceBootstrap({ userKey, onStart, onResolved }: WorkspaceBootstrapOptions) {
  const [state, dispatch] = useReducer(workspaceBootstrapReducer, initialWorkspaceBootstrapState)
  const generationRef = useRef(0)
  const activeUserKeyRef = useRef<string | null>(null)
  const onStartRef = useRef(onStart)
  const onResolvedRef = useRef(onResolved)
  onStartRef.current = onStart
  onResolvedRef.current = onResolved

  const invalidate = useCallback(() => {
    activeUserKeyRef.current = null
    generationRef.current += 1
    dispatch({ type: 'RESET', generation: generationRef.current })
  }, [])

  useEffect(() => {
    if (!userKey) {
      if (activeUserKeyRef.current !== null) invalidate()
      return
    }
    if (activeUserKeyRef.current === userKey) return

    activeUserKeyRef.current = userKey
    generationRef.current += 1
    const generation = generationRef.current
    onStartRef.current()
    dispatch({ type: 'START', generation, userKey })

    void loadInitialWorkspace().then((initial) => {
      if (generationRef.current !== generation || activeUserKeyRef.current !== userKey) return
      onResolvedRef.current(initial)
      dispatch({ type: 'RESOLVE', generation, userKey, resolvedKind: initial.kind })
    }).catch((reason) => {
      if (generationRef.current !== generation || activeUserKeyRef.current !== userKey) return
      dispatch({
        type: 'REJECT',
        generation,
        userKey,
        message: reason instanceof Error ? reason.message : '초기 데이터를 불러오지 못했습니다.',
      })
    })
    // StrictMode replays effects in development. The in-flight request remains
    // owned by this hook and the second setup observes the same active key.
  }, [invalidate, userKey])

  return { state, invalidate }
}
