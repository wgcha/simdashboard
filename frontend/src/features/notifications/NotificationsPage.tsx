import { Bell, CheckCheck, ExternalLink, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  NOTIFICATIONS_CHANGED_EVENT,
  NOTIFICATION_POLL_MS,
  announceNotificationsChanged,
  notificationsApi,
  type NotificationItem,
  type NotificationList,
} from '../../shared/api/notifications'
import { NotificationEntry } from './NotificationEntry'
import './notifications.css'

const PAGE_SIZE = 50
const MAX_LIMIT = 200

/** ``/workspace/notifications``: every notification of the signed-in user with filters and read state. */
export function NotificationsPage() {
  const navigate = useNavigate()
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [type, setType] = useState('')
  const [data, setData] = useState<NotificationList | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async (limit = PAGE_SIZE) => {
    setError('')
    try {
      setData(await notificationsApi.list({ unread_only: unreadOnly, type: type || undefined, limit: Math.min(MAX_LIMIT, limit) }))
    } catch {
      setError('알림을 불러오지 못했습니다. 잠시 후 다시 시도하세요.')
    }
  }, [type, unreadOnly])
  const loadMore = async () => {
    if (!data) return
    try {
      const next = await notificationsApi.list({ unread_only: unreadOnly, type: type || undefined, limit: PAGE_SIZE, offset: data.items.length })
      setData({ ...next, items: [...data.items, ...next.items.filter((item) => !data.items.some((known) => known.id === item.id))] })
    } catch {
      setError('알림을 더 불러오지 못했습니다.')
    }
  }

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    const refresh = () => { void load(Math.max(PAGE_SIZE, data?.items.length ?? 0)) }
    const timer = window.setInterval(() => { if (document.visibilityState !== 'hidden') refresh() }, NOTIFICATION_POLL_MS)
    window.addEventListener(NOTIFICATIONS_CHANGED_EVENT, refresh)
    window.addEventListener('focus', refresh)
    return () => { window.clearInterval(timer); window.removeEventListener(NOTIFICATIONS_CHANGED_EVENT, refresh); window.removeEventListener('focus', refresh) }
  }, [data?.items.length, load])

  const markRead = async (ids: string[] | 'all') => {
    setBusy(true)
    try {
      if (ids === 'all') await notificationsApi.markAllRead()
      else await notificationsApi.markRead(ids)
      announceNotificationsChanged()
    } catch {
      setError('읽음 처리하지 못했습니다.')
    } finally {
      setBusy(false)
    }
  }
  const open = async (item: NotificationItem) => {
    if (!item.read) await markRead([item.id])
    if (item.link) navigate(item.link)
  }

  return <section className="notifications-page" aria-labelledby="notifications-title" data-testid="notifications-page">
    <header className="notifications-page__head">
      <div>
        <span className="notifications-page__eyebrow"><Bell aria-hidden="true" />NOTIFICATIONS</span>
        <h1 id="notifications-title">알림</h1>
        <p>드라이브 반영, Final 지정, 원본 변경, 새 결과 반영처럼 확인할 일을 모았습니다. 볼 수 있는 의뢰의 알림만 표시하며 90일 동안 보관합니다.</p>
      </div>
      <div className="notifications-page__actions">
        <button type="button" onClick={() => void load(Math.max(PAGE_SIZE, data?.items.length ?? 0))}><RefreshCw aria-hidden="true" />새로고침</button>
        <button type="button" className="is-primary" disabled={busy || !data?.unread_count} onClick={() => void markRead('all')}><CheckCheck aria-hidden="true" />모두 읽음</button>
      </div>
    </header>
    <div className="notifications-page__filters" role="group" aria-label="알림 필터">
      <div className="notifications-page__segment" role="radiogroup" aria-label="읽음 상태">
        <button type="button" role="radio" aria-checked={!unreadOnly} onClick={() => setUnreadOnly(false)}>전체</button>
        <button type="button" role="radio" aria-checked={unreadOnly} onClick={() => setUnreadOnly(true)}>읽지 않음{data ? ` ${data.unread_count}` : ''}</button>
      </div>
      <label>
        <span>종류</span>
        <select aria-label="알림 종류" value={type} onChange={(event) => setType(event.target.value)}>
          <option value="">전체 종류</option>
          {(data?.types ?? []).map((item) => <option key={item.type} value={item.type}>{item.label}</option>)}
        </select>
      </label>
      {data && <span className="notifications-page__count">{data.total}건</span>}
    </div>
    {error && <p className="notifications-page__error" role="alert">{error}</p>}
    {!data && !error && <p className="notifications-page__empty">불러오는 중입니다.</p>}
    {data && data.items.length === 0 && <p className="notifications-page__empty">{unreadOnly || type ? '조건에 맞는 알림이 없습니다.' : '아직 알림이 없습니다.'}</p>}
    {data && data.items.length > 0 && <ul className="notifications-page__list" aria-label="알림 목록">
      {data.items.map((item) => <li key={item.id} className={`notifications-page__row${item.read ? '' : ' is-unread'}`}>
        <NotificationEntry item={item} onOpen={() => void open(item)} />
        <div className="notifications-page__row-actions">
          {item.link && <button type="button" onClick={() => void open(item)}><ExternalLink aria-hidden="true" />관련 화면</button>}
          {!item.read && <button type="button" disabled={busy} onClick={() => void markRead([item.id])}>읽음</button>}
        </div>
      </li>)}
    </ul>}
    {data && data.items.length < data.total && <div className="notifications-page__more">
      <button type="button" onClick={() => void loadMore()}>더 보기 ({data.items.length}/{data.total})</button>
    </div>}
  </section>
}
