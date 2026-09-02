import type { RequestResultDefinition, RequestResultWidget, ResultProfile, WorkbenchTaskType } from './types'
import { normalizeResultDataContracts } from './resultProfileContracts'

export { requestResultDefinitionValidation } from './resultProfileContracts'
import { catalogItemForWidget, widgetIdForType } from './requestResultWidgetCatalog'

export function normalizeRequestResultDefinition(value: RequestResultDefinition | null | undefined): RequestResultDefinition | null {
  if (!value || !Array.isArray(value.widgets)) return null
  return {
    page_name: value.page_name?.trim() || '요청 결과',
    page_description: value.page_description?.trim() || undefined,
    widgets: value.widgets.map((widget, index) => ({
      id: widget.id || widgetIdForType(widget.type, index), type: widget.type,
      title: widget.title?.trim() || catalogItemForWidget(widget).label,
      variable_key: widget.variable_key?.trim() || null,
      data_contracts: normalizeResultDataContracts(widget.data_contracts), required: widget.required === true,
    })),
  }
}

export function requestResultDefinitionFromProfile(profile: ResultProfile | null): RequestResultDefinition | null {
  if (!profile?.template) return null
  const pages = profile.template.page_definitions
  const allWidgets = pages.flatMap((page) => page.widgets)
  const included = new Set(profile.included_widget_ids?.length ? profile.included_widget_ids : allWidgets.map((widget) => widget.id))
  const widgets: RequestResultWidget[] = allWidgets.filter((widget) => included.has(widget.id)).map((widget) => ({
    id: widget.id, type: widget.type, title: widget.title,
    variable_key: typeof widget.settings?.variable_key === 'string' ? widget.settings.variable_key : null,
    data_contracts: normalizeResultDataContracts(Array.isArray(widget.settings?.data_contracts) ? widget.settings.data_contracts : []),
    required: widget.settings?.required === true || widget.settings?.required_widget === true,
  }))
  return normalizeRequestResultDefinition({ page_name: pages[0]?.name || '요청 결과', page_description: pages[0]?.description, widgets })
}
