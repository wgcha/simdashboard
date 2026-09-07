import type { ReactNode } from 'react'
import { CircleDot, ClipboardPlus, Play, Route, type LucideIcon } from 'lucide-react'

import type { AnalysisRequest, LoadCase, Project } from '../../types'
import './RequestWorkspaceHeader.css'

type JourneyItemProps = {
  active: boolean
  disabled?: boolean
  icon: LucideIcon
  label: string
  onClick?: () => void
}
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
  activeView,
  activeRunSelector,
  canOpenData = true,
  canOpenWorkbench = true,
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
  status,
  title,
  owner,
}: {
  activeView: string
  activeRunSelector?: ReactNode
  canOpenData?: boolean
  canOpenWorkbench?: boolean
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
  status: string
  title: string
  owner: string
}) {
  const requestUnavailable = !requestId || requests.length === 0
  const isOverview = activeView === 'workflow'

  return <>
    <section className="request-workspace-header">
      <div className="request-workspace-title">
        <h1>{title}</h1>
        <p><span>{owner || '담당자 미지정'}</span><b>·</b><strong>{(statusLabels[status] ?? status) || '상태 미지정'}</strong>{completedCount && <><b>·</b><span>{completedCount}</span></>}</p>
      </div>
      <div className="request-context-strip" aria-label="현재 의뢰 문맥">
        <label><span>프로젝트</span><select aria-label="프로젝트 선택" value={projectId} disabled={contextChanging} onChange={(event) => onProjectChange(event.target.value)}>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
        <label><span>의뢰</span><select aria-label="의뢰 선택" value={requestId} disabled={requestUnavailable || contextChanging} onChange={(event) => onRequestChange(event.target.value)}>{requestUnavailable ? <option value="">의뢰 접수 후 선택</option> : requests.map((request) => <option key={request.id} value={request.id}>{request.title}</option>)}</select></label>
        <label><span>하중 경우</span><select aria-label="하중 경우 선택" value={selectedLoadCaseId} disabled={!loadCases.length || contextChanging} onChange={(event) => onLoadCaseChange(event.target.value)}>{loadCases.length ? loadCases.map((loadCase) => <option key={loadCase.id} value={loadCase.id}>{loadCase.name}</option>) : <option value="">미지정</option>}</select></label>
        {activeRunSelector}
      </div>
    </section>

    <nav className="request-journey" aria-label="의뢰 작업 여정">
      <JourneyItem icon={CircleDot} label="의뢰 개요" active={isOverview} disabled={contextChanging} onClick={onViewOverview} />
      <JourneyItem icon={Play} label="작업 실행" active={false} disabled={contextChanging || requestUnavailable || !canOpenWorkbench} onClick={onOpenWorkbench} />
      <JourneyItem icon={ClipboardPlus} label="결과 등록" active={false} disabled={contextChanging || requestUnavailable || !canOpenData} onClick={onOpenData} />
      <div className={`request-journey-item result-review ${!isOverview ? 'active' : ''}`}>{resultReviewTab}</div>
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
  return <nav className="request-journey request-journey-compact" aria-label="의뢰 작업 여정">
    <JourneyItem icon={CircleDot} label="의뢰 개요" active={active === 'overview'} onClick={onOpenOverview} />
    <JourneyItem icon={Play} label="작업 실행" active={active === 'workbench'} disabled={disabled} onClick={onOpenWorkbench} />
    <JourneyItem icon={ClipboardPlus} label="결과 등록" active={active === 'data'} disabled={disabled} onClick={onOpenData} />
    <JourneyItem icon={Route} label="결과 검토" active={active === 'review'} disabled={disabled} onClick={onOpenReview} />
  </nav>
}
