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
  if (!state.data) return null
  if (!state.data.widgets.length) {
    if (!state.data.has_provenance) return null
    return <section className="semantic-run-results" aria-label="레시피로 연결한 결과">
      <header><h2>연결된 결과</h2></header>
      <p role="status">{state.data.empty_reason === 'NO_DISPLAY_TEMPLATE' ? '이 실행에는 표시 템플릿이 연결되어 있지 않습니다.' : '이 실행의 표시 템플릿에 위젯이 없습니다.'}</p>
      <p>샘플·레시피에서 위젯을 구성하고 결과 설정을 저장·활성화한 뒤, 폴더 연결을 저장하거나 같은 파일을 다시 처리하세요. 기존 실행은 보존됩니다.</p>
    </section>
  }
  return <section className="semantic-run-results" aria-label="레시피로 연결한 결과">
    <header><h2>연결된 결과</h2><p>이 실행에 저장된 표시 템플릿 · v{state.data.template_version ?? 1}</p><p>레시피 위젯 구성은 샘플·레시피의 표시 템플릿에서 수정합니다.</p></header>
    <SemanticWidgetGrid widgets={state.data.widgets} />
  </section>
}
