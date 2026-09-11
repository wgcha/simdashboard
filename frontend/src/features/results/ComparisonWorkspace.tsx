import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { AlertTriangle, Check, LoaderCircle, Plus } from 'lucide-react'
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../../api'
import type { AnalysisRunSummary, ReviewItem, RunComparison, RunComparisonReportContext, RunTrust } from '../../types'
import { ComparisonEvidenceTable } from './ComparisonEvidenceTable'
import './ComparisonWorkspace.css'
import { RunConditionComparison } from './RunConditionComparison'

type ReviewForm = { title: string; body: string; variableKey: string; timeValue: string; entityType: '' | 'NODE' | 'ELEMENT'; entityId: string }
const emptyReviewForm: ReviewForm = { title: '', body: '', variableKey: '', timeValue: '', entityType: '', entityId: '' }
const valueLabel = (value: number | null, unit: string | null) => value == null ? '-' : `${value.toFixed(Math.abs(value) >= 100 ? 0 : 2)} ${unit ?? ''}`.trim()
const changeLabel: Record<string, string> = { REGRESSION: '회귀', IMPROVED: '개선', UNCHANGED: '유지', ADDED: '추가', REMOVED: '제거', NOT_COMPARABLE: '비교 불가' }
function reviewEvidence(comparison: RunComparison, key: string) {
  const item = comparison.scalar_comparison.find((entry) => entry.variable_key === key)
  if (!item) return null
  const unitNeedsCheck = item.change === 'NOT_COMPARABLE'
  const rawValue = (value: number | null, unit: string | null) => value == null ? '-' : `${String(value)}${unit ? ` ${unit}` : ''}`
  const baseline = item.change === 'ADDED' ? '기준 Run에 없음' : unitNeedsCheck ? (item.baseline_value == null ? '비교 조건 확인 필요' : `${String(item.baseline_value)} (비교 조건 확인 필요)`) : rawValue(item.baseline_value, item.unit)
  const target = item.change === 'REMOVED' ? '대상 Run에 없음' : unitNeedsCheck ? (item.target_value == null ? '비교 조건 확인 필요' : `${String(item.target_value)} (비교 조건 확인 필요)`) : rawValue(item.target_value, item.unit)
  const delta = unitNeedsCheck || item.delta == null ? '비교 불가' : `${item.delta >= 0 ? '+' : ''}${String(item.delta)}${item.unit ? ` ${item.unit}` : ''}`
  return { item, title: `${item.display_name} 비교 검토`, body: `관찰 사실\n기준 Run ${comparison.baseline_run.run_no}: ${baseline} · ${item.baseline_verdict ?? '-'}\n대상 Run ${comparison.target_run.run_no}: ${target} · ${item.target_verdict ?? '-'}\n차이: ${delta} · 판정 변화: ${changeLabel[item.change]}\n\n원인 가설\n\n추가 확인\n\n설계 변경 방향` }
}

export function ComparisonWorkspace(props: { loadCaseId: string; currentRunId: string; onContextChange?: (context: RunComparisonReportContext | null) => void }) {
  return <ComparisonWorkspaceContent key={`${props.loadCaseId}:${props.currentRunId}`} {...props} />
}
function ComparisonWorkspaceContent({ loadCaseId, currentRunId, onContextChange }: { loadCaseId: string; currentRunId: string; onContextChange?: (context: RunComparisonReportContext | null) => void }) {
  const [runs, setRuns] = useState<AnalysisRunSummary[]>([])
  const [baselineRunId, setBaselineRunId] = useState('')
  const [targetRunId, setTargetRunId] = useState(currentRunId)
  const [seriesKey, setSeriesKey] = useState('')
  const [selectedKey, setSelectedKey] = useState('')
  const [comparison, setComparison] = useState<RunComparison | null>(null)
  const [trust, setTrust] = useState<RunTrust | null>(null)
  const [reviews, setReviews] = useState<ReviewItem[]>([])
  const [busy, setBusy] = useState(true)
  const [seriesLoading, setSeriesLoading] = useState(false)
  const [seriesError, setSeriesError] = useState('')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [form, setForm] = useState<ReviewForm>(emptyReviewForm)
  const draftRef = useRef(new Map<string, ReviewForm>())
  const formRef = useRef(form)
  const activeTargetRunRef = useRef(targetRunId)
  const activeContextKeyRef = useRef('')
  const onContextChangeRef = useRef(onContextChange)
  const comparisonRef = useRef<RunComparison | null>(null)
  const chartRef = useRef<HTMLDivElement>(null)
  const reviewFieldRef = useRef<HTMLTextAreaElement>(null)
  const pendingSeriesFocusRef = useRef('')
  const contextKey = baselineRunId && targetRunId ? `${loadCaseId}:${baselineRunId}:${targetRunId}` : ''
  activeTargetRunRef.current = targetRunId
  activeContextKeyRef.current = contextKey
  onContextChangeRef.current = onContextChange
  comparisonRef.current = comparison
  const setReviewForm = useCallback((next: ReviewForm | ((current: ReviewForm) => ReviewForm)) => setForm((current) => { const value = typeof next === 'function' ? next(current) : next; formRef.current = value; if (contextKey) draftRef.current.set(contextKey, value); return value }), [contextKey])

  useEffect(() => {
    let cancelled = false
    onContextChangeRef.current?.(null); setBusy(true); setSeriesLoading(false); setSeriesError(''); setMessage(''); setComparison(null); setTrust(null); setReviews([]); setSeriesKey(''); setSelectedKey('')
    api.analysisRuns(loadCaseId).then((items) => { if (cancelled) return; setRuns(items); const target = items.find((item) => item.id === currentRunId) ?? items[0]; const baseline = items.find((item) => item.id !== target?.id); setTargetRunId(target?.id ?? ''); setBaselineRunId(baseline?.id ?? '') }).catch((reason) => { if (!cancelled) setMessage(reason instanceof Error ? reason.message : 'Run 목록을 불러오지 못했습니다.') }).finally(() => { if (!cancelled) setBusy(false) })
    return () => { cancelled = true }
  }, [loadCaseId, currentRunId])
  useEffect(() => { if (!contextKey) return; const draft = draftRef.current.get(contextKey) ?? emptyReviewForm; formRef.current = draft; setForm(draft) }, [contextKey])
  useEffect(() => {
    if (!baselineRunId || !targetRunId || baselineRunId === targetRunId) return
    let cancelled = false
    onContextChangeRef.current?.(null); setBusy(true); setSeriesLoading(false); setSeriesError(''); setMessage(''); setComparison(null); setTrust(null); setReviews([])
    Promise.all([api.runComparison(loadCaseId, baselineRunId, targetRunId), api.runTrust(targetRunId), api.reviewItems(targetRunId)]).then(([comparisonData, trustData, reviewData]) => { if (cancelled) return; setComparison(comparisonData); setTrust(trustData); setReviews(reviewData); setSelectedKey((current) => comparisonData.scalar_comparison.some((item) => item.variable_key === current) ? current : comparisonData.scalar_comparison[0]?.variable_key ?? '') }).catch((reason) => { if (!cancelled) setMessage(reason instanceof Error ? reason.message : '비교 데이터를 불러오지 못했습니다.') }).finally(() => { if (!cancelled) setBusy(false) })
    return () => { cancelled = true }
  }, [loadCaseId, baselineRunId, targetRunId])
  useEffect(() => {
    if (!seriesKey || !baselineRunId || !targetRunId || baselineRunId === targetRunId) return
    let cancelled = false
    onContextChangeRef.current?.(null); setSeriesLoading(true); setSeriesError('')
    api.runComparison(loadCaseId, baselineRunId, targetRunId, seriesKey).then((seriesData) => {
      const current = comparisonRef.current
      if (cancelled || !current || current.baseline_run.id !== baselineRunId || current.target_run.id !== targetRunId || seriesData.baseline_run.id !== baselineRunId || seriesData.target_run.id !== targetRunId) return
      if (seriesData.time_series?.variable_key !== seriesKey) { pendingSeriesFocusRef.current = ''; setSeriesError('요청한 변수와 다른 시계열이 반환되었습니다.'); return }
      setComparison((current) => current?.baseline_run.id === baselineRunId && current.target_run.id === targetRunId ? { ...current, time_series: seriesData.time_series } : current)
    }).catch((reason) => { if (!cancelled) { pendingSeriesFocusRef.current = ''; setSeriesError(reason instanceof Error ? reason.message : '선택한 시계열을 불러오지 못했습니다.') } }).finally(() => { if (!cancelled) setSeriesLoading(false) })
    return () => { cancelled = true }
  }, [loadCaseId, baselineRunId, targetRunId, seriesKey])
  useEffect(() => { if (!busy && !seriesLoading && comparison && trust && comparison.baseline_run.id === baselineRunId && comparison.target_run.id === targetRunId && trust.run.id === targetRunId && (!seriesKey || comparison.time_series?.variable_key === seriesKey)) onContextChangeRef.current?.({ loadCaseId, baselineRunId, targetRunId, comparison, trust, reviews }) }, [busy, seriesLoading, loadCaseId, baselineRunId, targetRunId, comparison, trust, reviews, seriesKey])
  useEffect(() => { if (!comparison || pendingSeriesFocusRef.current !== comparison.time_series?.variable_key) return; pendingSeriesFocusRef.current = ''; const frame = requestAnimationFrame(() => { chartRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }); chartRef.current?.focus() }); return () => cancelAnimationFrame(frame) }, [comparison])

  const targetVariableKeys = useMemo(() => new Set(comparison?.scalar_comparison.filter((item) => item.target_value != null && item.change !== 'REMOVED').map((item) => item.variable_key)), [comparison])
  const viewSeries = useCallback((key: string) => { if (!comparison?.available_series.some((item) => item.variable_key === key)) { setMessage('선택한 변수에는 두 Run이 공통으로 가진 시계열 근거가 없습니다.'); return }; if (comparison.time_series?.variable_key === key && (!seriesKey || seriesKey === key) && !seriesLoading && !seriesError) { chartRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }); chartRef.current?.focus(); return }; pendingSeriesFocusRef.current = key; setSeriesKey(key) }, [comparison, seriesKey, seriesLoading, seriesError])
  const prepareReview = useCallback((key: string) => {
    if (!comparison) return
    const evidence = reviewEvidence(comparison, key); if (!evidence) return
    const canPinVariable = targetVariableKeys.has(key), current = formRef.current, hasDraft = Boolean(current.title.trim() || current.body.trim() || current.timeValue || current.entityType || current.entityId), marker = `[비교 근거: ${key} · ${evidence.item.display_name}]`
    const body = hasDraft ? (current.body.includes(marker) ? current.body : `${current.body.trim()}${current.body.trim() ? '\n\n' : ''}${marker}\n${evidence.body}`) : `${marker}\n${evidence.body}`
    setReviewForm({ ...current, title: current.title.trim() || evidence.title, body, variableKey: hasDraft ? current.variableKey : (canPinVariable ? key : '') })
    setMessage(hasDraft ? '기존 초안과 변수 연결을 유지하고 비교 근거를 추가했습니다.' : canPinVariable ? '비교 근거를 검토 초안에 채웠습니다.' : '대상 Run에 없는 변수이므로 Run 전체 검토로 준비했습니다.')
    requestAnimationFrame(() => { const field = reviewFieldRef.current; field?.scrollIntoView({ behavior: 'smooth', block: 'center' }); field?.focus() })
  }, [comparison, setReviewForm, targetVariableKeys])
  const submitReview = async (event: FormEvent) => {
    event.preventDefault(); if (saving || !form.title.trim() || !form.body.trim() || !targetRunId) return
    const safeVariableKey = form.variableKey && targetVariableKeys.has(form.variableKey) ? form.variableKey : null, requestedRunId = targetRunId, requestedContextKey = contextKey, submittedForm = form
    setSaving(true)
    try { const created = await api.createReviewItem(requestedRunId, { title: form.title.trim(), body: form.body.trim(), variable_key: safeVariableKey, time_value: form.timeValue ? Number(form.timeValue) : null, entity_type: form.entityType || null, entity_id: form.entityId.trim() || null, review_status: 'OPEN', created_by: '대시보드 검토자' }); if (draftRef.current.get(requestedContextKey) === submittedForm) draftRef.current.set(requestedContextKey, emptyReviewForm); if (requestedContextKey !== activeContextKeyRef.current) return; setReviews((items) => [created, ...items]); if (formRef.current === submittedForm) setReviewForm(emptyReviewForm); setMessage('검토 의견을 결과 문맥에 저장했습니다.') } catch (reason) { if (requestedContextKey === activeContextKeyRef.current) setMessage(reason instanceof Error ? reason.message : '검토 의견을 저장하지 못했습니다.') } finally { setSaving(false) }
  }
  const updateReviewStatus = async (item: ReviewItem, status: ReviewItem['review_status']) => { try { const updated = await api.updateReviewItem(item.id, status); if (updated.analysis_run_id === activeTargetRunRef.current) setReviews((items) => items.map((entry) => entry.id === updated.id ? updated : entry)) } catch (reason) { if (item.analysis_run_id === activeTargetRunRef.current) setMessage(reason instanceof Error ? reason.message : '검토 상태를 변경하지 못했습니다.') } }
  const runLabel = (run: AnalysisRunSummary) => `Run ${run.run_no} · ${run.overall_verdict} · ${run.completed_at ? new Date(run.completed_at).toLocaleDateString('ko-KR') : run.status}`
  if (busy && runs.length === 0) return <div className="comparison-state"><LoaderCircle className="spin" /> Run 비교 데이터를 준비하고 있습니다.</div>
  if (!runs.length && message) return <div className="comparison-state" role="alert"><AlertTriangle /><strong>Run 목록을 불러오지 못했습니다.</strong><span>{message}</span></div>
  if (runs.length < 2) return <div className="comparison-state"><AlertTriangle /><strong>{runs.length ? '비교할 Run이 하나뿐입니다.' : '비교할 결과가 없습니다.'}</strong><span>같은 하중 경우에 결과를 한 번 더 적재하면 기준 Run과 비교할 수 있습니다.</span></div>
  return <section className="comparison-workspace"><header className="comparison-hero"><div><span>RUN DIFF · TRUST · REVIEW</span><h2>Run 비교·검토</h2><p>같은 하중 경우라도 해석기·설정의 동일성은 인증되지 않습니다. 변화와 데이터 근거를 별도로 확인합니다.</p></div><div className="run-pickers"><label><span>기준 Run</span><select aria-label="기준 Run" value={baselineRunId} onChange={(event) => { setBaselineRunId(event.target.value); setSeriesKey('') }}>{runs.filter((item) => item.id !== targetRunId).map((item) => <option key={item.id} value={item.id}>{runLabel(item)}</option>)}</select></label><b>→</b><label><span>대상 Run</span><select aria-label="대상 Run" value={targetRunId} onChange={(event) => { setTargetRunId(event.target.value); setSeriesKey('') }}>{runs.filter((item) => item.id !== baselineRunId).map((item) => <option key={item.id} value={item.id}>{runLabel(item)}</option>)}</select></label></div></header>{message ? <div className="comparison-message" role="status">{message}</div> : null}
    {busy && <div className="comparison-message" role="status">선택한 Run 비교 데이터를 준비하고 있습니다.</div>}{comparison ? <><div className="comparison-kpis"><article className={comparison.summary.regression ? 'danger' : ''}><span>REGRESSION</span><strong>{comparison.summary.regression}</strong><small>PASS → FAIL</small></article><article className="positive"><span>IMPROVED</span><strong>{comparison.summary.improved}</strong><small>FAIL → PASS</small></article><article><span>COMPARABLE</span><strong>{comparison.summary.comparable}</strong><small>동일 키·단위</small></article><article className={`trust-${trust?.trust_status.toLowerCase()}`}><span>DATA TRUST</span><strong>{trust?.trust_status ?? '-'}</strong><small>{trust?.is_latest ? '최신 Run' : '과거 Run'} · {trust?.age_days ?? '-'}일</small></article></div><RunConditionComparison comparison={comparison.condition_comparison} baselineRunNo={comparison.baseline_run.run_no} targetRunNo={comparison.target_run.run_no} /><div className="comparison-grid"><ComparisonEvidenceTable comparison={comparison} selectedKey={selectedKey} onSelect={setSelectedKey} onViewSeries={viewSeries} onPrepareReview={prepareReview} />
      <article className="comparison-card series-diff" ref={chartRef} tabIndex={-1} data-testid="comparison-series-chart" data-series-key={seriesLoading ? undefined : comparison.time_series?.variable_key}><header><div><span>SYNCED TIME SERIES</span><h3>공통 시계열 비교</h3></div><select value={seriesKey || comparison.time_series?.variable_key || ''} onChange={(event) => setSeriesKey(event.target.value)} aria-label="시계열 변수 선택">{comparison.available_series.map((item) => <option key={item.variable_key} value={item.variable_key}>{item.display_name}</option>)}</select></header><div className="comparison-chart">{seriesLoading ? <div className="comparison-empty" data-testid="comparison-series-loading"><LoaderCircle className="spin" /> 선택한 시계열을 불러오고 있습니다.</div> : seriesError ? <div className="comparison-empty" role="alert" data-testid="comparison-series-error">{seriesError}</div> : comparison.time_series ? <ResponsiveContainer width="100%" height="100%"><LineChart data={comparison.time_series.points} margin={{ top: 12, right: 20, left: 8, bottom: 8 }}><CartesianGrid vertical={false} stroke="var(--color-chart-grid)" strokeDasharray="3 3"/><XAxis dataKey="time_value" name="시간" unit={comparison.time_series.points[0]?.time_unit ?? ''} tick={{ fill: 'var(--color-chart-axis)', fontSize: 10.8 }} axisLine={false}/><YAxis width={85} name={comparison.time_series.display_name} unit={comparison.time_series.unit} tick={{ fill: 'var(--color-chart-axis)', fontSize: 10.8 }} axisLine={false}/><Tooltip contentStyle={{ background: 'var(--color-surface-raised)', color: 'var(--color-text)', border: '1px solid var(--color-border-strong)', borderRadius: 8 }}/><Legend/><Line type="monotone" dataKey="baseline_value" name={`기준 Run ${comparison.baseline_run.run_no} (${comparison.time_series.unit})`} stroke="var(--color-chart-series-1)" dot={false} strokeWidth={2}/><Line type="monotone" dataKey="target_value" name={`대상 Run ${comparison.target_run.run_no} (${comparison.time_series.unit})`} stroke="var(--color-chart-series-target)" dot={false} strokeWidth={2}/></LineChart></ResponsiveContainer> : <div className="comparison-empty">공통 시계열 변수가 없습니다.</div>}</div></article>
      {trust ? <aside className="trust-card"><header><div><span>DATA TRUST</span><h3>대상 Run 신뢰도</h3></div><b className={`trust-${trust.trust_status.toLowerCase()}`}>{trust.trust_status}</b></header><div className="trust-source"><span>출처</span><strong>{trust.metadata?.source_name ?? trust.import_job?.source_folder ?? '추적 정보 없음'}</strong><code>{trust.metadata?.source_checksum ? trust.metadata.source_checksum.slice(0, 16) : 'NO CHECKSUM'}</code><small>{trust.metadata?.parser_version ?? '-'}{trust.metadata?.schema_id ? ` · ${trust.metadata.schema_id} v${trust.metadata.schema_version}` : ''}</small></div><div className="trust-counts"><span>정량 <b>{trust.counts.scalar}</b></span><span>시계열 <b>{trust.counts.time_series}</b></span><span>커브 <b>{trust.counts.curve}</b></span><span>미디어 <b>{trust.counts.media}</b></span></div><div className="trust-checks">{trust.checks.map((check) => <div key={check.code}><i className={check.status.toLowerCase()}>{check.status === 'PASS' ? <Check /> : <AlertTriangle />}</i><span><strong>{check.label}</strong><small>{check.detail}</small></span></div>)}</div></aside> : null}</div>
      <article className="review-card"><header><div><span>PINNED REVIEW</span><h3>결과 북마크·검토 의견</h3><p>관찰과 확인할 근거를 기록하며, 원인은 자동으로 추론하지 않습니다.</p></div><strong>{reviews.filter((item) => item.review_status !== 'RESOLVED').length} OPEN</strong></header><div className="review-layout"><form onSubmit={submitReview}><label><span>제목</span><input aria-label="제목" value={form.title} onChange={(event) => setReviewForm({ ...form, title: event.target.value })} placeholder="예: 하단 엣지 회귀 원인 확인" required /></label><label><span>결과 변수</span><select aria-label="결과 변수" value={form.variableKey} onChange={(event) => setReviewForm({ ...form, variableKey: event.target.value })}><option value="">Run 전체</option>{comparison.scalar_comparison.filter((item) => targetVariableKeys.has(item.variable_key)).map((item) => <option key={item.variable_key} value={item.variable_key}>{item.display_name}</option>)}</select></label><div className="review-context"><label><span>시점</span><input type="number" step="any" value={form.timeValue} onChange={(event) => setReviewForm({ ...form, timeValue: event.target.value })} placeholder="선택 사항" /></label><label><span>엔티티</span><select value={form.entityType} onChange={(event) => setReviewForm({ ...form, entityType: event.target.value as ReviewForm['entityType'] })}><option value="">없음</option><option value="NODE">NODE</option><option value="ELEMENT">ELEMENT</option></select></label><label><span>ID</span><input value={form.entityId} onChange={(event) => setReviewForm({ ...form, entityId: event.target.value })} disabled={!form.entityType} /></label></div><label><span>검토 의견</span><textarea aria-label="검토 의견" ref={reviewFieldRef} value={form.body} onChange={(event) => setReviewForm({ ...form, body: event.target.value })} placeholder="관찰 사실 / 원인 가설 / 추가 확인 / 설계 변경 방향을 구분해 기록하세요." required /></label><button className="primary-button" type="submit" disabled={saving}>{saving ? <LoaderCircle className="spin" /> : <Plus />}{saving ? '저장 중…' : '북마크 저장'}</button></form><div className="review-list">{reviews.length ? reviews.map((item) => <article key={item.id}><header><span className={`review-status ${item.review_status.toLowerCase()}`}>{item.review_status}</span><small>{new Date(item.updated_at).toLocaleString('ko-KR')}</small></header><strong>{item.title}</strong><p>{item.body}</p><div><code>{item.variable_key ?? 'RUN'}</code>{item.time_value != null ? <span>t={item.time_value}</span> : null}{item.entity_type ? <span>{item.entity_type} {item.entity_id}</span> : null}</div><footer><span>{item.created_by}</span><select value={item.review_status} onChange={(event) => void updateReviewStatus(item, event.target.value as ReviewItem['review_status'])}><option value="OPEN">OPEN</option><option value="IN_REVIEW">IN REVIEW</option><option value="RESOLVED">RESOLVED</option></select></footer></article>) : <div className="comparison-empty">아직 저장된 검토 의견이 없습니다.</div>}</div></div></article></> : null}
  </section>
}
