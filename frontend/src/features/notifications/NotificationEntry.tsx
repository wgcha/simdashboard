import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react'

import { notificationTime, type NotificationItem } from '../../shared/api/notifications'

const SEVERITY_ICON = { INFO: Info, SUCCESS: CheckCircle2, WARNING: AlertTriangle, ERROR: XCircle } as const
const SEVERITY_LABEL = { INFO: '안내', SUCCESS: '완료', WARNING: '확인 필요', ERROR: '실패' } as const

type Props = {
  item: NotificationItem
  compact?: boolean
  onOpen: () => void
}

/** One notification row (dropdown and page): severity, title, body, type and time. */
export function NotificationEntry({ item, compact = false, onOpen }: Props) {
  const Icon = SEVERITY_ICON[item.severity] ?? Info
  return <button type="button" className={`notification-entry notification-entry--${item.severity.toLowerCase()}${item.read ? '' : ' is-unread'}${compact ? ' is-compact' : ''}`}
    onClick={onOpen} data-testid="notification-entry" aria-label={`${item.read ? '' : '읽지 않음, '}${item.title}`}>
    <Icon className="notification-entry__icon" aria-label={SEVERITY_LABEL[item.severity]} />
    <span className="notification-entry__text">
      <strong>{item.title}</strong>
      {item.body && <span className="notification-entry__body">{item.body}</span>}
      <small><span>{item.type_label}</span><time dateTime={item.created_at ?? undefined}>{notificationTime(item.created_at)}</time></small>
    </span>
    {!item.read && <span className="notification-entry__dot" aria-hidden="true" />}
  </button>
}
