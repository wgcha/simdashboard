import type { ReportLayout, ReportLayoutDefinition, ReportLayoutVersion } from '../../types'
import { responseRecord, responseRecordArray } from '../../shared/api/adapters'
import { apiClient, unwrapGenerated } from '../../shared/api/client'
import type { components } from '../../shared/api/generated/openapi'

type ReportLayoutDefinitionResponse = components['schemas']['ReportLayoutDefinition']
type ReportVariablePlacementResponse = components['schemas']['ReportVariablePlacement']
type ReportLayoutVersionSummaryResponse = components['schemas']['ReportLayoutVersionSummaryResponse']
type ReportLayoutVersionDetailResponse = components['schemas']['ReportLayoutVersionDetailResponse']
type ReportLayoutDeactivationResponse = components['schemas']['ReportLayoutDeactivationResponse']

function stringField(value: Record<string, unknown>, key: string, label: string): string {
  const field = value[key]
  if (typeof field !== 'string') throw new TypeError(`${label}.${key} 응답 형식이 올바르지 않습니다.`)
  return field
}

function numberField(value: Record<string, unknown>, key: string, label: string): number {
  const field = value[key]
  if (typeof field !== 'number' || !Number.isFinite(field)) throw new TypeError(`${label}.${key} 응답 형식이 올바르지 않습니다.`)
  return field
}

function booleanField(value: Record<string, unknown>, key: string, label: string): boolean {
  const field = value[key]
  if (typeof field !== 'boolean') throw new TypeError(`${label}.${key} 응답 형식이 올바르지 않습니다.`)
  return field
}

function coverVariant(value: unknown, label: string): ReportLayoutDefinitionResponse['coverVariant'] {
  if (value === 'balanced' || value === 'executive' || value === 'evidence') return value
  throw new TypeError(`${label} 응답 값이 올바르지 않습니다.`)
}

function section(value: unknown, label: string): ReportLayoutDefinitionResponse['sectionOrder'][number] {
  if (value === 'series' || value === 'scalar' || value === 'media') return value
  throw new TypeError(`${label} 응답 값이 올바르지 않습니다.`)
}

function placementPresentation(value: unknown, label: string): ReportVariablePlacementResponse['presentation'] {
  if (value === 'chart' || value === 'table' || value === 'both') return value
  throw new TypeError(`${label} 응답 값이 올바르지 않습니다.`)
}

function adaptPlacement(value: unknown): ReportVariablePlacementResponse {
  const item = responseRecord(value, 'reportLayout.definition.variablePlacements')
  return {
    ...item,
    variableKey: stringField(item, 'variableKey', 'reportLayout.definition.variablePlacements'),
    presentation: placementPresentation(item.presentation, 'reportLayout.definition.variablePlacements.presentation'),
    order: numberField(item, 'order', 'reportLayout.definition.variablePlacements'),
  }
}

function adaptDefinition(value: unknown): ReportLayoutDefinitionResponse {
  const item = responseRecord(value, 'reportLayout.definition')
  const sectionOrder = item.sectionOrder
  const placements = item.variablePlacements
  if (!Array.isArray(sectionOrder)) throw new TypeError('reportLayout.definition.sectionOrder 응답 형식이 올바르지 않습니다.')
  if (!Array.isArray(placements)) throw new TypeError('reportLayout.definition.variablePlacements 응답 형식이 올바르지 않습니다.')
  return {
    ...item,
    id: stringField(item, 'id', 'reportLayout.definition'),
    name: stringField(item, 'name', 'reportLayout.definition'),
    description: stringField(item, 'description', 'reportLayout.definition'),
    version: numberField(item, 'version', 'reportLayout.definition'),
    coverVariant: coverVariant(item.coverVariant, 'reportLayout.definition.coverVariant'),
    accentColor: stringField(item, 'accentColor', 'reportLayout.definition'),
    sectionOrder: sectionOrder.map((item) => section(item, 'reportLayout.definition.sectionOrder')),
    variablePlacements: placements.map(adaptPlacement),
    includeMedia: booleanField(item, 'includeMedia', 'reportLayout.definition'),
  }
}

function adaptLayout(value: unknown): ReportLayout {
  const item = responseRecord(value, 'reportLayout')
  const description = item.description
  if (description !== null && typeof description !== 'string') throw new TypeError('reportLayout.description 응답 형식이 올바르지 않습니다.')
  return {
    ...item,
    id: stringField(item, 'id', 'reportLayout'),
    name: stringField(item, 'name', 'reportLayout'),
    description: description ?? '',
    version: numberField(item, 'version', 'reportLayout'),
    definition: adaptDefinition(item.definition),
    is_system: booleanField(item, 'is_system', 'reportLayout'),
    updated_at: stringField(item, 'updated_at', 'reportLayout'),
    updated_by: stringField(item, 'updated_by', 'reportLayout'),
  }
}

function adaptVersions(value: unknown): ReportLayoutVersion[] {
  return responseRecordArray(value, 'reportLayoutVersions').map((item): ReportLayoutVersionSummaryResponse => ({
    ...item,
    layout_id: stringField(item, 'layout_id', 'reportLayoutVersions'),
    version: numberField(item, 'version', 'reportLayoutVersions'),
    created_by: stringField(item, 'created_by', 'reportLayoutVersions'),
    created_at: stringField(item, 'created_at', 'reportLayoutVersions'),
    is_valid: booleanField(item, 'is_valid', 'reportLayoutVersions'),
  }))
}

function adaptVersion(value: unknown): ReportLayoutVersionDetailResponse {
  const item = responseRecord(value, 'reportLayoutVersion')
  return {
    ...item,
    layout_id: stringField(item, 'layout_id', 'reportLayoutVersion'),
    version: numberField(item, 'version', 'reportLayoutVersion'),
    definition: adaptDefinition(item.definition),
    created_by: stringField(item, 'created_by', 'reportLayoutVersion'),
    created_at: stringField(item, 'created_at', 'reportLayoutVersion'),
  }
}

function adaptDeactivation(value: unknown): ReportLayoutDeactivationResponse {
  const item = responseRecord(value, 'reportLayoutDeactivation')
  const status = stringField(item, 'status', 'reportLayoutDeactivation')
  if (status !== 'deactivated') throw new TypeError('reportLayoutDeactivation.status 응답 값이 올바르지 않습니다.')
  return { ...item, status, id: stringField(item, 'id', 'reportLayoutDeactivation') }
}

export const reportApi = {
  layouts: async () => responseRecordArray(unwrapGenerated(await apiClient.GET('/api/report-layouts')), 'reportLayouts').map(adaptLayout),
  createLayout: async (payload: { name: string; description: string; definition: ReportLayoutDefinition; updated_by: string }) =>
    adaptLayout(unwrapGenerated(await apiClient.POST('/api/report-layouts', { body: payload }))),
  updateLayout: async (layoutId: string, payload: { name: string; description: string; definition: ReportLayoutDefinition; updated_by: string }) =>
    adaptLayout(unwrapGenerated(await apiClient.PUT('/api/report-layouts/{layout_id}', { params: { path: { layout_id: layoutId } }, body: payload }))),
  deleteLayout: async (layoutId: string) => adaptDeactivation(unwrapGenerated(await apiClient.DELETE('/api/report-layouts/{layout_id}', { params: { path: { layout_id: layoutId } } }))),
  versions: async (layoutId: string) => adaptVersions(unwrapGenerated(await apiClient.GET('/api/report-layouts/{layout_id}/versions', { params: { path: { layout_id: layoutId } } }))),
  version: async (layoutId: string, version: number) =>
    adaptVersion(unwrapGenerated(await apiClient.GET('/api/report-layouts/{layout_id}/versions/{version}', { params: { path: { layout_id: layoutId, version } } }))),
}
