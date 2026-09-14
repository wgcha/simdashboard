import { useEffect, useState } from 'react'
import { semanticMappingApi, type ResultsResponse } from '../../shared/api/semanticMapping'
import { SemanticWidgetGrid } from '../../shared/components/semanticResults'

/** The requested Run owns the presentation; stale responses never cross Runs. */
export function SemanticResultPanel({ loadCaseId, runId }: { loadCaseId: string; runId: string }) {
  const key = `${loadCaseId}/${runId}`
  const [state, setState] = useState<{ key: string; data?: ResultsResponse; error?: string }>({ key })
  useEffect(() => {
    let cancelled = false
    const currentKey = `${loadCaseId}/${runId}`
    semanticMappingApi.results({ load_case_id: loadCaseId, run_id: runId }).then(
      (data) => { if (!cancelled) setState({ key: currentKey, data }) },
      () => { if (!cancelled) setState({ key: currentKey, error: '연결된 결과 템플릿을 불러오지 못했습니다.' }) },
    )
    return () => { cancelled = true }
  }, [loadCaseId, runId])
  if (state.key !== key) return null
  if (state.error) return <p role="status">{state.error}</p>
  if (!state.data?.widgets.length) return null
  return <section className="semantic-run-results" aria-label="레시피로 연결한 결과">
    <header><h2>연결된 결과</h2><p>이 실행에 저장된 표시 템플릿 · v{state.data.template_version ?? 1}</p></header>
    <SemanticWidgetGrid widgets={state.data.widgets} />
  </section>
}
