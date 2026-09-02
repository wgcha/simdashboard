import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../../api'
import type { AnalysisRunSummary, Overview } from '../../types'

type Options = {
  loadCaseId: string
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
  selectAnalysisRun: (runId: string) => Promise<void>
}

const errorMessage = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback

/** Loads immutable result versions and keeps overview changes race-safe. */
export function useResultVersionSelection({ loadCaseId, enabled = true, overview, setOverview }: Options): ResultVersionSelection {
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
    if (!enabled || !loadCaseId) {
      setAnalysisRuns([])
      setSelectedAnalysisRunId('')
      setAnalysisRunsLoading(false)
      setAnalysisRunChanging(false)
      return
    }
    setAnalysisRunsLoading(true)
    api.analysisRuns(loadCaseId).then((runs) => {
      if (sequence !== requestSequence.current) return
      const latest = runs.find((run) => run.is_latest) ?? runs[0]
      runsRef.current = runs
      setAnalysisRuns(runs)
      setSelectedAnalysisRunId(latest?.id ?? '')
      const currentOverview = overviewRef.current
      if (latest && currentOverview?.load_case.id === loadCaseId && currentOverview.run !== latest.id) {
        setAnalysisRunChanging(true)
        return api.overview(loadCaseId, latest.id).then((overviewData) => {
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
  }, [enabled, loadCaseId, setOverview])

  const selectAnalysisRun = useCallback(async (runId: string) => {
    if (!loadCaseId || !enabled || !runsRef.current.some((run) => run.id === runId)) return
    const sequence = ++requestSequence.current
    setAnalysisRunError('')
    setAnalysisRunChanging(true)
    try {
      const overviewData = await api.overview(loadCaseId, runId)
      if (sequence !== requestSequence.current) return
      setSelectedAnalysisRunId(runId)
      setOverview(overviewData)
    } catch (reason) {
      if (sequence === requestSequence.current) setAnalysisRunError(errorMessage(reason, '선택한 결과 버전을 불러오지 못했습니다.'))
    } finally {
      if (sequence === requestSequence.current) setAnalysisRunChanging(false)
    }
  }, [enabled, loadCaseId, setOverview])

  return { analysisRuns, selectedAnalysisRunId, analysisRunsLoading, analysisRunChanging, analysisRunError, selectAnalysisRun }
}
