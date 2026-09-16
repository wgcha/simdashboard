import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../../api'
import type { AnalysisRunSummary, Overview } from '../../types'

type Options = {
  loadCaseId: string
  requestedRunId?: string
  enabled?: boolean
  overview: Overview | null
  setOverview: (overview: Overview) => void
}

export type ResultVersionSelection = {
  analysisRuns: AnalysisRunSummary[]
  selectedAnalysisRunId: string
  analysisRunsLoading: boolean
  analysisRunChanging: boolean
  analysisRunError: string
  selectAnalysisRun: (runId: string) => Promise<boolean>
}

const errorMessage = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback

/** Loads immutable result versions and keeps overview changes race-safe. */
export function useResultVersionSelection({ loadCaseId, requestedRunId, enabled = true, overview, setOverview }: Options): ResultVersionSelection {
  const [analysisRuns, setAnalysisRuns] = useState<AnalysisRunSummary[]>([])
  const [selectedAnalysisRunId, setSelectedAnalysisRunId] = useState('')
  const [analysisRunsLoading, setAnalysisRunsLoading] = useState(false)
  const [analysisRunChanging, setAnalysisRunChanging] = useState(false)
  const [analysisRunError, setAnalysisRunError] = useState('')
  const requestSequence = useRef(0)
  const overviewRef = useRef<Overview | null>(overview)
  const runsRef = useRef<AnalysisRunSummary[]>(analysisRuns)
  overviewRef.current = overview
  runsRef.current = analysisRuns

  useEffect(() => {
    const sequence = ++requestSequence.current
    setAnalysisRunError('')
    runsRef.current = []
    setAnalysisRuns([])
    setSelectedAnalysisRunId('')
    if (!enabled || !loadCaseId) {
      setAnalysisRunsLoading(false)
      setAnalysisRunChanging(false)
      return
    }
    setAnalysisRunsLoading(true)
    api.analysisRuns(loadCaseId).then((runs) => {
      if (sequence !== requestSequence.current) return
      const latest = runs.find((run) => run.is_latest) ?? runs[0]
      const requested = requestedRunId && runs.some((run) => run.id === requestedRunId) ? requestedRunId : undefined
      const loadedOverviewRun = overviewRef.current?.load_case.id === loadCaseId && runs.some((run) => run.id === overviewRef.current?.run) ? overviewRef.current?.run : undefined
      const selectedRunId = requested ?? loadedOverviewRun ?? latest?.id ?? ''
      runsRef.current = runs
      setAnalysisRuns(runs)
      setSelectedAnalysisRunId(selectedRunId)
      const currentOverview = overviewRef.current
      if (selectedRunId && currentOverview?.load_case.id === loadCaseId && currentOverview.run !== selectedRunId) {
        setAnalysisRunChanging(true)
        return api.overview(loadCaseId, selectedRunId).then((overviewData) => {
          if (sequence === requestSequence.current) setOverview(overviewData)
        })
      }
      return undefined
    }).catch((reason) => {
      if (sequence !== requestSequence.current) return
      setAnalysisRuns([])
      setSelectedAnalysisRunId('')
      setAnalysisRunError(errorMessage(reason, '결과 버전을 불러오지 못했습니다.'))
    }).finally(() => {
      if (sequence !== requestSequence.current) return
      setAnalysisRunsLoading(false)
      setAnalysisRunChanging(false)
    })
    return () => {
      if (sequence === requestSequence.current) requestSequence.current += 1
    }
  }, [enabled, loadCaseId, requestedRunId, setOverview])

  const selectAnalysisRun = useCallback(async (runId: string) => {
    if (!loadCaseId || !enabled) return false
    const sequence = ++requestSequence.current
    setAnalysisRunError('')
    setAnalysisRunChanging(true)
    try {
      // A result refresh can create a Run after the initial list was loaded.
      // Reload before rejecting the requested immutable provenance.
      if (!runsRef.current.some((run) => run.id === runId)) {
        const runs = await api.analysisRuns(loadCaseId)
        if (sequence !== requestSequence.current) return false
        runsRef.current = runs
        setAnalysisRuns(runs)
      }
      if (!runsRef.current.some((run) => run.id === runId)) {
        if (sequence === requestSequence.current) setAnalysisRunError('새 결과 Run을 결과 버전 목록에서 찾지 못했습니다. 잠시 후 다시 시도하세요.')
        return false
      }
      const overviewData = await api.overview(loadCaseId, runId)
      if (sequence !== requestSequence.current) return false
      setSelectedAnalysisRunId(runId)
      setOverview(overviewData)
      return true
    } catch (reason) {
      if (sequence === requestSequence.current) setAnalysisRunError(errorMessage(reason, '선택한 결과 버전을 불러오지 못했습니다.'))
      return false
    } finally {
      if (sequence === requestSequence.current) setAnalysisRunChanging(false)
    }
  }, [enabled, loadCaseId, setOverview])

  return { analysisRuns, selectedAnalysisRunId, analysisRunsLoading, analysisRunChanging, analysisRunError, selectAnalysisRun }
}
