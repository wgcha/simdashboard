import type { ReactNode } from 'react'
import { CircleDot, ClipboardPlus, Play, Route, type LucideIcon } from 'lucide-react'

import type { AnalysisRequest, LoadCase, Project } from '../../types'
import './RequestWorkspaceHeader.css'
import { SearchableSelect } from '../../shared/components/selectionLabels'

type JourneyItemProps = {
  active: boolean
  disabled?: boolean
  icon: LucideIcon
  label: string
  onClick?: () => void
}
export type RequestWorkspaceTab = 'overview' | 'execution' | 'import' | 'review'

const statusLabels: Record<string, string> = { READY: '시작 대기', IN_PROGRESS: '진행 중', COMPLETED: '완료', WAITING: '대기', BLOCKED: '차단', FAILED: '실패', PASS: '통과', FAIL: '검토 필요', NO_DATA: '결과 없음' }

function JourneyItem({ active, disabled = false, icon: Icon, label, onClick }: JourneyItemProps) {
  return <button
    type="button"
    className={`request-journey-item ${active ? 'active' : ''}`}
    aria-current={active ? 'step' : undefined}
    disabled={disabled}
    onClick={onClick}
  ><Icon /><span>{label}</span></button>
}

export function RequestWorkspaceHeader({
  activeTab,
  activeView,
  activeRunSelector,
  canOpenData = true,
  canOpenWorkbench = true,
  caseResultsMode = false,
  contextChanging = false,
  completedCount,
  loadCases,
  onOpenData,
  onOpenWorkbench,
  onProjectChange,
  onRequestChange,
  onViewOverview,
  projectId,
  projects,
  requestId,
  requests,
  resultReviewTab,
  selectedLoadCaseId,
  onLoadCaseChange,
  onRefreshResults,
  canRefreshResults = false,
  resultsRefreshing = false,
  status,
  title,
  owner,
}: {
  /** The four request-workspace destinations. `activeView` remains for legacy callers. */
  activeTab?: RequestWorkspaceTab
  activeView: string
  activeRunSelector?: ReactNode
  canOpenData?: boolean
  canOpenWorkbench?: boolean
  caseResultsMode?: boolean
  contextChanging?: boolean
  completedCount?: string
  loadCases: LoadCase[]
  onOpenData: () => void
  onOpenWorkbench: () => void
  onProjectChange: (id: string) => void
  onRequestChange: (id: string) => void
  onViewOverview: () => void
  projectId: string
  projects: Project[]
  requestId: string
  requests: AnalysisRequest[]
  resultReviewTab: ReactNode
  selectedLoadCaseId: string
  onLoadCaseChange: (id: string) => void
  onRefreshResults?: () => void
  canRefreshResults?: boolean
  resultsRefreshing?: boolean
  status: string
  title: string
  owner: string
}) {
  const requestUnavailable = !requestId || requests.length === 0
  const currentTab = activeTab ?? (activeView === 'workflow' ? 'overview' : 'review')

  return <>
    <section className={`request-workspace-header${caseResultsMode ? ' request-workspace-header--case' : ''}`} data-ui-density="v1">
      <div className="request-workspace-title">
        <h1>{title}</h1>
        <p><span>{owner || '담당자 미지정'}</span><b>·</b><strong>{(statusLabels[status] ?? status) || '상태 미지정'}</strong>{completedCount && <><b>·</b><span>{completedCount}</span></>}</p>
      </div>
      <div className="request-context-strip" aria-label="현재 의뢰 문맥">
        <label><span>프로젝트</span><SearchableSelect ariaLabel="프로젝트 선택" items={projects} kind="project" value={projectId} disabled={contextChanging} onChange={onProjectChange} placeholder="프로젝트 선택" /></label>
        <label><span>의뢰</span><SearchableSelect ariaLabel="의뢰 선택" items={requests} kind="request" value={requestId} disabled={requestUnavailable || contextChanging} onChange={onRequestChange} placeholder={requestUnavailable ? '의뢰 접수 후 선택' : '의뢰 선택'} /></label>
        {!caseResultsMode ? <label><span>하중 경우</span><SearchableSelect ariaLabel="하중 경우 선택" items={loadCases} kind="load_case" value={selectedLoadCaseId} disabled={!loadCases.length || contextChanging} onChange={onLoadCaseChange} placeholder={loadCases.length ? '하중 경우 선택' : '미지정'} /></label> : null}
        {!caseResultsMode && onRefreshResults ? <button type="button" className="ghost-button request-results-refresh" aria-label="현재 하중 경우 결과 파일 확인" title="결과 파일을 처리하고 표시 가능한 Run을 선택합니다." onClick={onRefreshResults} disabled={!canRefreshResults || resultsRefreshing || contextChanging || !selectedLoadCaseId}>{resultsRefreshing ? '조회 중…' : '결과 조회'}</button> : null}
        {!caseResultsMode ? activeRunSelector : null}
      </div>
    </section>

    <nav className={`request-journey${caseResultsMode ? ' request-journey--case' : ''}`} data-ui-density="v1" aria-label="의뢰 작업 여정">
      <JourneyItem icon={CircleDot} label="의뢰 개요" active={currentTab === 'overview'} disabled={contextChanging} onClick={onViewOverview} />
      <JourneyItem icon={Play} label="작업 실행" active={currentTab === 'execution'} disabled={contextChanging || requestUnavailable || !canOpenWorkbench} onClick={onOpenWorkbench} />
      <JourneyItem icon={ClipboardPlus} label="결과 등록" active={currentTab === 'import'} disabled={contextChanging || requestUnavailable || !canOpenData} onClick={onOpenData} />
      <div className={`request-journey-item result-review ${currentTab === 'review' && !caseResultsMode ? 'active' : ''}`}>{resultReviewTab}</div>
    </nav>
  </>
}

export function RequestJourneyCompact({ active, disabled = false, onOpenData, onOpenOverview, onOpenReview, onOpenWorkbench }: {
  active: 'overview' | 'workbench' | 'data' | 'review'
  disabled?: boolean
  onOpenData: () => void
  onOpenOverview: () => void
  onOpenReview: () => void
  onOpenWorkbench: () => void
}) {
  return <nav className="request-journey request-journey-compact" data-ui-density="v1" aria-label="의뢰 작업 여정">
    <JourneyItem icon={CircleDot} label="의뢰 개요" active={active === 'overview'} onClick={onOpenOverview} />
    <JourneyItem icon={Play} label="작업 실행" active={active === 'workbench'} disabled={disabled} onClick={onOpenWorkbench} />
    <JourneyItem icon={ClipboardPlus} label="결과 등록" active={active === 'data'} disabled={disabled} onClick={onOpenData} />
    <JourneyItem icon={Route} label="결과 검토" active={active === 'review'} disabled={disabled} onClick={onOpenReview} />
  </nav>
}
