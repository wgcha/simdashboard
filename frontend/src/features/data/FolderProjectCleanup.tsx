import { useEffect, useRef, useState } from 'react'
import { folderEnvironmentApi as service, type ProjectCleanupCandidate, type ProjectCleanupCategory, type ProjectCleanupPreview, type ProjectCleanupPreviewItem } from '../../shared/api/folderEnvironment'
import { errorCode, errorItems } from '../../shared/api/depthSchemaModel'

type Props = {
  busy: boolean
  /** Called after a successful delete so the app can re-read projects and drop a deleted selection. */
  onDeleted: (deletedIds: string[]) => void | Promise<void>
}
type Confirm = { ids: string[]; preview: ProjectCleanupPreview | null; items: ProjectCleanupPreviewItem[]; error: string; deleting: boolean; typed: Record<string, string> }

const CATEGORY_LABELS: Record<ProjectCleanupCategory, string> = { DEMO: '데모', EMPTY: '등록 없음', REGISTERED: '등록됨' }
const USER_DATA_LABELS: Record<string, string> = {
  analysis_pages: '분석 페이지', workflow_runs: '실행 기록', batch_executions: '배치 실행', managed_runs: 'PC 실행',
  validations: '검증', review_notes: '검토 메모', result_drafts: '결과 등록 초안',
}
const SUMMARY_TABLES: Array<[string, string]> = [
  ['projects', '프로젝트'], ['analysis_requests', '의뢰'], ['load_cases', '하중경우'], ['analysis_runs', '해석 이력'],
  ['dashboard_cases', 'Case'], ['dashboards', '분석 페이지'],
]
const TABLE_LABELS: Record<string, string> = {
  ...Object.fromEntries(SUMMARY_TABLES),
  dashboard_captures: '캡처', dashboard_assets: '결과 자산', dashboard_versions: '분석 페이지 버전', validations: '검증',
  request_steps: '작업 단계', request_work_items: '작업 항목', request_work_plans: '작업 계획', workflow_runs: '실행 기록',
  result_registration_drafts: '결과 등록 초안', media_assets: '미디어', drop_video_assets: '영상', asset_blobs: '미디어 원본',
  folder_discovery_registry: '예전 폴더 연결', spdm_storage_bindings: 'SPDM 저장소 연결', semantic_folder_bindings: '예전 결과 매핑',
  'folder_environment_scans.project_id': '폴더 조사 이력(연결만 해제)', 'folder_environment_registrations.project_id': '삭제된 등록 기록(연결만 해제)',
}
const BLOCKER_LABELS: Record<string, string> = {
  LIVE_REGISTRATION: '폴더 스키마 등록이 있습니다. 등록 이력에서 먼저 삭제하세요.',
  RUNNING_EXECUTION: '실행 중인 작업이 있습니다.',
  PROJECT_TEMPLATE_IN_USE: '다른 곳에서 쓰는 프로젝트 템플릿이 있습니다.',
}
const RETAINED_LABELS: Record<string, string> = {
  runs: '해석 이력', results: '결과 값', cases: 'Case', captures: '캡처', legacy_links: '예전 연결', media: '미디어',
}
const userDataText = (data: Record<string, number>) => Object.entries(data).map(([key, value]) => `${USER_DATA_LABELS[key] ?? key} ${value}`).join(' · ')
const retainedText = (data: Record<string, number>) => Object.entries(data).map(([key, value]) => `${RETAINED_LABELS[key] ?? key} ${value.toLocaleString('ko-KR')}`).join(' · ')
const ACK_LABELS: Record<string, string> = {
  RETAINED_DATA: '가져오거나 등록한 결과·Case·예전 연결이 함께 지워집니다.',
  USER_DATA: '사람이 직접 만든 데이터가 함께 지워집니다.',
  SYSTEM_ANALYSIS_PAGES: '모든 하중경우에서 쓰는 시스템 분석 페이지가 이 프로젝트에 묶여 있어 함께 지워지고 다시 만들어지지 않습니다.',
}

/** 프로젝트 정리 tab (contract depth-schema §16): demo and unregistered projects, preview → confirm → delete. */
export function FolderProjectCleanup({ busy, onDeleted }: Props) {
  const [items, setItems] = useState<ProjectCleanupCandidate[]>([])
  const [loaded, setLoaded] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [confirm, setConfirm] = useState<Confirm | null>(null)
  const [notice, setNotice] = useState('')
  const [reload, setReload] = useState(0)
  const dialog = useRef<HTMLDialogElement>(null)
  const preselected = useRef(false)

  useEffect(() => {
    const controller = new AbortController()
    service.projectCleanupCandidates(controller.signal)
      .then((value) => {
        setItems(value.items); setLoaded(true)
        const selectable = value.items.filter((item) => item.selectable)
        if (!preselected.current) {
          // Default: demo projects only. Other projects are chosen explicitly (review H1).
          preselected.current = true
          setSelected(new Set(selectable.filter((item) => item.category === 'DEMO').map((item) => item.project_id)))
        } else setSelected((current) => new Set([...current].filter((id) => selectable.some((item) => item.project_id === id))))
      })
      .catch((error) => { if (!controller.signal.aborted) setNotice(error instanceof Error ? error.message : String(error)) })
    return () => controller.abort()
  }, [reload])
  useEffect(() => {
    const node = dialog.current
    if (!node) return
    if (confirm && !node.open) node.showModal?.()
    else if (!confirm && node.open) node.close()
  }, [confirm])

  const selectable = items.filter((item) => item.selectable)
  const allSelected = selectable.length > 0 && selectable.every((item) => selected.has(item.project_id))
  const toggle = (id: string) => setSelected((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next })
  const openPreview = async () => {
    const ids = [...selected]
    setConfirm({ ids, preview: null, items: [], error: '', deleting: false, typed: {} }); setNotice('')
    try {
      const preview = await service.projectCleanupPreview(ids)
      setConfirm((current) => current && { ...current, preview, items: preview.items })
    } catch (error) { setConfirm((current) => current && { ...current, error: error instanceof Error ? error.message : '미리보기를 만들지 못했습니다.' }) }
  }
  const runDelete = async () => {
    if (!confirm?.preview) return
    setConfirm({ ...confirm, deleting: true, error: '' })
    try {
      const result = await service.deleteProjects(confirm.ids, confirm.preview.confirm_token, needsName.map((item) => item.project_id))
      setConfirm(null); setSelected(new Set()); setReload((value) => value + 1)
      setNotice(`프로젝트 ${result.deleted.length}개를 정리했습니다. SPDM 폴더·파일은 그대로입니다.`)
      await onDeleted(result.deleted)
    } catch (error) {
      const code = errorCode(error)
      const blockedItems = errorItems<ProjectCleanupPreviewItem>(error)
      const text = code === 'DELETE_PREVIEW_STALE' ? '미리보기 이후 데이터가 바뀌었습니다. 닫고 다시 미리보기를 눌러 주세요.'
        : code === 'PROJECT_CLEANUP_BLOCKED' ? '삭제할 수 없는 프로젝트가 있어 아무것도 삭제하지 않았습니다.'
          : code === 'PROJECT_CLEANUP_CONFIRM_REQUIRED' ? '보관 데이터가 있는 프로젝트는 이름을 입력해 확인해야 합니다. 아무것도 삭제하지 않았습니다.'
          : error instanceof Error ? error.message : '삭제하지 못했습니다.'
      setConfirm((current) => current && { ...current, deleting: false, error: text, items: blockedItems.length ? blockedItems : current.items, preview: code === 'DELETE_PREVIEW_STALE' ? null : current.preview })
    }
  }

  const totals = confirm?.preview?.totals ?? {}
  const blockers = (confirm?.items ?? []).flatMap((item) => item.blockers.map((blocker) => ({ ...blocker, project: item.name })))
  const blocked = blockers.length > 0 || (confirm?.items ?? []).some((item) => !item.deletable)
  const needsName = (confirm?.items ?? []).filter((item) => item.requires_acknowledgement)
  const namesTyped = needsName.every((item) => (confirm?.typed[item.project_id] ?? '').trim() === item.name.trim())
  const detailRows = Object.entries(totals).sort(([a], [b]) => a.localeCompare(b))

  return <article className="folder-environment-card folder-cleanup">
    <h2>프로젝트 정리</h2>
    <p className="folder-cleanup-intro">데모 프로젝트와 폴더 스키마 등록이 없는 프로젝트를 DB에서 지웁니다. 등록된 프로젝트는 등록 이력에서 삭제합니다. SPDM 폴더·파일은 지우지 않습니다.</p>
    {notice && <div className="folder-environment-notice info" role="status">{notice}</div>}
    {loaded && !items.length && <p>프로젝트가 없습니다.</p>}
    {items.length > 0 && <>
      <div className="folder-environment-footer folder-history-toolbar">
        <label><input type="checkbox" checked={allSelected} disabled={busy || !selectable.length} onChange={(event) => setSelected(event.target.checked ? new Set(selectable.map((item) => item.project_id)) : new Set())} /> 전체 선택</label>
        <span>{selected.size ? `${selected.size}개 선택` : ''}</span>
        <button type="button" className="ghost-button folder-danger-button" disabled={busy || !selected.size || Boolean(confirm)} onClick={() => void openPreview()}>미리보기</button>
      </div>
      <table className="folder-cleanup-table" aria-label="정리할 프로젝트">
        <thead><tr><th scope="col" aria-label="선택" /><th scope="col">이름</th><th scope="col">구분</th><th scope="col">의뢰</th><th scope="col">Case · 이력</th><th scope="col">보관 데이터</th><th scope="col">직접 만든 데이터</th></tr></thead>
        <tbody>{items.map((item) => <tr key={item.project_id} className={item.selectable ? '' : 'is-disabled'}>
          <td><input type="checkbox" aria-label={`${item.name} 선택`} checked={selected.has(item.project_id)} disabled={busy || !item.selectable} onChange={() => toggle(item.project_id)} /></td>
          <td><strong>{item.name}</strong><small>{item.project_id}</small></td>
          <td><span className={`folder-cleanup-category is-${item.category.toLowerCase()}`}>{CATEGORY_LABELS[item.category]}</span>{!item.selectable && <small>등록 이력에서 삭제</small>}</td>
          <td>{item.requests}</td>
          <td>{item.cases} · {item.runs}</td>
          <td>{item.retained_total > 0 ? <span className={item.category === 'DEMO' ? '' : 'folder-cleanup-warning'}>{retainedText(item.retained_data)}</span> : '—'}{item.system_pages.length > 0 && <small>시스템 분석 페이지 {item.system_pages.length}개</small>}</td>
          <td>{item.user_data_total > 0 ? <span className="folder-cleanup-warning">⚠ {userDataText(item.user_data)}</span> : '—'}</td>
        </tr>)}</tbody>
      </table>
    </>}
    {confirm && <dialog ref={dialog} className="folder-delete-dialog" aria-labelledby="folder-cleanup-title" onCancel={(event) => { event.preventDefault(); if (!confirm.deleting) setConfirm(null) }}>
      <h3 id="folder-cleanup-title">프로젝트 {confirm.ids.length}개 정리</h3>
      {!confirm.preview && !confirm.error && <p role="status">삭제 범위를 확인하는 중…</p>}
      {confirm.preview && <p className="folder-delete-totals">{SUMMARY_TABLES.map(([key, label]) => `${label} ${totals[key] ?? 0}`).join(' · ')}</p>}
      {confirm.items.length > 0 && <ul className="folder-cleanup-preview-list" aria-label="프로젝트별 삭제 건수">{confirm.items.map((item) => <li key={item.project_id}>
        <strong>{item.name}</strong> <span>{CATEGORY_LABELS[item.category]}</span> · 의뢰 {item.counts.analysis_requests ?? 0} · Case {item.counts.dashboard_cases ?? 0} · 해석 이력 {item.counts.analysis_runs ?? 0}
      </li>)}</ul>}
      {detailRows.length > 0 && <details className="folder-cleanup-details"><summary>표별 삭제 건수 {detailRows.length}개</summary>
        <table aria-label="표별 삭제 건수"><tbody>{detailRows.map(([table, count]) => <tr key={table}><th scope="row">{TABLE_LABELS[table] ?? table}{TABLE_LABELS[table] ? <code>{table}</code> : null}</th><td>{count}</td></tr>)}</tbody></table>
      </details>}
      {needsName.length > 0 && <div className="folder-cleanup-acknowledge" role="group" aria-label="프로젝트 이름 확인">
        <strong>⚠ 아래 프로젝트는 이름을 입력해야 삭제할 수 있습니다.</strong>
        {needsName.map((item) => <div key={item.project_id} className="folder-cleanup-acknowledge-item">
          <p><b>{item.name}</b></p>
          <ul>{item.acknowledge_reasons.map((reason) => <li key={reason}>{ACK_LABELS[reason] ?? reason}
            {reason === 'RETAINED_DATA' && ` (${retainedText(item.retained_data)})`}
            {reason === 'USER_DATA' && item.user_data_total > 0 && ` (${userDataText(item.user_data)})`}
            {reason === 'SYSTEM_ANALYSIS_PAGES' && <> {item.system_pages.map((page) => `${page.name} (버전 ${page.versions}${page.user_versions ? `, 사용자 수정 ${page.user_versions}` : ''})`).join(', ')}</>}
          </li>)}</ul>
          <label><span>확인하려면 프로젝트 이름 <code>{item.name}</code>을(를) 입력하세요</span>
            <input type="text" aria-label={`${item.name} 이름 입력`} value={confirm.typed[item.project_id] ?? ''} disabled={confirm.deleting} autoComplete="off"
              onChange={(event) => { const value = event.target.value; setConfirm((current) => current && { ...current, typed: { ...current.typed, [item.project_id]: value } }) }} />
          </label>
        </div>)}
      </div>}
      <p className="folder-delete-keep"><strong>SPDM 폴더·파일은 삭제되지 않습니다.</strong> 계정·감사 기록·공용 설정도 그대로 둡니다.</p>
      {blocked && <div className="folder-delete-blockers" role="alert"><strong>삭제할 수 없습니다</strong><ul>{blockers.map((blocker) => <li key={`${blocker.project}:${blocker.table}:${blocker.id}:${blocker.reason}`}>{blocker.project}: {BLOCKER_LABELS[blocker.reason] ?? blocker.reason} <code>{blocker.table}</code></li>)}{!blockers.length && <li>삭제할 수 없는 프로젝트가 포함되어 있습니다.</li>}</ul></div>}
      {confirm.error && <p role="alert" className="folder-delete-error">{confirm.error}</p>}
      <footer className="folder-environment-footer"><span />
        <button type="button" className="ghost-button" disabled={confirm.deleting} onClick={() => setConfirm(null)}>취소</button>
        <button type="button" className="primary-button folder-danger-button" disabled={confirm.deleting || !confirm.preview || blocked || !namesTyped} onClick={() => void runDelete()}>{confirm.deleting ? '삭제 중…' : '삭제'}</button>
      </footer>
    </dialog>}
  </article>
}
