import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import { AlertTriangle, BarChart3, CheckCircle2, Database, Download, GripVertical, LoaderCircle, Search, SlidersHorizontal } from 'lucide-react'
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from './api'
import type { PortfolioLayout, PortfolioOverview } from './types'

const COLORS = ['#50d5ff', '#70e0a8', '#ffbf57', '#ff647d', '#8b9cff']
const STATUS_LABEL: Record<string, string> = { READY: '대기', IN_PROGRESS: '진행 중', COMPLETED: '완료', BLOCKED: '차단', FAILED: '실패' }

export function PortfolioDashboard({ editMode, layout, onLayoutChange, onCancelEdit, onOpen }: { editMode: boolean; layout: PortfolioLayout; onLayoutChange: (layout: PortfolioLayout) => void; onCancelEdit: () => void; onOpen: (projectId: string, requestId: string, loadCaseId: string) => void }) {
  const [data, setData] = useState<PortfolioOverview | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [draggedChart, setDraggedChart] = useState('')
  const [filters, setFilters] = useState({ date_from: '', date_to: '', project_id: '', analysis_type: '', status: '', search: '' })
  const params = useMemo(() => { const value = new URLSearchParams(); Object.entries(filters).forEach(([key, entry]) => entry && value.set(key, entry)); return value }, [filters])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setLoading(true); setError('')
      api.portfolio(params).then(setData).catch((reason) => setError(reason instanceof Error ? reason.message : '운영 현황을 불러오지 못했습니다.')).finally(() => setLoading(false))
    }, 220)
    return () => window.clearTimeout(timer)
  }, [params.toString()])

  const set = (key: keyof typeof filters, value: string) => setFilters((current) => ({ ...current, [key]: value }))
  const reset = () => setFilters({ date_from: '', date_to: '', project_id: '', analysis_type: '', status: '', search: '' })
  const reorderChart = (target: string) => {
    if (!draggedChart || draggedChart === target) return
    const next = layout.chartOrder.filter((item) => item !== draggedChart)
    next.splice(next.indexOf(target), 0, draggedChart)
    onLayoutChange({ ...layout, chartOrder: next })
    setDraggedChart('')
  }
  if (!data && loading) return <div className="portfolio-state"><LoaderCircle className="spin" /> 운영 데이터를 집계하고 있습니다.</div>
  if (!data || error) return <div className="portfolio-state error"><AlertTriangle /> {error || '운영 현황을 표시할 수 없습니다.'}<button onClick={reset}>필터 초기화</button></div>

  const chartFontSize = Math.max(8, layout.fontSize)
  const chartCards: Record<string, ReactNode> = {
    trend: <ChartCard title="의뢰·완료·실패 추이" subtitle="의뢰 접수일 기준"><ResponsiveContainer width="100%" height="100%"><AreaChart data={data.trend}><CartesianGrid stroke="#20394d" vertical={false} /><XAxis dataKey="date" tick={{ fill:'#6f899b', fontSize:chartFontSize }} /><YAxis allowDecimals={false} tick={{ fill:'#6f899b', fontSize:chartFontSize }} /><Tooltip /><Legend /><Area dataKey="requests" name="하중 경우" stroke="#50d5ff" fill="#50d5ff22" /><Area dataKey="completed" name="결과 보유" stroke="#70e0a8" fill="#70e0a822" /><Area dataKey="failed" name="FAIL" stroke="#ff647d" fill="#ff647d22" /></AreaChart></ResponsiveContainer></ChartCard>,
    status: <ChartCard title="의뢰 상태 분포" subtitle="현재 상태"><ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={data.status_distribution} dataKey="value" nameKey="name" innerRadius="52%" outerRadius="76%" paddingAngle={3}>{data.status_distribution.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}</Pie><Tooltip formatter={(v, n) => [v, STATUS_LABEL[String(n)] ?? n]} /><Legend formatter={(v) => STATUS_LABEL[String(v)] ?? v} /></PieChart></ResponsiveContainer></ChartCard>,
    quality: <ChartCard title="해석 유형별 품질" subtitle="최신 실행 판정"><ResponsiveContainer width="100%" height="100%"><BarChart data={data.quality_by_type}><CartesianGrid stroke="#20394d" vertical={false} /><XAxis dataKey="type" tick={{ fill:'#7892a4', fontSize:chartFontSize }} /><YAxis allowDecimals={false} tick={{ fontSize:chartFontSize }} /><Tooltip /><Legend /><Bar dataKey="pass" name="PASS" stackId="a" fill="#70e0a8" /><Bar dataKey="fail" name="FAIL" stackId="a" fill="#ff647d" /><Bar dataKey="no_data" name="NO DATA" stackId="a" fill="#526c7e" /></BarChart></ResponsiveContainer></ChartCard>,
    type: <ChartCard title="해석 유형 구성" subtitle="필터 적용 결과"><ResponsiveContainer width="100%" height="100%"><BarChart data={data.type_distribution} layout="vertical"><CartesianGrid stroke="#20394d" horizontal={false} /><XAxis type="number" allowDecimals={false} tick={{ fontSize:chartFontSize }} /><YAxis dataKey="name" type="category" width={90} tick={{ fill:'#7892a4', fontSize:chartFontSize }} /><Tooltip /><Bar dataKey="value" name="하중 경우" fill="#50d5ff" radius={[0,5,5,0]} /></BarChart></ResponsiveContainer></ChartCard>,
  }

  return <div className="portfolio-page" data-custom-font="true" style={{ '--portfolio-font-size': `${layout.fontSize}px` } as CSSProperties}>
    <header className="portfolio-head"><div><span>ANALYSIS OPERATIONS</span><h1>해석 운영 현황</h1><p>프로젝트부터 최신 해석 판정까지 한 화면에서 추적합니다.</p></div><div><small>데이터 기준</small><strong>{data.grain.replaceAll('_', ' ')}</strong><span>최근 결과 {data.freshness ? new Date(data.freshness).toLocaleString('ko-KR') : '없음'}</span></div></header>
    {editMode && <section className="portfolio-edit-toolbar"><GripVertical /><span><strong>운영 대시보드 편집</strong> 차트 카드를 끌어 순서를 바꾸고 글자 크기를 조절하세요.</span><label><span>글자 크기</span><input aria-label="운영 대시보드 글자 크기" type="range" min="8" max="18" step="1" value={layout.fontSize} onChange={(event)=>onLayoutChange({...layout,fontSize:Number(event.target.value)})}/><output>{layout.fontSize}px</output></label><button onClick={onCancelEdit}>편집 취소</button></section>}
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
        {layout.chartOrder.map((id) => <div key={id} className={editMode ? 'portfolio-chart-editable' : ''} draggable={editMode} onDragStart={()=>setDraggedChart(id)} onDragOver={(event)=>event.preventDefault()} onDrop={()=>reorderChart(id)}>{editMode && <span className="portfolio-drag-handle"><GripVertical /> 끌어서 이동</span>}{chartCards[id]}</div>)}
      </section>
      <section className="portfolio-table-card"><header><div><span>DETAIL RECORDS</span><h2>해석 의뢰 상세</h2></div><strong>{data.records.length}건</strong></header><div className="portfolio-table"><div className="portfolio-row table-header"><span>프로젝트 / 제품</span><span>의뢰 / 하중 경우</span><span>담당자</span><span>상태</span><span>유형</span><span>판정</span><span>접수일</span></div>{data.records.map((item) => <button className="portfolio-row" key={item.load_case_id} onClick={() => onOpen(item.project_id,item.request_id,item.load_case_id)}><span><strong>{item.project_name}</strong><small>{item.product_name}</small></span><span><strong>{item.request_title}</strong><small>{item.load_case_name}</small></span><span>{item.owner || '-'}</span><span><i className={`status-dot ${item.request_status.toLowerCase()}`} />{STATUS_LABEL[item.request_status] ?? item.request_status}</span><span>{item.analysis_type.replace('_',' ')}</span><span><b className={item.verdict.toLowerCase()}>{item.verdict}</b></span><span>{new Date(item.requested_at).toLocaleDateString('ko-KR')}</span></button>)}</div></section>
    </>}
    {loading && <div className="portfolio-refresh"><LoaderCircle className="spin" /> 필터 적용 중</div>}
  </div>
}

function Kpi({ label, value, note, danger=false }: { label:string; value:string|number; note:string; danger?:boolean }) { return <article className={danger?'danger':''}><span>{label}</span><strong>{value}</strong><small>{danger ? <AlertTriangle /> : <CheckCircle2 />}{note}</small></article> }
function ChartCard({ title, subtitle, children }: { title:string; subtitle:string; children:ReactNode }) { return <article className="portfolio-chart"><header><div><BarChart3 /><span><strong>{title}</strong><small>{subtitle}</small></span></div></header><div>{children}</div></article> }
