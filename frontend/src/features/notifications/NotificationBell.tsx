import { Bell, CheckCheck, LoaderCircle } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { ApiError } from '../../shared/api/errors'
import {
  NOTIFICATIONS_CHANGED_EVENT,
  NOTIFICATION_POLL_MS,
  announceNotificationsChanged,
  notificationsApi,
  type NotificationItem,
} from '../../shared/api/notifications'
import { NotificationEntry } from './NotificationEntry'
import './notifications.css'

export const NOTIFICATIONS_PATH = '/workspace/notifications'
const DROPDOWN_LIMIT = 8

/** Top bar bell: unread badge (60 s poll + window focus) and a dropdown of recent notifications. */
export function NotificationBell() {
  const navigate = useNavigate()
  const [unread, setUnread] = useState(0)
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<NotificationItem[] | null>(null)
  const [error, setError] = useState('')
  const [unavailable, setUnavailable] = useState(false)
  const root = useRef<HTMLDivElement>(null)

  const refreshCount = useCallback(async () => {
    try {
      setUnread(await notificationsApi.unreadCount())
    } catch (reason) {
      if (reason instanceof ApiError && (reason.status === 401 || reason.status === 403)) setUnavailable(true)
    }
  }, [])

  const loadRecent = useCallback(async () => {
    setError('')
    try {
      const list = await notificationsApi.list({ limit: DROPDOWN_LIMIT })
      setItems(list.items)
      setUnread(list.unread_count)
    } catch {
      setError('알림을 불러오지 못했습니다.')
    }
  }, [])

  useEffect(() => {
    void refreshCount()
    const timer = window.setInterval(() => { if (document.visibilityState !== 'hidden') void refreshCount() }, NOTIFICATION_POLL_MS)
    const onFocus = () => { void refreshCount() }
    const onChanged = () => { void refreshCount(); if (open) void loadRecent() }
    window.addEventListener('focus', onFocus)
    window.addEventListener(NOTIFICATIONS_CHANGED_EVENT, onChanged)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', onFocus)
      window.removeEventListener(NOTIFICATIONS_CHANGED_EVENT, onChanged)
    }
  }, [loadRecent, open, refreshCount])

  useEffect(() => {
    if (!open) return
    void loadRecent()
    const onPointer = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false) }
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('pointerdown', onPointer); document.removeEventListener('keydown', onKey) }
  }, [loadRecent, open])

  if (unavailable) return null

  const openItem = async (item: NotificationItem) => {
    if (!item.read) {
      try { await notificationsApi.markRead([item.id]); announceNotificationsChanged() } catch { /* the link still opens */ }
    }
    setOpen(false)
    if (item.link) navigate(item.link)
  }
  const markAll = async () => {
    try { await notificationsApi.markAllRead(); announceNotificationsChanged(); await loadRecent() } catch { setError('읽음 처리하지 못했습니다.') }
  }
  const label = unread ? `알림, 읽지 않은 알림 ${unread}건` : '알림'

  return <div className="notification-bell" ref={root}>
    <button type="button" className="notification-bell__trigger" aria-label={label} title={label} aria-haspopup="dialog" aria-expanded={open}
      data-testid="notification-bell" onClick={() => setOpen((value) => !value)}>
      <Bell aria-hidden="true" />
      {unread > 0 && <span className="notification-bell__badge" data-testid="notification-badge">{unread > 99 ? '99+' : unread}</span>}
    </button>
    {open && <div className="notification-popover" role="dialog" aria-label="최근 알림">
      <header className="notification-popover__head">
        <strong>알림</strong>
        <button type="button" className="notification-popover__mark" onClick={() => void markAll()} disabled={!unread}><CheckCheck aria-hidden="true" />모두 읽음</button>
      </header>
      {error && <p className="notification-popover__error" role="alert">{error}</p>}
      {items === null && !error ? <p className="notification-popover__empty"><LoaderCircle className="spin" aria-hidden="true" /> 불러오는 중</p>
        : items && items.length === 0 ? <p className="notification-popover__empty">새 알림이 없습니다.</p>
          : <ul className="notification-popover__items">{(items ?? []).map((item) => <li key={item.id}>
            <NotificationEntry item={item} compact onOpen={() => void openItem(item)} />
          </li>)}</ul>}
      <footer className="notification-popover__foot">
        <button type="button" onClick={() => { setOpen(false); navigate(NOTIFICATIONS_PATH) }}>모두 보기</button>
      </footer>
    </div>}
  </div>
}
