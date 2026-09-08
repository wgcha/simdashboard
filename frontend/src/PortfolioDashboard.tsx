import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { AlertTriangle, ArrowLeft, ArrowRight, BarChart3, CheckCircle2, Database, Download, GripVertical, LoaderCircle, Minus, Plus, RotateCcw, Search, SlidersHorizontal } from 'lucide-react'
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from './api'
import type { PortfolioLayout, PortfolioOverview } from './types'
import { ResultOverviewDashboard } from './features/result-overview/ResultOverviewDashboard'
import { useMemoryQuery } from './shared/cache/useMemoryQuery'

const COLORS = ['#50d5ff', '#70e0a8', '#ffbf57', '#ff647d', '#8b9cff']
const STATUS_LABEL: Record<string, string> = { READY: '대기', IN_PROGRESS: '진행 중', COMPLETED: '완료', BLOCKED: '차단', FAILED: '실패' }
const CHART_LABELS: Record<string, string> = { trend: '의뢰·완료·실패 추이', status: '의뢰 상태 분포', quality: '해석 유형별 품질', type: '해석 유형 구성' }

export function PortfolioDashboard({ refreshToken, editMode, layout, layoutVersion, onLayoutChange, onCancelEdit, onResetLayout, onOpen, onOpenResult }: { refreshToken: number; editMode: boolean; layout: PortfolioLayout; layoutVersion: number; onLayoutChange: (layout: PortfolioLayout) => void; onCancelEdit: () => void; onResetLayout: () => void; onOpen: (projectId: string, requestId: string, loadCaseId: string) => void; onOpenResult?: (projectId: string, requestId: string, loadCaseId: string, runId: string) => void }) {
  const [showOperations, setShowOperations] = useState(false)
  const [draggedChart, setDraggedChart] = useState('')
  const [filters, setFilters] = useState({ date_from: '', date_to: '', project_id: '', analysis_type: '', status: '', search: '' })
  const [debouncedSearch, setDebouncedSearch] = useState(filters.search)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(filters.search), 220)
    return () => window.clearTimeout(timer)
  }, [filters.search])
  const queryString = useMemo(() => {
    const value = new URLSearchParams()
    Object.entries({ ...filters, search: debouncedSearch }).forEach(([key, entry]) => entry && value.set(key, entry))
    return value.toString()
  }, [debouncedSearch, filters])
  const params = useMemo(() => new URLSearchParams(queryString), [queryString])
  const query = useCallback(() => api.portfolio(new URLSearchParams(queryString)), [queryString])
  const { data, error, isLoading: loading, isRefreshing, isBlocked, isForbidden, retry } = useMemoryQuery<PortfolioOverview>({
    key: `portfolio:${queryString}`,
    query,
  })
  const previousRefresh = useRef(refreshToken)
  const filterProjects = useRef<PortfolioOverview['filter_options']['projects']>([])
  if (isBlocked) filterProjects.current = []
  else if (data) filterProjects.current = data.filter_options.projects
  useEffect(() => {
    if (previousRefresh.current === refreshToken) return
    previousRefresh.current = refreshToken
    retry()
  }, [refreshToken, retry])

  const set = (key: keyof typeof filters, value: string) => setFilters((current) => ({ ...current, [key]: value }))
  const reset = () => setFilters({ date_from: '', date_to: '', project_id: '', analysis_type: '', status: '', search: '' })
  const reorderChart = (target: string, source = draggedChart) => {
    if (!source || source === target) return
    const next = layout.chartOrder.filter((item) => item !== source)
    const targetIndex = next.indexOf(target)
    next.splice(targetIndex < 0 ? next.length : targetIndex, 0, source)
    onLayoutChange({ ...layout, chartOrder: next })
    setDraggedChart('')
  }
  const moveChart = (id: string, offset: -1 | 1) => {
    const index = layout.chartOrder.indexOf(id)
    const target = index + offset
    if (index < 0 || target < 0 || target >= layout.chartOrder.length) return
    const next = [...layout.chartOrder]
    ;[next[index], next[target]] = [next[target], next[index]]
    onLayoutChange({ ...layout, chartOrder: next })
  }
  const changeFontSize = (value: number) => onLayoutChange({ ...layout, fontSize: Math.max(8, Math.min(18, value)) })
  if (!showOperations && !editMode && (data || loading)) return <div className="portfolio-page result-overview-shell" style={{ padding: 0 }}><ResultOverviewDashboard data={data ?? { records: [], filter_options: { projects: filterProjects.current } }} loading={loading || filters.search !== debouncedSearch} projectId={filters.project_id} search={filters.search} onProjectChange={(value) => set('project_id', value)} onSearchChange={(value) => set('search', value)} onOpenResult={(record) => { if (record.run_id && onOpenResult) onOpenResult(record.project_id, record.request_id, record.load_case_id, record.run_id); else onOpen(record.project_id, record.request_id, record.load_case_id) }} onOpenRequest={(record) => onOpen(record.project_id, record.request_id, record.load_case_id)} onShowOperations={() => setShowOperations(true)} />{error && <div className="portfolio-refresh error" role="alert"><AlertTriangle /> {error.message}<button onClick={retry}>다시 시도</button></div>}</div>
  if (!data && loading) return <div className="portfolio-state"><LoaderCircle className="spin" /> 운영 데이터를 집계하고 있습니다.</div>
  if (!data) return <div className="portfolio-state error"><AlertTriangle /> {error?.message || (isBlocked ? '접근 권한이 변경되어 운영 데이터를 다시 불러올 수 없습니다.' : '운영 현황을 표시할 수 없습니다.')}{(!isBlocked || isForbidden) && <button onClick={retry}>다시 시도</button>}<button onClick={reset}>필터 초기화</button></div>


  const renderedFontSize = Math.max(8, layout.fontSize) * 1.2
  const chartFontSize = renderedFontSize
  const chartCards: Record<string, ReactNode> = {
    trend: <ChartCard title="의뢰·완료·실패 추이" subtitle="의뢰 접수일 기준"><ResponsiveContainer width="100%" height="100%"><AreaChart data={data.trend}><CartesianGrid stroke="var(--color-chart-grid)" vertical={false} /><XAxis dataKey="date" tick={{ fill:'var(--color-chart-axis)', fontSize:chartFontSize }} /><YAxis allowDecimals={false} tick={{ fill:'var(--color-chart-axis)', fontSize:chartFontSize }} /><Tooltip /><Legend /><Area dataKey="requests" name="하중 경우" stroke="#50d5ff" fill="#50d5ff22" /><Area dataKey="completed" name="결과 보유" stroke="#70e0a8" fill="#70e0a822" /><Area dataKey="failed" name="FAIL" stroke="#ff647d" fill="#ff647d22" /></AreaChart></ResponsiveContainer></ChartCard>,
    status: <ChartCard title="의뢰 상태 분포" subtitle="현재 상태"><ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={data.status_distribution} dataKey="value" nameKey="name" innerRadius="52%" outerRadius="76%" paddingAngle={3}>{data.status_distribution.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}</Pie><Tooltip formatter={(v, n) => [v, STATUS_LABEL[String(n)] ?? n]} /><Legend formatter={(v) => STATUS_LABEL[String(v)] ?? v} /></PieChart></ResponsiveContainer></ChartCard>,
    quality: <ChartCard title="해석 유형별 품질" subtitle="최신 실행 판정"><ResponsiveContainer width="100%" height="100%"><BarChart data={data.quality_by_type}><CartesianGrid stroke="var(--color-chart-grid)" vertical={false} /><XAxis dataKey="type" tick={{ fill:'var(--color-chart-axis)', fontSize:chartFontSize }} /><YAxis allowDecimals={false} tick={{ fontSize:chartFontSize }} /><Tooltip /><Legend /><Bar dataKey="pass" name="PASS" stackId="a" fill="#70e0a8" /><Bar dataKey="fail" name="FAIL" stackId="a" fill="#ff647d" /><Bar dataKey="no_data" name="NO DATA" stackId="a" fill="var(--color-text-muted)" /></BarChart></ResponsiveContainer></ChartCard>,
    type: <ChartCard title="해석 유형 구성" subtitle="필터 적용 결과"><ResponsiveContainer width="100%" height="100%"><BarChart data={data.type_distribution} layout="vertical"><CartesianGrid stroke="var(--color-chart-grid)" horizontal={false} /><XAxis type="number" allowDecimals={false} tick={{ fontSize:chartFontSize }} /><YAxis dataKey="name" type="category" width={90} tick={{ fill:'var(--color-chart-axis)', fontSize:chartFontSize }} /><Tooltip /><Bar dataKey="value" name="하중 경우" fill="#50d5ff" radius={[0,5,5,0]} /></BarChart></ResponsiveContainer></ChartCard>,
  }

  return <div className="portfolio-page" data-custom-font="true" style={{ '--portfolio-font-size': `${renderedFontSize}px` } as CSSProperties}>
    <button className="result-overview-return" disabled={editMode} onClick={() => { setShowOperations(false); setFilters((current) => ({ ...current, date_from: '', date_to: '', analysis_type: '', status: '' })) }}><ArrowLeft /> 결과 대시보드</button>
    <header className="portfolio-head"><div><span>ANALYSIS OPERATIONS</span><h1>해석 운영 현황</h1><p>프로젝트부터 최신 해석 판정까지 한 화면에서 추적합니다.</p></div><div><small>데이터 기준</small><strong>{data.grain.replaceAll('_', ' ')}</strong><span>최근 결과 {data.freshness ? new Date(data.freshness).toLocaleString('ko-KR') : '없음'}</span></div></header>
    {editMode && <section className="portfolio-edit-toolbar" data-testid="portfolio-layout-editor">
      <GripVertical />
      <span><strong>운영 대시보드 편집 · v{layoutVersion}</strong> 카드의 이동 버튼이나 손잡이를 사용하고, 상단의 운영 설정 저장으로 새 버전을 만드세요.</span>
      <div className="portfolio-font-control"><span>글자 크기</span><button aria-label="글자 크기 줄이기" onClick={() => changeFontSize(layout.fontSize - 1)} disabled={layout.fontSize <= 8}><Minus /></button><input aria-label="운영 대시보드 글자 크기" type="range" min="8" max="18" step="1" value={layout.fontSize} onChange={(event)=>changeFontSize(Number(event.target.value))}/><button aria-label="글자 크기 늘리기" onClick={() => changeFontSize(layout.fontSize + 1)} disabled={layout.fontSize >= 18}><Plus /></button><output>{layout.fontSize}px</output></div>
      <button className="portfolio-reset-button" onClick={onResetLayout}><RotateCcw /> 기본값</button>
      <button onClick={onCancelEdit}>편집 취소</button>
    </section>}
    <section className="portfolio-filters">
      <label className="portfolio-search"><Search /><input aria-label="상세 검색" value={filters.search} onChange={(e) => set('search', e.target.value)} placeholder="프로젝트, 제품, 의뢰, 작업자 검색" /></label>
      <label><span>시작일</span><input aria-label="시작일" type="date" value={filters.date_from} onChange={(e) => set('date_from', e.target.value)} /></label>
      <label><span>종료일</span><input aria-label="종료일" type="date" value={filters.date_to} onChange={(e) => set('date_to', e.target.value)} /></label>
      <label><span>프로젝트</span><select aria-label="운영 프로젝트" value={filters.project_id} onChange={(e) => set('project_id', e.target.value)}><option value="">전체</option>{data.filter_options.projects.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
      <label><span>해석 유형</span><select aria-label="해석 유형" value={filters.analysis_type} onChange={(e) => set('analysis_type', e.target.value)}><option value="">전체</option>{data.filter_options.analysis_types.map((item) => <option key={item}>{item.replace('_', ' ')}</option>)}</select></label>
      <label><span>의뢰 상태</span><select aria-label="의뢰 상태" value={filters.status} onChange={(e) => set('status', e.target.value)}><option value="">전체</option>{data.filter_options.statuses.map((item) => <option key={item} value={item}>{STATUS_LABEL[item] ?? item}</option>)}</select></label>
      <button onClick={reset}><SlidersHorizontal /> 초기화</button><a href={api.portfolioCsvUrl(params)}><Download /> CSV</a>
    </section>
    <section className="portfolio-kpis">
      <Kpi label="대상 하중 경우" value={data.kpis.load_cases} note={`${data.kpis.requests}개 의뢰`} />
      <Kpi label="진행 중" value={data.kpis.in_progress} note="하중 경우 기준" />
      <Kpi label="결과 보유" value={data.kpis.completed_runs} note="최신 실행 기준" />
      <Kpi label="FAIL" value={data.kpis.failed} note="조치 필요" danger={data.kpis.failed > 0} />
      <Kpi label="PASS 비율" value={data.kpis.pass_rate == null ? '-' : `${data.kpis.pass_rate}%`} note="판정 데이터만 분모" />
    </section>
    {data.records.length === 0 ? <div className="portfolio-empty"><Database /><h2>조건에 맞는 해석 데이터가 없습니다.</h2><p>기간이나 분류 필터를 넓혀 보세요.</p><button onClick={reset}>전체 데이터 보기</button></div> : <>
      <section className="portfolio-grid">
        {layout.chartOrder.map((id, index) => <div key={id} className={editMode ? 'portfolio-chart-editable' : ''} onDragOver={(event)=>{ if (editMode) event.preventDefault() }} onDrop={(event)=>{ event.preventDefault(); reorderChart(id, event.dataTransfer.getData('text/plain') || draggedChart) }}>
          {editMode && <div className="portfolio-card-controls">
            <span className="portfolio-drag-handle" draggable onDragStart={(event)=>{ setDraggedChart(id); event.dataTransfer.setData('text/plain', id); event.dataTransfer.effectAllowed = 'move' }} onDragEnd={()=>setDraggedChart('')}><GripVertical /> 이동</span>
            <button aria-label={`${CHART_LABELS[id]} 왼쪽으로 이동`} onClick={()=>moveChart(id,-1)} disabled={index===0}><ArrowLeft /></button>
            <button aria-label={`${CHART_LABELS[id]} 오른쪽으로 이동`} onClick={()=>moveChart(id,1)} disabled={index===layout.chartOrder.length-1}><ArrowRight /></button>
          </div>}
          {chartCards[id]}
        </div>)}
      </section>
      <section className="portfolio-table-card"><header><div><span>DETAIL RECORDS · REQUEST MONITORING PROJECTION</span><h2>해석 의뢰 상세</h2></div><strong>{data.records.length}건</strong></header><div className="portfolio-table"><div className="portfolio-row table-header"><span>프로젝트 / 제품</span><span>의뢰 / 하중 경우</span><span>담당자</span><span>상태 / 현재 단계</span><span>유형</span><span>판정</span><span>접수일</span></div>{data.records.map((item) => <button className="portfolio-row" key={`${item.request_id}:${item.load_case_id || 'unassigned'}`} onClick={() => onOpen(item.project_id,item.request_id,item.load_case_id)}><span><strong>{item.project_name}</strong><small>{item.product_name}</small></span><span><strong>{item.request_title}</strong><small>{item.load_case_name}</small></span><span>{item.owner || '-'}</span><span className="portfolio-request-progress"><span><i className={`status-dot ${item.request_status.toLowerCase()}`} />{STATUS_LABEL[item.request_status] ?? item.request_status}<b>{item.request_progress}%</b></span><small>{item.current_step || '단계 미지정'}</small>{item.latest_demo_run && <em>DEMO · {item.latest_demo_run.status} {item.latest_demo_run.progress}%</em>}</span><span>{item.analysis_type.replace('_',' ')}</span><span><b className={item.verdict.toLowerCase()}>{item.verdict}</b></span><span>{new Date(item.requested_at).toLocaleDateString('ko-KR')}</span></button>)}</div></section>
    </>}
    {isRefreshing && <div className="portfolio-refresh"><LoaderCircle className="spin" /> 최신 데이터를 확인하고 있습니다.</div>}
    {error && <div className="portfolio-refresh error" role="alert"><AlertTriangle /> {error.message}<button onClick={retry}>다시 시도</button></div>}
  </div>
}

function Kpi({ label, value, note, danger=false }: { label:string; value:string|number; note:string; danger?:boolean }) { return <article className={danger?'danger':''}><span>{label}</span><strong>{value}</strong><small>{danger ? <AlertTriangle /> : <CheckCircle2 />}{note}</small></article> }
function ChartCard({ title, subtitle, children }: { title:string; subtitle:string; children:ReactNode }) { return <article className="portfolio-chart"><header><div><BarChart3 /><span><strong>{title}</strong><small>{subtitle}</small></span></div></header><div>{children}</div></article> }
