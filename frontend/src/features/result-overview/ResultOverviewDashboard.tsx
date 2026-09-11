import { AlertCircle, CheckCircle2, ChevronRight, Clock3, ExternalLink, LoaderCircle, Search, SlidersHorizontal } from 'lucide-react'
import { useEffect, useId, useState, type ReactNode } from 'react'
import type { PortfolioOverview } from '../../types'
import './ResultOverviewDashboard.css'

export type ResultOverviewRecord = PortfolioOverview['records'][number]

type ResultOverviewDashboardProps = {
  data: { records: PortfolioOverview['records']; filter_options: Pick<PortfolioOverview['filter_options'], 'projects'> }
  loading: boolean
  projectId: string
  search: string
  onProjectChange: (projectId: string) => void
  onSearchChange: (search: string) => void
  onOpenResult: (record: ResultOverviewRecord) => void
  onOpenRequest: (record: ResultOverviewRecord) => void
  onShowOperations: () => void
}

type TypeSummary = { name: string; pass: number; fail: number; pending: number; noData: number; other: number }

const PAGE_SIZE = 5
const verdictText: Record<string, string> = { PASS: 'PASS', FAIL: 'FAIL', NO_DATA: '판정 없음' }
const dateValue = (value: string | null | undefined) => value ? new Date(value).getTime() || 0 : 0
const formatDate = (value: string | null | undefined) => value ? new Date(value).toLocaleString('ko-KR', { dateStyle: 'short', timeStyle: 'short' }) : '—'
const hasLoadCase = (record: ResultOverviewRecord) => Boolean(record.load_case_id)
const hasRun = (record: ResultOverviewRecord) => Boolean(record.run_id)
const resultCount = (record: ResultOverviewRecord) => Number.isFinite(record.result_count) ? record.result_count : 0
const hasResult = (record: ResultOverviewRecord) => hasRun(record) && resultCount(record) > 0
const isPending = (record: ResultOverviewRecord) => hasLoadCase(record) && !hasRun(record)
const isNoData = (record: ResultOverviewRecord) => hasRun(record) && (String(record.verdict).toUpperCase() === 'NO_DATA' || resultCount(record) === 0)

function buildTypeSummaries(records: ResultOverviewRecord[]) {
  const summaries = new Map<string, TypeSummary>()
  records.filter(hasLoadCase).forEach((record) => {
    const name = record.analysis_type || '유형 미지정'
    const current = summaries.get(name) ?? { name, pass: 0, fail: 0, pending: 0, noData: 0, other: 0 }
    const verdict = String(record.verdict).toUpperCase()
    if (isPending(record)) current.pending += 1
    else if (isNoData(record)) current.noData += 1
    else if (verdict === 'PASS' && hasResult(record)) current.pass += 1
    else if (verdict === 'FAIL' && hasResult(record)) current.fail += 1
    else current.other += 1
    summaries.set(name, current)
  })
  return [...summaries.values()].sort((a, b) => (b.pass + b.fail + b.pending + b.noData + b.other) - (a.pass + a.fail + a.pending + a.noData + a.other))
}

export function ResultOverviewDashboard({ data, loading, projectId, search, onProjectChange, onSearchChange, onOpenResult, onOpenRequest, onShowOperations }: ResultOverviewDashboardProps) {
  const [filter, setFilter] = useState<'all' | 'review' | 'available' | 'pending'>('all')
  const [page, setPage] = useState(0)
  useEffect(() => { setPage(0) }, [data, filter])
  const records = [...data.records].sort((a, b) => Number(hasRun(b)) - Number(hasRun(a)) || dateValue(b.completed_at || b.requested_at) - dateValue(a.completed_at || a.requested_at))
  const typeSummaries = buildTypeSummaries(records)
  const reviewCount = records.filter((record) => hasResult(record) && String(record.verdict).toUpperCase() === 'FAIL').length
  const resultCountTotal = records.filter(hasRun).length
  const pendingCount = records.filter(isPending).length
  const unassignedCount = records.filter((record) => !hasLoadCase(record)).length
  const matchingRecords = records.filter((record) => filter === 'all' || (filter === 'review' ? hasResult(record) && String(record.verdict).toUpperCase() === 'FAIL' : filter === 'available' ? hasRun(record) : isPending(record)))
  const pageCount = Math.max(1, Math.ceil(matchingRecords.length / PAGE_SIZE))
  const recentRecords = matchingRecords.slice(Math.min(page, pageCount - 1) * PAGE_SIZE, (Math.min(page, pageCount - 1) + 1) * PAGE_SIZE)
  const toggleFilter = (next: typeof filter) => setFilter((current) => current === next ? 'all' : next)
  const pendingRecords = records.filter(isPending).slice(0, 4)

  return <div className="result-overview-dashboard" data-testid="result-overview-dashboard">
    <header className="result-overview-heading">
      <div><span className="result-overview-kicker">RESULTS</span><h1>결과 대시보드</h1><p>최신 결과를 확인하고 해당 의뢰로 이어갑니다.</p></div>
      <button className="result-overview-operations" onClick={onShowOperations}><SlidersHorizontal aria-hidden="true" /> 운영 현황 <ChevronRight aria-hidden="true" /></button>
    </header>

    <section className="result-overview-filters" aria-label="결과 대시보드 필터">
      <label><span className="sr-only">프로젝트</span><select aria-label="결과 프로젝트" value={projectId} onChange={(event) => onProjectChange(event.target.value)}><option value="">모든 프로젝트</option>{data.filter_options.projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
      <label className="result-overview-search"><Search aria-hidden="true" /><input aria-label="의뢰 제목, 하중 경우 검색" value={search} onChange={(event) => onSearchChange(event.target.value)} placeholder="의뢰 제목, 하중 경우 검색" /></label>
    </section>

    {loading ? <div className="result-overview-loading" role="status" aria-busy="true"><LoaderCircle className="spin" aria-hidden="true" /> 결과 대시보드를 갱신하고 있습니다.</div> : <>
    <section className="result-overview-summary" aria-label="결과 요약">
      <SummaryCard label="기준 미충족" value={reviewCount} note="최신 Run의 수치 판정 FAIL" tone="danger" selected={filter === 'review'} onClick={() => toggleFilter('review')} icon={<AlertCircle aria-hidden="true" />} />
      <SummaryCard label="결과 보유" value={resultCountTotal} note="최신 Run이 있는 하중 경우" tone="success" selected={filter === 'available'} onClick={() => toggleFilter('available')} icon={<CheckCircle2 aria-hidden="true" />} />
      <SummaryCard label="결과 대기" value={pendingCount} note="Run이 없는 하중 경우" tone="warning" selected={filter === 'pending'} onClick={() => toggleFilter('pending')} icon={<Clock3 aria-hidden="true" />} />
    </section>

    <div className="result-overview-content">
      <section className="result-overview-panel recent-results-panel"><header className="result-overview-panel-heading"><div><h2>최근 해석 결과</h2><p>{filter === 'all' ? '최신 Run의 완료일 순' : filter === 'review' ? '수치 판정 FAIL 결과' : filter === 'available' ? '결과를 보유한 하중 경우' : '결과를 기다리는 하중 경우'}</p></div><span>{matchingRecords.length}건{filter !== 'all' && <button className="result-overview-clear" onClick={() => setFilter('all')}>전체 보기</button>}</span></header>
        {recentRecords.length === 0 ? <EmptyState /> : <div className="result-overview-result-list" role="table" aria-label="최근 해석 결과 목록">
          <div className="result-overview-result-row result-overview-result-header" role="row"><span role="columnheader">의뢰 제목 / 하중 경우</span><span role="columnheader">판정</span><span role="columnheader">완료일</span><span role="columnheader">작업</span></div>
          {recentRecords.map((record) => <article className="result-overview-result-row" role="row" key={`${record.request_id}:${record.load_case_id || 'unassigned'}`}>
            <span role="cell" className="result-overview-record-name"><strong title={record.request_title}>{record.request_title}</strong><small title={`${record.load_case_name || '하중 경우 미지정'} · ${record.project_name}`}>{record.load_case_name || '하중 경우 미지정'} · {record.project_name}</small></span>
            <span role="cell"><b className={`result-overview-verdict verdict-${String(record.verdict).toLowerCase()}`}>{!hasLoadCase(record) ? '미지정' : isPending(record) ? '대기' : isNoData(record) ? '판정 없음' : verdictText[String(record.verdict).toUpperCase()] ?? '확인 필요'}</b></span>
            <span role="cell" className="result-overview-date">{formatDate(record.completed_at)}</span>
            <span role="cell" className="result-overview-row-actions">{hasRun(record) ? <button onClick={() => onOpenResult(record)}>결과 검토</button> : <button onClick={() => onOpenRequest(record)}>의뢰 보기</button>}<button className="result-overview-icon-action" aria-label={`${record.request_title} 의뢰 열기`} onClick={() => onOpenRequest(record)}><ExternalLink aria-hidden="true" /></button></span>
          </article>)}
        </div>}
        {pageCount > 1 && <nav className="result-overview-pagination" aria-label="결과 목록 페이지"><button disabled={page === 0} onClick={() => setPage((value) => value - 1)}>이전</button><span>{page + 1} / {pageCount}</span><button disabled={page >= pageCount - 1} onClick={() => setPage((value) => value + 1)}>다음</button></nav>}
      </section>

      <aside className="result-overview-side-column">
        <section className="result-overview-panel type-summary-panel"><header className="result-overview-panel-heading"><div><h2>유형별 판정</h2><p>최신 Run의 수치 판정</p></div></header><div className="result-overview-type-list">{typeSummaries.length === 0 ? <EmptyState /> : typeSummaries.map((summary) => <TypeBar key={summary.name} summary={summary} />)}</div><div className="result-overview-legend"><span className="legend-pass">PASS</span><span className="legend-fail">FAIL</span><span className="legend-pending">대기</span><span className="legend-no-data">판정 없음</span>{typeSummaries.some((summary) => summary.other > 0) && <span className="legend-other">확인 필요</span>}</div></section>
        <section className="result-overview-panel pending-panel"><header className="result-overview-panel-heading"><div><h2>결과 대기</h2><p>하중 경우에 결과가 아직 연결되지 않았습니다.</p></div><span>{pendingCount}</span></header>{pendingRecords.length ? <div className="result-overview-pending-list">{pendingRecords.map((record) => <button key={`${record.request_id}:${record.load_case_id}`} onClick={() => onOpenRequest(record)}><span><strong>{record.request_title}</strong><small>{record.load_case_name || '하중 경우 미지정'}</small></span><ChevronRight aria-hidden="true" /></button>)}</div> : <p className="result-overview-empty-copy">현재 결과 대기 항목이 없습니다.</p>}{unassignedCount > 0 && <small className="result-overview-unassigned">하중 경우 미지정 의뢰 {unassignedCount}건은 별도로 표시됩니다.</small>}</section>
      </aside>
    </div></>}
  </div>
}

function SummaryCard({ label, value, note, tone, icon, selected, onClick }: { label: string; value: number; note: string; tone: string; icon: ReactNode; selected: boolean; onClick: () => void }) { const descriptionId = useId(); return <button className={`result-overview-summary-card tone-${tone}`} aria-label={label} aria-describedby={`${descriptionId}-value ${descriptionId}-note`} aria-pressed={selected} onClick={onClick}><span className="result-overview-summary-icon">{icon}</span><div><strong>{label}</strong><b id={`${descriptionId}-value`}>{value}</b><small id={`${descriptionId}-note`}>{note}</small></div><ChevronRight aria-hidden="true" /></button> }
function TypeBar({ summary }: { summary: TypeSummary }) { const total = summary.pass + summary.fail + summary.pending + summary.noData + summary.other; return <div className="result-overview-type-row"><div className="result-overview-type-label"><span title={summary.name}>{summary.name}</span><b>{total}</b></div><div className="result-overview-bar" role="img" aria-label={`${summary.name} ${total}건: PASS ${summary.pass}, FAIL ${summary.fail}, 대기 ${summary.pending}, 판정 없음 ${summary.noData}, 확인 필요 ${summary.other}`}><i className="bar-pass" style={{ width: `${summary.pass / total * 100}%` }} /><i className="bar-fail" style={{ width: `${summary.fail / total * 100}%` }} /><i className="bar-pending" style={{ width: `${summary.pending / total * 100}%` }} /><i className="bar-no-data" style={{ width: `${summary.noData / total * 100}%` }} /><i className="bar-other" style={{ width: `${summary.other / total * 100}%` }} /></div></div> }
function EmptyState() { return <p className="result-overview-empty-copy">조건에 맞는 결과가 없습니다. 필터를 조정해 보세요.</p> }
