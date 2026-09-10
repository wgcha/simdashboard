import { apiClient, unwrapGenerated } from './client'

export type VocPost = { id: string; author_user_id: string; author_username: string; author_display_name: string; content: string; created_at: string }
export type VocPage = { items: VocPost[]; total: number; limit: number; offset: number }

function page(value: unknown): VocPage {
  if (!value || typeof value !== 'object') throw new Error('VOC 목록 응답이 올바르지 않습니다.')
  const item = value as VocPage
  if (!Array.isArray(item.items) || !Number.isSafeInteger(item.total) || !Number.isSafeInteger(item.limit) || !Number.isSafeInteger(item.offset)) throw new Error('VOC 목록 응답이 올바르지 않습니다.')
  return item
}

export const vocApi = {
  list: async (limit = 50, offset = 0, signal?: AbortSignal) => page(unwrapGenerated(await apiClient.GET('/api/voc/posts', { params: { query: { limit, offset } }, signal }))),
  create: async (content: string, signal?: AbortSignal) => unwrapGenerated(await apiClient.POST('/api/voc/posts', { body: { content }, signal })) as VocPost,
  download: async (format: 'csv' | 'json', signal?: AbortSignal) => {
    const blob = unwrapGenerated(await apiClient.GET('/api/voc/export', { params: { query: { format } }, parseAs: 'blob', signal })) as Blob
    const anchor = document.createElement('a')
    anchor.href = URL.createObjectURL(blob)
    anchor.download = `voc-posts.${format}`
    document.body.append(anchor)
    anchor.click()
    anchor.remove()
    setTimeout(() => URL.revokeObjectURL(anchor.href), 0)
  },
}
