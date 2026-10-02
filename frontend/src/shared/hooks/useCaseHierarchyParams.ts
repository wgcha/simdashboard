import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { applySearchPatch, withClearedChildren, type SearchParamPatch } from './caseHierarchyParams'

export type CaseHierarchyUpdateOptions = {
  /** Replace the current history entry (automatic repairs). Defaults to push (user changes). */
  replace?: boolean
  /** Clear children of every hierarchy key in the patch (see CASE_HIERARCHY_CHILDREN). */
  clearChildren?: boolean
}

type PendingWrite = { patch: SearchParamPatch; replace: boolean }

/**
 * Router-backed Case hierarchy selection (`case`, `case_load`, `case_run`,
 * `case_option`) plus any caller-owned keys such as `scene` or `part`.
 *
 * Writes merge into the current query, so keys owned by the workspace shell
 * (`project`, `request`, `view`, `resultTab`, ...) are preserved. Several
 * updates in the same task (for example cascaded repairs inside one effect)
 * are coalesced into a single navigation; it is a push if any of them was a
 * push, otherwise a replace.
 */
export function useCaseHierarchyParams() {
  const location = useLocation()
  const navigate = useNavigate()
  const searchParams = useMemo(() => new URLSearchParams(location.search), [location.search])
  // Latest known query: the rendered one, or the one we just asked the router
  // to navigate to when the next render has not happened yet.
  const observedSearch = useRef(location.search)
  const baseSearch = useRef(location.search)
  if (observedSearch.current !== location.search) {
    observedSearch.current = location.search
    baseSearch.current = location.search
  }
  const pathname = useRef(location.pathname)
  pathname.current = location.pathname
  const pending = useRef<PendingWrite | null>(null)
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const update = useCallback((patch: SearchParamPatch, options: CaseHierarchyUpdateOptions = {}) => {
    const effective = options.clearChildren ? withClearedChildren(patch) : patch
    const replace = options.replace ?? false
    if (pending.current) {
      pending.current = { patch: { ...pending.current.patch, ...effective }, replace: pending.current.replace && replace }
      return
    }
    pending.current = { patch: effective, replace }
    queueMicrotask(() => {
      const write = pending.current
      pending.current = null
      if (!write || !mounted.current) return
      const current = baseSearch.current.startsWith('?') ? baseSearch.current.slice(1) : baseSearch.current
      const next = applySearchPatch(current, write.patch)
      if (next === current) return
      baseSearch.current = next ? `?${next}` : ''
      navigate({ pathname: pathname.current, search: baseSearch.current }, { replace: write.replace })
    })
  }, [navigate])

  /** Select one hierarchy level and clear its children. */
  const select = useCallback((key: 'case' | 'case_load' | 'case_run' | 'case_option', value: string, options: Omit<CaseHierarchyUpdateOptions, 'clearChildren'> = {}) => {
    update({ [key]: value || null }, { ...options, clearChildren: true })
  }, [update])

  const get = useCallback((key: string) => searchParams.get(key) ?? '', [searchParams])

  return {
    searchParams,
    get,
    caseId: get('case'),
    loadCaseId: get('case_load'),
    runId: get('case_run'),
    optionId: get('case_option'),
    update,
    select,
  }
}
