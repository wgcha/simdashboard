// Per-user notifications API (docs/features/notifications.md).
import { apiClient, unwrapGenerated } from './client'
import type { components } from './generated/openapi'

export type NotificationItem = components['schemas']['NotificationItem']
export type NotificationList = components['schemas']['NotificationList']
export type NotificationReadResult = components['schemas']['NotificationReadResult']

/** Badge and list refresh cadence: the same 60 s as the auto-reflect and drive status polls. */
export const NOTIFICATION_POLL_MS = 60_000
/** Window event the bell and the page use to refresh each other after a read change. */
export const NOTIFICATIONS_CHANGED_EVENT = 'simdashboard:notifications-changed'

export const notificationsApi = {
  list: async (query: { unread_only?: boolean; type?: string; limit?: number; offset?: number } = {}, signal?: AbortSignal) =>
    unwrapGenerated(await apiClient.GET('/api/notifications', { params: { query }, signal })) as NotificationList,
  unreadCount: async (signal?: AbortSignal) =>
    (unwrapGenerated(await apiClient.GET('/api/notifications/unread-count', { signal })) as { unread_count: number }).unread_count,
  markRead: async (ids: string[]) =>
    unwrapGenerated(await apiClient.POST('/api/notifications/read', { body: { ids, all: false } })) as NotificationReadResult,
  markAllRead: async () =>
    unwrapGenerated(await apiClient.POST('/api/notifications/read', { body: { ids: null, all: true } })) as NotificationReadResult,
}

export function announceNotificationsChanged(): void {
  window.dispatchEvent(new Event(NOTIFICATIONS_CHANGED_EVENT))
}

/** "3분 전" style relative time; older than a week shows the date. */
export function notificationTime(value: string | null | undefined, now = Date.now()): string {
  if (!value) return ''
  const time = Date.parse(value)
  if (Number.isNaN(time)) return ''
  const minutes = Math.max(0, Math.round((now - time) / 60_000))
  if (minutes < 1) return '방금'
  if (minutes < 60) return `${minutes}분 전`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}시간 전`
  const days = Math.round(hours / 24)
  if (days < 7) return `${days}일 전`
  return new Date(time).toLocaleDateString('ko-KR')
}
