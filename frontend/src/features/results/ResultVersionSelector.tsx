import { LoaderCircle } from 'lucide-react'
import type { AnalysisRunSummary } from '../../types'

type Props = {
  runs: AnalysisRunSummary[]
  selectedRunId: string
  loading?: boolean
  changing?: boolean
  error?: string
  onChange: (runId: string) => void
}

const formatTime = (value: string | null | undefined) => {
  if (!value) return '완료 시각 없음'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '완료 시각 없음' : date.toLocaleString('ko-KR', { dateStyle: 'short', timeStyle: 'short' })
}

const runLabel = (run: AnalysisRunSummary) => [
  `v${run.run_no}`,
  run.run_no === 0 ? '기준' : undefined,
  run.is_latest ? '최신' : undefined,
  ['SUCCEEDED', 'COMPLETED'].includes(run.status) ? '완료' : run.status,
  formatTime(run.completed_at),
].filter(Boolean).join(' · ')

/** Selects the immutable result version shown by the main analysis dashboard. */
export function ResultVersionSelector({ runs, selectedRunId, loading = false, changing = false, error = '', onChange }: Props) {
  const disabled = loading || changing || runs.length === 0
  return <label className="result-version-selector">
    <span>결과 버전</span>
    <span className="result-version-control">
      <select aria-label="결과 버전 선택" data-testid="result-version-selector" value={selectedRunId} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
        {loading ? <option value="">결과 버전 불러오는 중</option> : runs.length === 0 ? <option value="">결과 없음</option> : runs.map((run) => <option key={run.id} value={run.id}>{runLabel(run)}</option>)}
      </select>
      {(loading || changing) && <LoaderCircle className="result-version-spinner" aria-label="결과 버전 불러오는 중" />}
    </span>
    {error ? <small role="alert">{error}</small> : runs.length === 0 && !loading ? <small>이 하중 경우에는 등록된 결과가 없습니다.</small> : null}
  </label>
}
