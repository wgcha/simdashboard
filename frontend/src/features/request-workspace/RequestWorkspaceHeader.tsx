import type { ReactNode } from 'react'
import { CircleDot, ClipboardPlus, Route, type LucideIcon } from 'lucide-react'

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
  newResultsCheck,
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
  /** [새 결과 확인] (app-level slot, shown on every tab except 결과 등록). */
  newResultsCheck?: ReactNode
  status: string
  title: string
  owner: string
}) {
  const requestUnavailable = !requestId || requests.length === 0
  const currentTab = activeTab ?? (activeView === 'workflow' ? 'overview' : 'review')
  const reviewTab = currentTab === 'review' && !caseResultsMode

  return <>
    {/* One compact header row on every request tab (C layout, 2026-10-29): title left, context right. */}
    <section className="request-workspace-header" data-case-results={caseResultsMode ? 'true' : undefined} data-ui-density="v1">
      <div className="request-workspace-title">
        <h1>{title}</h1>
        <p><span>{owner || '담당자 미지정'}</span><b>·</b><strong>{(statusLabels[status] ?? status) || '상태 미지정'}</strong>{completedCount && <><b>·</b><span>{completedCount}</span></>}</p>
      </div>
      {/* Case 결과 portals its sync status, 보고서 and Final actions here so its context bar stays one row. */}
      <div className="request-header-actions" data-request-header-actions="" />
      <div className="request-context-strip" aria-label="현재 의뢰 문맥" data-load-case-id={caseResultsMode ? undefined : selectedLoadCaseId}>
        <label><span>프로젝트</span><SearchableSelect ariaLabel="프로젝트 선택" items={projects} kind="project" value={projectId} disabled={contextChanging} onChange={onProjectChange} placeholder="프로젝트 선택" /></label>
        <label><span>의뢰</span><SearchableSelect ariaLabel="의뢰 선택" items={requests} kind="request" value={requestId} disabled={requestUnavailable || contextChanging} onChange={onRequestChange} placeholder={requestUnavailable ? currentTab === 'import' ? 'SPDM 의뢰 연결 필요' : '의뢰 접수 후 선택' : '의뢰 선택'} /></label>
        {/* 하중 경우 (user decision 2026-10-14): chosen automatically; shown only in the legacy Run review,
            as text when there is one and as a small selector only when a request still has several. */}
        {reviewTab && loadCases.length > 1 ? <label className="request-context-compact"><span>하중 경우</span><SearchableSelect ariaLabel="하중 경우 선택" items={loadCases} kind="load_case" value={selectedLoadCaseId} disabled={contextChanging} onChange={onLoadCaseChange} placeholder="하중 경우 선택" /></label> : null}
        {reviewTab && loadCases.length === 1 ? <span className="request-context-readonly" data-testid="request-load-case-label"><span>하중 경우</span><strong title={loadCases[0].name}>{loadCases[0].name}</strong></span> : null}
        {reviewTab ? activeRunSelector : null}
        {currentTab !== 'import' ? newResultsCheck : null}
      </div>
    </section>

    <nav className="request-journey" data-ui-density="v1" aria-label="의뢰 작업 여정">
      <JourneyItem icon={CircleDot} label="의뢰 개요" active={currentTab === 'overview'} disabled={contextChanging} onClick={onViewOverview} />
      <JourneyItem icon={ClipboardPlus} label="결과 등록" active={currentTab === 'import'} disabled={contextChanging || requestUnavailable || !canOpenData} onClick={onOpenData} />
      <div className="request-journey-item result-review">{resultReviewTab}</div>
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
    <JourneyItem icon={ClipboardPlus} label="결과 등록" active={active === 'data'} disabled={disabled} onClick={onOpenData} />
    <JourneyItem icon={Route} label="결과 검토" active={active === 'review'} disabled={disabled} onClick={onOpenReview} />
  </nav>
}
