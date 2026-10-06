import { AlertTriangle } from 'lucide-react'
import { TEMPLATE_NOT_APPLIED_TEXT, type CaseReportMeta } from './reportPreferences'

/** Shown next to a layout select whenever the chosen layout is bound to an uploaded PPTX template. */
export function TemplateNotAppliedNotice() {
  return <p className="case-report__template-note" role="note" data-testid="case-report-template-note"><AlertTriangle size={13} aria-hidden="true" />{TEMPLATE_NOT_APPLIED_TEXT}</p>
}

/** Author (prefilled) and optional 개발단계·검토조건·결론 for the PPTX cover/summary and the HTML scope block. */
export function CaseReportMetaFields({ value, disabled, onChange }: { value: CaseReportMeta; disabled?: boolean; onChange: (next: CaseReportMeta) => void }) {
  const field = (key: keyof CaseReportMeta) => (event: { target: { value: string } }) => onChange({ ...value, [key]: event.target.value })
  return <fieldset className="case-report__meta" disabled={disabled} data-testid="case-report-meta">
    <legend>보고서 정보 <span className="case-report__hint">(작성자 외 선택)</span></legend>
    <label><span>작성자</span><input value={value.author} maxLength={80} onChange={field('author')} /></label>
    <label><span>개발단계</span><input value={value.developmentStage} maxLength={80} placeholder="예: DV 1차" onChange={field('developmentStage')} /></label>
    <label className="case-report__meta-wide"><span>검토조건</span><textarea value={value.reviewConditions} maxLength={2000} rows={2} onChange={field('reviewConditions')} /></label>
    <label className="case-report__meta-wide"><span>결론</span><textarea value={value.reviewConclusion} maxLength={2000} rows={2} onChange={field('reviewConclusion')} /></label>
  </fieldset>
}
