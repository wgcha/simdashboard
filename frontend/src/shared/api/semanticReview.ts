import { apiClient, unwrapGenerated } from './client'
import type { PreviewResponse, SemanticBinding, SemanticCatalog } from './semanticMapping'
import type { components } from './generated/openapi'

export type ReviewState = 'OPEN' | 'SELECTED' | 'READY' | 'STALE' | 'IMPORTED' | 'SKIPPED' | string
export type ReviewListState = 'OPEN' | 'SELECTED' | 'READY' | 'STALE' | 'IMPORTED' | 'SKIPPED'
export type ScanStatus = 'UNMAPPED' | 'AMBIGUOUS' | 'INVALID' | 'PENDING' | string
export type ReviewCandidate = components['schemas']['ReviewCandidate']
export type SemanticReviewItem = components['schemas']['ReviewItemResponse'] & { widgets?: PreviewResponse['widgets']; summary?: unknown }
export type ReviewListResponse = components['schemas']['ReviewPageResponse']
export type ReviewRevalidation = SemanticReviewItem & { widgets?: PreviewResponse['widgets']; summary?: unknown }
export type ReviewConfirmation = { status: 'IMPORTED' | 'SKIPPED' | string; run_id?: string | null; item?: SemanticReviewItem | null }
export type ReviewEvent = components['schemas']['ReviewEventResponse']
export type ReviewHistoryResponse = components['schemas']['ReviewHistoryResponse']

export const semanticReviewApi = {
  list: async (bindingId: string, query: { state?: ReviewListState; limit?: number; cursor?: string }, signal?: AbortSignal) => unwrapGenerated(await apiClient.GET('/api/semantic-mapping/bindings/{binding_id}/review-items', { params: { path: { binding_id: bindingId }, query }, signal })) as ReviewListResponse,
  revalidate: async (id: string, body: { expected_revision: number; recipe_id?: string; recipe_version?: number }, signal?: AbortSignal) => unwrapGenerated(await apiClient.POST('/api/semantic-mapping/review-items/{item_id}/revalidate', { params: { path: { item_id: id } }, body, signal })) as ReviewRevalidation,
  confirm: async (id: string, body: { expected_revision: number }, signal?: AbortSignal) => unwrapGenerated(await apiClient.POST('/api/semantic-mapping/review-items/{item_id}/confirm', { params: { path: { item_id: id } }, body, signal })) as ReviewConfirmation,
  reopen: async (id: string, body: { expected_revision: number }, signal?: AbortSignal) => unwrapGenerated(await apiClient.POST('/api/semantic-mapping/review-items/{item_id}/reopen', { params: { path: { item_id: id } }, body, signal })) as SemanticReviewItem,
  history: async (id: string, signal?: AbortSignal) => unwrapGenerated(await apiClient.GET('/api/semantic-mapping/review-items/{item_id}/history', { params: { path: { item_id: id } }, signal })) as ReviewHistoryResponse,
}

export type ReviewQueueProps = { binding: SemanticBinding; catalog: SemanticCatalog; canReview: boolean; refreshToken?: number; onMessage: (message: { kind: 'success' | 'error' | 'info'; text: string }) => void }
