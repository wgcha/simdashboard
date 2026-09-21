import { useEffect, useMemo, useRef, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { AlertTriangle, Expand, Image as ImageIcon, Layers3, Pause, Play, RotateCcw } from 'lucide-react'
import { simulationDashboardApi, type DashboardAsset, type DashboardCatalog, type DashboardChoice, type DashboardComparisonMember, type DashboardDistribution, type DashboardEdgePeak, type DashboardMember, type DashboardScene, type DashboardSceneDetail, type DashboardValue, type UsageDashboard } from '../../shared/api/simulationDashboard'
import { Button } from '../../shared/components/Button'
import { Select } from '../../shared/components/Select'
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from '../../shared/components/Table'
import './SimulationDashboard.css'
import { SimulationLocationMap } from './SimulationLocationMap'
import { SimulationResultGraph } from './SimulationResultGraph'

type Props = { projectId: string; requestId: string; canManageFolders?: boolean }
type Tab = 'usage' | 'distribution'
const EDGES = ['TOP', 'BOTTOM', 'LEFT', 'RIGHT'] as const
const ROLES = ['CELL', 'CUSHION', 'BOX'] as const
const COLORS = ['var(--color-chart-series-1)', 'var(--color-chart-series-2)', 'var(--color-chart-series-3)', 'var(--color-chart-series-4)']

function errorText(reason: unknown) { return reason instanceof Error ? reason.message : '대시보드 결과를 불러오지 못했습니다.' }
function assetUrl(asset: DashboardAsset) { return asset.url || simulationDashboardApi.assetUrl(asset.asset_id) }
function valueText(value?: DashboardValue | null) { return value?.value == null ? '값 없음' : `${value.value}${value.unit ? ` ${value.unit}` : ' · 단위 미확인'}` }
function missingText(status?: string | null, reason?: string | null) { return reason || status || '자료 없음' }
function SelectField({ label, value, choices, onChange, disabled = false }: { label: string; value: string; choices: DashboardChoice[]; onChange: (value: string) => void; disabled?: boolean }) {
  return <label className="simulation-dashboard__select"><span>{label}</span><Select controlSize="sm" value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}><option value="">선택</option>{Array.from(new Map(choices.map((choice) => [choice.id, choice])).values()).map((choice) => <option key={choice.id} value={choice.id} disabled={choice.disabled} title={choice.reason ?? undefined}>{choice.label}</option>)}</Select></label>
}
function CompactChoice({ label, value, choices, onChange, disabled = false }: { label: string; value: string; choices: DashboardChoice[]; onChange: (value: string) => void; disabled?: boolean }) {
  const unique = Array.from(new Map(choices.map((choice) => [choice.id, choice])).values())
  if (unique.length === 1) return <div className="simulation-dashboard__choice"><span>{label}</span><b title={unique[0].label}>{unique[0].label}</b></div>
  return <SelectField label={label} value={value} choices={unique} onChange={onChange} disabled={disabled} />
}
function initialParam(name: string) { return typeof window === 'undefined' ? '' : new URLSearchParams(window.location.search).get(name) ?? '' }

export function SimulationDashboard({ projectId, requestId, canManageFolders = false }: Props) {
  const [tab, setTab] = useState<Tab>(() => initialParam('result_environment') === 'DISTRIBUTION' ? 'distribution' : 'usage')
  const [catalog, setCatalog] = useState<DashboardCatalog | null>(null)
  const [catalogError, setCatalogError] = useState('')
  const [caseId, setCaseId] = useState(() => initialParam('case'))
  const [captureId, setCaptureId] = useState(() => initialParam('capture'))
  const [loadCaseId, setLoadCaseId] = useState(() => initialParam('case_load'))
  const [runId, setRunId] = useState(() => initialParam('case_run'))
  const [optionId, setOptionId] = useState(() => initialParam('case_option'))
  const [mode, setMode] = useState('')
  const [componentId, setComponentId] = useState('')
  const [basis, setBasis] = useState<'' | 'REPORTED_SUMMARY' | 'DETAIL'>('')
  const [referenceCaseId, setReferenceCaseId] = useState('')
  const [referenceCaptureId, setReferenceCaptureId] = useState('')
  const [catalogRevision, setCatalogRevision] = useState(0)
  const latestCatalogKey = useRef('')
  // Treat URL-derived selection as the initial scope. Only a later request or
  // environment change clears it; otherwise a shared deep link would be
  // erased before its first catalog response arrives.
  const catalogScope = useRef(`${requestId}:${tab}`)

  useEffect(() => {
    const controller = new AbortController(); const key = `${requestId}:${tab}`; latestCatalogKey.current = key
    if (catalogScope.current !== key) {
      catalogScope.current = key; setCatalog(null); setCaseId(''); setCaptureId(''); setLoadCaseId(''); setRunId(''); setOptionId(''); setMode(''); setComponentId(''); setBasis('')
    }
    setCatalogError('')
    simulationDashboardApi.catalog(requestId, tab === 'usage' ? 'USAGE' : 'DISTRIBUTION', controller.signal).then((next) => {
      if (!controller.signal.aborted && latestCatalogKey.current === key) setCatalog(next)
    }).catch((reason) => { if (!controller.signal.aborted) setCatalogError(errorText(reason)) })
    return () => controller.abort()
  }, [catalogRevision, requestId, tab])

  const options = useMemo(() => {
    if (!catalog || tab !== 'distribution') return []
    const explicit = catalog.run_options ?? []
    if (explicit.length) return explicit.filter((item) => item.execution_run_id === runId && item.capture_id === captureId)
    return catalog.modes.filter((item) => item.execution_run_id === runId && item.capture_id === captureId).map((item) => ({ ...item, run_option_id: item.id, option_status: item.id === 'UNKNOWN' ? 'UNRESOLVED' as const : 'PRESENT' as const }))
  }, [captureId, catalog, runId, tab])
  useEffect(() => {
    if (!catalog) return
    const choose = (current: string, choices: DashboardChoice[], setter: (value: string) => void) => {
      const unique = Array.from(new Map(choices.map((item) => [item.id, item])).values())
      if (unique.some((item) => item.id === current)) return
      setter(unique.length === 1 ? unique[0].id : '')
    }
    choose(caseId, catalog.cases, setCaseId)
    if (caseId) {
      const captures = Array.from(new Map(catalog.captures.filter((item) => item.case_id === caseId).map((item) => [item.id, item])).values())
      if (!captures.some((item) => item.id === captureId)) setCaptureId(captures[0]?.id ?? '')
    }
    if (tab === 'distribution' && captureId) choose(loadCaseId, catalog.load_cases.filter((item) => item.case_id === caseId && item.capture_id === captureId), setLoadCaseId)
    if (tab === 'distribution' && loadCaseId) choose(runId, catalog.execution_runs.filter((item) => item.case_id === caseId && item.load_case_id === loadCaseId && item.capture_id === captureId), setRunId)
    if (tab === 'distribution' && runId) choose(optionId, options, setOptionId)
    const selectedOption = options.find((item) => item.id === optionId)
    if (selectedOption && mode !== selectedOption.mode) setMode(selectedOption.mode ?? selectedOption.id)
    if (tab === 'distribution' && mode) choose(componentId, catalog.components.filter((item) => item.execution_run_id === runId && item.mode === mode && item.capture_id === captureId && (!item.run_option_id || item.run_option_id === optionId)), setComponentId)
    if (tab === 'distribution') choose(basis, catalog.bases, (value) => setBasis(value as typeof basis))
  }, [basis, captureId, caseId, catalog, componentId, loadCaseId, mode, optionId, options, runId, tab])
  useEffect(() => {
    const url = new URL(window.location.href)
    const values: Record<string, string> = { result_environment: tab === 'usage' ? 'USAGE' : 'DISTRIBUTION', case: caseId, capture: captureId, case_load: loadCaseId, case_run: runId, case_option: optionId }
    Object.entries(values).forEach(([key, value]) => value ? url.searchParams.set(key, value) : url.searchParams.delete(key))
    window.history.replaceState(window.history.state, '', url)
  }, [captureId, caseId, loadCaseId, optionId, runId, tab])
  const folderHref = `/workspace/catalog/schemas?${new URLSearchParams({ project: projectId, request: requestId, result_environment: tab === 'usage' ? 'USAGE' : 'DISTRIBUTION' }).toString()}`

  const controls = catalog ? <div className="simulation-dashboard__controls">
    <CompactChoice label="해석 Case" value={caseId} choices={catalog.cases} onChange={(value) => { setCaseId(value); setCaptureId(''); setLoadCaseId(''); setRunId(''); setOptionId(''); setMode(''); setComponentId(''); setReferenceCaseId(''); setReferenceCaptureId('') }} />
    <div className="simulation-dashboard__capture-pin"><span>수집 버전</span><b>{catalog.captures.find((item) => item.id === captureId)?.label ?? '선택 필요'}</b></div>
    {tab === 'distribution' ? <>
      <CompactChoice label="하중경우" value={loadCaseId} choices={catalog.load_cases.filter((item) => item.case_id === caseId && item.capture_id === captureId)} disabled={!captureId} onChange={(value) => { setLoadCaseId(value); setRunId(''); setOptionId(''); setMode(''); setComponentId('') }} />
      <CompactChoice label="Run Case" value={runId} choices={catalog.execution_runs.filter((item) => item.case_id === caseId && item.load_case_id === loadCaseId && item.capture_id === captureId)} disabled={!loadCaseId} onChange={(value) => { setRunId(value); setOptionId(''); setMode(''); setComponentId('') }} />
      <CompactChoice label="Run Option" value={optionId} choices={options} disabled={!runId} onChange={(value) => { const choice = options.find((item) => item.id === value); setOptionId(value); setMode(choice?.mode ?? ''); setComponentId('') }} />
      <CompactChoice label="Component" value={componentId} choices={catalog.components.filter((item) => (item.execution_run_id === runId || item.run_id === runId) && item.mode === mode && item.capture_id === captureId && (!item.run_option_id || item.run_option_id === optionId))} disabled={!mode} onChange={setComponentId} />
      <CompactChoice label="Basis" value={basis} choices={catalog.bases} onChange={(value) => setBasis(value === 'DETAIL' ? 'DETAIL' : value === 'REPORTED_SUMMARY' ? 'REPORTED_SUMMARY' : '')} />
    </> : null}
    <details className="simulation-dashboard__history"><summary>수집 이력{tab === 'usage' ? ' · 비교' : ''}</summary><SelectField label="수집 버전" value={captureId} choices={catalog.captures.filter((item) => item.case_id === caseId)} disabled={!caseId} onChange={(value) => setCaptureId(value)} />{tab === 'usage' ? <><SelectField label="Reference Case" value={referenceCaseId} choices={catalog.cases.filter((item) => item.id !== caseId)} onChange={(value) => { setReferenceCaseId(value); setReferenceCaptureId('') }} /><SelectField label="Reference Capture" value={referenceCaptureId} choices={catalog.captures.filter((item) => item.case_id === referenceCaseId)} disabled={!referenceCaseId} onChange={setReferenceCaptureId} /></> : null}</details>
  </div> : null

  return <section className="simulation-dashboard" data-ui-density="v1" aria-label="SPDM 해석 결과 대시보드">
    <header className="simulation-dashboard__head"><div><span>RESULT EXPLORER</span><h2>해석 결과 대시보드</h2><p>표시값과 자산은 선택한 Case·Run·capture 문맥의 서버 결과만 사용합니다.</p></div><nav aria-label="결과 환경"><Button size="sm" className={tab === 'usage' ? 'active' : ''} onClick={() => setTab('usage')}>사용환경</Button><Button size="sm" className={tab === 'distribution' ? 'active' : ''} onClick={() => setTab('distribution')}>유통환경</Button></nav></header>
    {catalogError ? <State message={catalogError} error /> : <>{controls}{catalog && !catalog.cases.length ? <State message="등록된 해석 Case가 없습니다. 폴더 연결·규칙에서 Case와 결과를 등록하세요." /> : null}</>}
    <div className="simulation-dashboard__source-link"><span>새 결과는 폴더 연결에서 등록하고 읽습니다.</span>{canManageFolders ? <a href={folderHref}>폴더 연결·규칙 열기</a> : null}<Button size="sm" onClick={() => setCatalogRevision((value) => value + 1)}>결과 새로고침</Button></div>
    {tab === 'usage' ? <UsageArea key={`${caseId}:${captureId}:${referenceCaseId}:${referenceCaptureId}`} caseId={caseId} captureId={captureId} referenceCaseId={referenceCaseId} referenceCaptureId={referenceCaptureId} /> : <DistributionArea key={`${projectId}:${requestId}`} projectId={projectId} requestId={requestId} caseId={caseId} loadCaseId={loadCaseId} captureId={captureId} runId={runId} optionId={optionId} mode={mode} componentId={componentId} basis={basis} />}
  </section>
}

function State({ message, error = false }: { message: string; error?: boolean }) { return <div className={`simulation-dashboard__state ${error ? 'error' : ''}`} role={error ? 'alert' : 'status'}>{error ? <AlertTriangle /> : <Layers3 />}<span>{message}</span></div> }

function UsageArea({ caseId, captureId, referenceCaseId, referenceCaptureId }: { caseId: string; captureId: string; referenceCaseId: string; referenceCaptureId: string }) {
  const [data, setData] = useState<UsageDashboard | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    setData(null); setError('')
    if (!caseId || !captureId || Boolean(referenceCaseId) !== Boolean(referenceCaptureId)) return
    const controller = new AbortController()
    simulationDashboardApi.usage(caseId, captureId, referenceCaseId || undefined, referenceCaptureId || undefined, controller.signal)
      .then((next) => { if (!controller.signal.aborted) setData(next) })
      .catch((reason) => { if (!controller.signal.aborted) setError(errorText(reason)) })
    return () => controller.abort()
  }, [caseId, captureId, referenceCaseId, referenceCaptureId])
  if (!caseId || !captureId) return <State message="Simulation Case와 수집 버전을 선택하세요." />
  if (Boolean(referenceCaseId) !== Boolean(referenceCaptureId)) return <State message="Reference 수집 버전을 선택하세요." />
  if (error) return <State message={error} error />
  if (!data) return <State message="사용환경 평가를 불러오는 중입니다." />
  const media = data.evaluations.flatMap((evaluation) => evaluation.media ?? [])
  type Metric = 'value' | 'verdict'
  const directions = ['common', 'front', 'rear'] as const
  const keys: Record<string, string> = { Settle: 'Set Tilt Angle @ Settle (deg)', Wobble: 'Wobble Disp. (mm)', Horizontal_Force_Angle: 'Set Tilt Angle Difference (deg)', Slope_Angle: 'Slope Angle (deg)', Slope_Angle_360: 'OK/NG' }
  const metricStatus = (value: DashboardValue, metric: Metric) => value[`${metric}_status`] ?? (value[metric] != null ? 'READY' : value.status ?? 'MISSING')
  const stateLabel = (status: string) => status === 'READY' ? '확인' : ['MISSING', 'MISSING_SOURCE', 'MISSING_NUMERIC', 'ABSENT', 'NO_DATA', 'EXCLUDED'].includes(status) ? '자료 없음' : status === 'NOT_APPLICABLE' ? '해당 없음' : '검수 필요'
  const cell = (value: DashboardValue | null | undefined, metric: Metric) => {
    if (!value) return <span className="usage-missing">해당 없음</span>
    const state = metricStatus(value, metric)
    const label = stateLabel(state)
    const text = state === 'READY' && value[metric] != null ? metric === 'verdict' ? value.verdict : valueText(value) : '—'
    return <span className={`usage-cell usage-cell-${label === '검수 필요' ? 'review' : state === 'READY' ? 'ready' : 'missing'}`}><strong>{text}</strong><small>{label}</small></span>
  }
  const fieldKey = (evaluation: UsageDashboard['evaluations'][number], metric: Metric) => directions.map((direction) => evaluation[direction]?.[`${metric}_key`]).find(Boolean) || (metric === 'verdict' ? 'OK/NG' : keys[evaluation.id])
  type EvaluationRow = { evaluation: UsageDashboard['evaluations'][number]; label: string; key?: string; metric: Metric }
  const evaluationRows: EvaluationRow[] = data.evaluations.flatMap((evaluation): EvaluationRow[] => {
    const metric = evaluation.id === 'Slope_Angle_360' ? 'verdict' : 'value'
    const row: EvaluationRow = { evaluation, label: keys[evaluation.id] ? evaluation.id : evaluation.name, key: fieldKey(evaluation, metric), metric }
    return evaluation.id === 'Slope_Angle' ? [row, { evaluation, label: '', key: fieldKey(evaluation, 'verdict'), metric: 'verdict' }] : [row]
  })
  return <div className="simulation-dashboard__usage" data-testid="usage-dashboard">
    <div className="simulation-dashboard__usage-media">{media.length ? media.map((asset) => <div key={asset.asset_id}><h3>{asset.title}</h3><Media asset={asset} /></div>) : <State message="연결된 미디어 없음" />}</div>
    <div className="simulation-dashboard__usage-table"><header><h3>다섯 평가 종합</h3><small>{data.status === 'READY' ? '확인' : '일부 항목 확인 필요'}</small></header><Table><TableHead><TableRow><TableHeaderCell>평가 / 원문 키</TableHeaderCell><TableHeaderCell>공통</TableHeaderCell><TableHeaderCell>전방</TableHeaderCell><TableHeaderCell>후방</TableHeaderCell><TableHeaderCell>Reference</TableHeaderCell></TableRow></TableHead><TableBody>{evaluationRows.map((row) => <TableRow key={`${row.evaluation.id}:${row.metric}`}><TableHeaderCell><strong>{row.label}</strong>{row.key ? <small className="usage-source-key" title={row.key}>{row.key}</small> : null}</TableHeaderCell><TableCell>{cell(row.evaluation.common, row.metric)}</TableCell><TableCell>{cell(row.evaluation.front, row.metric)}</TableCell><TableCell>{cell(row.evaluation.rear, row.metric)}</TableCell><TableCell>{row.evaluation.reference ? <>{row.evaluation.reference.reason || directions.map((direction) => { const value = row.evaluation.reference?.[direction]; return value ? <div key={direction}>{direction === 'common' ? '공통' : direction === 'front' ? '전방' : '후방'}: {cell(value, row.metric)}</div> : null })}</> : '미선택'}</TableCell></TableRow>)}</TableBody></Table></div>
    <div className="simulation-dashboard__usage-values">{data.evaluations.filter((evaluation) => evaluation.id !== 'Slope_Angle_360').map((evaluation) => {
      const points = directions.filter((key) => evaluation[key] != null || evaluation.reference?.[key] != null).map((key) => ({ direction: key === 'common' ? '공통' : key === 'front' ? '전방' : '후방', current: evaluation[key] && metricStatus(evaluation[key]!, 'value') === 'READY' ? evaluation[key]?.value : null, reference: evaluation.reference?.[key] && metricStatus(evaluation.reference[key]!, 'value') === 'READY' ? evaluation.reference[key]?.value : null }))
      const key = fieldKey(evaluation, 'value')
      return <article key={evaluation.id}><h3>{keys[evaluation.id] ? evaluation.id : evaluation.name}</h3>{key ? <small className="usage-source-key" title={key}>{key}</small> : null}<small>{evaluation.common?.unit || evaluation.front?.unit || evaluation.rear?.unit}</small><div style={{height: 150}}><ResponsiveContainer width="100%" height="100%"><BarChart data={points}><XAxis dataKey="direction" /><YAxis /><Tooltip /><Bar dataKey="current" name="현재 Case" fill="var(--color-chart-series-1)" /><Bar dataKey="reference" name="Reference" fill="var(--color-chart-series-2)" /></BarChart></ResponsiveContainer></div></article>
    })}</div>
  </div>
}

function DistributionArea({ projectId, requestId, caseId, loadCaseId, captureId, runId, optionId, mode, componentId, basis }: { projectId: string; requestId: string; caseId: string; loadCaseId: string; captureId: string; runId: string; optionId: string; mode: string; componentId: string; basis: '' | 'REPORTED_SUMMARY' | 'DETAIL' }) {
  const [edges, setEdges] = useState<string[]>([...EDGES]); const [lines, setLines] = useState<string[]>(['1', '2', '3', '4']); const [data, setData] = useState<DashboardDistribution | null>(null); const [error, setError] = useState(''); const [sceneId, setSceneId] = useState(''); const [memberId, setMemberId] = useState(''); const [section, setSection] = useState<'summary' | 'edges' | 'contours' | 'behavior'>('summary'); const [comparison, setComparison] = useState<DashboardComparisonMember[]>([]); const latestKey = useRef('')
  useEffect(() => { setComparison([]); setData(null); setSceneId(''); setMemberId('') }, [projectId, requestId])
  const contextKey = `${projectId}:${requestId}:${caseId}:${loadCaseId}:${runId}:${optionId}:${mode}:${captureId}:${componentId}:${basis}:${edges.join(',')}:${lines.join(',')}:${comparison.map((item) => `${item.simulation_case_id}/${item.load_case_id}/${item.execution_run_id}/${item.run_option_id}/${item.capture_id}/${item.mode}/${item.component_id}/${item.basis}`).join('|')}`
  useEffect(() => {
    if (!runId || !captureId || !mode || !componentId || !basis) { setData(null); return }
    const controller = new AbortController(); latestKey.current = contextKey; setError(''); setData(null); setSceneId(''); setMemberId('')
    const current = { simulation_case_id: caseId, load_case_id: loadCaseId, execution_run_id: runId, run_option_id: optionId, capture_id: captureId, mode, component_id: componentId, basis }
    const members = comparison.length ? [...comparison, ...(comparison.some((item) => item.simulation_case_id === caseId) ? [] : [current])] : []
    const request = members.length > 1 ? simulationDashboardApi.comparison(members, edges.join(','), lines.join(','), controller.signal) : simulationDashboardApi.distribution(runId, { capture_id: captureId, run_option_id: optionId, mode, component_id: componentId, basis, edge_keys: edges.join(','), line_indices: lines.join(',') }, controller.signal)
    request.then((next) => { if (!controller.signal.aborted && latestKey.current === contextKey) setData(next) }).catch((reason) => { if (!controller.signal.aborted && latestKey.current === contextKey) setError(errorText(reason)) })
    return () => controller.abort()
  }, [basis, captureId, caseId, comparison, componentId, contextKey, edges, lines, loadCaseId, mode, optionId, projectId, requestId, runId])
  if (!runId || !captureId || !mode || !componentId || !basis) return <State message="Run, mode, component, basis와 capture를 모두 선택하세요." />
  if (error) return <State message={error} error />
  if (!data) return <State message="유통환경 요약을 불러오는 중입니다." />
  const choose = (scene: string, member: string) => { setSceneId(scene); setMemberId(member) }
  const addCurrent = () => { const next = { simulation_case_id: caseId, load_case_id: loadCaseId, execution_run_id: runId, run_option_id: optionId, capture_id: captureId, mode, component_id: componentId, basis: basis as DashboardComparisonMember['basis'] }; if (!comparison.some((item) => item.simulation_case_id === caseId) && comparison.length < 8) setComparison([...comparison, next]) }
  return <div className="simulation-dashboard__distribution" data-testid="distribution-dashboard"><div className="simulation-dashboard__toolbar"><EdgePicker edges={edges} onChange={setEdges} /><LinePicker lines={lines} onChange={setLines} /><button type="button" onClick={addCurrent} disabled={comparison.some((item) => item.simulation_case_id === caseId) || comparison.length >= 8}>현재 Case 비교에 추가</button>{comparison.map((item) => <button type="button" key={item.simulation_case_id} onClick={() => setComparison(comparison.filter((candidate) => candidate.simulation_case_id !== item.simulation_case_id))}>Case 제거 {item.simulation_case_id}</button>)}<nav aria-label="유통환경 상세"><button className={section === 'summary' ? 'active' : ''} onClick={() => setSection('summary')}>결과 요약</button><button className={section === 'edges' ? 'active' : ''} onClick={() => setSection('edges')}>엣지별 수준</button><button className={section === 'contours' ? 'active' : ''} onClick={() => setSection('contours')}>컨투어</button><button className={section === 'behavior' ? 'active' : ''} onClick={() => setSection('behavior')}>거동</button></nav></div>{section === 'summary' ? <Summary data={data} sceneId={sceneId} memberId={memberId} selectedEdges={edges} onSelect={choose} /> : null}{section === 'edges' ? <EdgePanels data={data} onSelect={choose} /> : null}{section === 'contours' ? <ContourMatrix data={data} onSelect={choose} /> : null}{section === 'behavior' ? <BehaviorMatrix data={data} sceneId={sceneId} onSelect={choose} /> : null}<SimulationLocationMap data={data} onSelect={choose} /><SceneDetail sceneId={sceneId} member={data.members.find((member) => member.id === memberId)} lineIndices={lines.join(',')} />{data.quality_issues.length ? <Issues issues={data.quality_issues} /> : null}</div>
}

function EdgePicker({ edges, onChange }: { edges: string[]; onChange: (next: string[]) => void }) { const toggle = (edge: string) => onChange(edges.includes(edge) ? edges.filter((item) => item !== edge) : [...edges, edge]); return <fieldset className="simulation-dashboard__picker"><legend>표시 엣지</legend>{EDGES.map((edge) => <label key={edge}><input type="checkbox" checked={edges.includes(edge)} onChange={() => toggle(edge)} />{edge}</label>)}</fieldset> }
function LinePicker({ lines, onChange }: { lines: string[]; onChange: (next: string[]) => void }) { const toggle = (line: string) => onChange(lines.includes(line) ? lines.filter((item) => item !== line) : [...lines, line]); return <fieldset className="simulation-dashboard__picker"><legend>네 라인</legend>{['1', '2', '3', '4'].map((line) => <label key={line}><input type="checkbox" checked={lines.includes(line)} onChange={() => toggle(line)} />L{line}</label>)}</fieldset> }
function memberColor(member: DashboardMember, index: number) { return member.color || COLORS[index % COLORS.length] }
function sceneLabel(scene: DashboardScene) { return scene.scene_sequence_number == null ? scene.label : String(scene.scene_sequence_number) }
function Summary({ data, sceneId, memberId, selectedEdges, onSelect }: { data: DashboardDistribution; sceneId: string; memberId: string; selectedEdges: string[]; onSelect: (sceneId: string, memberId: string) => void }) {
  const selectedMember = data.members.find((member) => member.id === memberId) ?? data.members[0]
  const selectedScene = data.scenes.find((scene) => scene.id === sceneId) ?? data.scenes[0]
  const peaks = selectedMember && selectedScene ? data.edge_peaks.filter((peak) => peak.member_id === selectedMember.id && peak.scene_id === selectedScene.id) : []
  const noEdgeSelection = data.status === 'NO_SELECTION' || data.series.some((point) => point.status === 'NO_SELECTION')
  const pointByKey = useMemo(() => new Map(data.series.map((point) => [`${point.scene_id}:${point.member_id}`, point])), [data.series])
  const rows = data.scenes.flatMap((scene) => data.members.map((member, index) => {
    const point = pointByKey.get(`${scene.id}:${member.id}`)
    return { scene_id: scene.id, member_id: member.id, scene: sceneLabel(scene), scene_sequence_number: scene.scene_sequence_number, order_status: scene.order_status, member: member.label, color: memberColor(member, index), value: point?.selected_edge_envelope ?? null, unit: point?.unit ?? null }
  }))
  return <div className="simulation-dashboard__summary"><section><header><h3>Open Cell 엣지 맵</h3><small>{selectedScene?.label ?? 'Scene 선택'}</small></header><EdgeMap peaks={peaks} selectedEdges={selectedEdges} /></section><section><header><h3>Scene별 엣지 최대응력</h3><small>선택 엣지 envelope는 서버가 반환한 값입니다.</small></header>{noEdgeSelection ? <State message="선택 없음: 표시 엣지를 하나 이상 선택하세요." /> : <SimulationResultGraph chartId="summary" title="Scene별 엣지 최대응력" points={rows} members={data.members} onSelect={onSelect} valueName="선택 엣지 envelope" />}</section></div>
}
function EdgeMap({ peaks, selectedEdges }: { peaks: DashboardEdgePeak[]; selectedEdges: string[] }) { const item = (edge: string) => peaks.find((peak) => peak.edge === edge); return <div className="simulation-dashboard__edge-map">{EDGES.map((edge) => { const peak = item(edge); return <div key={edge} className={`edge edge-${edge.toLowerCase()} ${selectedEdges.includes(edge) ? 'selected' : ''} ${peak?.is_selected_maximum ? 'maximum' : ''}`}><b>{edge}</b><span>{valueText(peak)}</span><small>{peak?.completeness ?? peak?.status ?? '자료 없음'}</small></div> })}</div> }
function EdgePanels({ data, onSelect }: { data: DashboardDistribution; onSelect: (sceneId: string, memberId: string) => void }) {
  const memberIds = data.members.map((member) => member.id)
  const memberKey = memberIds.join('|')
  const [visible, setVisible] = useState<string[]>(memberIds)
  const [order, setOrder] = useState<'scene' | 'case'>('scene')
  const [size, setSize] = useState<'compact' | 'wide'>('wide')
  useEffect(() => { setVisible(memberIds) }, [memberKey])
  const members = data.members.filter((member) => visible.includes(member.id))
  const peakByKey = useMemo(() => new Map(data.edge_peaks.map((peak) => [`${peak.edge}:${peak.scene_id}:${peak.member_id}`, peak])), [data.edge_peaks])
  const rows = (edge: string) => {
    const source = order === 'scene' ? data.scenes.flatMap((scene) => members.map((member) => ({ scene, member }))) : members.flatMap((member) => data.scenes.map((scene) => ({ scene, member })))
    return source.map(({ scene, member }) => {
      const point = peakByKey.get(`${edge}:${scene.id}:${member.id}`)
      const memberIndex = data.members.findIndex((candidate) => candidate.id === member.id)
      return { scene_id: scene.id, member_id: member.id, scene: sceneLabel(scene), scene_sequence_number: scene.scene_sequence_number, order_status: scene.order_status, member: member.label, value: point?.value ?? null, unit: point?.unit ?? null, color: memberColor(member, memberIndex) }
    })
  }
  return <><SceneTable scenes={data.scenes} /><div className="simulation-dashboard__panel-controls"><fieldset><legend>Case 범례</legend><div className="simulation-dashboard__case-actions"><button type="button" onClick={() => setVisible(memberIds)}>전체 선택</button><button type="button" onClick={() => setVisible([])}>전체 해제</button></div>{data.members.map((member, index) => <label key={member.id}><input type="checkbox" checked={visible.includes(member.id)} onChange={() => setVisible((current) => current.includes(member.id) ? current.filter((id) => id !== member.id) : [...current, member.id])} /><i style={{ background: memberColor(member, index) }} />{member.label}</label>)}</fieldset><label>표시 순서<select value={order} onChange={(event) => setOrder(event.target.value as 'scene' | 'case')}><option value="scene">Scene별</option><option value="case">Case별</option></select></label><label>패널 크기<select value={size} onChange={(event) => setSize(event.target.value as 'compact' | 'wide')}><option value="compact">작게</option><option value="wide">크게</option></select></label></div>{members.length ? <div className={`simulation-dashboard__edge-panels ${size}`}>{EDGES.map((edge) => <section key={edge}><header><h3>{edge}</h3><small>서버가 제공한 엣지별 최대값</small></header><SimulationResultGraph chartId={`edge-${edge}`} title={`${edge} 엣지 수준`} points={rows(edge)} members={members} onSelect={onSelect} valueName={edge} /></section>)}</div> : <State message="표시할 Simulation Case가 없습니다. 전체 선택 또는 Case를 선택하세요." />}</>
}
function SceneTable({ scenes }: { scenes: DashboardScene[] }) { return <div className="simulation-dashboard__scene-table"><span>Scene 설명</span>{scenes.map((scene) => <div key={scene.id}><b>{scene.scene_sequence_number ?? '순번 미확인'}</b><span>{scene.label}</span><small>{[scene.contact_code, scene.repetition, scene.order_status].filter(Boolean).join(' · ') || '설명 미확인'}</small></div>)}</div> }
function ContourMatrix({ data, onSelect }: { data: DashboardDistribution; onSelect: (sceneId: string, memberId: string) => void }) {
  const [transpose, setTranspose] = useState(false)
  const rows = transpose ? data.members : data.scenes
  const columns = transpose ? data.scenes : data.members
  const cellByContext = useMemo(() => new Map(data.contours.map((cell) => [`${cell.scene_id}:${cell.member_id}`, cell])), [data.contours])
  return <section className="simulation-dashboard__matrix"><header><div><h3>Scene별 Cell Tmax 마지막 Frame Contour</h3><small>FINAL_FRAME 근거가 없는 자산은 프레임 미확인으로 표시합니다. 공통 Scale bar 정합성은 미확인으로 셀별 원본 범례를 유지합니다.</small></div><button type="button" onClick={() => setTranspose(!transpose)}><RotateCcw /> 행/열 전치</button></header><div className="simulation-dashboard__matrix-scroll"><div className="simulation-dashboard__matrix-grid" style={{ gridTemplateColumns: `minmax(220px, .7fr) repeat(${columns.length}, minmax(190px, 1fr))` }}><div className="simulation-dashboard__matrix-header">{transpose ? 'Case' : 'Scene / 자세·충돌'}</div>{columns.map((column) => <div className="simulation-dashboard__matrix-header" key={column.id} title={'design_description' in column ? [column.label, column.design_description].filter(Boolean).join(' · ') : column.label}>{column.label}{'design_description' in column && column.design_description ? <small>{column.design_description}</small> : null}</div>)}{rows.flatMap((row) => [<SceneOrCaseHeader key={`${row.id}-label`} item={row} />, ...columns.map((column) => { const scene = transpose ? column as DashboardScene : row as DashboardScene; const member = transpose ? row as DashboardMember : column as DashboardMember; const cell = cellByContext.get(`${scene.id}:${member.id}`); return <MatrixCell key={cell?.cell_id ?? `${scene.id}:${member.id}`} cellId={cell?.cell_id ?? `${scene.id}:${member.id}`} asset={cell?.asset} status={cell?.status} reason={cell?.reason} value={cell?.value} scaleStatus={cell?.scale_status} showContourMeta onClick={() => onSelect(scene.id, member.id)} /> })])}</div></div></section>
}
function SceneOrCaseHeader({ item }: { item: DashboardScene | DashboardMember }) {
  if ('scene_sequence_number' in item) return <div className="simulation-dashboard__matrix-header simulation-dashboard__scene-context" title={[item.label, item.description].filter(Boolean).join(' · ')}><b>{item.scene_sequence_number ?? '순번 미확인'} · {item.label}</b><small>{[item.description, item.contact_code, item.repetition, item.order_status].filter(Boolean).join(' · ') || '자세·충돌 정보 미확인'}</small></div>
  return <div className="simulation-dashboard__matrix-header" title={[item.label, item.design_description].filter(Boolean).join(' · ')}><b>{item.label}</b>{item.design_description ? <small>{item.design_description}</small> : null}</div>
}
function BehaviorMatrix({ data, sceneId, onSelect }: { data: DashboardDistribution; sceneId: string; onSelect: (sceneId: string, memberId: string) => void }) { const scene = data.scenes.find((item) => item.id === sceneId) ?? data.scenes[0]; if (!scene) return <State message="Scene이 없습니다." />; return <section className="simulation-dashboard__matrix"><header><div><h3>Scene별 거동</h3><small>{scene.label}</small></div></header><div className="simulation-dashboard__matrix-scroll"><div className="simulation-dashboard__matrix-grid" style={{ gridTemplateColumns: `minmax(150px,.45fr) repeat(${data.members.length}, minmax(190px, 1fr))` }}><strong>대상</strong>{data.members.map((member) => <strong key={member.id}>{member.label}</strong>)}{ROLES.flatMap((role) => [<strong key={`${role}-label`}>{role}</strong>, ...data.members.map((member) => { const cell = data.behaviors.find((candidate) => candidate.scene_id === scene.id && candidate.member_id === member.id && candidate.subject_role === role); return <MatrixCell key={cell?.cell_id ?? `${scene.id}:${member.id}:${role}`} cellId={cell?.cell_id ?? `${scene.id}:${member.id}:${role}`} asset={cell?.asset} status={cell?.status} reason={cell?.reason} onClick={() => onSelect(scene.id, member.id)} /> })])}</div></div></section> }
function scopeText(scope?: string | null) { return scope === 'EXTRACTED_SIDES_AND_CORNERS' ? '추출 측면·코너' : scope === 'EXTRACTED_SELECTED_LINES' ? '선택 라인의 추출 측면' : scope === 'SELECTED_SIDE_LINES' || scope === 'SELECTED_EDGE_LINES' ? '선택 엣지·라인' : scope ?? '집계 범위 미확인' }
function MatrixCell({ cellId, asset, status, reason, value, scaleStatus, showContourMeta = false, onClick }: { cellId: string; asset?: DashboardAsset | null; status?: string; reason?: string | null; value?: DashboardValue | null; scaleStatus?: string | null; showContourMeta?: boolean; onClick: () => void }) { return <div className="simulation-dashboard__matrix-cell" role="button" tabIndex={0} data-cell-id={cellId} onClick={onClick} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') onClick() }}>{asset ? <Media asset={asset} /> : <span>{missingText(status, reason)}</span>}{showContourMeta ? <div className="simulation-dashboard__matrix-meta"><small>값: {value ? valueText(value) : '값 없음'}</small><small>집계: {value?.basis === 'REPORTED_SUMMARY' ? '원본 요약' : value?.basis === 'DETAIL' ? '상세 추출' : '집계 범위 미확인'} · {scopeText(value?.scope)}</small><small>상태: {value?.completeness ?? value?.status ?? status ?? '미확인'}</small><small>이미지·수치 시간 정합성 미확인</small><small>Scale bar: {!scaleStatus || scaleStatus === 'UNCONFIRMED' ? '정합성 미확인' : scaleStatus}</small></div> : null}</div> }
function SceneDetail({ sceneId, member, lineIndices }: { sceneId: string; member?: DashboardMember; lineIndices: string }) { const [data, setData] = useState<DashboardSceneDetail | null>(null); const [error, setError] = useState(''); const [position, setPosition] = useState('TOP'); const latestKey = useRef('')
  useEffect(() => { if (!sceneId || !member) { setData(null); return }; const controller = new AbortController(); const key = `${sceneId}:${member.id}:${lineIndices}:${position}`; latestKey.current = key; setData(null); setError(''); simulationDashboardApi.sceneDetail(sceneId, { execution_run_id: member.execution_run_id, capture_id: member.capture_id, run_option_id: member.run_option_id, mode: member.mode, component_id: member.component_id, basis: member.basis, line_indices: lineIndices, position }, controller.signal).then((next) => { if (!controller.signal.aborted && latestKey.current === key) setData(next) }).catch((reason) => { if (!controller.signal.aborted && latestKey.current === key) setError(errorText(reason)) }); return () => controller.abort() }, [lineIndices, member, position, sceneId])
  if (!sceneId || !member) return null
  if (error) return <State message={error} error />
  if (!data) return <State message="선택 Scene 상세를 불러오는 중입니다." />
  return <section className="simulation-dashboard__detail"><header><h3>Scene 상세 · {data.scene.label}</h3><label>위치 <select value={position} onChange={(event) => setPosition(event.target.value)} aria-label="Scene 상세 위치">{['TOP', 'BOT', 'LH', 'RH'].map((item) => <option key={item}>{item}</option>)}</select></label><small>{data.context.context_key}</small></header><div className="simulation-dashboard__chart"><ResponsiveContainer width="100%" height="100%"><LineChart><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="ref_coord" type="number" /><YAxis /><Tooltip /><Legend />{[1, 2, 3, 4].map((line) => <Line key={line} data={data.line_points.filter((point) => point.line_index === line)} dataKey="value" name={`L${line}`} type="linear" connectNulls={false} stroke={COLORS[line - 1]} dot={false} />)}</LineChart></ResponsiveContainer></div><div className="simulation-dashboard__detail-assets">{data.assets.map((asset) => <Media key={asset.asset_id} asset={asset} />)}</div>{data.quality_issues.length ? <Issues issues={data.quality_issues} /> : null}</section> }
function Media({ asset }: { asset: DashboardAsset }) { const [expanded, setExpanded] = useState(false); const [playError, setPlayError] = useState(''); const video = useRef<HTMLVideoElement>(null); const dialog = useRef<HTMLDialogElement>(null); const close = () => { dialog.current?.close(); setExpanded(false) }; useEffect(() => { setExpanded(false); setPlayError(''); video.current?.pause() }, [asset.asset_id]); useEffect(() => { if (expanded && dialog.current && !dialog.current.open) dialog.current.showModal() }, [expanded]); const toggle = async () => { if (!video.current) return; try { if (video.current.paused) await video.current.play(); else video.current.pause(); setPlayError('') } catch { setPlayError('영상을 재생하지 못했습니다.') } }; const frame = asset.frame_role === 'FINAL_FRAME' ? '최종 프레임' : '프레임 미확인'; const frameMeta = [frame, asset.frame_index == null ? null : `frame ${asset.frame_index}`, asset.time_value == null ? null : `t=${asset.time_value}${asset.time_unit ? ` ${asset.time_unit}` : ' · 단위 미확인'}`, asset.provenance].filter(Boolean).join(' · '); return <div className="simulation-dashboard__media">{asset.kind === 'VIDEO' ? <video ref={video} controls src={assetUrl(asset)} aria-label={asset.title ?? '결과 영상'} /> : asset.status === 'READY' ? <img src={assetUrl(asset)} alt={asset.title ?? '결과 이미지'} /> : <span><ImageIcon />{asset.status}</span>}<small>{frameMeta}</small><div className="simulation-dashboard__media-actions">{asset.kind === 'VIDEO' ? <button type="button" aria-label="영상 재생 또는 일시정지" onClick={() => void toggle()}><Play /></button> : null}<button type="button" aria-label="자산 확대" onClick={() => setExpanded(true)}><Expand /></button></div>{playError ? <em role="alert">{playError}</em> : null}{expanded ? <dialog ref={dialog} className="simulation-dashboard__lightbox" onCancel={(event) => { event.preventDefault(); close() }} onClose={() => setExpanded(false)}><button type="button" onClick={close} aria-label="확대 보기 닫기">닫기</button>{asset.kind === 'VIDEO' ? <video controls autoPlay src={assetUrl(asset)} aria-label={asset.title ?? '확대 결과 영상'} /> : <img src={assetUrl(asset)} alt={asset.title ?? '결과 이미지 확대'} />}</dialog> : null}</div> }
const unprocessedReasons: Record<string, string> = { EXCLUDED_DIRECTORY: '제외된 디렉터리', UNSUPPORTED_EXTENSION: '미지원 확장자', UNKNOWN_LOAD_CASE_DIRECTORY: '알 수 없는 하중 경우 경로', UNEXPECTED_RESULT_PATH_DEPTH: '지원하지 않는 폴더 깊이', INCOMPLETE_RESULT_PATH: '결과 경로 불완전', UNSUPPORTED_DISTRIBUTION_FORMAT: '미지원 유통환경 형식', UNRECOGNIZED_RESULT_FILE: '인식하지 못한 결과 파일' }
function issueText(issue: string) { if (!issue.startsWith('UNPROCESSED_FILE:')) return issue; const source = issue.slice('UNPROCESSED_FILE:'.length); const marker = source.lastIndexOf(':'); if (marker < 1) return `미처리 파일: ${source}`; const path = source.slice(0, marker); const reason = source.slice(marker + 1); return `미처리 파일: ${path} · ${unprocessedReasons[reason] ?? reason}` }
function Issues({ issues }: { issues: string[] }) { return <aside className="simulation-dashboard__issues"><AlertTriangle />{issues.map((issue) => <span key={issue}>{issueText(issue)}</span>)}</aside> }
