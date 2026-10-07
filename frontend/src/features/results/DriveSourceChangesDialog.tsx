import { X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { driveApi, driveErrorText, type DriveSourceChangeItem, type DriveSourceChanges } from '../../shared/api/drive'
import { Button } from '../../shared/components/Button'
import './DriveSourceChangesDialog.css'

type Props = {
  projectId: string
  requestId: string
  /** Accept/dismiss need result import permission (the server checks it again). */
  canDecide: boolean
  onClose: () => void
  /** Called after a decision so the screen checks the folders again. */
  onDecided: () => void
}

const TOKEN_KIND: Record<string, string> = { sha1: 'sha1', size_mtime: '크기·시각' }

function stamp(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('ko-KR', { hour12: false })
}

function size(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : `${value.toLocaleString('ko-KR')} B`
}

function itemLabel(item: DriveSourceChangeItem) {
  if (item.kind === 'MISSING') return item.review_state === 'IGNORED' ? '원본 없음 · 무시함' : '원본 없음'
  return item.review_state === 'IGNORED' ? '변경됨 · 무시함' : '변경됨 — 확인 필요'
}

/** Drive source changes of one request (SCX drive mode, plan D2 / integration 05 §4). */
export function DriveSourceChangesDialog({ projectId, requestId, canDecide, onClose, onDecided }: Props) {
  const [data, setData] = useState<DriveSourceChanges | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const load = useCallback(async () => {
    try { setData(await driveApi.sourceChanges(projectId, requestId)); setError('') } catch (reason) { setError(driveErrorText(reason, '원본 변경 목록을 불러오지 못했습니다.')) }
  }, [projectId, requestId])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape' && !busy) onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onClose])

  const decide = async (action: 'accept' | 'dismiss', ids?: string[]) => {
    if (busy) return
    const scope = ids ? '선택한 파일' : '이 의뢰의 모든 변경'
    const question = action === 'accept'
      ? `${scope}을(를) 새 버전으로 등록합니다. 이전 버전은 보존되고, 다음 확인(1분 안)에 결과가 갱신됩니다. 진행할까요?`
      : `${scope}을(를) 무시하고 지금 등록된 버전을 계속 사용합니다. 진행할까요?`
    if (!window.confirm(question)) return
    setBusy(action)
    try {
      if (action === 'accept') await driveApi.acceptSourceChanges(projectId, requestId, ids)
      else await driveApi.dismissSourceChanges(projectId, requestId, ids)
      onDecided()
      await load()
    } catch (reason) {
      setError(driveErrorText(reason, action === 'accept' ? '새 버전을 등록하지 못했습니다.' : '변경을 무시하지 못했습니다.'))
    } finally { setBusy('') }
  }

  const open = (data?.items ?? []).filter((item) => item.review_state !== 'IGNORED')
  return <div className="drive-changes-backdrop" role="presentation" onMouseDown={() => { if (!busy) onClose() }}>
    <section className="drive-changes-dialog" role="dialog" aria-modal="true" aria-labelledby="drive-changes-title" data-testid="drive-source-changes" onMouseDown={(event) => event.stopPropagation()}>
      <header className="drive-changes-heading">
        <h2 id="drive-changes-title">드라이브 원본 변경</h2>
        <Button variant="ghost" size="sm" aria-label="닫기" disabled={Boolean(busy)} onClick={onClose}><X aria-hidden="true" /></Button>
      </header>
      <p className="drive-changes-note">이미 등록된 파일이 드라이브에서 바뀌거나 사라지면 자동으로 반영하지 않습니다. 확인할 때까지 등록된 버전을 계속 보여 줍니다. 새 파일·새 Scene은 자동으로 반영됩니다.</p>
      {error ? <p className="drive-changes-error" role="alert">{error}</p> : null}
      {!data ? <p role="status">불러오는 중…</p> : data.items.length === 0 ? <p role="status">확인할 원본 변경이 없습니다.</p> : <table className="drive-changes-table">
        <thead><tr><th>파일</th><th>상태</th><th>등록된 버전</th><th>드라이브</th>{canDecide ? <th aria-label="결정" /> : null}</tr></thead>
        <tbody>{data.items.map((item) => <tr key={item.id} data-kind={item.kind} data-review={item.review_state}>
          <td><code title={item.relative_path}>{item.relative_path}</code></td>
          <td><span className={`drive-changes-state drive-changes-state--${item.kind === 'MISSING' ? 'missing' : 'changed'}`}>{itemLabel(item)}</span></td>
          <td>v{item.version_no} · {size(item.registered.size)} · {stamp(item.registered.modified_at)}{item.registered.token_kind ? ` · ${TOKEN_KIND[item.registered.token_kind] ?? item.registered.token_kind}` : ''}</td>
          <td>{item.kind === 'MISSING' ? '없음' : item.drive ? `${size(item.drive.size)} · ${stamp(item.drive.modified_at)}` : '—'}</td>
          {canDecide ? <td className="drive-changes-actions">
            <Button size="sm" variant="secondary" disabled={Boolean(busy)} onClick={() => void decide('accept', [item.id])}>{item.kind === 'MISSING' ? '삭제 반영' : '새 버전 등록'}</Button>
            {item.review_state !== 'IGNORED' ? <Button size="sm" variant="ghost" disabled={Boolean(busy)} onClick={() => void decide('dismiss', [item.id])}>무시</Button> : null}
          </td> : null}
        </tr>)}</tbody>
      </table>}
      {canDecide && open.length > 1 ? <footer className="drive-changes-footer">
        <Button variant="primary" disabled={Boolean(busy)} onClick={() => void decide('accept')}>모두 새 버전 등록</Button>
        <Button variant="secondary" disabled={Boolean(busy)} onClick={() => void decide('dismiss')}>모두 무시</Button>
      </footer> : null}
      {!canDecide ? <p className="drive-changes-note">새 버전 등록·무시는 이 의뢰의 결과 등록 권한이 있는 사용자가 할 수 있습니다.</p> : null}
    </section>
  </div>
}
