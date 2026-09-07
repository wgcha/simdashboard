import { useEffect, useMemo, useRef, useState, type ComponentType } from 'react'
import { AlertTriangle, Database, LayoutDashboard, LoaderCircle, RefreshCw } from 'lucide-react'
import { Responsive, WidthProvider } from 'react-grid-layout'

import { api } from '../../api'
import type { DashboardDefinition, DashboardWidget, Overview, QualityThreshold } from '../../types'
import type { RequestResultLayout, ResultLayoutBindings, ResultScalarBinding } from '../../shared/api/resultLayouts'
import { ComparisonWorkspace, WidgetCard } from './AnalysisWidgets'
import { DropVideoGrid } from './DropVideoGrid'
import { resultLayoutPollDelay, resultWidgetMessage, resultWidgetState, shouldPollResultLayout } from './resultLayoutRuntime'

const ResponsiveGridLayout = WidthProvider(Responsive) as unknown as ComponentType<any>
const domainWidgetTypes = new Set<DashboardWidget['type']>(['edge_bar', 'time_series', 'scatter', 'contour', 'video', 'note', 'open_cell_map', 'open_cell_summary', 'chassis_summary', 'chassis_diagram', 'chassis_bar', 'chassis_table', 'run_comparison'])
const noOp = () => {}

function scalarForWidget(widget: DashboardWidget, bindings: ResultLayoutBindings) {
  const key = widget.settings?.variable_key ?? widget.settings?.binding_key
  return bindings.scalars.find((item) => item.variable_key === key) ?? bindings.scalars[0]
}

function scalarValue(scalar: ResultScalarBinding | undefined) {
  if (!scalar || scalar.value === null) return '값 없음'
  return `${scalar.value}${scalar.unit ? ` ${scalar.unit}` : ''}`
}

function BoundResult({ widget, bindings }: { widget: DashboardWidget; bindings: ResultLayoutBindings }) {
  const scalar = scalarForWidget(widget, bindings)
  if (widget.type === 'result_table') return <table className="result-layout-values"><thead><tr><th>결과</th><th>값</th><th>판정</th></tr></thead><tbody>{bindings.scalars.slice(0, 8).map((item) => <tr key={item.variable_key}><td>{item.display_name}</td><td>{scalarValue(item)}</td><td>{item.verdict ?? '-'}</td></tr>)}</tbody></table>
  if (['kpi', 'gauge', 'verdict'].includes(widget.type)) return <strong className="result-layout-value">{scalarValue(scalar)}{scalar?.verdict ? <small>{scalar.verdict}</small> : null}</strong>
  return <dl className="result-layout-summary-values"><div><dt>하중 경우</dt><dd>{bindings.load_cases[0]?.name ?? '-'}</dd></div><div><dt>결과 Run</dt><dd>{bindings.latest_result_run ? `#${bindings.latest_result_run.run_no} · ${bindings.latest_result_run.status}` : '-'}</dd></div><div><dt>대표 값</dt><dd>{scalarValue(scalar)}</dd></div></dl>
}

function DomainRenderer({ widget, overview, thresholds, loadCaseId, currentRunId }: { widget: DashboardWidget; overview: Overview; thresholds: QualityThreshold[]; loadCaseId: string; currentRunId: string }) {
  if (widget.type === 'run_comparison') return <ComparisonWorkspace loadCaseId={loadCaseId} currentRunId={currentRunId} />
  const settings = { ...widget.settings, variableId: widget.settings?.variableId ?? widget.settings?.variable_key }
  return <WidgetCard widget={{ ...widget, settings }} overview={overview} selectedEdges={['top', 'bottom', 'left', 'right']} editMode={false} canManageThresholds={false} threshold={thresholds.find((item) => item.analysis_key === 'chassis_rear')} openCellThreshold={thresholds.find((item) => item.analysis_key === 'open_cell')} onSaveThreshold={noOp} onSaveOpenCellThreshold={noOp} onRemove={noOp} onConfigure={noOp} />
}

function SnapshotWidget({ widget, requiredDataContracts, bindings, overview, thresholds, domainError }: { widget: DashboardWidget; requiredDataContracts: string[]; bindings: ResultLayoutBindings; overview: Overview | null; thresholds: QualityThreshold[]; domainError: string }) {
  const state = resultWidgetState(widget, requiredDataContracts, bindings)
  const loadCaseId = bindings.load_cases[0]?.id ?? ''
  return <article className={`result-layout-widget state-${state.toLowerCase()}`} data-testid={`result-layout-widget-${widget.id}`}>
    <header><div><small>{widget.type.replaceAll('_', ' ')}</small><h4>{widget.title}</h4></div><span className="result-layout-widget-state">{state === 'WAITING' ? '결과 대기' : state}</span></header>
    <Database aria-hidden="true" />
    {state !== 'READY' ? <p>{resultWidgetMessage(state)}</p>
      : widget.type === 'video_grid' && loadCaseId ? <DropVideoGrid loadCaseId={loadCaseId} pageSize={Number(widget.settings?.pageSize ?? 20)} />
        : domainWidgetTypes.has(widget.type) && overview && loadCaseId ? <DomainRenderer widget={widget} overview={overview} thresholds={thresholds} loadCaseId={loadCaseId} currentRunId={bindings.latest_result_run?.id ?? ''} />
          : domainWidgetTypes.has(widget.type) ? <p>{domainError || '전용 결과 렌더러 데이터를 불러오는 중입니다.'}</p>
            : <BoundResult widget={widget} bindings={bindings} />}
  </article>
}

function EmptyLayout({ canOpenData, onOpenData }: { canOpenData: boolean; onOpenData: () => void }) {
  return <section className="result-layout-empty" data-testid="result-layout-unconfigured"><LayoutDashboard aria-hidden="true" /><h2>결과 구성 미지정</h2><p>이 의뢰는 결과 레이아웃 없이 접수되었습니다. 특정 분석 유형을 추정하지 않으며, 하중 경우와 결과를 등록한 뒤 작업 유형의 결과 구성을 버전으로 지정할 수 있습니다.</p>{canOpenData ? <button type="button" onClick={onOpenData}>하중 경우·결과 설정</button> : null}</section>
}

export function GenericResultLayoutWorkspace({ projectId, projectName, requestId, requestTitle, selectedLoadCaseId, canOpenData = false, onOpenData, onLoadLayout, onSnapshotPageChange }: { projectId: string; projectName: string; requestId: string; requestTitle: string; selectedLoadCaseId?: string; canOpenData?: boolean; onOpenData: () => void; onLoadLayout: (requestId: string, loadCaseId?: string) => Promise<RequestResultLayout>; onSnapshotPageChange?: (page: DashboardDefinition) => void }) {
  const [layout, setLayout] = useState<RequestResultLayout | null>(null)
  const [activePageId, setActivePageId] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reloadToken, setReloadToken] = useState(0)
  const [domainOverview, setDomainOverview] = useState<Overview | null>(null)
  const [domainThresholds, setDomainThresholds] = useState<QualityThreshold[]>([])
  const [domainError, setDomainError] = useState('')
  const loaderRef = useRef(onLoadLayout)
  loaderRef.current = onLoadLayout

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined
    let polling = false
    let fetching = false
    let pollAttempt = 0
    const clearTimer = () => { if (timer !== undefined) { window.clearTimeout(timer); timer = undefined } }
    const schedule = () => {
      clearTimer()
      if (cancelled || !polling) return
      timer = window.setTimeout(() => void load(false), resultLayoutPollDelay(pollAttempt++, document.visibilityState !== "visible"))
    }
    const load = async (initial: boolean) => {
      if (fetching) return
      fetching = true
      if (initial) { setLoading(true); setError("") }
      try {
        const result = await loaderRef.current(requestId, selectedLoadCaseId || undefined)
        if (cancelled) return
        setLayout(result)
        setActivePageId((current) => result.snapshot?.pages.some((page) => page.id === current) ? current : result.snapshot?.pages[0]?.id ?? "")
        polling = shouldPollResultLayout(result)
        if (!polling) pollAttempt = 0
      } catch (reason) {
        if (!cancelled) { polling = false; setError(reason instanceof Error ? reason.message : "결과 레이아웃 snapshot을 불러오지 못했습니다.") }
      } finally {
        fetching = false
        if (!cancelled) { if (initial) setLoading(false); schedule() }
      }
    }
    const resumeOnVisible = () => {
      if (document.visibilityState !== "visible" || !polling || fetching) return
      clearTimer(); void load(false)
    }
    document.addEventListener("visibilitychange", resumeOnVisible)
    void load(true)
    return () => { cancelled = true; clearTimer(); document.removeEventListener("visibilitychange", resumeOnVisible) }
  }, [requestId, selectedLoadCaseId, reloadToken])

  const snapshot = layout?.snapshot
  const activePage = snapshot?.pages.find((page) => page.id === activePageId) ?? snapshot?.pages[0]
  const bindings: ResultLayoutBindings = layout?.bindings ?? { available_data_contracts: [], load_cases: [], latest_result_run: null, scalars: [], error: null }
  useEffect(() => { if (activePage) onSnapshotPageChange?.(activePage) }, [activePage, onSnapshotPageChange])
  const needsDomainRenderer = Boolean(activePage?.widgets.some((widget) => domainWidgetTypes.has(widget.type) && resultWidgetState(widget, snapshot?.required_data_contracts ?? [], bindings) === 'READY'))
  const domainLoadCaseId = bindings.load_cases[0]?.id ?? ''

  useEffect(() => {
    let cancelled = false
    setDomainOverview(null); setDomainThresholds([]); setDomainError('')
    if (!needsDomainRenderer || !domainLoadCaseId) return () => { cancelled = true }
    Promise.all([api.overview(domainLoadCaseId), api.qualityThresholds(projectId)])
      .then(([overview, thresholds]) => { if (!cancelled) { setDomainOverview(overview); setDomainThresholds(thresholds) } })
      .catch((reason) => { if (!cancelled) setDomainError(reason instanceof Error ? reason.message : '전용 결과 렌더러 데이터를 불러오지 못했습니다.') })
    return () => { cancelled = true }
  }, [domainLoadCaseId, needsDomainRenderer, projectId])

  const gridLayouts = useMemo(() => ({ lg: (activePage?.widgets ?? []).map((widget) => ({ i: widget.id, x: widget.x, y: widget.y, w: widget.w, h: widget.h })), md: (activePage?.widgets ?? []).map((widget) => { const w = Math.min(widget.w, 6); return { i: widget.id, x: Math.min(widget.x, 6 - w), y: widget.y, w, h: widget.h } }), sm: (activePage?.widgets ?? []).map((widget) => ({ i: widget.id, x: 0, y: widget.y, w: 1, h: widget.h })) }), [activePage])

  if (loading) return <section className="canvas-area result-layout-workspace" data-testid="pending-analysis-workspace"><div className="result-layout-empty"><LoaderCircle className="spin" /><h2>상세 분석 구성 확인 중</h2><p>의뢰 생성 시점에 고정된 결과 레이아웃을 불러오고 있습니다.</p></div></section>
  if (error) return <section className="canvas-area result-layout-workspace" data-testid="pending-analysis-workspace"><div className="result-layout-error"><AlertTriangle /><h2>결과 레이아웃을 불러오지 못했습니다.</h2><p>{error}</p><button type="button" onClick={() => setReloadToken((value) => value + 1)}><RefreshCw /> 다시 시도</button></div></section>
  if (!snapshot || !activePage) return <section className="canvas-area result-layout-workspace" data-testid="pending-analysis-workspace"><header className="result-layout-head"><div><small>DETAILED ANALYSIS · RESULT PENDING</small><h2>상세 분석</h2><p>{projectName} · {requestTitle}</p></div><span>UNCONFIGURED</span></header><EmptyLayout canOpenData={canOpenData} onOpenData={onOpenData} /></section>

  return <section className="canvas-area result-layout-workspace" data-testid="pending-analysis-workspace"><header className="result-layout-head"><div><small>DETAILED ANALYSIS · TEMPLATE v{snapshot.template_version}</small><h2>상세 분석</h2><p><strong>{snapshot.template_name}</strong> · {projectName} · {requestTitle} · 작업 유형 v{layout?.source_request_type_version ?? snapshot.request_type_version} · 의뢰 생성 시점 결과 레이아웃</p></div><span>{bindings.error ? 'RESULT FAILED' : bindings.latest_result_run ? `최신 Run #${bindings.latest_result_run.run_no} · 결과 보유` : '결과 대기'}</span></header><nav className="result-layout-page-tabs" aria-label="상세 분석 결과 페이지">{snapshot.pages.map((page) => <button type="button" key={page.id} className={page.id === activePage.id ? 'active' : ''} aria-current={page.id === activePage.id ? 'page' : undefined} onClick={() => setActivePageId(page.id)}>{page.name}</button>)}</nav><header className="result-layout-page-head"><div><span>12-COLUMN SNAPSHOT</span><h3>{activePage.name}</h3><p>{activePage.description}</p></div><span>{activePage.widgets.length} WIDGETS</span></header><ResponsiveGridLayout className="layout result-layout-grid" layouts={gridLayouts} breakpoints={{ lg: 900, md: 600, sm: 0 }} cols={{ lg: 12, md: 6, sm: 1 }} rowHeight={70} margin={[14, 14]} isDraggable={false} isResizable={false} compactType="vertical">{activePage.widgets.map((widget) => <div key={widget.id}><SnapshotWidget widget={widget} requiredDataContracts={snapshot.required_data_contracts} bindings={bindings} overview={domainOverview} thresholds={domainThresholds} domainError={domainError} /></div>)}</ResponsiveGridLayout></section>
}
