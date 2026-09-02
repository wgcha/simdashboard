import { ClipboardPlus } from 'lucide-react'

type ProjectSetupStateProps = {
  canCreateRequest: boolean
  onOpenIntake: () => void
  projectName: string
}

/**
 * A project can legitimately exist before its first request is received.
 * Keep that state navigable instead of treating it as a failed workspace load.
 */
export function ProjectSetupState({ canCreateRequest, onOpenIntake, projectName }: ProjectSetupStateProps) {
  return <section className="portfolio-empty project-setup-state" data-testid="project-setup-state">
    <ClipboardPlus aria-hidden="true" />
    <span className="eyebrow">PROJECT SETUP</span>
    <h2>{projectName}에 아직 의뢰가 없습니다.</h2>
    <p>프로젝트는 선택되었지만 분석을 시작할 의뢰가 아직 접수되지 않았습니다.</p>
    {canCreateRequest ? <button type="button" onClick={onOpenIntake}><ClipboardPlus aria-hidden="true" /> 해석 의뢰 접수</button> : <small>의뢰 접수 권한이 있는 담당자에게 새 의뢰 생성을 요청하세요.</small>}
  </section>
}
