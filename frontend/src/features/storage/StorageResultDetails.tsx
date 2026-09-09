import { useState } from 'react'
import { api } from '../../api'
import type { Overview } from '../../types'

export function StorageResultDetails({ loadCaseId, runId }: { loadCaseId: string; runId: string }) {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const load = async (open: boolean) => {
    if (!open || overview || loading) return
    setLoading(true); setError('')
    try { setOverview(await api.overview(loadCaseId, runId)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '수치 결과를 불러오지 못했습니다.') }
    finally { setLoading(false) }
  }
  return <details className="storage-result-details" onToggle={(event) => void load(event.currentTarget.open)}><summary>수치 결과 보기</summary>{loading ? <span>불러오는 중...</span> : error ? <span role="alert">{error}</span> : overview ? <div><strong>{overview.overall_verdict}</strong>{overview.scalar_results.length ? <ul>{overview.scalar_results.map((item) => <li key={item.id}><span>{item.display_name}</span><b>{Number.isFinite(item.value_double) ? item.value_double.toFixed(2) : '—'} {item.unit}</b><em>{item.verdict}</em></li>)}</ul> : <span>등록된 수치 결과가 없습니다.</span>}</div> : null}</details>
}
