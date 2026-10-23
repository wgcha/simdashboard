import { useState } from 'react'
import { folderEnvironmentApi as service, type RegistrationResetPreview } from '../../shared/api/folderEnvironment'
import { errorCode } from '../../shared/api/depthSchemaModel'

type Props = {
  busy: boolean
  /** Called after a successful reset so the app can re-read projects and drop a deleted selection. */
  onReset: (deletedIds: string[]) => void | Promise<void>
}

const TABLE_LABELS: Record<string, string> = {
  projects: '프로젝트', analysis_requests: '의뢰', load_cases: '하중경우', analysis_runs: '해석 이력', dashboard_cases: 'Case',
  dashboard_captures: '캡처', dashboard_assets: '결과 자산', folder_environment_registrations: '폴더 등록(삭제 기록 포함)',
  folder_environment_registry: '폴더 역할 연결', folder_environment_scans: '폴더 조사·새로고침 스냅샷', folder_environment_previews: '폴더 조사 미리보기',
  folder_environment_capture_jobs: '결과 받기 작업', folder_project_links: '프로젝트 폴더 연결', folder_link_reservations: '폴더 구조 만들기 예약',
  drive_source_versions: '드라이브 원본 버전', folder_discovery_registry: '예전 폴더 연결', folder_discovery_scans: '예전 폴더 조사',
  folder_discovery_previews: '예전 폴더 미리보기', spdm_storage_bindings: 'SPDM 저장소 연결', spdm_storage_files: 'SPDM 저장소 파일',
  spdm_storage_project_parents: 'SPDM 프로젝트 폴더', spdm_storage_request_parents: 'SPDM 의뢰 폴더', semantic_folder_bindings: '예전 결과 매핑',
  result_registration_paths: '결과 등록 경로', result_registration_location_links: '결과 위치 연결', result_registration_drafts: '결과 등록 초안',
  folder_discovery_exclusions: '관리자 제외 폴더', notifications: '알림',
}
const SUMMARY = ['projects', 'analysis_requests', 'dashboard_cases', 'folder_environment_registrations', 'drive_source_versions']

/** 「폴더 등록 전체 초기화」 danger zone (contract depth-schema §16.8): preview → type 초기화 → reset → [저장 폴더 새로고침]. */
export function FolderRegistrationReset({ busy, onReset }: Props) {
  const [keepExclusions, setKeepExclusions] = useState(true)
  const [preview, setPreview] = useState<RegistrationResetPreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [typed, setTyped] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const loadPreview = async (keep = keepExclusions) => {
    setLoading(true); setError(''); setNotice(''); setTyped('')
    try { setPreview(await service.registrationResetPreview(keep)) } catch (failure) {
      setPreview(null); setError(failure instanceof Error ? failure.message : '미리보기를 만들지 못했습니다.')
    } finally { setLoading(false) }
  }
  const toggleKeep = (keep: boolean) => { setKeepExclusions(keep); if (preview) void loadPreview(keep) }
  const run = async () => {
    if (!preview) return
    setRunning(true); setError('')
    try {
      const result = await service.resetRegistrations(preview.confirm_token, typed, keepExclusions)
      setPreview(null); setTyped('')
      setNotice(`폴더 등록을 모두 초기화했습니다(프로젝트 ${result.deleted_projects.length}개). 드라이브·SPDM 폴더와 파일은 그대로입니다. 이제 [저장 폴더 새로고침]을 눌러 현재 폴더를 다시 등록하세요.`)
      await onReset(result.deleted_projects)
    } catch (failure) {
      const code = errorCode(failure)
      setError(code === 'DELETE_PREVIEW_STALE' ? '미리보기 이후 데이터가 바뀌었습니다. 미리보기를 다시 눌러 주세요.'
        : code === 'FOLDER_RESET_BUSY' ? '실행 중인 작업(새로고침·결과 받기·업로드 등)이 있어 초기화하지 않았습니다. 끝난 뒤 다시 시도하세요.'
          : code === 'FOLDER_RESET_BLOCKED' ? '다른 곳에서 쓰는 프로젝트 템플릿이 있어 아무것도 삭제하지 않았습니다.'
            : failure instanceof Error ? failure.message : '초기화하지 못했습니다.')
      if (code === 'DELETE_PREVIEW_STALE') setPreview(null)
    } finally { setRunning(false) }
  }

  const rows = Object.entries(preview?.counts ?? {}).sort(([a], [b]) => a.localeCompare(b))
  const blocked = Boolean(preview && (preview.busy.length || preview.blockers.length))
  const ready = Boolean(preview) && !blocked && typed.trim() === (preview?.confirm_text ?? '초기화')
  return <article className="folder-environment-card folder-reset" aria-labelledby="folder-reset-title">
    <h2 id="folder-reset-title">폴더 등록 전체 초기화</h2>
    <p className="folder-cleanup-intro">폴더 등록과 폴더에서 만든 프로젝트·의뢰·Case·결과, 그리고 폴더 경로를 기억하는 모든 기록(삭제된 등록 기록, 폴더 조사·스냅샷, 드라이브 원본 버전, 예전 연결)을 한 번에 지웁니다.
      등록 이력 삭제·프로젝트 정리 뒤에도 예전 폴더 이름이 계속 보일 때 사용합니다. 계정·권한·깊이 스키마·드라이브 연결 설정·감사 기록은 그대로 둡니다. <strong>드라이브·SPDM 폴더와 파일은 지우지 않습니다.</strong></p>
    {notice && <div className="folder-environment-notice info" role="status">{notice}</div>}
    <div className="folder-environment-footer folder-history-toolbar">
      <label><input type="checkbox" checked={keepExclusions} disabled={busy || loading || running} onChange={(event) => toggleKeep(event.target.checked)} /> 관리자가 제외한 폴더 목록은 유지{preview ? ` (${preview.exclusions}개)` : ''}</label>
      <span />
      <button type="button" className="ghost-button folder-danger-button" disabled={busy || loading || running} onClick={() => void loadPreview()}>{loading ? '확인 중…' : preview ? '미리보기 다시' : '미리보기'}</button>
    </div>
    {preview && <div className="folder-reset-preview">
      <p className="folder-delete-totals">{SUMMARY.map((key) => `${TABLE_LABELS[key]} ${(preview.counts[key] ?? 0).toLocaleString('ko-KR')}`).join(' · ')}{preview.counts_approximate ? ' (대략)' : ''}</p>
      {preview.projects.length > 0 && <details><summary>지울 프로젝트 {preview.projects.length}개</summary>
        <ul className="folder-cleanup-preview-list">{preview.projects.map((item) => <li key={item.project_id}><strong>{item.name}</strong> <small>{item.project_id}</small></li>)}</ul>
      </details>}
      {rows.length > 0 && <details className="folder-cleanup-details"><summary>표별 삭제 건수 {rows.length}개</summary>
        <table aria-label="표별 삭제 건수"><tbody>{rows.map(([table, count]) => <tr key={table}><th scope="row">{TABLE_LABELS[table] ?? table}{TABLE_LABELS[table] ? <code>{table}</code> : null}</th><td>{count.toLocaleString('ko-KR')}</td></tr>)}</tbody></table>
      </details>}
      {blocked && <div className="folder-delete-blockers" role="alert"><strong>지금은 초기화할 수 없습니다</strong><ul>
        {preview.busy.map((item) => <li key={item.code}>{item.message}</li>)}
        {preview.blockers.map((item) => <li key={`${item.table}:${item.id}`}>다른 곳에서 쓰는 프로젝트 템플릿 <code>{item.id}</code></li>)}
      </ul></div>}
      <label className="folder-reset-confirm"><span>확인하려면 <code>{preview.confirm_text}</code>를 입력하세요</span>
        <input type="text" aria-label="초기화 확인 문구" value={typed} autoComplete="off" disabled={running || blocked} onChange={(event) => setTyped(event.target.value)} />
      </label>
      <footer className="folder-environment-footer"><span />
        <button type="button" className="ghost-button" disabled={running} onClick={() => { setPreview(null); setTyped('') }}>취소</button>
        <button type="button" className="primary-button folder-danger-button" disabled={busy || running || !ready} onClick={() => void run()}>{running ? '초기화 중…' : '전체 초기화'}</button>
      </footer>
    </div>}
    {error && <p role="alert" className="folder-delete-error">{error}</p>}
  </article>
}
