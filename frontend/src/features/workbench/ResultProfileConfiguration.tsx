import { LayoutDashboard } from 'lucide-react'
import { useMemo } from 'react'

import type { AnalysisTemplateVersion, ResultProfile } from './types'
import { normalizeResultDataContracts, RESULT_DATA_CONTRACTS, resultProfileValidation } from './resultProfileContracts'

function widgetsFor(template: AnalysisTemplateVersion) {
  return template.page_definitions.flatMap((page) => page.widgets.map((widget) => ({ pageName: page.name, widget })))
}

export function ResultProfileConfiguration({ templates, value, onChange }: {
  templates: AnalysisTemplateVersion[]
  value: ResultProfile | null
  onChange: (value: ResultProfile | null) => void
}) {
  const selectedTemplate = useMemo(
    () => templates.find((item) => item.template_id === value?.template_id && item.version === value?.template_version) ?? value?.template ?? null,
    [templates, value],
  )
  const widgets = selectedTemplate ? widgetsFor(selectedTemplate) : []
  const allWidgetIds = widgets.map(({ widget }) => widget.id)
  const requiredWidgetIds = widgets.filter(({ widget }) => widget.settings?.required === true || widget.settings?.required_widget === true).map(({ widget }) => widget.id)
  const includedWidgetIds = [...new Set([...(value?.included_widget_ids?.length ? value.included_widget_ids : allWidgetIds), ...requiredWidgetIds])]
  const requiredDataContracts = normalizeResultDataContracts(value?.required_data_contracts)
  const validationError = resultProfileValidation(value, [])

  const chooseTemplate = (templateId: string) => {
    if (!templateId) {
      onChange(null)
      return
    }
    const template = templates.find((item) => `${item.template_id}:${item.version}` === templateId)
    if (!template) return
    onChange({
      request_type_id: value?.request_type_id ?? '',
      request_type_version: value?.request_type_version ?? 0,
      template_id: template.template_id,
      template_version: template.version,
      template,
      included_widget_ids: widgetsFor(template).map(({ widget }) => widget.id),
      overrides: {},
      required_data_contracts: normalizeResultDataContracts(value?.required_data_contracts ?? ["LOAD_CASE", "RESULT_RUN"]),
    })
  }

  const update = (patch: Partial<ResultProfile>) => {
    if (!value || !selectedTemplate) return
    onChange({ ...value, ...patch, included_widget_ids: [...new Set([...((patch.included_widget_ids ?? includedWidgetIds) ?? includedWidgetIds), ...requiredWidgetIds])], template: selectedTemplate })
  }

  return <section className="result-profile-configuration">
    <header><div><span>03 · RESULT LAYOUT</span><h3>예상 결과 구성</h3><p>게시된 분석 템플릿과 선택 위젯을 이 작업 유형 버전에 고정합니다.</p></div><LayoutDashboard aria-hidden="true" /></header>
    <label><span>게시된 분석 템플릿</span><select aria-label="결과 레이아웃 템플릿" value={selectedTemplate ? `${selectedTemplate.template_id}:${selectedTemplate.version}` : ''} onChange={(event) => chooseTemplate(event.target.value)}><option value="">결과 구성 미지정 (legacy 호환)</option>{templates.map((template) => <option key={`${template.template_id}:${template.version}`} value={`${template.template_id}:${template.version}`}>{template.display_name} · v{template.version}</option>)}</select></label>
    {!templates.length && <div className="result-profile-empty">게시된 분석 템플릿이 없습니다. API로 DashboardDefinition 기반 템플릿을 먼저 게시하면 이 작업 유형에 연결할 수 있습니다.</div>}
    {selectedTemplate && <><fieldset className="result-profile-contracts"><legend>결과 표시 전 필요한 데이터 계약</legend>{RESULT_DATA_CONTRACTS.map((contract) => <label key={contract}><input type="checkbox" checked={requiredDataContracts.includes(contract) ?? false} onChange={(event) => update({ required_data_contracts: event.target.checked ? normalizeResultDataContracts([...requiredDataContracts, contract]) : requiredDataContracts.filter((item) => item !== contract) })} />{contract}</label>)}</fieldset>
      {validationError && <p className="result-profile-validation" role="alert">{validationError}</p>}<div className="result-profile-widget-list">{widgets.map(({ pageName, widget }) => { const checked = includedWidgetIds.includes(widget.id); const required = requiredWidgetIds.includes(widget.id); return <label key={widget.id} title={`${pageName} · ${widget.title}`}><input type="checkbox" checked={checked} disabled={required || (checked && includedWidgetIds.length === 1)} onChange={() => update({ included_widget_ids: checked ? includedWidgetIds.filter((item) => item !== widget.id) : [...includedWidgetIds, widget.id] })} /><span>{pageName} · {widget.title}{required ? ' · 필수' : ''}</span></label> })}</div>
    </>}
  </section>
}
