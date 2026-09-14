import { AlertTriangle, CheckCircle2, CircleDashed, LoaderCircle, RefreshCw, ShieldCheck } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { semanticReviewApi, type ReviewEvent, type ReviewListState, type SemanticReviewItem } from '../../shared/api/semanticReview'
import { SemanticWidgetGrid } from '../../shared/components/semanticResults'
import type { SemanticBinding, SemanticCatalog } from '../../shared/api/semanticMapping'
import './SemanticReviewQueue.css'

type Message = { kind: 'success' | 'error' | 'info'; text: string }
type Props = { binding: SemanticBinding; catalog: SemanticCatalog; canReview: boolean; refreshToken?: number; onMessage: (message: Message) => void }

function statusIcon(status: string) {
  if (status === 'READY') return <CheckCircle2 aria-hidden="true" />
  if (status === 'STALE' || status === 'INVALID') return <AlertTriangle aria-hidden="true" />
  return <CircleDashed aria-hidden="true" />
}

function formatSize(value: number | null) {
  if (value === null || !Number.isFinite(value)) return '읽기 불가'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

function errorMessage(error: Record<string, unknown> | null | undefined) {
  if (!error) return ''
  return typeof error.message === 'string' ? error.message : typeof error.code === 'string' ? error.code : '검토 오류가 있습니다.'
}

export function SemanticReviewQueue({ binding, catalog, canReview, refreshToken = 0, onMessage }: Props) {
  const [state, setState] = useState<ReviewListState | ''>('OPEN')
  const [items, setItems] = useState<SemanticReviewItem[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [cursor, setCursor] = useState<string | undefined>()
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [recipeChoices, setRecipeChoices] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [history, setHistory] = useState<ReviewEvent[]>([])
  const [historyTruncated, setHistoryTruncated] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyVersion, setHistoryVersion] = useState(0)
  const [reloadVersion, setReloadVersion] = useState(0)
  const generation = useRef(0)
  const fetchVersion = useRef(0)
  const actionVersion = useRef(0)

  const bindingRecipes = useMemo(() => catalog.recipes.filter((recipe) => binding.recipe_ids.includes(recipe.id) && recipe.active_version), [binding.recipe_ids, catalog.recipes])
  const selected = items.find((item) => item.id === selectedId) ?? null
  const contextKey = `${binding.id}:${binding.revision ?? 0}:${state}:${refreshToken}:${reloadVersion}:${cursor ?? ''}`

  useEffect(() => {
    const current = ++generation.current
    const version = ++fetchVersion.current
    const controller = new AbortController()
    setItems([])
    setSelectedId('')
    setRecipeChoices({})
    setNextCursor(null)
    setError('')
    setLoading(true)
    semanticReviewApi.list(binding.id, { state: state || undefined, limit: 50, cursor }, controller.signal).then((result) => {
      if (controller.signal.aborted || generation.current !== current || fetchVersion.current !== version) return
      setItems(result.items)
      setNextCursor(result.next_cursor ?? null)
    }).catch((reason) => {
      if (controller.signal.aborted || generation.current !== current || fetchVersion.current !== version) return
      setItems([])
      setNextCursor(null)
      setError(reason instanceof Error ? reason.message : '검토함을 불러오지 못했습니다.')
    }).finally(() => {
      if (!controller.signal.aborted && generation.current === current && fetchVersion.current === version) setLoading(false)
    })
    return () => { controller.abort(); if (generation.current === current) generation.current += 1 }
  }, [binding.id, binding.revision, contextKey, cursor, state])

  useEffect(() => {
    if (!selectedId) { setHistory([]); setHistoryTruncated(false); return }
    const current = ++generation.current
    const controller = new AbortController()
    setHistory([]); setHistoryTruncated(false); setHistoryLoading(true)
    semanticReviewApi.history(selectedId, controller.signal).then((result) => {
      if (!controller.signal.aborted && generation.current === current) { setHistory(result.events); setHistoryTruncated(result.truncated) }
    }).catch(() => undefined).finally(() => { if (!controller.signal.aborted && generation.current === current) setHistoryLoading(false) })
    return () => { controller.abort(); if (generation.current === current) generation.current += 1 }
  }, [historyVersion, selectedId])

  const recipeOptions = (item: SemanticReviewItem) => {
    const values = new Map<string, { id: string; version: number; label: string }>()
    bindingRecipes.forEach((recipe) => values.set(`${recipe.id}:${recipe.active_version}`, { id: recipe.id, version: recipe.active_version ?? 0, label: `${recipe.name ?? recipe.id} · 활성 v${recipe.active_version}` }))
    item.candidates?.forEach((candidate) => values.set(`${candidate.recipe_id}:${candidate.recipe_version}`, { id: candidate.recipe_id, version: candidate.recipe_version, label: `${catalog.recipes.find((recipe) => recipe.id === candidate.recipe_id)?.name ?? candidate.recipe_id} · v${candidate.recipe_version}` }))
    return [...values.values()].filter((value) => value.version > 0)
  }

  const chooseItem = (item: SemanticReviewItem) => {
    generation.current += 1
    setSelectedId(item.id)
    const option = recipeOptions(item).find((candidate) => candidate.id === item.selected_recipe_id && candidate.version === item.selected_recipe_version) ?? recipeOptions(item)[0]
    if (option) setRecipeChoices((current) => ({ ...current, [item.id]: `${option.id}:${option.version}` }))
  }

  const parseChoice = (item: SemanticReviewItem) => {
    const choice = recipeChoices[item.id]
    const [recipeId, version] = choice?.split(':') ?? []
    if (!recipeId || !version) return null
    return { recipe_id: recipeId, recipe_version: Number(version) }
  }

  const choiceMatchesValidatedRecipe = (item: SemanticReviewItem) => {
    const choice = parseChoice(item)
    return Boolean(choice && item.review_state === 'READY' && choice.recipe_id === item.selected_recipe_id && choice.recipe_version === item.selected_recipe_version)
  }

  const revalidate = async () => {
    if (!selected || !canReview) return
    const choice = parseChoice(selected)
    if (!choice) { onMessage({ kind: 'error', text: '검증할 활성 레시피를 선택하세요.' }); return }
    const current = generation.current
    const action = ++actionVersion.current
    const controller = new AbortController()
    setBusy(`revalidate:${selected.id}`)
    try {
      const result = await semanticReviewApi.revalidate(selected.id, { expected_revision: selected.revision, ...choice }, controller.signal)
      if (generation.current !== current) return
      setItems((currentItems) => currentItems.map((item) => item.id === selected.id ? result : item))
      onMessage({ kind: result.review_state === 'READY' ? 'success' : 'info', text: result.review_state === 'READY' ? '파일을 선택한 레시피 버전으로 검증했습니다. 확인 후 등록할 수 있습니다.' : `재검증 결과: ${result.review_state}` })
    } catch (reason) {
      if (generation.current === current) onMessage({ kind: 'error', text: reason instanceof Error ? reason.message : '파일 재검증에 실패했습니다.' })
    } finally { if (actionVersion.current === action) setBusy('') }
  }

  const confirm = async () => {
    if (!selected || selected.review_state !== 'READY' || !canReview) return
    const current = generation.current
    const action = ++actionVersion.current
    setBusy(`confirm:${selected.id}`)
    try {
      const result = await semanticReviewApi.confirm(selected.id, { expected_revision: selected.revision })
      if (generation.current !== current) return
      onMessage({ kind: 'success', text: result.status === 'SKIPPED' ? '동일한 결과가 이미 등록되어 기존 Run을 재사용했습니다.' : `검토 파일을 등록했습니다${result.run_id ? ` · Run ${result.run_id}` : ''}.` })
      setSelectedId('')
      setState('OPEN')
      setCursor(undefined)
      setReloadVersion((value) => value + 1)
    } catch (reason) {
      if (generation.current === current) onMessage({ kind: 'error', text: reason instanceof Error ? reason.message : '검토 파일 등록에 실패했습니다. 최신 상태를 다시 확인하세요.' })
    } finally { if (actionVersion.current === action) setBusy('') }
  }

  const reopen = async () => {
    if (!selected || !canReview || !['IMPORTED', 'SKIPPED'].includes(selected.review_state)) return
    const current = generation.current
    const action = ++actionVersion.current
    setBusy(`reopen:${selected.id}`)
    try {
      const result = await semanticReviewApi.reopen(selected.id, { expected_revision: selected.revision })
      if (generation.current !== current) return
      setItems((currentItems) => currentItems.map((item) => item.id === selected.id ? result : item))
      const option = recipeOptions(result)[0]
      setRecipeChoices((choices) => option ? { ...choices, [selected.id]: `${option.id}:${option.version}` } : choices)
      setHistoryVersion((version) => version + 1)
      onMessage({ kind: 'success', text: '검토 항목을 다시 검토 상태로 열었습니다. 현재 활성 레시피를 선택해 재검증하세요.' })
    } catch (reason) {
      if (generation.current === current) onMessage({ kind: 'error', text: reason instanceof Error ? reason.message : '검토 항목을 다시 열지 못했습니다. 최신 상태를 확인하세요.' })
    } finally { if (actionVersion.current === action) setBusy('') }
  }

  return <section className="semantic-card semantic-review-queue" aria-labelledby="semantic-review-heading">
    <div className="semantic-card-heading"><div><span>RESULT REVIEW QUEUE</span><h2 id="semantic-review-heading">검토함 · {binding.relative_path}</h2></div><button className="ghost-button" type="button" onClick={() => setReloadVersion((value) => value + 1)} disabled={loading}><RefreshCw className={loading ? 'spin' : ''} /> 새로고침</button></div>
    <p className="semantic-review-note">새로고침 중 자동 등록되지 않은 파일입니다. 선택한 레시피를 서버에서 다시 검증한 뒤 명시적으로 등록합니다.</p>
    <div className="semantic-review-toolbar"><label>검토 상태<select aria-label="검토 상태" value={state} onChange={(event) => { setState(event.target.value as ReviewListState | ''); setCursor(undefined) }}><option value="OPEN">미처리</option><option value="SELECTED">선택됨</option><option value="READY">검증 완료</option><option value="STALE">검증 만료</option><option value="IMPORTED">등록 완료</option><option value="SKIPPED">중복 제외</option><option value="">전체</option></select></label><label>표시 항목 수<input value={items.length} readOnly aria-label="검토 항목 수" /></label><span>{loading ? '불러오는 중…' : `${items.length}개 항목`}</span><button type="button" className="ghost-button" onClick={() => setCursor(nextCursor ?? undefined)} disabled={!nextCursor || loading}>다음 페이지</button></div>
    {error ? <p className="semantic-review-item-error" role="alert">{error}</p> : null}
    {!loading && !items.length ? <div className="semantic-review-empty">현재 상태의 검토 항목이 없습니다.</div> : null}
    <div className="semantic-review-items">{items.map((item) => <article className={`semantic-review-item ${selectedId === item.id ? 'selected' : ''}`} key={item.id}>
      <button type="button" className="semantic-review-item-header" onClick={() => chooseItem(item)} aria-expanded={selectedId === item.id}><i>{statusIcon(item.review_state)}</i><strong>{item.relative_path}</strong><b>{item.review_state}</b></button>
      <div className="semantic-review-item-meta"><span><em>판별</em>{item.scan_status}</span><span><em>크기</em>{formatSize(item.source_size ?? null)}</span><span><em>개정</em>{item.revision}</span><span><em>후보</em>{item.candidates?.length ?? 0}개</span></div>
      {item.error ? <p className="semantic-review-item-error">{errorMessage(item.error)}</p> : null}
      {selectedId === item.id ? <div className="semantic-review-detail"><label>적용할 활성 레시피<select aria-label="검증 레시피" disabled={!canReview || !!busy} value={recipeChoices[item.id] ?? ''} onChange={(event) => setRecipeChoices((current) => ({ ...current, [item.id]: event.target.value }))}><option value="">레시피 선택</option>{recipeOptions(item).map((option) => <option key={`${option.id}:${option.version}`} value={`${option.id}:${option.version}`}>{option.label}</option>)}</select></label><div className="semantic-review-candidates">{(item.candidates ?? []).map((candidate) => <div className="semantic-review-candidate" key={`${candidate.recipe_id}:${candidate.recipe_version}`}><span>{catalog.recipes.find((recipe) => recipe.id === candidate.recipe_id)?.name ?? candidate.recipe_id} · v{candidate.recipe_version}</span><small>내용 검증 필요</small></div>)}</div>{choiceMatchesValidatedRecipe(item) && item.widgets?.length ? <SemanticWidgetGrid widgets={item.widgets} /> : null}{item.review_state === 'READY' && !choiceMatchesValidatedRecipe(item) ? <p className="semantic-review-note">레시피 선택이 바뀌었습니다. 새 선택을 서버에서 재검증해야 등록할 수 있습니다.</p> : null}<details><summary>처리 이력 {historyLoading ? '불러오는 중…' : `(${history.length})`}</summary><div className="semantic-review-history">{history.length ? history.map((event, index) => <div key={`${event.revision}:${index}`}><strong>{event.old_state ?? '—'} → {event.new_state}</strong><small>개정 {event.revision} · {event.occurred_at} · {event.actor}</small>{event.prior_run_id || event.current_run_id ? <small>Run {event.current_run_id ?? event.prior_run_id}</small> : null}</div>) : <small>저장된 상태 변경 이력이 없습니다.</small>}{historyTruncated ? <small>이력 일부만 표시됩니다.</small> : null}</div></details><div className="semantic-review-actions">{['IMPORTED', 'SKIPPED'].includes(item.review_state) ? <button type="button" className="secondary-button" onClick={() => void reopen()} disabled={!canReview || !!busy}>{busy === `reopen:${item.id}` ? <LoaderCircle className="spin" /> : <ShieldCheck />} 다시 검토</button> : <button type="button" className="secondary-button" onClick={() => void revalidate()} disabled={!canReview || !!busy || !recipeChoices[item.id]}>{busy === `revalidate:${item.id}` ? <LoaderCircle className="spin" /> : <ShieldCheck />} 서버 재검증</button>}<button type="button" className="primary-button" onClick={() => void confirm()} disabled={!canReview || !!busy || !choiceMatchesValidatedRecipe(item)}><CheckCircle2 /> 명시적으로 등록</button></div></div> : null}
    </article>)}</div>
  </section>
}
