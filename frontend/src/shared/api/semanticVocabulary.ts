import { apiClient, unwrapGenerated } from './client'
import type { components } from './generated/openapi'

export type VocabularyTargetKind = components['schemas']['VocabularyBody']['target_kind']
export type VocabularyMatchStatus = 'MATCHED' | 'UNMAPPED' | 'AMBIGUOUS'

export type SemanticVocabularyEntry = {
  id: string
  key: string
  label: string
  description: string
  target_kind: VocabularyTargetKind
  target_id: string
  scope_project_id: string | null
  aliases: string[]
  enabled: boolean
  revision: number
  target_label: string
  project_id?: string | null
  request_id?: string | null
  load_case_id?: string | null
  target_available: boolean
}

export type SemanticVocabularyEntryInput = Omit<components['schemas']['VocabularyBody'], 'scope_project_id' | 'aliases'> & { scope_project_id: string | null; aliases: string[] }
export type SemanticVocabularyResolveMatch = {
  term: string
  status: VocabularyMatchStatus
  candidates: SemanticVocabularyEntry[]
}
export type SemanticVocabularyResolveResponse = { matches: SemanticVocabularyResolveMatch[] }

export const semanticVocabularyApi = {
  list: async (): Promise<{ entries: SemanticVocabularyEntry[] }> => unwrapGenerated(await apiClient.GET('/api/semantic-vocabulary')) as { entries: SemanticVocabularyEntry[] },
  create: async (body: SemanticVocabularyEntryInput): Promise<SemanticVocabularyEntry> => unwrapGenerated(await apiClient.POST('/api/semantic-vocabulary', { body })) as SemanticVocabularyEntry,
  update: async (id: string, body: SemanticVocabularyEntryInput, expectedRevision: number): Promise<SemanticVocabularyEntry> => unwrapGenerated(await apiClient.PUT('/api/semantic-vocabulary/{entry_id}', { params: { path: { entry_id: id } }, body: { ...body, expected_revision: expectedRevision } })) as SemanticVocabularyEntry,
  resolve: async (terms: string[], targetKinds?: VocabularyTargetKind[], scopeProjectId?: string): Promise<SemanticVocabularyResolveResponse> => unwrapGenerated(await apiClient.POST('/api/semantic-vocabulary/resolve', { body: { terms, target_kinds: targetKinds, scope_project_id: scopeProjectId } satisfies components['schemas']['ResolveBody'] })) as SemanticVocabularyResolveResponse,
}

export function normalizeVocabularyTerm(value: string): string {
  return value.normalize('NFKC').toLocaleLowerCase().trim().replace(/\s+/g, ' ')
}
