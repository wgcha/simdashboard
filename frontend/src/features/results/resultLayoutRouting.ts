import { useRef, useState } from 'react'

import type { RequestResultLayout } from '../../shared/api/resultLayouts'
import type { Workflow } from '../../types'

export type DetailedAnalysisRoute = 'SNAPSHOT' | 'DOMAIN' | 'UNCONFIGURED'

/** A domain result screen is reachable only through a server-owned legacy marker. */
export function detailedAnalysisRoute(layout: RequestResultLayout): DetailedAnalysisRoute {
  if (layout.compatibility?.route_kind === 'DOMAIN') return 'DOMAIN'
  if (layout.snapshot?.pages.length) return 'SNAPSHOT'
  return 'UNCONFIGURED'
}

export function createRequestContextIntentGate() {
  let latestIntent = 0
  return { start: () => ++latestIntent, isCurrent: (intent: number) => intent === latestIntent }
}

export function createUnifiedContextIntentGate() {
  const entries = createRequestContextIntentGate()
  const monitoring = createRequestContextIntentGate()
  return {
    beginContextEntry: () => { monitoring.start(); return entries.start() },
    isCurrentContextEntry: entries.isCurrent,
    beginMonitoringContext: monitoring.start,
    isCurrentMonitoringContext: monitoring.isCurrent,
  }
}

export function canOpenResultLayout(requestId: string, requestContextLoading: boolean) {
  return Boolean(requestId) && !requestContextLoading
}

export function canCommitResultLayoutOpen(localIntent: number, currentLocalIntent: number, contextIntent: number, isCurrentContext: (intent: number) => boolean) {
  return localIntent === currentLocalIntent && isCurrentContext(contextIntent)
}

export function isPendingResultAnalysis(resultLayoutPending: boolean, activeDashboardId: string) {
  return resultLayoutPending || activeDashboardId === 'request-result-layout' || activeDashboardId === 'pending-open-cell'
}

export function useResultAnalysisIntent() {
  const gate = useRef(createUnifiedContextIntentGate())
  const selection = useRef(0)
  const [requestContextLoading, setRequestContextLoading] = useState(false)
  return {
    requestContextLoading,
    ...gate.current,
    runRequestSelection: async (selectRequest: () => void, load: () => Promise<unknown>) => {
      const intent = ++selection.current; selectRequest(); setRequestContextLoading(true)
      try { return await load() } finally { if (intent === selection.current) setRequestContextLoading(false) }
    },
  }
}

type WorkflowAnalysisOpenerOptions = {
  selectRequestContext: (workflow: Workflow, preferredView?: 'open_cell') => Promise<unknown>
  loadLayout: (requestId: string) => Promise<RequestResultLayout>
  beginIntent: () => number
  isCurrentIntent: (intent: number) => boolean
  setActiveDashboardId: (dashboardId: string) => void
  setActiveView: (view: 'custom') => void
  setError: (message: string) => void
}

export function createWorkflowAnalysisOpener({ selectRequestContext, loadLayout, beginIntent, isCurrentIntent, setActiveDashboardId, setActiveView, setError }: WorkflowAnalysisOpenerOptions) {
  return async (workflow: Workflow) => {
    const intent = beginIntent()
    try {
      const route = detailedAnalysisRoute(await loadLayout(workflow.request.id))
      if (!isCurrentIntent(intent)) return
      await selectRequestContext(workflow, route === 'DOMAIN' ? 'open_cell' : undefined)
      if (!isCurrentIntent(intent)) return
      if (route === 'DOMAIN') return
      setActiveDashboardId(route === 'SNAPSHOT' ? 'request-result-layout' : 'pending-open-cell')
      setActiveView('custom')
    } catch (reason) { if (isCurrentIntent(intent)) setError(reason instanceof Error ? reason.message : '상세 분석을 열지 못했습니다.') }
  }
}
