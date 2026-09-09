import { apiFetch } from './auth'
import { apiUrl } from './url'
import { apiErrorFromResponse } from './errors'
import { apiClient, unwrapGenerated } from './client'
import { responseRecord, responseRecordArray, requireStringField } from './adapters'
import type { components } from './generated/openapi'

export type ModelingTemplateCard = components['schemas']['CardResponse']
export type ModelingTemplateFile = components['schemas']['FileResponse']
export type ModelingTemplateVersion = components['schemas']['VersionResponse']
export type ModelingTemplateDetail = components['schemas']['DetailResponse']
export type ModelingTemplateList = components['schemas']['CatalogResponse']
export type ModelingTemplateFileUpload = components['schemas']['FilePayload']


function adaptCard(value: unknown): ModelingTemplateCard {
  const item = responseRecord(value, 'modelingTemplate')
  for (const key of ['id', 'name', 'product_name', 'load_case_name', 'description', 'created_at', 'updated_at']) requireStringField(item, key, 'modelingTemplate')
  for (const key of ['latest_version', 'file_count', 'total_bytes']) if (typeof item[key] !== 'number' || !Number.isFinite(item[key])) throw new TypeError(`modelingTemplate.${key} 응답 형식이 올바르지 않습니다.`)
  return item as ModelingTemplateCard
}
function adaptVersion(value: unknown): ModelingTemplateVersion {
  const item = responseRecord(value, 'modelingTemplateVersion')
  requireStringField(item, 'created_at', 'modelingTemplateVersion')
  for (const key of ['version', 'file_count', 'total_bytes']) if (typeof item[key] !== 'number' || !Number.isFinite(item[key])) throw new TypeError(`modelingTemplateVersion.${key} 응답 형식이 올바르지 않습니다.`)
  return item as ModelingTemplateVersion
}
function adaptDetail(value: unknown): ModelingTemplateDetail {
  const item = responseRecord(value, 'modelingTemplateDetail')
  const files = responseRecordArray(item.files, 'modelingTemplateFiles').map(file => {
    for (const key of ['id', 'relative_path', 'checksum']) requireStringField(file, key, 'modelingTemplateFile')
    if (typeof file.size_bytes !== 'number' || !Number.isFinite(file.size_bytes)) throw new TypeError('CSV 크기 응답이 올바르지 않습니다.')
    return file as ModelingTemplateFile
  })
  return { ...adaptCard(item), files, versions: responseRecordArray(item.versions, 'modelingTemplateVersions').map(adaptVersion), selected_version: adaptVersion(item.selected_version) }
}
function adaptCatalog(value: unknown): ModelingTemplateList {
  const item = responseRecord(value, 'modelingTemplateCatalog')
  if (!Array.isArray(item.products) || !item.products.every(value => typeof value === 'string') || !Array.isArray(item.load_cases) || !item.load_cases.every(value => typeof value === 'string') || typeof item.can_manage !== 'boolean') throw new TypeError('템플릿 목록 응답이 올바르지 않습니다.')
  return { items: responseRecordArray(item.items, 'modelingTemplates').map(adaptCard), products: item.products, load_cases: item.load_cases, can_manage: item.can_manage }
}

async function download(url: string): Promise<Blob> {
  const response = await apiFetch(url)
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.blob()
}

export const modelingTemplatesApi = {
  list: async (query: { q?: string; product_name?: string; load_case_name?: string } = {}) => adaptCatalog(unwrapGenerated(await apiClient.GET('/api/modeling-templates', { params: { query } }))),
  create: async (body: components['schemas']['CardCreate']) => adaptDetail(unwrapGenerated(await apiClient.POST('/api/modeling-templates', { body }))),
  detail: async (id: string, version?: number) => version === undefined
    ? adaptDetail(unwrapGenerated(await apiClient.GET('/api/modeling-templates/{template_id}', { params: { path: { template_id: id } } })))
    : adaptDetail(unwrapGenerated(await apiClient.GET('/api/modeling-templates/{template_id}/versions/{version}', { params: { path: { template_id: id, version } } }))),
  uploadVersion: async (id: string, expected_version: number, mode: 'merge' | 'replace', files: ModelingTemplateFileUpload[]) => adaptDetail(unwrapGenerated(await apiClient.POST('/api/modeling-templates/{template_id}/versions', { params: { path: { template_id: id } }, body: { expected_version, mode, files } }))),
  zip: (id: string, version: number) => download(apiUrl('/api/modeling-templates/{template_id}/versions/{version}/download', { template_id: id, version })),
  file: (id: string, version: number, fileId: string) => download(apiUrl('/api/modeling-templates/{template_id}/versions/{version}/files/{file_id}/download', { template_id: id, version, file_id: fileId })),
}
