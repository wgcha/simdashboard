import { apiClient, unwrapGenerated } from './client'
import type { components } from './generated/openapi'

export type SemanticItemKind = 'scalar' | 'vector' | 'curve' | 'image' | 'video'
export type SemanticDataType = 'FLOAT' | 'INTEGER' | 'TEXT' | 'BOOLEAN'
export type SemanticFormat = 'csv' | 'json'
export type MissingPolicy = 'error' | 'skip' | 'preserve'
export type AggregatePolicy = 'none' | 'max' | 'min' | 'mean'

export type SemanticItemDefinition = { id?: string; key: string; label: string; kind: SemanticItemKind; data_type: SemanticDataType; unit: string; dimensions: string[]; components?: string[] }
export type RecipeMapping = { result_item_id: string; source: string; x_source?: string; series_source?: string; dimensions: Record<string, string>; source_unit?: string; x_unit?: string; target_x_unit?: string; missing: MissingPolicy; aggregate?: AggregatePolicy }
export type SemanticRecipeDefinition = { reader_version?: number; input_layout?: string; format: SemanticFormat; delimiter: string; encoding: string; header_row: number; records_path: string; required_fields: string[]; mappings: RecipeMapping[]; display_template_id?: string; display_template_version?: number }
export type SemanticWidgetType = 'kpi' | 'gauge' | 'table' | 'bar' | 'line' | 'scatter' | 'image' | 'video'
export type SemanticWidgetDefinition = { id: string; type: SemanticWidgetType; title: string; item_ids: string[]; x_item_id?: string; y_item_id?: string; filters: Record<string, string>; decimals: number; display_unit?: string; threshold?: number; vector_component?: string; x_display_unit?: string; y_display_unit?: string }
export type SemanticTemplateDefinition = { widgets: SemanticWidgetDefinition[] }
export type VersionedDefinition<T> = { id: string; name?: string; version?: number; latest_version?: number; active_version?: number | null; active_format?: SemanticFormat; lifecycle_status?: string; status?: string; definition: T; updated_at?: string; updated_by?: string }
export type SemanticBinding = { id: string; relative_path: string; project_id: string; request_id?: string | null; load_case_id?: string | null; role: string; recipe_ids: string[]; template_id?: string | null; status?: string; updated_at?: string; revision?: number }
export type SemanticCatalog = { items: VersionedDefinition<SemanticItemDefinition>[]; recipes: VersionedDefinition<SemanticRecipeDefinition>[]; templates: VersionedDefinition<SemanticTemplateDefinition>[]; bindings: SemanticBinding[] }
export type InspectFieldDetail = {
  source?: string
  label?: string
  value?: unknown
  data_type?: string
  unit?: string | null
  missing?: boolean
  missing_count?: number
  null_count?: number
  empty_count?: number
  mappable?: boolean
  [key: string]: unknown
}
export type InspectOptions = { row_offset?: number; row_limit?: number; field_offset?: number; field_limit?: number; overrides?: Record<string, unknown> }
export type RecipeSuggestion = { reader_version?: number; input_layout?: string; format?: SemanticFormat; encoding?: string; delimiter?: string | null; header_row?: number | null; records_path?: string; [key: string]: unknown }
export type InspectResponse = {
  format: SemanticFormat
  fields: string[]
  rows: Record<string, unknown>[]
  row_count?: number
  field_count?: number
  field_details?: InspectFieldDetail[]
  recipe_suggestion?: RecipeSuggestion | null
  upload_id?: string
  row_offset?: number
  row_limit?: number
  field_offset?: number
  field_limit?: number
  has_more_rows?: boolean
  has_more_fields?: boolean
  warnings?: string[]
  page?: { row_offset?: number; row_limit?: number; field_offset?: number; field_limit?: number; has_more_rows?: boolean; has_more_fields?: boolean; [key: string]: unknown }
}
export type ParsedSemanticResult = { schema_id?: string; schema_version?: number; scalars?: Array<Record<string, unknown>>; vectors?: Array<Record<string, unknown>>; curves?: Array<Record<string, unknown>>; media?: Array<Record<string, unknown>>; observations?: Array<Record<string, unknown>>; summary?: Record<string, unknown>; warnings?: string[]; note?: string }
export type PreviewWidget = { id: string; type: SemanticWidgetType; title: string; status: 'READY' | 'MISSING_RESULT' | 'AMBIGUOUS_RESULT' | 'INCOMPATIBLE_RESULT' | string; unit?: string; data?: unknown[]; points?: Array<{ x: number; y: number; series?: string }>; message?: string; decimals?: number; threshold?: number; verdict?: string; x_unit?: string; y_unit?: string }
export type PreviewResponse = { parsed: ParsedSemanticResult; widgets: PreviewWidget[] }
export type ImportResponse = PreviewResponse & { status: string; run_id?: string; summary?: Record<string, unknown>; recipe_version?: number; template_version?: number; template_id?: string | null; review_available?: boolean; clear_reason?: string | null }
export type ResultsResponse = { widgets: PreviewWidget[]; run_id?: string; template_version?: number; has_provenance?: boolean; empty_reason?: string | null }
export type FolderResponse = { path?: string; relative_path?: string; entries?: Array<{ name: string; relative_path: string; is_directory: boolean }> }
export type ContextProject = { id: string; name: string; product_name?: string }
export type ContextRequest = { id: string; project_id: string; title: string; status?: string }
export type ContextLoadCase = { id: string; request_id: string; name: string; analysis_type?: string; status?: string }

type UploadCall = { body: FormData; bodySerializer: (value: unknown) => BodyInit }

function uploadForm(file: File, fields: Record<string, string | undefined> = {}) {
  const form = new FormData()
  form.append('file', file)
  Object.entries(fields).forEach(([key, value]) => { if (value !== undefined) form.append(key, value) })
  return form
}

function inspectFields(options: InspectOptions = {}): Record<string, string | undefined> {
  return {
    row_offset: options.row_offset === undefined ? undefined : String(options.row_offset),
    row_limit: options.row_limit === undefined ? undefined : String(options.row_limit),
    field_offset: options.field_offset === undefined ? undefined : String(options.field_offset),
    field_limit: options.field_limit === undefined ? undefined : String(options.field_limit),
    overrides: options.overrides ? JSON.stringify(options.overrides) : undefined,
  }
}

function uploadOptions(form: FormData): UploadCall { return { body: form, bodySerializer: (value) => value as BodyInit } }

function records<T extends object>(payload: unknown, key: string): T[] {
  if (Array.isArray(payload)) return payload as T[]
  if (payload && typeof payload === 'object' && Array.isArray((payload as Record<string, unknown>)[key])) return (payload as Record<string, unknown>)[key] as T[]
  return []
}

export type DefinitionSave<T> = { id?: string; name: string; definition: T; expected_version?: number; sample_upload_id?: string }
export type SemanticConfiguration = { recipe: VersionedDefinition<SemanticRecipeDefinition>; template: VersionedDefinition<SemanticTemplateDefinition> | null }
export type SemanticItemUsage = { id: string; recipes: number; templates: number; widgets: number; [key: string]: unknown }
export const semanticMappingApi = {
  catalog: async () => unwrapGenerated(await apiClient.GET('/api/semantic-mapping/catalog')) as SemanticCatalog,
  saveItem: async (definition: SemanticItemDefinition, id?: string, expected_version?: number) => unwrapGenerated(await apiClient.POST('/api/semantic-mapping/items', { body: { definition, id, expected_version } as components['schemas']['ItemSave'] })) as VersionedDefinition<SemanticItemDefinition>,
  itemUsage: async (id: string) => unwrapGenerated(await apiClient.GET('/api/semantic-mapping/items/{item_id}/usage', { params: { path: { item_id: id } } })) as SemanticItemUsage,
  archiveItem: async (id: string, expected_version: number) => unwrapGenerated(await apiClient.POST('/api/semantic-mapping/items/{item_id}/archive', { params: { path: { item_id: id } }, body: { expected_version } })) as { id: string; version: number; lifecycle_status: string },
  restoreItem: async (id: string, expected_version: number) => unwrapGenerated(await apiClient.POST('/api/semantic-mapping/items/{item_id}/restore', { params: { path: { item_id: id } }, body: { expected_version } })) as { id: string; version: number; lifecycle_status: string },
  configuration: async (id: string) => unwrapGenerated(await apiClient.GET('/api/semantic-mapping/configurations/{recipe_id}', { params: { path: { recipe_id: id } } })) as SemanticConfiguration,
  saveConfiguration: async (recipe: DefinitionSave<SemanticRecipeDefinition>, template: DefinitionSave<SemanticTemplateDefinition>, sample?: File) => unwrapGenerated(await apiClient.POST('/api/semantic-mapping/configurations', { body: { recipe: { ...recipe, ...(sample && !recipe.sample_upload_id ? { sample_filename: sample.name, sample_content_base64: await bytesToBase64(sample.arrayBuffer()) } : {}) }, template } as components['schemas']['ConfigurationSave'] })) as SemanticConfiguration,
  createItem: async (definition: SemanticItemDefinition) => unwrapGenerated(await apiClient.POST('/api/semantic-mapping/items', { body: { definition } as components['schemas']['ItemSave'] })) as VersionedDefinition<SemanticItemDefinition>,
  saveRecipe: async (payload: { id?: string; name: string; definition: SemanticRecipeDefinition; expected_version?: number; sample_upload_id?: string }, sample?: File) => {
    const body: components['schemas']['DefinitionSave'] = { ...payload, ...(sample && !payload.sample_upload_id ? { sample_filename: sample.name, sample_content_base64: await bytesToBase64(sample.arrayBuffer()) } : {}) }
    return unwrapGenerated(await apiClient.POST('/api/semantic-mapping/recipes', { body })) as VersionedDefinition<SemanticRecipeDefinition>
  },
  saveTemplate: async (payload: { id?: string; name: string; definition: SemanticTemplateDefinition; expected_version?: number }) => unwrapGenerated(await apiClient.POST('/api/semantic-mapping/templates', { body: payload })) as VersionedDefinition<SemanticTemplateDefinition>,
  activateRecipe: async (id: string, version: number) => unwrapGenerated(await apiClient.POST('/api/semantic-mapping/recipes/{recipe_id}/activate', { params: { path: { recipe_id: id } }, body: { version } })) as VersionedDefinition<SemanticRecipeDefinition>,
  activateTemplate: async (id: string, version: number) => unwrapGenerated(await apiClient.POST('/api/semantic-mapping/templates/{template_id}/activate', { params: { path: { template_id: id } }, body: { version } })) as VersionedDefinition<SemanticTemplateDefinition>,
  activateBundle: async (payload: components['schemas']['BundleActivation']) => unwrapGenerated(await apiClient.POST('/api/semantic-mapping/activate-bundle', { body: payload })) as { recipe_version?: number; template_version?: number; [key: string]: unknown },
  inspect: async (file: File, options: InspectOptions = {}, signal?: AbortSignal) => unwrapGenerated(await apiClient.POST('/api/semantic-mapping/inspect', { ...uploadOptions(uploadForm(file, inspectFields(options))), signal } as never)) as InspectResponse,
  inspectUpload: async (uploadId: string, options: InspectOptions = {}, signal?: AbortSignal) => {
    const fields = inspectFields(options)
    return unwrapGenerated(await apiClient.GET('/api/semantic-mapping/sample-uploads/{upload_id}', { params: { path: { upload_id: uploadId }, query: { row_offset: fields.row_offset ? Number(fields.row_offset) : undefined, row_limit: fields.row_limit ? Number(fields.row_limit) : undefined, field_offset: fields.field_offset ? Number(fields.field_offset) : undefined, field_limit: fields.field_limit ? Number(fields.field_limit) : undefined, overrides: fields.overrides ?? undefined } }, signal } as never)) as InspectResponse
  },
  deleteUpload: async (uploadId: string) => unwrapGenerated(await apiClient.DELETE('/api/semantic-mapping/sample-uploads/{upload_id}', { params: { path: { upload_id: uploadId } } })) as { status: string },
  preview: async (file: File | undefined, recipe: SemanticRecipeDefinition, template?: SemanticTemplateDefinition, uploadId?: string, signal?: AbortSignal) => {
    const form = uploadId ? uploadForm(file as File, { upload_id: uploadId, recipe: JSON.stringify(recipe), template: template ? JSON.stringify(template) : undefined }) : file ? uploadForm(file, { recipe: JSON.stringify(recipe), template: template ? JSON.stringify(template) : undefined }) : uploadForm(new File([''], 'sample.csv'), { upload_id: uploadId, recipe: JSON.stringify(recipe), template: template ? JSON.stringify(template) : undefined })
    if (uploadId) form.delete('file')
    return unwrapGenerated(await apiClient.POST('/api/semantic-mapping/preview', { ...uploadOptions(form), signal } as never)) as PreviewResponse
  },
  folders: async (relativePath: string) => unwrapGenerated(await apiClient.GET('/api/semantic-mapping/folders', { params: { query: { relative_path: relativePath || null } } })) as FolderResponse,
  createBinding: async (binding: Omit<SemanticBinding, 'id' | 'status' | 'updated_at' | 'revision'>) => unwrapGenerated(await apiClient.POST('/api/semantic-mapping/bindings', { body: binding as components['schemas']['BindingSave'] })) as SemanticBinding,
  reconnectBinding: async (id: string, binding: Omit<SemanticBinding, 'id' | 'status' | 'updated_at' | 'revision'> & { expected_revision: number }) => unwrapGenerated(await apiClient.PUT('/api/semantic-mapping/bindings/{binding_id}', { params: { path: { binding_id: id } }, body: { ...binding, id } as components['schemas']['BindingSave'] })) as SemanticBinding,
  refreshBinding: async (id: string) => unwrapGenerated(await apiClient.POST('/api/semantic-mapping/bindings/{binding_id}/refresh', { params: { path: { binding_id: id } } })),
  importFile: async (file: File, recipeId: string, loadCaseId: string, templateId?: string) => unwrapGenerated(await apiClient.POST('/api/semantic-mapping/import', uploadOptions(uploadForm(file, { recipe_id: recipeId, load_case_id: loadCaseId, template_id: templateId })) as never)) as ImportResponse,
  results: async (params: { load_case_id?: string; run_id?: string; template_id?: string }) => unwrapGenerated(await apiClient.GET('/api/semantic-mapping/results', { params: { query: { load_case_id: params.load_case_id ?? '', run_id: params.run_id, template_id: params.template_id } } })) as ResultsResponse,
  exportDefinitions: async () => unwrapGenerated(await apiClient.GET('/api/semantic-mapping/export')) as { format_version: number; items: unknown[]; recipes: unknown[]; templates: unknown[]; warnings?: Array<{ recipe_id: string; code: string }> },
  importDefinitions: async (definitions: unknown) => unwrapGenerated(await apiClient.POST('/api/semantic-mapping/import-definitions', { body: definitions as Record<string, unknown> })) as { status: string; created: unknown[] },
}

export const semanticContextApi = {
  projects: async () => records<ContextProject>(unwrapGenerated(await apiClient.GET('/api/projects')), 'projects'),
  requests: async (projectId: string) => records<ContextRequest>(unwrapGenerated(await apiClient.GET('/api/projects/{project_id}/requests', { params: { path: { project_id: projectId } } })), 'requests'),
  loadCases: async (requestId: string) => records<ContextLoadCase>(unwrapGenerated(await apiClient.GET('/api/requests/{request_id}/load-cases', { params: { path: { request_id: requestId } } })), 'loadCases'),
}

async function bytesToBase64(bufferPromise: Promise<ArrayBuffer>) {
  let binary = ''
  for (const byte of new Uint8Array(await bufferPromise)) binary += String.fromCharCode(byte)
  return globalThis.btoa(binary)
}
