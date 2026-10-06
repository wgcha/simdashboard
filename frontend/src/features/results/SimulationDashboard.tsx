import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Link } from 'react-router-dom'
import { AlertTriangle, Expand, Image as ImageIcon, Info, Layers3, Play, RotateCcw, SlidersHorizontal } from 'lucide-react'
import { simulationDashboardApi, type DashboardAsset, type DashboardCatalog, type DashboardChoice, type DashboardComparisonMember, type DashboardDistribution, type DashboardEdgePeak, type DashboardEnvironment, type DashboardMember, type DashboardScene, type DashboardSceneDetail, type DashboardValue, type UsageDashboard } from '../../shared/api/simulationDashboard'
import { Select } from '../../shared/components/Select'
import { HierarchyChoice, type HierarchyChoiceOption } from '../../shared/components/HierarchyChoice'
import { HierarchyPath } from '../../shared/components/HierarchyPath'
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from '../../shared/components/Table'
import './SimulationDashboard.css'
import { SimulationLocationMap } from './SimulationLocationMap'
import { SimulationResultGraph } from './SimulationResultGraph'
import { CaseFinalizationPanel } from './CaseFinalizationPanel'
import { CaseVideoGrid } from './CaseVideoGrid'
import { FolderNameWarnings, SceneNameWarningIcon, SceneNameWarningProvider } from './FolderNameWarnings'
import { useCaseHierarchyParams } from '../../shared/hooks/useCaseHierarchyParams'
import { CaseReportLauncher } from './caseReport/CaseReportLauncher'
import type { CaseReportFinalScope, CaseReportScope } from './caseReport/caseReport'
import { USAGE_DIRECTION_LABELS, USAGE_DIRECTIONS, usageCell, usageEvaluationLabel, usageEvaluationRows, usageFieldKey, usageMetricStatus, usageStatusText, type UsageMetric } from './usageEvaluations'

type Props = {
  projectId: string
  requestId: string
  canManageFolders?: boolean
  canRefreshSchema?: boolean
  refreshToken?: number
  /** `materials` opens the 소재·물성 tab (URL `resultTab=materials`). */
  activeTab?: 'case_results' | 'materials'
  /** Environments of the request's Cases (E1–E4): `null` while pending; omitted keeps the manual toggle. */
  resultEnvironments?: DashboardEnvironment[] | null
  /** Rendered at the right of the header row (folder sync status). */
  headerExtra?: ReactNode
  /** Materials tab content; it renders its own path bar into `pathTarget`. */
  renderMaterials?: (pathTarget: HTMLElement | null) => ReactNode
}
type Tab = 'usage' | 'distribution'
const SCHEMA_CONTEXT = '__folder_schema__'
const EDGES = ['TOP', 'BOTTOM', 'LEFT', 'RIGHT'] as const
const ROLES = ['CELL', 'CUSHION', 'BOX'] as const
const COLORS = ['var(--color-chart-series-1)', 'var(--color-chart-series-2)', 'var(--color-chart-series-3)', 'var(--color-chart-series-4)']

function errorText(reason: unknown) { return reason instanceof Error ? reason.message : '대시보드 결과를 불러오지 못했습니다.' }
function assetUrl(asset: DashboardAsset) { return asset.url || simulationDashboardApi.assetUrl(asset.asset_id) }
function valueText(value?: DashboardValue | null) { return value?.value == null ? '값 없음' : `${value.value}${value.unit ? ` ${value.unit}` : ' · 단위 미확인'}` }
const STATUS_TEXT: Record<string, string> = { COMPLETE: '확인', READY: '확인', CONFIRMED: '확인', PRESENT: '확인', PARTIAL: '일부', UNMATCHED: '대응 없음', MISSING: '결과 없음', MISSING_SOURCE: '결과 없음', NO_DATA: '결과 없음', ABSENT: '결과 없음', UNCAPTURED: '결과 없음', NO_SELECTION: '선택 없음', UNCONFIRMED: '미확인', UNKNOWN: '미확인', INFERRED: '추정', PENDING: '처리 중', NOT_APPLICABLE: '해당 없음' }
/** Korean label for a server status code; internal codes are never shown. */
function statusText(code?: string | null) { return code ? STATUS_TEXT[code] ?? '확인 필요' : '결과 없음' }
function basisLabel(id: string) { return id === 'REPORTED_SUMMARY' ? '원본 요약' : id === 'DETAIL' ? '상세 추출값' : id }
function missingText(status?: string | null, reason?: string | null) { return reason || statusText(status) }
function InfoTip({ text }: { text: string }) { return <span className="case-info" role="img" aria-label={text} title={text}><Info aria-hidden="true" /></span> }
function SelectField({ label, value, choices, onChange, disabled = false }: { label: string; value: string; choices: DashboardChoice[]; onChange: (value: string) => void; disabled?: boolean }) {
  const unique = Array.from(new Map(choices.map((choice) => [choice.id, choice])).values())
  return <label className="simulation-dashboard__select"><span>{label}</span><Select controlSize="sm" value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>{unique.some((choice) => !choice.id) ? null : <option value="">선택</option>}{unique.map((choice) => <option key={choice.id || 'folder-schema'} value={choice.id} disabled={choice.disabled} title={choice.reason ?? undefined}>{choice.label}</option>)}</Select></label>
}
function pathChoices(choices: DashboardChoice[]): HierarchyChoiceOption[] { return choices.map((item) => ({ id: item.id, label: item.label, title: item.relative_path ?? item.label, disabled: item.disabled, reason: item.reason })) }
function currentHierarchyChoice(item: DashboardChoice, activeCaptureId: string) {
  return item.capture_id == null || (activeCaptureId !== '' && item.capture_id === activeCaptureId)
}

type ViewTab = 'summary' | 'compare' | 'video' | 'materials'
type CompareView = 'edges' | 'contours' | 'behavior'
const VIEW_TABS: Array<{ id: ViewTab; label: string }> = [{ id: 'summary', label: '요약' }, { id: 'compare', label: 'Scene 비교' }, { id: 'video', label: '영상' }, { id: 'materials', label: '소재·물성' }]

function ViewTabs({ tabs, active, onSelect }: { tabs: Array<{ id: ViewTab; label: string }>; active: ViewTab; onSelect: (tab: ViewTab) => void }) {
  const refs = useRef(new Map<ViewTab, HTMLButtonElement>())
  // Manual activation: arrows move focus, Enter/Space (button click) opens the tab.
  const move = (index: number) => { const next = tabs[(index + tabs.length) % tabs.length]; refs.current.get(next.id)?.focus() }
  return <div className="case-view-tabs" role="tablist" aria-label="결과 보기" onKeyDown={(event) => {
    const focused = tabs.findIndex((tab) => refs.current.get(tab.id) === document.activeElement)
    const index = focused >= 0 ? focused : tabs.findIndex((tab) => tab.id === active)
    if (event.key === 'ArrowRight') { event.preventDefault(); move(index + 1) }
    else if (event.key === 'ArrowLeft') { event.preventDefault(); move(index - 1) }
    else if (event.key === 'Home') { event.preventDefault(); move(0) }
    else if (event.key === 'End') { event.preventDefault(); move(tabs.length - 1) }
  }}>{tabs.map((tab) => <button key={tab.id} ref={(node) => { if (node) refs.current.set(tab.id, node); else refs.current.delete(tab.id) }} type="button" role="tab" id={`case-view-tab-${tab.id}`} aria-controls="case-view-panel" aria-selected={tab.id === active} tabIndex={tab.id === active ? 0 : -1} onClick={() => onSelect(tab.id)}>{tab.label}</button>)}</div>
}

const ENVIRONMENT_LABELS: Record<DashboardEnvironment, string> = { USAGE: '사용환경', DISTRIBUTION: '유통환경' }
export const NO_RESULTS_MESSAGE = '이 의뢰에는 아직 등록된 결과가 없습니다. 의뢰 폴더의 Working에 결과를 추가하면 자동 탐색이 등록합니다.'

export function SimulationDashboard({ projectId, requestId, canManageFolders = false, canRefreshSchema = false, refreshToken = 0, activeTab = 'case_results', resultEnvironments, headerExtra, renderMaterials }: Props) {
  // Selection lives in the router URL (shared with the materials tab): user
  // changes push a history entry, automatic repairs replace it.
  const { get: getParam, caseId, loadCaseId, runId, optionId, update: updateParams } = useCaseHierarchyParams()
  const materialsActive = activeTab === 'materials'
  // Materials decks exist only in the distribution environment.
  // E2: one registered environment wins over the URL; the toggle remains only for both (E4).
  const environments = resultEnvironments === undefined ? (['USAGE', 'DISTRIBUTION'] as DashboardEnvironment[]) : resultEnvironments
  const resolvedEnvironment = environments?.length === 1 ? environments[0] : null
  const noResults = environments?.length === 0
  const tab: Tab = materialsActive || (resolvedEnvironment ? resolvedEnvironment === 'DISTRIBUTION' : getParam('result_environment') === 'DISTRIBUTION') ? 'distribution' : 'usage'
  // §3.5: no catalog while the environment is pending or the request has no Cases.
  const catalogReady = Boolean(environments?.length)
  const captureId = getParam('capture')
  const clearedPath = { case: null, capture: null, case_load: null, case_run: null, case_option: null, scene: null, part: null }
  const setTab = (next: Tab) => { if (next !== tab || materialsActive) updateParams({ result_environment: next === 'usage' ? 'USAGE' : 'DISTRIBUTION', ...(next !== tab ? clearedPath : {}), ...(materialsActive ? { resultTab: null } : {}) }) }
  const setCaseId = (value: string) => updateParams({ case: value || null })
  const setCaptureId = (value: string) => updateParams({ capture: value || null })
  const setLoadCaseId = (value: string) => updateParams({ case_load: value || null })
  const setRunId = (value: string) => updateParams({ case_run: value || null })
  const setOptionId = (value: string) => updateParams({ case_option: value || null })
  const [catalog, setCatalog] = useState<DashboardCatalog | null>(null)
  const [catalogError, setCatalogError] = useState('')
  const [mode, setMode] = useState('')
  const [componentId, setComponentId] = useState('')
  const [basis, setBasis] = useState<'' | 'REPORTED_SUMMARY' | 'DETAIL'>('')
  const [referenceCaseId, setReferenceCaseId] = useState('')
  const [referenceCaptureId, setReferenceCaptureId] = useState('')
  const [view, setView] = useState<Exclude<ViewTab, 'materials'>>('summary')
  const [compareView, setCompareView] = useState<CompareView>('edges')
  const [edges, setEdges] = useState<string[]>([...EDGES])
  const [lines, setLines] = useState<string[]>(['1', '2', '3', '4'])
  const [comparison, setComparison] = useState<DashboardComparisonMember[]>([])
  const [pathTarget, setPathTarget] = useState<HTMLDivElement | null>(null)
  const latestCatalogKey = useRef('')
  const loadedCatalogKey = useRef('')
  // A request or environment change drops the previous catalog and local
  // display state. URL ids are kept and validated against the new catalog,
  // so deep links and browser history restore their selection.
  const catalogScope = useRef(`${projectId}:${requestId}:${tab}`)
  useEffect(() => { setComparison([]) }, [projectId, requestId])
  // §3.2: correct the URL to the resolved environment without a history entry.
  useEffect(() => { if (resolvedEnvironment && !materialsActive && getParam('result_environment') !== resolvedEnvironment) updateParams({ result_environment: resolvedEnvironment }, { replace: true }) }, [getParam, materialsActive, resolvedEnvironment, updateParams])

  useEffect(() => {
    const controller = new AbortController(); const key = `${projectId}:${requestId}:${tab}`; latestCatalogKey.current = key
    if (catalogScope.current !== key) {
      catalogScope.current = key; setCatalog(null); setMode(''); setComponentId(''); setBasis('')
    }
    setCatalogError('')
    if (!catalogReady) return
    simulationDashboardApi.catalog(projectId, requestId, tab === 'usage' ? 'USAGE' : 'DISTRIBUTION', controller.signal).then((next) => {
      if (!controller.signal.aborted && latestCatalogKey.current === key) { loadedCatalogKey.current = key; setCatalog(next) }
    }).catch((reason) => { if (!controller.signal.aborted) setCatalogError(errorText(reason)) })
    return () => controller.abort()
  }, [refreshToken, projectId, requestId, tab, catalogReady])

  const options = useMemo(() => {
    if (!catalog || tab !== 'distribution') return []
    const activeCaptureId = catalog.captures.some((item) => item.id === captureId) ? captureId : ''
    const explicit = catalog.run_options ?? []
    if (explicit.length) return explicit.filter((item) => item.execution_run_id === runId && currentHierarchyChoice(item, activeCaptureId))
    return catalog.modes.filter((item) => item.execution_run_id === runId && currentHierarchyChoice(item, activeCaptureId)).map((item) => ({ ...item, run_option_id: item.id, option_status: item.id === 'UNKNOWN' ? 'UNRESOLVED' as const : 'PRESENT' as const }))
  }, [captureId, catalog, runId, tab])
  const activeCaptureIdForChoices = catalog?.captures.some((item) => item.id === captureId) ? captureId : ''
  const loadChoices = catalog?.load_cases.filter((item) => item.case_id === caseId && currentHierarchyChoice(item, activeCaptureIdForChoices)) ?? []
  const runChoices = catalog?.execution_runs.filter((item) => item.case_id === caseId && item.load_case_id === loadCaseId && currentHierarchyChoice(item, activeCaptureIdForChoices)) ?? []
  useEffect(() => {
    // The materials tab owns the shared path while it is open (its own catalog).
    if (materialsActive || !catalog || loadedCatalogKey.current !== `${projectId}:${requestId}:${tab}`) return
    // Automatic repairs replace the history entry instead of pushing one.
    const setCaseId = (value: string) => updateParams({ case: value || null }, { replace: true })
    const setCaptureId = (value: string) => updateParams({ capture: value || null }, { replace: true })
    const setLoadCaseId = (value: string) => updateParams({ case_load: value || null }, { replace: true })
    const setRunId = (value: string) => updateParams({ case_run: value || null }, { replace: true })
    const setOptionId = (value: string) => updateParams({ case_option: value || null }, { replace: true })
    // Path levels need a user choice when there are several candidates; the
    // display options (Component, Basis) default to the first candidate.
    const choose = (current: string, choices: DashboardChoice[], setter: (value: string) => void, firstIfMany = false) => {
      const unique = Array.from(new Map(choices.map((item) => [item.id, item])).values())
      if (unique.some((item) => item.id === current)) return
      // Prefer a candidate that carries values (the server lists those first).
      const preferred = unique.find((item) => item.has_values) ?? unique[0]
      const next = unique.length === 1 || (firstIfMany && unique.length) ? preferred.id : ''
      // Clearing an empty level is a no-op; skipping it also keeps a render that
      // still sees the old URL from undoing a choice the user just made.
      if (next !== current) setter(next)
    }
    const caseChoice = catalog.cases.find((item) => item.id === caseId || item.dashboard_case_id === caseId)
    if (caseChoice && caseChoice.id !== caseId) setCaseId(caseChoice.id)
    else choose(caseId, catalog.cases, setCaseId)
    const activeCaptureId = catalog.captures.some((item) => item.id === captureId) ? captureId : ''
    if (caseId) {
      const captures = Array.from(new Map(catalog.captures.filter((item) => item.case_id === (caseChoice?.id ?? caseId)).map((item) => [item.id, item])).values())
      // Always show the Case's merged latest result: every Scene of the Run
      // option with its newest captured result. Stored captures are history.
      const latest = captures.find((item) => item.kind === 'LATEST')
      if (latest) { if (captureId !== latest.id) setCaptureId(latest.id) }
      else if (captureId !== SCHEMA_CONTEXT && !captures.some((item) => item.id === captureId)) setCaptureId(captures[0]?.id ?? SCHEMA_CONTEXT)
    }
    if (tab === 'distribution' && caseId) choose(loadCaseId, catalog.load_cases.filter((item) => item.case_id === caseId && currentHierarchyChoice(item, activeCaptureId)), setLoadCaseId)
    if (tab === 'distribution' && loadCaseId) choose(runId, catalog.execution_runs.filter((item) => item.case_id === caseId && item.load_case_id === loadCaseId && currentHierarchyChoice(item, activeCaptureId)), setRunId)
    if (tab === 'distribution' && runId) choose(optionId, options, setOptionId)
    const selectedOption = options.find((item) => item.id === optionId)
    if (selectedOption && mode !== selectedOption.mode) setMode(selectedOption.mode ?? selectedOption.id)
    if (tab === 'distribution' && mode) choose(componentId, catalog.components.filter((item) => item.execution_run_id === runId && item.mode === mode && item.capture_id === activeCaptureId && (!item.run_option_id || item.run_option_id === optionId)), setComponentId, true)
    if (tab === 'distribution') choose(basis, catalog.bases, (value) => setBasis(value as typeof basis), true)
  }, [basis, captureId, caseId, catalog, componentId, loadCaseId, materialsActive, mode, optionId, options, projectId, requestId, runId, tab, updateParams])
  // Router Link applies the deployment basename (for example /home/).
  const folderHref = `/workspace/catalog/schemas?${new URLSearchParams({ project: projectId, request: requestId, result_environment: tab === 'usage' ? 'USAGE' : 'DISTRIBUTION' }).toString()}`

  const selectedCapture = catalog?.captures.find((item) => item.id === captureId)
  // Stored captures of this Case, newest first (the merged LATEST entry is virtual).
  const storedCaptures = (catalog?.captures ?? []).filter((item) => item.case_id === caseId && item.kind !== 'LATEST')
  const selectedCase = catalog?.cases.find((item) => item.id === caseId)
  // A capture left over from another Case (e.g. Case changed in the materials path) is ignored.
  const captureMatchesCase = !selectedCapture?.case_id || selectedCapture.case_id === caseId
  const dashboardCaseId = (captureMatchesCase ? selectedCapture?.dashboard_case_id : null) ?? selectedCase?.dashboard_case_id ?? caseId
  const hasCapturedCase = Boolean(selectedCase?.dashboard_case_id
    || catalog?.captures.some((item) => item.case_id === caseId))
  const activeCaptureId = selectedCapture?.id ?? ''
  const sceneChoices = (catalog?.scenes ?? []).filter((item) => item.case_id === caseId
    && (!item.load_case_id || item.load_case_id === loadCaseId)
    && (!item.execution_run_id || item.execution_run_id === runId)
    && (!item.run_option_id || item.run_option_id === optionId)
    && currentHierarchyChoice(item, activeCaptureId)) ?? []
  // One chip per Scene folder: folder Scenes from the schema, marked when the
  // merged latest result has data for them.
  const sceneSummary = Array.from(sceneChoices.reduce((map, item) => {
    const key = item.label.toLocaleLowerCase()
    const current = map.get(key)
    map.set(key, { label: current?.label ?? item.label, title: current?.title ?? item.relative_path ?? item.label, hasResult: Boolean(current?.hasResult || item.capture_id) })
    return map
  }, new Map<string, { label: string; title: string; hasResult: boolean }>()).values())
  const componentChoices = catalog?.components.filter((item) => (item.execution_run_id === runId || item.run_id === runId) && item.mode === mode && item.capture_id === activeCaptureId && (!item.run_option_id || item.run_option_id === optionId)) ?? []
  const caseLabel = (id: string) => catalog?.cases.find((item) => item.id === id || item.dashboard_case_id === id)?.label ?? 'Case'
  const addCurrent = () => {
    if (comparison.some((item) => item.simulation_case_id === dashboardCaseId) || comparison.length >= 8 || !basis) return
    setComparison([...comparison, { simulation_case_id: dashboardCaseId, load_case_id: loadCaseId, execution_run_id: runId, run_option_id: optionId, capture_id: activeCaptureId, mode, component_id: componentId, basis }])
  }

  const tabs = VIEW_TABS.filter((item) => tab === 'distribution' || item.id === 'summary' || item.id === 'materials')
  const activeView: ViewTab = materialsActive ? 'materials' : tabs.some((item) => item.id === view) ? view : 'summary'
  const selectView = (next: ViewTab) => {
    // `result_environment` is left as is while 소재·물성 is open (materials are
    // always distribution), so leaving returns to the environment the user came from.
    if (next === 'materials') {
      if (!materialsActive) updateParams({ resultTab: 'materials' })
      return
    }
    setView(next)
    if (materialsActive) updateParams({ resultTab: null, ...(getParam('result_environment') ? {} : { result_environment: 'DISTRIBUTION' }) })
  }

  const pathBar = catalog && !materialsActive ? <HierarchyPath label="Case 경로" className="case-path-bar" trailing={tab === 'distribution' ? <div className="case-scene-chips" role="list" aria-label="Scene 목록">{sceneSummary.length ? sceneSummary.map((item) => <span role="listitem" key={item.label} className={`case-scene-chip${item.hasResult ? ' case-scene-chip--result' : ''}`} title={`${item.title} · ${item.hasResult ? '결과 있음' : '결과 없음'}`}><i aria-hidden="true" />{item.label}<span className="case-sr-only">{item.hasResult ? ' 결과' : ' 결과 없음'}</span></span>) : null}</div> : undefined}>
    {resolvedEnvironment ? <HierarchyChoice label="환경" value={resolvedEnvironment} choices={[{ id: resolvedEnvironment, label: ENVIRONMENT_LABELS[resolvedEnvironment] }]} onChange={() => undefined} /> : null}
    <HierarchyChoice label="Case" value={caseId} choices={pathChoices(catalog.cases)} disabledReason="확정된 Case가 없습니다." onChange={(value) => { setCaseId(value); setCaptureId(''); setLoadCaseId(''); setRunId(''); setOptionId(''); setMode(''); setComponentId(''); setReferenceCaseId(''); setReferenceCaptureId('') }} />
    {tab === 'distribution' ? <HierarchyChoice label="하중경우" value={loadCaseId} choices={pathChoices(loadChoices)} disabled={!caseId} disabledReason={caseId ? '하중경우가 없습니다.' : 'Case를 먼저 선택하세요.'} onChange={(value) => { setLoadCaseId(value); setRunId(''); setOptionId(''); setMode(''); setComponentId('') }} /> : null}
    {tab === 'distribution' ? <HierarchyChoice label="Run Case" value={runId} choices={pathChoices(runChoices)} disabled={!loadCaseId} disabledReason={loadCaseId ? 'Run Case가 없습니다.' : '하중경우를 먼저 선택하세요.'} onChange={(value) => { setRunId(value); setOptionId(''); setMode(''); setComponentId('') }} /> : null}
    {tab === 'distribution' ? <HierarchyChoice label="Run Option" value={optionId} choices={pathChoices(options)} disabled={!runId} disabledReason={runId ? 'Run Option이 없습니다.' : 'Run Case를 먼저 선택하세요.'} onChange={(value) => { const choice = options.find((item) => item.id === value); setOptionId(value); setMode(choice?.mode ?? ''); setComponentId('') }} /> : null}
  </HierarchyPath> : null

  const displayOptions = catalog && !materialsActive ? <details className="case-display-options">
    <summary><SlidersHorizontal aria-hidden="true" />표시 옵션</summary>
    <div className="case-display-options__panel">
      {tab === 'distribution' ? <div className="case-display-options__row">
        <HierarchyChoice label="Component" value={componentId} choices={pathChoices(componentChoices)} disabled={!activeCaptureId || !mode} disabledReason="결과가 있는 Run Option을 먼저 선택하세요." onChange={setComponentId} />
        <HierarchyChoice label="Basis" value={basis} choices={catalog.bases.map((item) => ({ id: item.id, label: basisLabel(item.id) }))} disabled={!activeCaptureId} disabledReason="결과가 있는 Run Option을 먼저 선택하세요." onChange={(value) => setBasis(value === 'DETAIL' ? 'DETAIL' : value === 'REPORTED_SUMMARY' ? 'REPORTED_SUMMARY' : '')} />
      </div> : null}
      {tab === 'distribution' ? <div className="case-display-options__row"><EdgePicker edges={edges} onChange={setEdges} /><LinePicker lines={lines} onChange={setLines} /></div> : null}
      {tab === 'distribution' ? <fieldset className="case-display-options__compare"><legend>Case 비교</legend>
        <button type="button" onClick={addCurrent} disabled={!activeCaptureId || !basis || comparison.some((item) => item.simulation_case_id === dashboardCaseId) || comparison.length >= 8}>현재 Case 비교에 추가</button>
        {comparison.map((item) => <button type="button" key={item.simulation_case_id} onClick={() => setComparison(comparison.filter((candidate) => candidate.simulation_case_id !== item.simulation_case_id))}>{caseLabel(item.simulation_case_id)} 제거</button>)}
      </fieldset> : <fieldset className="case-display-options__compare"><legend>Reference 비교</legend>
        <SelectField label="Reference Case" value={referenceCaseId} choices={catalog.cases.filter((item) => item.id !== caseId)} onChange={(value) => { setReferenceCaseId(value); setReferenceCaptureId('') }} />
        <SelectField label="Reference 결과" value={referenceCaptureId} choices={catalog.captures.filter((item) => item.case_id === referenceCaseId)} disabled={!referenceCaseId} onChange={setReferenceCaptureId} />
      </fieldset>}
      <div className="case-display-options__history"><strong>업데이트 이력</strong><ul aria-label="결과 업데이트 이력">{storedCaptures.length ? storedCaptures.map((item) => <li key={item.id} title={item.label}>{item.label}</li>) : <li>아직 결과가 없습니다.</li>}</ul></div>
      {canManageFolders ? <Link className="case-display-options__link" to={folderHref}>폴더 연결·규칙 열기</Link> : null}
    </div>
  </details> : null

  const componentLabel = componentChoices.find((item) => item.id === componentId)?.label
  const viewContext = tab === 'distribution' && !materialsActive && (activeView === 'summary' || activeView === 'compare') && componentLabel && basis ? `${componentLabel} · ${basisLabel(basis)}` : ''
  const distributionEmpty = catalog?.folder_schema?.status === 'AVAILABLE' && selectedCase?.source === 'FOLDER_SCHEMA' && !selectedCase.capture_count ? '이 Case에는 아직 결과 파일이 없습니다. Scene 폴더에 결과를 넣으면 자동으로 표시됩니다.' : '결과를 불러오는 중입니다.'
  const distribution = (section: 'summary' | 'compare') => <DistributionArea key={`${projectId}:${requestId}`} section={section} compareView={compareView} onCompareView={setCompareView} edges={edges} lines={lines} comparison={comparison} caseId={dashboardCaseId} loadCaseId={loadCaseId} captureId={activeCaptureId} runId={runId} optionId={optionId} mode={mode} componentId={componentId} basis={basis} hasCapturedRun={runChoices.some((item) => item.id === runId && item.capture_id === activeCaptureId)} hasCapturedOption={options.some((item) => item.id === optionId && item.capture_id === activeCaptureId)} emptyContextMessage={distributionEmpty} />
  const content = activeView === 'materials' ? renderMaterials?.(pathTarget) ?? null
    : tab === 'usage' ? <UsageArea key={`${caseId}:${captureId}:${referenceCaseId}:${referenceCaptureId}`} caseId={dashboardCaseId} captureId={activeCaptureId} referenceCaseId={catalog?.cases.find((item) => item.id === referenceCaseId)?.dashboard_case_id ?? referenceCaseId} referenceCaptureId={referenceCaptureId} />
    : activeView === 'video' ? (runId && !optionId && options.length > 1 ? <State message="Run Option을 선택하세요." /> : activeCaptureId && runId ? <CaseVideoGrid key={`${activeCaptureId}:${runId}:${optionId}`} captureId={activeCaptureId} runId={runId} runOptionId={optionId || undefined} mode={mode || undefined} /> : <State message={distributionEmpty} />)
    : distribution(activeView === 'compare' ? 'compare' : 'summary')

  // The report dialog copies this scope when it opens (later changes do not reach it).
  const optionChoice = options.find((item) => item.id === optionId)
  const reportReady = tab === 'distribution' && !materialsActive && Boolean(activeCaptureId && runId && mode && componentId && basis && (optionId || !options.length)) && runChoices.some((item) => item.id === runId && item.capture_id === activeCaptureId)
  const referenceDashboardCaseId = catalog?.cases.find((item) => item.id === referenceCaseId)?.dashboard_case_id ?? referenceCaseId
  const usageReady = tab === 'usage' && !materialsActive && Boolean(dashboardCaseId && activeCaptureId) && Boolean(referenceCaseId) === Boolean(referenceCaptureId)
  const caseText = selectedCase?.label ?? caseLabel(caseId)
  const reportScope: CaseReportScope | null = reportReady ? {
    source: { kind: 'case_results', projectId, requestId, caseId: dashboardCaseId, captureId: activeCaptureId, loadCaseId, runId, optionId, mode, componentId, basis, edgeKeys: edges.join(','), lineIndices: lines.join(',') },
    labels: { project: '', request: '', caseLabel: caseText, loadCase: loadChoices.find((item) => item.id === loadCaseId)?.label ?? '', run: runChoices.find((item) => item.id === runId)?.label ?? '', option: optionChoice?.option_label || optionChoice?.label || '', component: componentLabel ?? '', basis: basisLabel(basis) },
  } : usageReady ? {
    source: { kind: 'case_usage', projectId, requestId, caseId: dashboardCaseId, captureId: activeCaptureId, referenceCaseId: referenceCaptureId ? referenceDashboardCaseId : '', referenceCaptureId },
    labels: { project: '', request: '', caseLabel: caseText, reference: referenceCaseId ? [caseLabel(referenceCaseId), catalog?.captures.find((item) => item.id === referenceCaptureId)?.label].filter(Boolean).join(' · ') : '' },
  } : null
  // Final designation reports cover the whole Case regardless of the on-screen selection.
  // The Final report always uses the same basis as Final designation: the merged latest
  // result (newest capture per Scene; for usage the newest capture), never a history entry.
  const finalCaptureId = dashboardCaseId ? `latest:${dashboardCaseId}` : ''
  const finalScope: CaseReportFinalScope | null = materialsActive || !dashboardCaseId || !activeCaptureId || !hasCapturedCase ? null : tab === 'usage'
    ? { source: { kind: 'case_usage', projectId, requestId, caseId: dashboardCaseId, captureId: finalCaptureId, referenceCaseId: '', referenceCaptureId: '' }, labels: { project: '', request: '', caseLabel: caseText, reference: '' } }
    : { source: { kind: 'case_final', projectId, requestId, caseId: dashboardCaseId, captureId: finalCaptureId, catalogCaseId: selectedCase?.id ?? caseId, basis, edgeKeys: edges.join(','), lineIndices: lines.join(',') }, labels: { project: '', request: '', caseLabel: caseText, component: componentLabel ?? '' } }
  return <section className="simulation-dashboard" data-ui-density="v1" aria-label="SPDM 해석 결과 대시보드">
    <header className="case-results-head">
      {environments?.length === 2 ? <div className="case-env-toggle" role="group" aria-label="결과 환경"><button type="button" aria-pressed={tab === 'usage'} className={tab === 'usage' ? 'active' : ''} onClick={() => setTab('usage')}>사용환경</button><button type="button" aria-pressed={tab === 'distribution'} className={tab === 'distribution' ? 'active' : ''} onClick={() => setTab('distribution')}>유통환경</button></div> : null}
      {materialsActive ? null : <FolderNameWarnings warnings={catalog?.name_warnings} />}
      <div className="case-results-head__actions">{headerExtra}{catalog ? <CaseReportLauncher scope={reportScope} disabledReason={materialsActive ? '소재·물성 탭에서는 보고서를 만들지 않습니다.' : tab === 'usage' ? '결과가 있는 사용환경 Case를 선택하면 보고서를 만들 수 있습니다.' : '유통환경에서 결과가 있는 Run Case와 Run Option을 선택하면 보고서를 만들 수 있습니다.'} /> : null}{catalog ? <CaseFinalizationPanel projectId={projectId} requestId={requestId} environment={tab === 'usage' ? 'USAGE' : 'DISTRIBUTION'} caseId={dashboardCaseId} captureId={hasCapturedCase ? finalCaptureId : ''} hasCapturedCase={hasCapturedCase} canFinalize={canRefreshSchema} reportScope={finalScope} /> : null}</div>
    </header>
    {noResults && !materialsActive ? <State message={NO_RESULTS_MESSAGE} /> : null}
    {catalogError && !catalog && !materialsActive ? <State message={catalogError} error /> : null}
    {catalogError && catalog ? <State message={`${catalogError} · 마지막으로 읽은 결과를 표시합니다.`} error /> : null}
    {pathBar}
    {/* The materials tab renders its own path (same component) into this slot. */}
    <div ref={setPathTarget} className="case-path-slot" hidden={!materialsActive} />
    {!materialsActive && catalog?.folder_schema?.status === 'UNAVAILABLE' ? <State message="폴더 구조를 읽을 수 없습니다. 저장된 결과를 표시합니다." title={catalog.folder_schema?.diagnostic?.message} error /> : null}
    {!materialsActive && catalog && !catalog.cases.length ? <State message="확정된 Case가 없습니다. 폴더 연결·규칙에서 구조를 확인하세요." /> : null}
    {noResults && !materialsActive ? null : <>
    <div className="case-view-bar"><ViewTabs tabs={tabs} active={activeView} onSelect={selectView} /><div className="case-view-bar__end">{viewContext ? <span className="case-view-context" title={`표시 중: ${viewContext}`}>{viewContext}</span> : null}{displayOptions}</div></div>
    <div className="case-view-panel" id="case-view-panel" role="tabpanel" aria-labelledby={`case-view-tab-${activeView}`}><SceneNameWarningProvider warnings={catalog?.name_warnings} runOptionId={optionId}>{content}</SceneNameWarningProvider></div>
    </>}
  </section>
}

function State({ message, error = false, title }: { message: string; error?: boolean; title?: string }) { return <div className={`simulation-dashboard__state ${error ? 'error' : ''}`} role={error ? 'alert' : 'status'} title={title}>{error ? <AlertTriangle /> : <Layers3 />}<span className="case-prose">{message}</span></div> }

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
  if (!caseId || !captureId) return <State message="Case를 선택하세요." />
  if (Boolean(referenceCaseId) !== Boolean(referenceCaptureId)) return <State message="표시 옵션에서 Reference 결과를 선택하세요." />
  if (error) return <State message={error} error />
  if (!data) return <State message="사용환경 평가를 불러오는 중입니다." />
  const media = data.evaluations.flatMap((evaluation) => evaluation.media ?? [])
  const cell = (value: DashboardValue | null | undefined, metric: UsageMetric) => {
    const view = usageCell(value, metric)
    if (view.tone === 'absent') return <span className="usage-missing">{view.text}</span>
    return <span className={`usage-cell usage-cell-${view.tone}`}><strong>{view.text}</strong><small>{view.label}</small></span>
  }
  const directions = USAGE_DIRECTIONS
  const evaluationRows = usageEvaluationRows(data)
  return <div className="simulation-dashboard__usage" data-testid="usage-dashboard">
    <div className="simulation-dashboard__usage-media">{media.length ? media.map((asset) => <div key={asset.asset_id}><h3>{asset.title}</h3><Media asset={asset} /></div>) : <State message="연결된 미디어 없음" />}</div>
    <div className="simulation-dashboard__usage-table"><header><h3>다섯 평가 종합</h3><small>{usageStatusText(data)}</small></header><Table><TableHead><TableRow><TableHeaderCell>평가 / 원문 키</TableHeaderCell><TableHeaderCell>공통</TableHeaderCell><TableHeaderCell>전방</TableHeaderCell><TableHeaderCell>후방</TableHeaderCell><TableHeaderCell>Reference</TableHeaderCell></TableRow></TableHead><TableBody>{evaluationRows.map((row) => <TableRow key={`${row.evaluation.id}:${row.metric}`}><TableHeaderCell><strong>{row.label}</strong>{row.key ? <small className="usage-source-key" title={row.key}>{row.key}</small> : null}</TableHeaderCell><TableCell>{cell(row.evaluation.common, row.metric)}</TableCell><TableCell>{cell(row.evaluation.front, row.metric)}</TableCell><TableCell>{cell(row.evaluation.rear, row.metric)}</TableCell><TableCell>{row.evaluation.reference ? <>{row.evaluation.reference.reason || directions.map((direction) => { const value = row.evaluation.reference?.[direction]; return value ? <div key={direction}>{USAGE_DIRECTION_LABELS[direction]}: {cell(value, row.metric)}</div> : null })}</> : '미선택'}</TableCell></TableRow>)}</TableBody></Table></div>
    <div className="simulation-dashboard__usage-values">{data.evaluations.filter((evaluation) => evaluation.id !== 'Slope_Angle_360').map((evaluation) => {
      const points = directions.filter((key) => evaluation[key] != null || evaluation.reference?.[key] != null).map((key) => ({ direction: USAGE_DIRECTION_LABELS[key], current: evaluation[key] && usageMetricStatus(evaluation[key]!, 'value') === 'READY' ? evaluation[key]?.value : null, reference: evaluation.reference?.[key] && usageMetricStatus(evaluation.reference[key]!, 'value') === 'READY' ? evaluation.reference[key]?.value : null }))
      const key = usageFieldKey(evaluation, 'value')
      return <article key={evaluation.id}><h3>{usageEvaluationLabel(evaluation)}</h3>{key ? <small className="usage-source-key" title={key}>{key}</small> : null}<small>{evaluation.common?.unit || evaluation.front?.unit || evaluation.rear?.unit}</small><div style={{height: 150}}><ResponsiveContainer width="100%" height="100%"><BarChart data={points}><XAxis dataKey="direction" /><YAxis /><Tooltip /><Bar dataKey="current" name="현재 Case" fill="var(--color-chart-series-1)" /><Bar dataKey="reference" name="Reference" fill="var(--color-chart-series-2)" /></BarChart></ResponsiveContainer></div></article>
    })}</div>
  </div>
}

type DistributionProps = { section: 'summary' | 'compare'; compareView: CompareView; onCompareView: (view: CompareView) => void; edges: string[]; lines: string[]; comparison: DashboardComparisonMember[]; caseId: string; loadCaseId: string; captureId: string; runId: string; optionId: string; mode: string; componentId: string; basis: '' | 'REPORTED_SUMMARY' | 'DETAIL'; hasCapturedRun: boolean; hasCapturedOption: boolean; emptyContextMessage: string }
function DistributionArea({ section, compareView, onCompareView, edges, lines, comparison, caseId, loadCaseId, captureId, runId, optionId, mode, componentId, basis, hasCapturedRun, hasCapturedOption, emptyContextMessage }: DistributionProps) {
  const [data, setData] = useState<DashboardDistribution | null>(null); const [error, setError] = useState(''); const [sceneId, setSceneId] = useState(''); const [memberId, setMemberId] = useState(''); const latestKey = useRef('')
  const contextKey = `${caseId}:${loadCaseId}:${runId}:${optionId}:${mode}:${captureId}:${componentId}:${basis}:${edges.join(',')}:${lines.join(',')}:${comparison.map((item) => `${item.simulation_case_id}/${item.load_case_id}/${item.execution_run_id}/${item.run_option_id}/${item.capture_id}/${item.mode}/${item.component_id}/${item.basis}`).join('|')}`
  useEffect(() => {
    if (!runId || !captureId || !hasCapturedRun || !hasCapturedOption || !mode || !componentId || !basis) { setData(null); return }
    const controller = new AbortController(); latestKey.current = contextKey; setError(''); setData(null); setSceneId(''); setMemberId('')
    const current = { simulation_case_id: caseId, load_case_id: loadCaseId, execution_run_id: runId, run_option_id: optionId, capture_id: captureId, mode, component_id: componentId, basis }
    const members = comparison.length ? [...comparison, ...(comparison.some((item) => item.simulation_case_id === caseId) ? [] : [current])] : []
    const request = members.length > 1 ? simulationDashboardApi.comparison(members, edges.join(','), lines.join(','), controller.signal) : simulationDashboardApi.distribution(runId, { capture_id: captureId, run_option_id: optionId, mode, component_id: componentId, basis, edge_keys: edges.join(','), line_indices: lines.join(',') }, controller.signal)
    request.then((next) => { if (!controller.signal.aborted && latestKey.current === contextKey) setData(next) }).catch((reason) => { if (!controller.signal.aborted && latestKey.current === contextKey) setError(errorText(reason)) })
    return () => controller.abort()
  }, [basis, captureId, caseId, comparison, componentId, contextKey, edges, hasCapturedOption, hasCapturedRun, lines, loadCaseId, mode, optionId, runId])
  if (!captureId) return <State message={emptyContextMessage} />
  if (runId && !hasCapturedRun) return <State message="이 Run의 수집 결과 없음" />
  if (runId && hasCapturedRun && optionId && !hasCapturedOption) return <State message="이 Run Option의 수집 결과 없음" />
  if (!runId || !mode || !componentId || !basis) return <State message="Run Case와 Run Option을 선택하세요." />
  if (error) return <State message={error} error />
  if (!data) return <State message="결과를 불러오는 중입니다." />
  const choose = (scene: string, member: string) => { setSceneId(scene); setMemberId(member) }
  const compareViews: Array<[CompareView, string]> = [['edges', '엣지별 수준'], ['contours', '컨투어'], ['behavior', '거동']]
  return <div className="simulation-dashboard__distribution" data-testid="distribution-dashboard">
    {section === 'summary' ? <Summary data={data} sceneId={sceneId} memberId={memberId} selectedEdges={edges} onSelect={choose} /> : <>
      <div className="case-compare-toggle" role="group" aria-label="Scene 비교 보기">{compareViews.map(([id, label]) => <button key={id} type="button" aria-pressed={compareView === id} className={compareView === id ? 'active' : ''} onClick={() => onCompareView(id)}>{label}</button>)}</div>
      {compareView === 'edges' ? <EdgePanels data={data} onSelect={choose} /> : null}
      {compareView === 'contours' ? <ContourMatrix data={data} onSelect={choose} /> : null}
      {compareView === 'behavior' ? <BehaviorMatrix data={data} sceneId={sceneId} onSelect={choose} /> : null}
    </>}
    <SceneDetail sceneId={sceneId} member={data.members.find((member) => member.id === memberId)} lineIndices={lines.join(',')} />
    {data.quality_issues.length ? <Issues issues={data.quality_issues} /> : null}
  </div>
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
  return <div className="simulation-dashboard__summary"><section className="case-summary-edge"><header><h3>Open Cell 엣지 맵</h3><small title={selectedScene?.label}>{selectedScene?.label ?? 'Scene 선택'}</small></header><EdgeMap peaks={peaks} selectedEdges={selectedEdges} /></section><section className="case-summary-chart"><header><h3>Scene별 엣지 최대응력 <InfoTip text="선택한 엣지의 최대값(envelope)입니다. 값은 서버가 계산합니다." /></h3></header>{noEdgeSelection ? <State message="선택 없음: 표시 옵션에서 엣지를 하나 이상 선택하세요." /> : <SimulationResultGraph chartId="summary" title="Scene별 엣지 최대응력" points={rows} members={data.members} onSelect={onSelect} valueName="선택 엣지 envelope" />}</section><SimulationLocationMap data={data} onSelect={onSelect} info={<InfoTip text="범주별 발생 위치입니다. 동률은 모두 표시하고, 라인 일부만 선택하면 코너는 제외합니다. 단위는 미확인입니다." />} /></div>
}
function EdgeMap({ peaks, selectedEdges }: { peaks: DashboardEdgePeak[]; selectedEdges: string[] }) { const item = (edge: string) => peaks.find((peak) => peak.edge === edge); return <div className="simulation-dashboard__edge-map">{EDGES.map((edge) => { const peak = item(edge); return <div key={edge} className={`edge edge-${edge.toLowerCase()} ${selectedEdges.includes(edge) ? 'selected' : ''} ${peak?.is_selected_maximum ? 'maximum' : ''}`}><b>{edge}</b><span>{valueText(peak)}</span><small>{statusText(peak?.completeness ?? peak?.status)}</small></div> })}</div> }
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
  return <><SceneTable scenes={data.scenes} /><div className="simulation-dashboard__panel-controls"><fieldset><legend>Case 범례</legend><div className="simulation-dashboard__case-actions"><button type="button" onClick={() => setVisible(memberIds)}>전체 선택</button><button type="button" onClick={() => setVisible([])}>전체 해제</button></div>{data.members.map((member, index) => <label key={member.id}><input type="checkbox" checked={visible.includes(member.id)} onChange={() => setVisible((current) => current.includes(member.id) ? current.filter((id) => id !== member.id) : [...current, member.id])} /><i style={{ background: memberColor(member, index) }} />{member.label}</label>)}</fieldset><label>표시 순서<select value={order} onChange={(event) => setOrder(event.target.value as 'scene' | 'case')}><option value="scene">Scene별</option><option value="case">Case별</option></select></label><label>패널 크기<select value={size} onChange={(event) => setSize(event.target.value as 'compact' | 'wide')}><option value="compact">작게</option><option value="wide">크게</option></select></label></div>{members.length ? <div className={`simulation-dashboard__edge-panels ${size}`}>{EDGES.map((edge) => <section key={edge}><header><h3>{edge}</h3></header><SimulationResultGraph chartId={`edge-${edge}`} title={`${edge} 엣지 수준`} points={rows(edge)} members={members} onSelect={onSelect} valueName={edge} /></section>)}</div> : <State message="표시할 Simulation Case가 없습니다. 전체 선택 또는 Case를 선택하세요." />}</>
}
function SceneTable({ scenes }: { scenes: DashboardScene[] }) { return <div className="simulation-dashboard__scene-table"><span>Scene 설명</span>{scenes.map((scene) => <div key={scene.id}><b>{scene.scene_sequence_number ?? '순번 미확인'}</b><span>{scene.label}<SceneNameWarningIcon label={scene.label} /></span><small>{[scene.contact_code, scene.repetition, scene.order_status ? statusText(scene.order_status) : null].filter(Boolean).join(' · ') || '설명 미확인'}</small></div>)}</div> }
function ContourMatrix({ data, onSelect }: { data: DashboardDistribution; onSelect: (sceneId: string, memberId: string) => void }) {
  const [transpose, setTranspose] = useState(false)
  const rows = transpose ? data.members : data.scenes
  const columns = transpose ? data.scenes : data.members
  const cellByContext = useMemo(() => new Map(data.contours.map((cell) => [`${cell.scene_id}:${cell.member_id}`, cell])), [data.contours])
  return <section className="simulation-dashboard__matrix"><header><div><h3>Scene별 Cell Tmax 마지막 Frame Contour <InfoTip text="최종 프레임 근거가 없는 이미지는 '프레임 미확인'으로 표시합니다. 공통 Scale bar가 확인되지 않아 셀별 원본 범례를 유지합니다." /></h3></div><button type="button" onClick={() => setTranspose(!transpose)}><RotateCcw /> 행/열 전치</button></header><div className="simulation-dashboard__matrix-scroll"><div className="simulation-dashboard__matrix-grid" style={{ gridTemplateColumns: `minmax(220px, .7fr) repeat(${columns.length}, minmax(190px, 1fr))` }}><div className="simulation-dashboard__matrix-header">{transpose ? 'Case' : 'Scene / 자세·충돌'}</div>{columns.map((column) => <div className="simulation-dashboard__matrix-header" key={column.id} title={'design_description' in column ? [column.label, column.design_description].filter(Boolean).join(' · ') : column.label}>{column.label}{'design_description' in column && column.design_description ? <small>{column.design_description}</small> : null}</div>)}{rows.flatMap((row) => [<SceneOrCaseHeader key={`${row.id}-label`} item={row} />, ...columns.map((column) => { const scene = transpose ? column as DashboardScene : row as DashboardScene; const member = transpose ? row as DashboardMember : column as DashboardMember; const cell = cellByContext.get(`${scene.id}:${member.id}`); return <MatrixCell key={cell?.cell_id ?? `${scene.id}:${member.id}`} cellId={cell?.cell_id ?? `${scene.id}:${member.id}`} asset={cell?.asset} status={cell?.status} reason={cell?.reason} value={cell?.value} scaleStatus={cell?.scale_status} showContourMeta onClick={() => onSelect(scene.id, member.id)} /> })])}</div></div></section>
}
function SceneOrCaseHeader({ item }: { item: DashboardScene | DashboardMember }) {
  if ('scene_sequence_number' in item) return <div className="simulation-dashboard__matrix-header simulation-dashboard__scene-context" title={[item.label, item.description].filter(Boolean).join(' · ')}><b>{item.scene_sequence_number ?? '순번 미확인'} · {item.label}<SceneNameWarningIcon label={item.label} /></b><small>{[item.description, item.contact_code, item.repetition, item.order_status ? statusText(item.order_status) : null].filter(Boolean).join(' · ') || '자세·충돌 정보 미확인'}</small></div>
  return <div className="simulation-dashboard__matrix-header" title={[item.label, item.design_description].filter(Boolean).join(' · ')}><b>{item.label}</b>{item.design_description ? <small>{item.design_description}</small> : null}</div>
}
function BehaviorMatrix({ data, sceneId, onSelect }: { data: DashboardDistribution; sceneId: string; onSelect: (sceneId: string, memberId: string) => void }) { const scene = data.scenes.find((item) => item.id === sceneId) ?? data.scenes[0]; if (!scene) return <State message="Scene이 없습니다." />; return <section className="simulation-dashboard__matrix"><header><div><h3>Scene별 거동</h3><small>{scene.label}<SceneNameWarningIcon label={scene.label} /></small></div></header><div className="simulation-dashboard__matrix-scroll"><div className="simulation-dashboard__matrix-grid" style={{ gridTemplateColumns: `minmax(150px,.45fr) repeat(${data.members.length}, minmax(190px, 1fr))` }}><strong>대상</strong>{data.members.map((member) => <strong key={member.id}>{member.label}</strong>)}{ROLES.flatMap((role) => [<strong key={`${role}-label`}>{role}</strong>, ...data.members.map((member) => { const cell = data.behaviors.find((candidate) => candidate.scene_id === scene.id && candidate.member_id === member.id && candidate.subject_role === role); return <MatrixCell key={cell?.cell_id ?? `${scene.id}:${member.id}:${role}`} cellId={cell?.cell_id ?? `${scene.id}:${member.id}:${role}`} asset={cell?.asset} status={cell?.status} reason={cell?.reason} onClick={() => onSelect(scene.id, member.id)} /> })])}</div></div></section> }
function scopeText(scope?: string | null) { return scope === 'EXTRACTED_SIDES_AND_CORNERS' ? '추출 측면·코너' : scope === 'EXTRACTED_SELECTED_LINES' ? '선택 라인의 추출 측면' : scope === 'SELECTED_SIDE_LINES' || scope === 'SELECTED_EDGE_LINES' ? '선택 엣지·라인' : '집계 범위 미확인' }
function MatrixCell({ cellId, asset, status, reason, value, scaleStatus, showContourMeta = false, onClick }: { cellId: string; asset?: DashboardAsset | null; status?: string; reason?: string | null; value?: DashboardValue | null; scaleStatus?: string | null; showContourMeta?: boolean; onClick: () => void }) { return <div className="simulation-dashboard__matrix-cell" role="button" tabIndex={0} data-cell-id={cellId} onClick={onClick} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') onClick() }}>{asset ? <Media asset={asset} /> : <span>{missingText(status, reason)}</span>}{showContourMeta ? <div className="simulation-dashboard__matrix-meta"><small>값: {value ? valueText(value) : '값 없음'}</small><small>집계: {value?.basis === 'REPORTED_SUMMARY' ? '원본 요약' : value?.basis === 'DETAIL' ? '상세 추출' : '집계 범위 미확인'} · {scopeText(value?.scope)}</small><small>상태: {statusText(value?.completeness ?? value?.status ?? status)}</small><small title="이미지와 수치의 시간 정합성은 확인되지 않았습니다.">시간 정합성 미확인 · Scale bar {!scaleStatus || scaleStatus === 'UNCONFIRMED' ? '미확인' : statusText(scaleStatus)}</small></div> : null}</div> }
function SceneDetail({ sceneId, member, lineIndices }: { sceneId: string; member?: DashboardMember; lineIndices: string }) { const [data, setData] = useState<DashboardSceneDetail | null>(null); const [error, setError] = useState(''); const [position, setPosition] = useState('TOP'); const latestKey = useRef('')
  useEffect(() => { if (!sceneId || !member) { setData(null); return }; const controller = new AbortController(); const key = `${sceneId}:${member.id}:${lineIndices}:${position}`; latestKey.current = key; setData(null); setError(''); simulationDashboardApi.sceneDetail(sceneId, { execution_run_id: member.execution_run_id, capture_id: member.capture_id, run_option_id: member.run_option_id, mode: member.mode, component_id: member.component_id, basis: member.basis, line_indices: lineIndices, position }, controller.signal).then((next) => { if (!controller.signal.aborted && latestKey.current === key) setData(next) }).catch((reason) => { if (!controller.signal.aborted && latestKey.current === key) setError(errorText(reason)) }); return () => controller.abort() }, [lineIndices, member, position, sceneId])
  if (!sceneId || !member) return null
  if (error) return <State message={error} error />
  if (!data) return <State message="선택 Scene 상세를 불러오는 중입니다." />
  return <section className="simulation-dashboard__detail"><header><h3>Scene 상세 · {data.scene.label}</h3><label>위치 <select value={position} onChange={(event) => setPosition(event.target.value)} aria-label="Scene 상세 위치">{['TOP', 'BOT', 'LH', 'RH'].map((item) => <option key={item}>{item}</option>)}</select></label></header><div className="simulation-dashboard__chart"><ResponsiveContainer width="100%" height="100%"><LineChart><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="ref_coord" type="number" /><YAxis /><Tooltip /><Legend />{[1, 2, 3, 4].map((line) => <Line key={line} data={data.line_points.filter((point) => point.line_index === line)} dataKey="value" name={`L${line}`} type="linear" connectNulls={false} stroke={COLORS[line - 1]} dot={false} />)}</LineChart></ResponsiveContainer></div><div className="simulation-dashboard__detail-assets">{data.assets.map((asset) => <Media key={asset.asset_id} asset={asset} />)}</div>{data.quality_issues.length ? <Issues issues={data.quality_issues} /> : null}</section> }
function Media({ asset }: { asset: DashboardAsset }) { const [expanded, setExpanded] = useState(false); const [playError, setPlayError] = useState(''); const video = useRef<HTMLVideoElement>(null); const dialog = useRef<HTMLDialogElement>(null); const close = () => { dialog.current?.close(); setExpanded(false) }; useEffect(() => { setExpanded(false); setPlayError(''); video.current?.pause() }, [asset.asset_id]); useEffect(() => { if (expanded && dialog.current && !dialog.current.open) dialog.current.showModal() }, [expanded]); const toggle = async () => { if (!video.current) return; try { if (video.current.paused) await video.current.play(); else video.current.pause(); setPlayError('') } catch { setPlayError('영상을 재생하지 못했습니다.') } }; const frame = asset.frame_role === 'FINAL_FRAME' ? '최종 프레임' : '프레임 미확인'; const frameMeta = [frame, asset.frame_index == null ? null : `frame ${asset.frame_index}`, asset.time_value == null ? null : `t=${asset.time_value}${asset.time_unit ? ` ${asset.time_unit}` : ' · 단위 미확인'}`, ].filter(Boolean).join(' · '); return <div className="simulation-dashboard__media">{asset.kind === 'VIDEO' ? <video ref={video} controls src={assetUrl(asset)} aria-label={asset.title ?? '결과 영상'} /> : asset.status === 'READY' ? <img src={assetUrl(asset)} alt={asset.title ?? '결과 이미지'} /> : <span><ImageIcon />{statusText(asset.status)}</span>}<small title={frameMeta}>{frame}</small><div className="simulation-dashboard__media-actions">{asset.kind === 'VIDEO' ? <button type="button" aria-label="영상 재생 또는 일시정지" onClick={() => void toggle()}><Play /></button> : null}<button type="button" aria-label="자산 확대" onClick={() => setExpanded(true)}><Expand /></button></div>{playError ? <em role="alert">{playError}</em> : null}{expanded ? <dialog ref={dialog} className="simulation-dashboard__lightbox" onCancel={(event) => { event.preventDefault(); close() }} onClose={() => setExpanded(false)}><button type="button" onClick={close} aria-label="확대 보기 닫기">닫기</button>{asset.kind === 'VIDEO' ? <video controls autoPlay src={assetUrl(asset)} aria-label={asset.title ?? '확대 결과 영상'} /> : <img src={assetUrl(asset)} alt={asset.title ?? '결과 이미지 확대'} />}</dialog> : null}</div> }
const unprocessedReasons: Record<string, string> = { EXCLUDED_DIRECTORY: '제외된 디렉터리', UNSUPPORTED_EXTENSION: '미지원 확장자', UNKNOWN_LOAD_CASE_DIRECTORY: '알 수 없는 하중 경우 경로', UNEXPECTED_RESULT_PATH_DEPTH: '지원하지 않는 폴더 깊이', INCOMPLETE_RESULT_PATH: '결과 경로 불완전', INCOMPLETE_HIERARCHY_ASSIGNMENT: '폴더 계층 지정 불완전', UNSUPPORTED_DISTRIBUTION_FORMAT: '미지원 유통환경 형식', UNRECOGNIZED_RESULT_FILE: '인식하지 못한 결과 파일', OUTSIDE_CAPTURE_ROOT: '수집 범위 밖 파일' }
const issueLabels: Record<string, string> = { CASE_SCENE_ALIGNMENT_UNCONFIRMED: 'Case 간 Scene 대응 미확인', SOURCE_PARSE_ERROR: '원본 파일을 읽지 못함', DIRECTORY_UNAVAILABLE: '폴더를 읽을 수 없음', SCAN_DEPTH_LIMIT: '폴더 깊이 제한으로 일부 생략', SCAN_LIMIT_REACHED: '파일 수 제한으로 일부 생략', UNSAFE_DIRECTORY_SKIPPED: '안전하지 않은 폴더 건너뜀', OUTSIDE_CAPTURE_ROOT: '수집 범위 밖 파일' }
/** Korean text for a quality issue; unknown codes get a generic phrase, never the raw code. */
function issueText(issue: string) {
  if (!issue.startsWith('UNPROCESSED_FILE:')) return issueLabels[issue] ?? (/^[A-Z0-9_:]+$/.test(issue) ? '결과 확인 필요 항목' : issue)
  const source = issue.slice('UNPROCESSED_FILE:'.length); const marker = source.lastIndexOf(':')
  if (marker < 1) return `미처리 파일: ${source}`
  const path = source.slice(0, marker); const reason = source.slice(marker + 1)
  return `미처리 파일: ${path} · ${unprocessedReasons[reason] ?? (/^[A-Z0-9_]+$/.test(reason) ? '처리하지 않은 형식' : reason)}`
}
function Issues({ issues }: { issues: string[] }) { const texts = Array.from(new Set(issues.map(issueText))); return <aside className="simulation-dashboard__issues"><AlertTriangle />{texts.map((text) => <span key={text}>{text}</span>)}</aside> }
