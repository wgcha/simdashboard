import { LayoutDashboard, LoaderCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import type { RequestResultLayout } from '../../shared/api/resultLayouts'
import { canCommitResultLayoutOpen, canOpenResultLayout, detailedAnalysisRoute } from './resultLayoutRouting'

type Props = {
  analysisLabel: string
  label?: string
  requestId: string
  requestContextLoading: boolean
  loadLayout: (requestId: string) => Promise<RequestResultLayout>
  isCurrentOpen: (intent: number) => boolean
  onBeginOpen: () => number
  onDomain: () => void
  onError: (message: string) => void
  onSnapshot: () => void
  onUnconfigured: () => void
}

export function ResultLayoutDetailTab({
  analysisLabel,
  label = '상세 분석',
  requestId,
  requestContextLoading,
  loadLayout,
  isCurrentOpen,
  onBeginOpen,
  onDomain,
  onError,
  onSnapshot,
  onUnconfigured,
}: Props) {
  const [loading, setLoading] = useState(false)
  const requestIntent = useRef(0)
  useEffect(() => { requestIntent.current += 1; setLoading(false); return () => { requestIntent.current += 1 } }, [requestId])
  const canOpen = canOpenResultLayout(requestId, requestContextLoading)
  const open = async () => {
    if (!canOpen || loading) return
    const contextIntent = onBeginOpen()
    const intent = ++requestIntent.current
    setLoading(true)
    try {
      const route = detailedAnalysisRoute(await loadLayout(requestId))
      if (!canCommitResultLayoutOpen(intent, requestIntent.current, contextIntent, isCurrentOpen)) return
      if (route === 'SNAPSHOT') onSnapshot()
      else if (route === 'DOMAIN') onDomain()
      else onUnconfigured()
    } catch (reason) {
      if (canCommitResultLayoutOpen(intent, requestIntent.current, contextIntent, isCurrentOpen)) onError(reason instanceof Error ? reason.message : '상세 분석을 열지 못했습니다.')
    } finally {
      if (intent === requestIntent.current) setLoading(false)
    }
  }

  return <button className={loading ? 'active' : ''} disabled={!canOpen || loading} onClick={() => void open()}>
    {loading ? <LoaderCircle className="spin" /> : <LayoutDashboard />}
    {label} <span>{analysisLabel}</span>
  </button>
}
