import { useEffect, useMemo, useRef, useState, type ReactElement } from 'react'
import { AlertTriangle, FileArchive, Layers3 } from 'lucide-react'
import { simulationDashboardApi, type DashboardCatalog, type DashboardDistribution, type DashboardEnvironment, type UsageDashboard } from '../../shared/api/simulationDashboard'
import { Select } from '../../shared/components/Select'
import { CaseFinalizationPanel } from './CaseFinalizationPanel'
import { SceneNameWarningIcon } from './FolderNameWarnings'
import type { CaseReportFinalScope } from './caseReport/caseReport'
import {
  COMPARE_MAX_CASES, COMPARE_MIN_CASES, COMPARE_MISSING, buildDistributionCompare, buildUsageCompare, compareCandidates, compareDeltaText, compareUnitText, compareValueText,
  defaultCompareSelection, distributionCompareReport, resolveCompareMember, unitMismatchText, usageCompareReport,
  type CaseCompareReport, type CompareCandidate, type ComparePath, type DistributionCompareCell, type DistributionCompareModel, type UsageCompareModel,
} from './caseCompare'
import './CaseCompareView.css'

type Props = {
  projectId: string
  requestId: string
  environment: DashboardEnvironment
  catalog: DashboardCatalog
  /** Catalog id of the Case selected in the path bar (default baseline). */
  currentCaseId: string
  /** 유통환경: the path bar's selection every Case is matched by (null until complete). */
  path: ComparePath | null
  /** Shown instead of the table while `path` is null. */
  pathHint: string
  /** Labels of the path for the report and the toolbar. */
  pathRows: Array<{ label: string; value: string }>
  canFinalize: boolean
  /** The current comparison as a frozen report table (null while incomplete or unmounted). */
  onReport: (report: CaseCompareReport | null) => void
}

type Loaded = { status: 'ok'; data: DashboardDistribution | UsageDashboard } | { status: 'error'; message: string } | { status: 'unmatched'; reason: string }

function errorText(reason: unknown) { return reason instanceof Error && reason.message ? reason.message : '비교 결과를 불러오지 못했습니다.' }

function State({ message, error = false }: { message: string; error?: boolean }) {
  return <div className={`simulation-dashboard__state ${error ? 'error' : ''}`} role={error ? 'alert' : 'status'}>{error ? <AlertTriangle /> : <Layers3 />}<span className="case-prose">{message}</span></div>
}

/** W5 Case 비교: 2–4 Cases side by side from each Case's merged latest result. */
export function CaseCompareView({ projectId, requestId, environment, catalog, currentCaseId, path, pathHint, pathRows, canFinalize, onReport }: Props) {
  const candidates = useMemo(() => compareCandidates(catalog), [catalog])
  const candidateKey = candidates.map((item) => item.id).join('|')
  const [selected, setSelected] = useState<string[]>(() => defaultCompareSelection(candidates))
  const [baselineChoice, setBaselineChoice] = useState('')
  const [finalTarget, setFinalTarget] = useState<{ id: string; token: number } | null>(null)
  const [cache, setCache] = useState<Map<string, Loaded>>(() => new Map())
  const cacheRef = useRef(cache)
  // Keep the selection across catalog refreshes; reset it when the Case list no longer fits.
  useEffect(() => {
    setSelected((current) => {
      const valid = current.filter((id) => candidates.some((item) => item.id === id))
      return valid.length >= Math.min(COMPARE_MIN_CASES, candidates.length) ? valid : defaultCompareSelection(candidates)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidateKey])
  const columns = useMemo(() => candidates.filter((item) => selected.includes(item.id)), [candidates, selected])
  const baselineId = columns.some((item) => item.id === baselineChoice) ? baselineChoice : columns.some((item) => item.id === currentCaseId) ? currentCaseId : columns[0]?.id ?? ''
  const pathKey = environment === 'USAGE' ? 'usage' : path ? JSON.stringify(path) : ''
  const keyOf = (candidate: CompareCandidate) => `${candidate.id}|${candidate.captureId}|${pathKey}`

  // A new catalog (refresh) drops the read results.
  useEffect(() => { cacheRef.current = new Map(); setCache(new Map()) }, [catalog])
  useEffect(() => {
    if (!pathKey) return
    const controller = new AbortController()
    const store = (key: string, value: Loaded) => {
      if (controller.signal.aborted) return
      const next = new Map(cacheRef.current); next.set(key, value); cacheRef.current = next; setCache(next)
    }
    for (const candidate of columns) {
      const key = keyOf(candidate)
      if (cacheRef.current.has(key)) continue
      if (environment === 'USAGE') {
        simulationDashboardApi.usage(candidate.dashboardCaseId, candidate.captureId, undefined, undefined, controller.signal)
          .then((data) => store(key, { status: 'ok', data })).catch((reason) => store(key, { status: 'error', message: errorText(reason) }))
        continue
      }
      const resolved = resolveCompareMember(catalog, candidate, path!)
      if ('reason' in resolved) { store(key, { status: 'unmatched', reason: resolved.reason }); continue }
      const { member } = resolved
      simulationDashboardApi.distribution(member.runId, { capture_id: member.captureId, run_option_id: member.optionId, mode: member.mode, component_id: member.componentId, basis: path!.basis, edge_keys: path!.edgeKeys, line_indices: path!.lineIndices }, controller.signal)
        .then((data) => store(key, { status: 'ok', data })).catch((reason) => store(key, { status: 'error', message: errorText(reason) }))
    }
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog, columns, environment, pathKey])

  const loaded = columns.map((candidate) => cache.get(keyOf(candidate)))
  const ready = Boolean(pathKey) && columns.length >= COMPARE_MIN_CASES && loaded.every(Boolean)
  const inputs = columns.map((candidate, index) => {
    const result = loaded[index]
    return { id: candidate.id, label: candidate.label, data: result?.status === 'ok' ? result.data : null, reason: result?.status === 'unmatched' ? result.reason : result?.status === 'error' ? `읽기 오류: ${result.message}` : '' }
  })
  const distributionModel = useMemo(() => ready && environment === 'DISTRIBUTION' ? buildDistributionCompare(inputs as Array<{ id: string; label: string; data: DashboardDistribution | null; reason: string }>, baselineId, path?.edgeKeys ?? '') : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [ready, environment, cache, columns, baselineId, path?.edgeKeys])
  const usageModel = useMemo(() => ready && environment === 'USAGE' ? buildUsageCompare(inputs as Array<{ id: string; label: string; data: UsageDashboard | null; reason: string }>, baselineId) : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [ready, environment, cache, columns, baselineId])
  const report = useMemo(() => distributionModel && !distributionModel.noEdge ? distributionCompareReport(distributionModel, pathRows) : usageModel ? usageCompareReport(usageModel) : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [distributionModel, usageModel, JSON.stringify(pathRows)])
  useEffect(() => { onReport(report) }, [onReport, report])
  useEffect(() => () => onReport(null), [onReport])

  const toggle = (id: string, checked: boolean) => setSelected((current) => checked ? (current.length >= COMPARE_MAX_CASES || current.includes(id) ? current : [...current, id]) : current.filter((item) => item !== id))
  const designate = (id: string) => setFinalTarget((current) => ({ id, token: (current?.token ?? 0) + 1 }))
  const target = candidates.find((item) => item.id === finalTarget?.id)
  const finalScope = (candidate: CompareCandidate): CaseReportFinalScope => environment === 'USAGE'
    ? { source: { kind: 'case_usage', projectId, requestId, caseId: candidate.dashboardCaseId, captureId: candidate.captureId, referenceCaseId: '', referenceCaptureId: '' }, labels: { project: '', request: '', caseLabel: candidate.label, reference: '' } }
    : { source: { kind: 'case_final', projectId, requestId, caseId: candidate.dashboardCaseId, captureId: candidate.captureId, catalogCaseId: candidate.id, basis: path?.basis ?? '', edgeKeys: path?.edgeKeys ?? '', lineIndices: path?.lineIndices ?? '' }, labels: { project: '', request: '', caseLabel: candidate.label, component: path?.component ?? '' } }
  const finalButton = (id: string, label: string) => <button type="button" className="case-compare__final-button" disabled={!canFinalize} title={canFinalize ? `${label}을(를) Final로 지정합니다.` : 'Final 지정 권한이 있는 사용자만 실행할 수 있습니다.'} aria-label={`${label}을(를) Final 지정`} onClick={() => designate(id)}><FileArchive aria-hidden="true" />이 Case를 Final 지정</button>

  if (candidates.length < COMPARE_MIN_CASES) return <State message="비교하려면 결과가 있는 Case가 2개 이상 필요합니다." />
  const body = columns.length < COMPARE_MIN_CASES ? <State message="비교할 Case를 2개 이상 고르세요." />
    : !pathKey ? <State message={pathHint} />
    : !ready ? <State message="비교 결과를 불러오는 중입니다." />
    : distributionModel ? (distributionModel.noEdge ? <State message="선택 없음: 표시 옵션에서 엣지를 하나 이상 선택하세요." /> : <DistributionTable model={distributionModel} finalButton={finalButton} />)
    : usageModel ? <UsageTable model={usageModel} finalButton={finalButton} /> : null
  const mismatch = distributionModel ? unitMismatchText(distributionModel) : ''
  return <div className="case-compare" data-testid="case-compare">
    <div className="case-compare__toolbar">
      <fieldset className="case-compare__cases">
        <legend>비교할 Case ({COMPARE_MIN_CASES}~{COMPARE_MAX_CASES}개)</legend>
        {candidates.map((item) => {
          const checked = selected.includes(item.id)
          return <label key={item.id} title={item.label}><input type="checkbox" checked={checked} disabled={!checked && selected.length >= COMPARE_MAX_CASES} onChange={(event) => toggle(item.id, event.target.checked)} />{item.label}</label>
        })}
      </fieldset>
      <label className="case-compare__baseline"><span>기준 Case</span><Select controlSize="sm" value={baselineId} disabled={columns.length < 1} onChange={(event) => setBaselineChoice(event.target.value)}>{columns.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</Select></label>
      {environment === 'DISTRIBUTION' && pathRows.length ? <dl className="case-compare__path" aria-label="비교 경로">{pathRows.map((row) => <div key={row.label}><dt>{row.label}</dt><dd title={row.value}>{row.value || '없음'}</dd></div>)}</dl> : null}
    </div>
    {distributionModel && !distributionModel.noEdge ? mismatch
      ? <p className="case-compare__unit case-compare__unit--warning" role="alert" data-testid="case-compare-unit-warning"><AlertTriangle aria-hidden="true" />{mismatch}</p>
      : <p className="case-compare__unit">단위: {distributionModel.units.map(compareUnitText).join(', ') || '단위 미확인'} · Δ는 기준 Case 대비 · 최저는 Scene별 가장 낮은 값</p> : null}
    {body}
    {target ? <div className="case-compare__final" role="group" aria-label={`Final 지정 대상 ${target.label}`} data-testid="case-compare-final">
      <span>Final 지정 대상 · <strong>{target.label}</strong></span>
      <CaseFinalizationPanel key={target.id} projectId={projectId} requestId={requestId} environment={environment} caseId={target.dashboardCaseId} captureId={target.captureId} hasCapturedCase canFinalize={canFinalize} reportScope={finalScope(target)} openRequest={finalTarget?.token} />
    </div> : null}
  </div>
}

type FinalButton = (id: string, label: string) => ReactElement

function ColumnHead({ column, finalButton, unit }: { column: { id: string; label: string; baseline: boolean; reason: string }; finalButton: FinalButton; unit?: string }) {
  return <th scope="col" data-case-id={column.id} className={column.baseline ? 'is-baseline' : undefined}>
    <div className="case-compare__col-head">
      <strong title={column.label}>{column.label}</strong>
      {column.baseline ? <span className="case-compare__tag case-compare__tag--baseline">기준</span> : null}
      {unit !== undefined ? <small>{unit}</small> : null}
      {column.reason ? <small className="case-compare__reason" title={column.reason}>{column.reason}</small> : null}
      {finalButton(column.id, column.label)}
    </div>
  </th>
}

function ValueCell({ cell, baseline }: { cell: DistributionCompareCell; baseline: boolean }) {
  if (cell.missing) return <td className="case-compare__cell is-missing" data-missing="true"><span className="case-compare__missing">{COMPARE_MISSING}</span></td>
  const delta = baseline ? '' : compareDeltaText(cell)
  return <td className={`case-compare__cell${cell.best ? ' is-best' : ''}${cell.columnMax ? ' is-max' : ''}`} data-best={cell.best || undefined}>
    <strong>{compareValueText(cell)}</strong>
    {delta ? <small className={`case-compare__delta${(cell.delta ?? 0) > 0 ? ' is-up' : (cell.delta ?? 0) < 0 ? ' is-down' : ''}`}>{delta}</small> : null}
    {cell.best || cell.columnMax ? <span className="case-compare__tags">{cell.best ? <span className="case-compare__tag case-compare__tag--best">최저</span> : null}{cell.columnMax ? <span className="case-compare__tag case-compare__tag--max">Case 최대</span> : null}</span> : null}
  </td>
}

function DistributionTable({ model, finalButton }: { model: DistributionCompareModel; finalButton: FinalButton }) {
  return <div className="case-compare__table-wrap">
    <table className="case-compare__table" data-testid="case-compare-distribution">
      <caption className="case-sr-only">Scene별 Case 비교 · 선택 엣지 최대응력</caption>
      <thead><tr><th scope="col">Scene</th>{model.columns.map((column) => <ColumnHead key={column.id} column={column} finalButton={finalButton} unit={column.hasData ? compareUnitText(column.unit) : undefined} />)}</tr></thead>
      <tbody>{model.rows.map((row) => <tr key={row.key} data-scene={row.label} className={row.missingIn.length ? 'has-missing' : undefined}>
        <th scope="row"><span className="case-compare__scene">{row.sequence != null ? <b>{row.sequence}</b> : null}<span title={row.label}>{row.label}</span><SceneNameWarningIcon label={row.label} /></span>{row.missingIn.length ? <small className="case-compare__missing-note">{row.missingIn.join(', ')}에 없음</small> : null}</th>
        {row.cells.map((cell, index) => <ValueCell key={model.columns[index].id} cell={cell} baseline={model.columns[index].baseline} />)}
      </tr>)}</tbody>
      <tfoot><tr data-scene="__summary__"><th scope="row">Case 최대 (Scene 전체)</th>{model.summary.map((cell, index) => <ValueCell key={model.columns[index].id} cell={{ ...cell, columnMax: false }} baseline={model.columns[index].baseline} />)}</tr></tfoot>
    </table>
  </div>
}

function UsageTable({ model, finalButton }: { model: UsageCompareModel; finalButton: FinalButton }) {
  return <div className="case-compare__table-wrap">
    <table className="case-compare__table" data-testid="case-compare-usage">
      <caption className="case-sr-only">다섯 평가 Case 비교</caption>
      <thead><tr><th scope="col">평가</th>{model.columns.map((column) => <ColumnHead key={column.id} column={column} finalButton={finalButton} />)}</tr></thead>
      <tbody>{model.rows.map((row) => <tr key={row.key} data-evaluation={row.key}>
        <th scope="row"><strong>{row.label}</strong>{row.sourceKey ? <small className="usage-source-key" title={row.sourceKey}>{row.sourceKey}</small> : null}</th>
        {row.cells.map((cell, index) => cell.missing
          ? <td key={model.columns[index].id} className="case-compare__cell is-missing" data-missing="true"><span className="case-compare__missing">{COMPARE_MISSING}</span></td>
          : <td key={model.columns[index].id} className="case-compare__cell">{cell.lines.length ? cell.lines.map((line) => <div key={line.direction} className={`case-compare__usage-line is-${line.tone}`} data-tone={line.tone}><span>{line.direction}</span><strong>{line.text}</strong>{line.label ? <small>{line.label}</small> : null}</div>) : <span className="case-compare__missing-text">해당 없음</span>}</td>)}
      </tr>)}</tbody>
    </table>
  </div>
}
