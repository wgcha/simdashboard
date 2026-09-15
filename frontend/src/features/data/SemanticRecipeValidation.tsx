import { useEffect, useRef, useState } from 'react'
import { semanticMappingApi, type SemanticRecipeDefinition } from '../../shared/api/semanticMapping'

type SampleCheck = { name: string; status: '대기' | '검증 중' | '통과' | '오류'; detail?: string }

/** Validate against the exact draft recipe; this never creates result Runs. */
export function SemanticRecipeValidation({ recipe }: { recipe: SemanticRecipeDefinition }) {
  const [checks, setChecks] = useState<SampleCheck[]>([])
  const [running, setRunning] = useState(false)
  const controller = useRef<AbortController | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const cancel = () => {
    controller.current?.abort()
    controller.current = null
    setChecks([]); setRunning(false)
    if (input.current) input.current.value = ''
  }
  useEffect(() => { cancel(); return () => controller.current?.abort() }, [recipe])
  const validate = async (files: File[]) => {
    cancel()
    if (!files.length) return
    const active = new AbortController()
    controller.current = active
    setChecks(files.map((file) => ({ name: file.name, status: '대기' })))
    setRunning(true)
    const update = (index: number, status: SampleCheck['status'], detail?: string) => {
      if (controller.current === active && !active.signal.aborted) setChecks((current) => current.map((item, row) => row === index ? { ...item, status, detail } : item))
    }
    for (const [index, file] of files.entries()) {
      if (active.signal.aborted) break
      update(index, '검증 중')
      try {
        const result = await semanticMappingApi.preview(file, recipe, undefined, undefined, active.signal)
        update(index, '통과', `읽은 행 ${String(result.parsed.summary?.row_count ?? '—')} · 결과 ${result.parsed.observations?.length ?? 0}개`)
      } catch (reason) { update(index, '오류', reason instanceof Error ? reason.message : '이 레시피로 읽을 수 없습니다.') }
    }
    if (controller.current === active) { controller.current = null; setRunning(false) }
  }
  const ready = recipe.mappings.length > 0 && recipe.mappings.every((mapping) => mapping.result_item_id && mapping.source)
  return <div className="semantic-card sample-validation" data-testid="semantic-multi-sample-validation">
    <h2>여러 샘플로 레시피 검증</h2>
    <p>현재 정의를 다른 파일에도 그대로 적용해 봅니다. 업무 결과는 등록하지 않습니다.</p>
    <label className="ghost-button">검증할 파일 선택<input ref={input} type="file" multiple accept=".csv,.json,.tsv,.txt" aria-label="검증할 샘플 파일들" disabled={!ready} onChange={(event) => void validate(Array.from(event.target.files ?? []))} /></label>
    {checks.length > 0 ? <><button className="ghost-button" onClick={cancel}>{running ? '검증 취소' : '검증 목록 초기화'}</button><ul>{checks.map((check, index) => <li key={index}><strong>{check.name}</strong> · {check.status}{check.detail ? ` · ${check.detail}` : ''}</li>)}</ul></> : <small>원본 필드와 결과 항목을 연결한 후 파일 여러 개를 선택하세요.</small>}
  </div>
}
