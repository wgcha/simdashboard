import { useEffect, useRef, useState } from 'react'
import { folderEnvironmentApi as service, type FolderEnvironmentRegistration, type RegistrationDeletePreview, type RegistrationDeletePreviewItem } from '../../shared/api/folderEnvironment'
import { errorCode, errorItems, sumDeleteCounts } from '../../shared/api/depthSchemaModel'

type Props = {
  isAdmin: boolean
  busy: boolean
  onOpen: (item: FolderEnvironmentRegistration) => void
  /** Called after a successful delete so the app can re-read projects and drop a deleted selection. */
  onDeleted: (deletedIds: string[]) => void | Promise<void>
}
type Confirm = { ids: string[]; preview: RegistrationDeletePreview | null; items: RegistrationDeletePreviewItem[]; error: string; deleting: boolean }

const COUNT_LABELS: Array<[string, string]> = [['projects', '프로젝트'], ['requests', '의뢰'], ['cases', 'Case'], ['captures', '캡처'], ['finalizations', 'Final 기록']]
const envLabel = (value: string) => value === 'USAGE' ? '사용환경' : '유통환경'

/** 등록 이력 tab (contract §13.7): DELETED rows are hidden; global admins can delete selected registrations. */
export function FolderRegistrationHistory({ isAdmin, busy, onOpen, onDeleted }: Props) {
  const [page, setPage] = useState(0)
  const [history, setHistory] = useState<{ items: FolderEnvironmentRegistration[]; total: number }>({ items: [], total: 0 })
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [confirm, setConfirm] = useState<Confirm | null>(null)
  const [notice, setNotice] = useState('')
  const [reload, setReload] = useState(0)
  const dialog = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const controller = new AbortController()
    service.history(page * 50, controller.signal, false)
      .then((value) => { setHistory(value); setSelected((current) => new Set([...current].filter((id) => value.items.some((item) => item.registration_id === id)))) })
      .catch((error) => { if (!controller.signal.aborted) setNotice(error instanceof Error ? error.message : String(error)) })
    return () => controller.abort()
  }, [page, reload])
  useEffect(() => {
    const node = dialog.current
    if (!node) return
    if (confirm && !node.open) node.showModal?.()
    else if (!confirm && node.open) node.close()
  }, [confirm])

  const visible = history.items.filter((item) => item.status !== 'DELETED')
  const allSelected = visible.length > 0 && visible.every((item) => selected.has(item.registration_id))
  const toggle = (id: string) => setSelected((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next })
  const openDelete = async () => {
    const ids = [...selected]
    setConfirm({ ids, preview: null, items: [], error: '', deleting: false }); setNotice('')
    try {
      const preview = await service.registrationDeletePreview(ids)
      setConfirm((current) => current && { ...current, preview, items: preview.items })
    } catch (error) { setConfirm((current) => current && { ...current, error: error instanceof Error ? error.message : '삭제 미리보기를 만들지 못했습니다.' }) }
  }
  const runDelete = async () => {
    if (!confirm?.preview) return
    setConfirm({ ...confirm, deleting: true, error: '' })
    try {
      const result = await service.deleteRegistrations(confirm.ids, confirm.preview.confirm_token)
      setConfirm(null); setSelected(new Set()); setReload((value) => value + 1)
      setNotice(`등록 ${result.deleted.length}건을 삭제했습니다. SPDM 폴더·파일은 그대로입니다.`)
      await onDeleted(result.deleted)
    } catch (error) {
      const code = errorCode(error)
      const items = errorItems<RegistrationDeletePreviewItem>(error)
      const text = code === 'DELETE_PREVIEW_STALE' ? '미리보기 이후 데이터가 바뀌었습니다. 닫고 다시 삭제를 눌러 주세요.'
        : code === 'REGISTRATION_DELETE_BLOCKED' ? '삭제할 수 없는 참조가 있어 아무것도 삭제하지 않았습니다.'
          : error instanceof Error ? error.message : '삭제하지 못했습니다.'
      setConfirm((current) => current && { ...current, deleting: false, error: text, items: items.length ? items : current.items, preview: code === 'DELETE_PREVIEW_STALE' ? null : current.preview })
    }
  }

  const totals = sumDeleteCounts(confirm?.items ?? [])
  const blockers = (confirm?.items ?? []).flatMap((item) => item.blockers.map((blocker) => ({ ...blocker, registration_id: item.registration_id })))
  const blocked = blockers.length > 0 || (confirm?.items ?? []).some((item) => !item.deletable)

  return <article className="folder-environment-card">
    <h2>등록 이력</h2>
    {notice && <div className="folder-environment-notice info" role="status">{notice}</div>}
    {isAdmin && visible.length > 0 && <div className="folder-environment-footer folder-history-toolbar">
      <label><input type="checkbox" checked={allSelected} disabled={busy} onChange={(event) => setSelected(event.target.checked ? new Set(visible.map((item) => item.registration_id)) : new Set())} /> 전체 선택</label>
      <span>{selected.size ? `${selected.size}건 선택` : ''}</span>
      <button type="button" className="ghost-button folder-danger-button" disabled={busy || !selected.size || Boolean(confirm)} onClick={() => void openDelete()}>삭제</button>
    </div>}
    {!visible.length && <p>현재 저장소의 등록 이력이 없습니다.</p>}
    {visible.map((item) => <div className="folder-history-item" key={item.registration_id}>
      {isAdmin && <input type="checkbox" aria-label={`${item.relative_path || '저장소 전체'} 선택`} checked={selected.has(item.registration_id)} disabled={busy} onChange={() => toggle(item.registration_id)} />}
      <button type="button" className="folder-history-row ghost-button" disabled={busy} onClick={() => onOpen(item)}>{new Date(item.created_at).toLocaleString('ko-KR')} · {envLabel(item.environment)} · {item.relative_path || '저장소 전체'} · Case {item.capture_jobs.length}개</button>
    </div>)}
    <div className="folder-environment-footer"><button type="button" disabled={!page} onClick={() => setPage(page - 1)}>이전</button><span>{history.total}건 · {page + 1}페이지</span><button type="button" disabled={(page + 1) * 50 >= history.total} onClick={() => setPage(page + 1)}>다음</button></div>
    {confirm && <dialog ref={dialog} className="folder-delete-dialog" aria-labelledby="folder-delete-title" onCancel={(event) => { event.preventDefault(); if (!confirm.deleting) setConfirm(null) }}>
      <h3 id="folder-delete-title">등록 {confirm.ids.length}건 삭제</h3>
      {!confirm.preview && !confirm.error && <p role="status">삭제 범위를 확인하는 중…</p>}
      {confirm.items.length > 0 && <p className="folder-delete-totals">{COUNT_LABELS.map(([key, label]) => `${label} ${totals[key] ?? 0}`).join(' · ')}</p>}
      <p className="folder-delete-keep"><strong>SPDM 폴더·파일은 삭제되지 않습니다.</strong> 등록 기록과 이 등록이 만든 업무 데이터만 지웁니다.</p>
      {blocked && <div className="folder-delete-blockers" role="alert"><strong>삭제할 수 없습니다</strong><ul>{blockers.map((blocker) => <li key={`${blocker.registration_id}:${blocker.table}:${blocker.id}`}>{blocker.reason} <code>{blocker.table}</code></li>)}{!blockers.length && <li>삭제할 수 없는 등록이 포함되어 있습니다.</li>}</ul></div>}
      {confirm.error && <p role="alert" className="folder-delete-error">{confirm.error}</p>}
      <footer className="folder-environment-footer"><span />
        <button type="button" className="ghost-button" disabled={confirm.deleting} onClick={() => setConfirm(null)}>취소</button>
        <button type="button" className="primary-button folder-danger-button" disabled={confirm.deleting || !confirm.preview || blocked} onClick={() => void runDelete()}>{confirm.deleting ? '삭제 중…' : '삭제'}</button>
      </footer>
    </dialog>}
  </article>
}
