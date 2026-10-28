import { AlertCircle, CheckCircle2, ChevronRight, Clock3, Download, ExternalLink, LoaderCircle, Search, SlidersHorizontal, XCircle } from 'lucide-react'
import { useEffect, useId, useState, type ReactNode } from 'react'
import type { PortfolioCaseRecord, PortfolioOverview } from '../../types'
import { openCaptureJobs } from '../../shared/events/captureJobs'
import { Button } from '../../shared/components/Button'
import { Input } from '../../shared/components/Input'
import { Select } from '../../shared/components/Select'
import './ResultOverviewDashboard.css'

export type ResultOverviewRecord = PortfolioOverview['records'][number]
export type ResultOverviewCaseRecord = PortfolioCaseRecord

type ResultOverviewDashboardProps = {
  data: { records: PortfolioOverview['records']; filter_options: Pick<PortfolioOverview['filter_options'], 'projects'> } & Partial<Pick<PortfolioOverview, 'case_records' | 'case_counts' | 'case_source' | 'case_freshness' | 'legacy_counts'>>
  /** Global administrators only: the 「결과 받기 상태」 panel is mounted for them. */
  isGlobalAdmin?: boolean
  loading: boolean
  projectId: string
  search: string
  onProjectChange: (projectId: string) => void
  onSearchChange: (search: string) => void
  onOpenResult: (record: ResultOverviewRecord) => void
  onOpenRequest: (record: ResultOverviewRecord) => void
  onOpenCase?: (record: ResultOverviewCaseRecord) => void
  onShowOperations: () => void
}

type TypeSummary = { name: string; pass: number; fail: number; pending: number; noData: number; other: number }

const DEFAULT_PAGE_SIZE = 25
const PAGE_SIZE_OPTIONS = [10, 25, 50] as const
const isPageSize = (value: number): value is (typeof PAGE_SIZE_OPTIONS)[number] => PAGE_SIZE_OPTIONS.includes(value as (typeof PAGE_SIZE_OPTIONS)[number])
type Filter = 'all' | 'review' | 'available' | 'receiving' | 'pending' | 'failed' | 'legacy'
const ENVIRONMENT_TEXT: Record<string, string> = { USAGE: '사용', DISTRIBUTION: '유통' }
const CASE_STATE_TEXT: Record<string, string> = { COMPLETED: '결과 보유', RECEIVING: '받는 중', WAITING: '대기', FAILED: '받기 실패' }
const CASE_STATE_CLASS: Record<string, string> = { COMPLETED: 'verdict-pass', RECEIVING: 'verdict-receiving', WAITING: 'verdict-waiting', FAILED: 'verdict-fail' }
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

export function ResultOverviewDashboard({ data, loading, projectId, search, isGlobalAdmin = false, onProjectChange, onSearchChange, onOpenResult, onOpenRequest, onOpenCase, onShowOperations }: ResultOverviewDashboardProps) {
  const [filter, setFilter] = useState<Filter>('all')
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState<number>(DEFAULT_PAGE_SIZE)
  useEffect(() => { setPage(0) }, [data, filter, pageSize, projectId, search])
  const safePageSize = isPageSize(pageSize) ? pageSize : DEFAULT_PAGE_SIZE
  const legacyRecords = [...data.records].filter(hasLoadCase).sort((a, b) => Number(hasRun(b)) - Number(hasRun(a)) || dateValue(b.completed_at || b.requested_at) - dateValue(a.completed_at || a.requested_at))
  const caseRecords = [...(data.case_records ?? [])].sort((a, b) => dateValue(b.captured_at || b.requested_at) - dateValue(a.captured_at || a.requested_at))
  const typeSummaries = buildTypeSummaries(legacyRecords)
  const counts = data.case_counts ?? { total: 0, completed: 0, receiving: 0, waiting: 0, failed: 0, judged: 0, criteria_fail: 0, refresh_failed: 0 }
  const legacyFail = legacyRecords.filter((record) => hasResult(record) && String(record.verdict).toUpperCase() === 'FAIL')
  const legacyJudged = data.legacy_counts?.judged ?? legacyRecords.filter((record) => hasRun(record) && ['PASS', 'FAIL'].includes(String(record.verdict).toUpperCase())).length
  const criteriaFail = legacyFail.length + counts.criteria_fail
  const noCriteria = legacyJudged + counts.judged === 0
  const legacyMode = filter === 'legacy' || filter === 'review'
  const caseState: Record<string, string> = { available: 'COMPLETED', receiving: 'RECEIVING', pending: 'WAITING', failed: 'FAILED' }
  const matchingCases = legacyMode ? [] : caseRecords.filter((record) => filter === 'all' || record.case_state === caseState[filter])
  const matchingLegacy = filter === 'review' ? legacyFail : filter === 'legacy' ? legacyRecords : []
  const matchingCount = legacyMode ? matchingLegacy.length : matchingCases.length
  const pageCount = Math.max(1, Math.ceil(matchingCount / safePageSize))
  const safePage = Math.min(Math.max(page, 0), pageCount - 1)
  const pageSlice = <T,>(items: T[]) => items.slice(safePage * safePageSize, (safePage + 1) * safePageSize)
  const recentCases = pageSlice(matchingCases)
  const recentLegacy = pageSlice(matchingLegacy)
  const toggleFilter = (next: Filter) => setFilter((current) => current === next ? 'all' : next)
  const waitingCases = caseRecords.filter((record) => record.case_state === 'WAITING').slice(0, 4)
  const legacyCount = data.legacy_counts?.load_cases ?? legacyRecords.length
  const headingText: Record<Filter, string> = { all: '최근 결과를 받은 순', review: '옛 결과 중 수치 판정 FAIL', available: '결과를 보유한 Case', receiving: '결과를 받고 있는 Case (대기·받는 중)', pending: '등록되었지만 아직 결과가 없는 Case', failed: '결과 받기에 실패한 Case', legacy: '옛 결과(하중 경우 기준) 목록' }
  const adminJobsAvailable = isGlobalAdmin && (filter === 'receiving' || filter === 'failed')

  return <div className="result-overview-dashboard" data-ui-density="v1" data-testid="result-overview-dashboard">
    <header className="result-overview-heading">
      <div><span className="result-overview-kicker">RESULTS</span><h1>결과 대시보드</h1><p>최신 결과를 확인하고 해당 의뢰로 이어갑니다.</p></div>
      <Button className="result-overview-operations" onClick={onShowOperations}><SlidersHorizontal aria-hidden="true" /> 운영 현황 <ChevronRight aria-hidden="true" /></Button>
    </header>

    <section className="result-overview-filters" aria-label="결과 대시보드 필터">
      <label><span className="sr-only">프로젝트</span><Select controlSize="lg" aria-label="결과 프로젝트" value={projectId} onChange={(event) => onProjectChange(event.target.value)}><option value="">모든 프로젝트</option>{data.filter_options.projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</Select></label>
      <label className="result-overview-search"><Search aria-hidden="true" /><Input controlSize="lg" aria-label="의뢰 제목, 하중 경우 검색" value={search} onChange={(event) => onSearchChange(event.target.value)} placeholder="의뢰 제목, 하중 경우 검색" /></label>
    </section>

    {loading ? <div className="result-overview-loading" role="status" aria-busy="true"><LoaderCircle className="spin" aria-hidden="true" /> 결과 대시보드를 갱신하고 있습니다.</div> : <>
    <section className="result-overview-summary" aria-label="결과 요약">
      <SummaryCard label="기준 미충족" value={noCriteria ? '판정 기준 없음' : criteriaFail} note={noCriteria ? '새 결과에는 아직 판정 기준이 없습니다. 기준이 생기면 FAIL 건수를 셉니다.' : '옛 결과의 최신 Run 수치 판정 FAIL 건수. 새 결과는 판정 기준이 없어 세지 않습니다.'} tone="danger" selected={filter === 'review'} onClick={() => toggleFilter('review')} icon={<AlertCircle aria-hidden="true" />} />
      <SummaryCard label="결과 보유" value={counts.completed} note="결과를 한 번 이상 받은 Case (의뢰 × Case × 환경)" tone="success" selected={filter === 'available'} onClick={() => toggleFilter('available')} icon={<CheckCircle2 aria-hidden="true" />} />
      <SummaryCard label="결과 받는 중" value={counts.receiving} note="결과가 없고 받기 작업이 대기 또는 진행 중인 Case" tone="info" selected={filter === 'receiving'} onClick={() => toggleFilter('receiving')} icon={<Download aria-hidden="true" />} />
      <SummaryCard label="결과 대기" value={counts.waiting} note="폴더에 등록됐지만 결과도 받기 작업도 아직 없는 Case" tone="warning" selected={filter === 'pending'} onClick={() => toggleFilter('pending')} icon={<Clock3 aria-hidden="true" />} />
      <SummaryCard label="받기 실패" value={counts.failed} note="결과가 없고 마지막 받기 작업이 실패한 Case" tone="danger" selected={filter === 'failed'} onClick={() => toggleFilter('failed')} icon={<XCircle aria-hidden="true" />} />
    </section>

    <div className="result-overview-content">
      <section className="result-overview-panel recent-results-panel"><header className="result-overview-panel-heading"><div><h2>{legacyMode ? '옛 결과' : '최근 해석 결과'}</h2><p>{headingText[filter]}</p></div><div className="result-overview-list-tools"><span>{matchingCount}건{filter !== 'all' && <button className="result-overview-clear" onClick={() => setFilter('all')}>전체 보기</button>}{adminJobsAvailable && <button className="result-overview-clear" onClick={() => openCaptureJobs({ filter: filter === 'failed' ? 'FAILED' : 'ALL' })}>결과 받기 상태 열기</button>}</span><label className="result-overview-page-size"><span>페이지 크기</span><Select controlSize="sm" aria-label="결과 목록 페이지 크기" value={safePageSize} onChange={(event) => { const next = Number(event.target.value); setPageSize(isPageSize(next) ? next : DEFAULT_PAGE_SIZE) }}>{PAGE_SIZE_OPTIONS.map((option) => <option key={option} value={option}>{option}개</option>)}</Select></label></div></header>
        {matchingCount === 0 ? <EmptyState noCases={!legacyMode && counts.total === 0} /> : legacyMode ? <div className="result-overview-result-list" role="table" aria-label="옛 결과 목록">
          <div className="result-overview-result-row result-overview-result-header" role="row"><span role="columnheader">의뢰 제목 / 하중 경우</span><span role="columnheader">판정</span><span role="columnheader">완료일</span><span role="columnheader">작업</span></div>
          {recentLegacy.map((record) => <article className="result-overview-result-row" role="row" key={`${record.request_id}:${record.load_case_id}`}>
            <span role="cell" className="result-overview-record-name"><strong title={record.request_title}>{record.request_title}</strong><small title={`${record.load_case_name} · ${record.project_name}`}>{record.load_case_name} · {record.project_name}</small></span>
            <span role="cell"><b className={`result-overview-verdict verdict-${String(record.verdict).toLowerCase()}`}>{isPending(record) ? '대기' : isNoData(record) ? '판정 없음' : verdictText[String(record.verdict).toUpperCase()] ?? '확인 필요'}</b></span>
            <span role="cell" className="result-overview-date">{formatDate(record.completed_at)}</span>
            <span role="cell" className="result-overview-row-actions">{hasRun(record) ? <Button size="sm" onClick={() => onOpenResult(record)}>결과 검토</Button> : <Button size="sm" onClick={() => onOpenRequest(record)}>의뢰 보기</Button>}<Button size="sm" variant="ghost" className="result-overview-icon-action" aria-label={`${record.request_title} 의뢰 열기`} onClick={() => onOpenRequest(record)}><ExternalLink aria-hidden="true" /></Button></span>
          </article>)}
        </div> : <div className="result-overview-result-list" role="table" aria-label="최근 해석 결과 목록">
          <div className="result-overview-result-row result-overview-result-header" role="row"><span role="columnheader">의뢰 제목 / Case</span><span role="columnheader">상태</span><span role="columnheader">결과 받은 시각</span><span role="columnheader">작업</span></div>
          {recentCases.map((record) => <article className="result-overview-result-row" role="row" key={record.case_id}>
            <span role="cell" className="result-overview-record-name"><strong title={record.request_title}>{record.request_title}</strong><small title={`${record.case_path} · ${record.project_name}`}>{record.case_name} · {ENVIRONMENT_TEXT[record.environment] ?? record.environment} 환경 · {record.project_name}</small></span>
            <span role="cell"><b className={`result-overview-verdict ${CASE_STATE_CLASS[record.case_state] ?? 'verdict-waiting'}`} title={record.case_state === 'FAILED' && record.error_code ? record.error_code : undefined}>{CASE_STATE_TEXT[record.case_state] ?? '확인 필요'}</b>{record.refresh_state && <small className="result-overview-refresh-note">{record.refresh_state === 'FAILED' ? '갱신 실패' : '갱신 중'}</small>}</span>
            <span role="cell" className="result-overview-date">{formatDate(record.captured_at)}</span>
            <span role="cell" className="result-overview-row-actions">{onOpenCase && <Button size="sm" onClick={() => onOpenCase(record)}>{record.case_state === 'COMPLETED' ? '결과 검토' : '의뢰 보기'}</Button>}{onOpenCase && <Button size="sm" variant="ghost" className="result-overview-icon-action" aria-label={`${record.request_title} 의뢰 열기`} onClick={() => onOpenCase(record)}><ExternalLink aria-hidden="true" /></Button>}</span>
          </article>)}
        </div>}
        {pageCount > 1 && <nav className="result-overview-pagination" aria-label="결과 목록 페이지"><Button size="sm" disabled={safePage === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>이전</Button><span>{safePage + 1} / {pageCount}</span><Button size="sm" disabled={safePage >= pageCount - 1} onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}>다음</Button></nav>}
        <footer className="result-overview-source" data-testid="result-overview-source">집계 기준: 폴더 등록 Case (의뢰 × Case × 환경) · 최근 결과 받은 시각 {formatDate(data.case_freshness)}</footer>
      </section>

      <aside className="result-overview-side-column">
        <section className="result-overview-panel type-summary-panel"><header className="result-overview-panel-heading"><div><h2>유형별 판정 (옛 결과)</h2><p>옛 결과의 최신 Run 수치 판정</p></div></header><div className="result-overview-type-list">{typeSummaries.length === 0 ? <p className="result-overview-empty-copy">옛 결과가 없습니다. 새 결과에는 판정 기준이 아직 없습니다.</p> : typeSummaries.map((summary) => <TypeBar key={summary.name} summary={summary} />)}</div><div className="result-overview-legend"><span className="legend-pass">PASS</span><span className="legend-fail">FAIL</span><span className="legend-pending">대기</span><span className="legend-no-data">판정 없음</span>{typeSummaries.some((summary) => summary.other > 0) && <span className="legend-other">확인 필요</span>}</div></section>
        <section className="result-overview-panel pending-panel"><header className="result-overview-panel-heading"><div><h2>결과 대기</h2><p>폴더에 등록됐지만 결과가 아직 없는 Case입니다.</p></div><span>{counts.waiting}</span></header>{waitingCases.length ? <div className="result-overview-pending-list">{waitingCases.map((record) => <button key={record.case_id} onClick={() => onOpenCase?.(record)}><span><strong>{record.request_title}</strong><small>{record.case_name} · {ENVIRONMENT_TEXT[record.environment] ?? record.environment}</small></span><ChevronRight aria-hidden="true" /></button>)}</div> : <p className="result-overview-empty-copy">현재 결과 대기 항목이 없습니다.</p>}{legacyCount > 0 && <button type="button" className="result-overview-unassigned result-overview-legacy-link" aria-pressed={filter === 'legacy'} onClick={() => toggleFilter('legacy')}>옛 결과 {legacyCount}건 {filter === 'legacy' ? '(목록 표시 중)' : '보기'}</button>}</section>
      </aside>
    </div></>}
  </div>
}

function SummaryCard({ label, value, note, tone, icon, selected, onClick }: { label: string; value: number | string; note: string; tone: string; icon: ReactNode; selected: boolean; onClick: () => void }) { const descriptionId = useId(); return <button className={`result-overview-summary-card tone-${tone}${typeof value === 'string' ? ' is-text-value' : ''}`} aria-label={label} title={`${label}: ${note}`} aria-describedby={`${descriptionId}-value ${descriptionId}-note`} aria-pressed={selected} onClick={onClick}><span className="result-overview-summary-icon">{icon}</span><div><strong>{label}</strong><b id={`${descriptionId}-value`}>{value}</b><small id={`${descriptionId}-note`}>{note}</small></div><ChevronRight aria-hidden="true" /></button> }
function TypeBar({ summary }: { summary: TypeSummary }) { const total = summary.pass + summary.fail + summary.pending + summary.noData + summary.other; return <div className="result-overview-type-row"><div className="result-overview-type-label"><span title={summary.name}>{summary.name}</span><b>{total}</b></div><div className="result-overview-bar" role="img" aria-label={`${summary.name} ${total}건: PASS ${summary.pass}, FAIL ${summary.fail}, 대기 ${summary.pending}, 판정 없음 ${summary.noData}, 확인 필요 ${summary.other}`}><i className="bar-pass" style={{ width: `${summary.pass / total * 100}%` }} /><i className="bar-fail" style={{ width: `${summary.fail / total * 100}%` }} /><i className="bar-pending" style={{ width: `${summary.pending / total * 100}%` }} /><i className="bar-no-data" style={{ width: `${summary.noData / total * 100}%` }} /><i className="bar-other" style={{ width: `${summary.other / total * 100}%` }} /></div></div> }
function EmptyState({ noCases = false }: { noCases?: boolean }) { return <p className="result-overview-empty-copy">{noCases ? '폴더에 등록된 Case가 없습니다. 저장소 폴더를 등록하면 여기에 표시됩니다.' : '조건에 맞는 결과가 없습니다. 필터를 조정해 보세요.'}</p> }
