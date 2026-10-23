import { DatabaseBackup, Download, RefreshCw, Trash2, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Button } from '../../shared/components/Button'
import { dbBackupApi, dbBackupCodeMessage, dbBackupErrorText, type DbBackupItem, type DbBackupJob, type DbBackupOverview } from '../../shared/api/dbBackups'
import './DbBackupPanel.css'

const POLL_MS = 1500
const STAGE_TEXT: Record<string, string> = {
  config: '준비 중', pg_dump_resolve: 'pg_dump 확인 중', pg_dump_version: '버전 확인 중', pg_dump: '덤프 만드는 중',
  pg_restore_list: '파일 검증 중', publish_dump: '파일 보관 중', write_manifest: '목록 기록 중',
}
const STATUS_TEXT: Record<string, string> = { ok: '정상', no_manifest: '목록 파일 없음', size_mismatch: '크기 불일치' }

function stamp(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('ko-KR', { hour12: false })
}

function sizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let index = 0
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1 }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[index]}`
}

function JobLine({ job }: { job: DbBackupJob }) {
  if (job.status === 'running') {
    return <p className="db-backup-result" role="status" data-testid="db-backup-running">백업 진행 중 · {STAGE_TEXT[job.stage ?? ''] ?? job.stage ?? '준비 중'} · 시작 {stamp(job.started_at)}</p>
  }
  if (job.status === 'succeeded') {
    return <p className="db-backup-result ok" role="status" data-testid="db-backup-done">백업 완료 · {job.backup?.id} · {sizeText(job.backup?.size ?? 0)}</p>
  }
  const error = job.error
  const stderrTail = error?.details && typeof error.details.stderr_tail === 'string' ? error.details.stderr_tail : ''
  return <div className="db-backup-result bad" role="alert" data-testid="db-backup-failed">
    <strong>백업 실패</strong> · {dbBackupCodeMessage(error?.code, error?.message ?? '')} <code>{error?.code}</code>
    {stderrTail ? <small>{stderrTail}</small> : null}
  </div>
}

function DeleteConfirm({ item, busy, onCancel, onConfirm }: { item: DbBackupItem; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  const [typed, setTyped] = useState('')
  return <div className="db-backup-confirm" role="alertdialog" aria-label="백업 삭제 확인" data-testid="db-backup-confirm">
    <p>삭제하면 되돌릴 수 없습니다. 확인을 위해 파일 이름 <code>{item.id}</code>을 그대로 입력하세요.</p>
    <input aria-label="삭제할 백업 이름" value={typed} autoFocus spellCheck={false} autoComplete="off" onChange={(event) => setTyped(event.target.value)} />
    <div className="db-backup-actions">
      <Button variant="danger" size="sm" disabled={busy || typed.trim() !== item.id} onClick={onConfirm}><Trash2 aria-hidden="true" /> 삭제</Button>
      <Button size="sm" disabled={busy} onClick={onCancel}>취소</Button>
    </div>
  </div>
}

export function DbBackupPanel({ onClose }: { onClose: () => void }) {
  const [overview, setOverview] = useState<DbBackupOverview | null>(null)
  const [memo, setMemo] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [starting, setStarting] = useState(false)
  const [deleting, setDeleting] = useState('')
  const [confirmId, setConfirmId] = useState('')
  const alive = useRef(true)

  const load = useCallback(async () => {
    try { const next = await dbBackupApi.overview(); if (alive.current) { setOverview(next); setError('') } } catch (reason) {
      if (alive.current) setError(dbBackupErrorText(reason, '백업 목록을 불러오지 못했습니다.'))
    }
  }, [])
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false } }, [load])
  const running = overview?.job?.status === 'running'
  useEffect(() => {
    if (!running) return undefined
    const timer = window.setInterval(() => void load(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [running, load])
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape' && !confirmId) onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [confirmId, onClose])

  const start = async () => {
    setStarting(true); setError(''); setMessage('')
    try { await dbBackupApi.start(memo); setMemo(''); await load() } catch (reason) { setError(dbBackupErrorText(reason, '백업을 시작하지 못했습니다.')); void load() } finally { setStarting(false) }
  }
  const remove = async (item: DbBackupItem) => {
    setDeleting(item.id); setError(''); setMessage('')
    try { await dbBackupApi.remove(item.id); setConfirmId(''); setMessage(`백업 ${item.id}을(를) 삭제했습니다.`); await load() } catch (reason) { setError(dbBackupErrorText(reason, '백업을 삭제하지 못했습니다.')) } finally { setDeleting('') }
  }
  const postgres = overview?.backend === 'postgresql'

  return <div className="db-backup-backdrop" role="presentation" onMouseDown={() => { if (!confirmId) onClose() }}>
    <section className="db-backup-dialog" role="dialog" aria-modal="true" aria-labelledby="db-backup-title" onMouseDown={(event) => event.stopPropagation()}>
      <header className="db-backup-heading">
        <h2 id="db-backup-title"><DatabaseBackup aria-hidden="true" /> DB 백업</h2>
        <Button variant="ghost" size="sm" aria-label="새로고침" disabled={starting} onClick={() => void load()}><RefreshCw aria-hidden="true" /></Button>
        <Button variant="ghost" size="sm" aria-label="닫기" onClick={onClose}><X aria-hidden="true" /></Button>
      </header>
      {error ? <p className="db-backup-result bad" role="alert">{error}</p> : null}
      {message ? <p className="db-backup-result ok" role="status">{message}</p> : null}
      {!overview ? <p role="status">백업 목록을 불러오는 중…</p> : <>
        <dl className="db-backup-facts">
          <div><dt>백업 폴더</dt><dd data-testid="db-backup-dir"><code>{overview.directory}</code></dd></div>
          <div><dt>DB</dt><dd>{postgres ? 'PostgreSQL (pg_dump -Fc, pg_restore --list로 검증)' : 'DuckDB (파일 복사)'}</dd></div>
          <div><dt>보관</dt><dd>자동 백업 최근 {overview.keep_automatic}개 · 직접 만든 백업은 자동으로 지우지 않습니다.</dd></div>
        </dl>
        <div className="db-backup-actions">
          <label className="db-backup-memo"><span>메모 (선택)</span>
            <input aria-label="백업 메모" value={memo} maxLength={200} placeholder="예: 배포 전" disabled={starting || running}
              onChange={(event) => setMemo(event.target.value)} /></label>
          <Button variant="primary" data-testid="db-backup-start" disabled={starting || running} onClick={() => void start()}>
            <DatabaseBackup aria-hidden="true" /> {starting || running ? '백업 중…' : '지금 백업'}
          </Button>
        </div>
        {overview.job ? <JobLine job={overview.job} /> : null}
        <table aria-label="DB 백업 목록" className="db-backup-table">
          <thead><tr><th>만든 시각</th><th>종류</th><th>메모</th><th>만든 사람</th><th>버전</th><th>크기</th><th>상태</th><th>작업</th></tr></thead>
          <tbody>{overview.backups.length ? overview.backups.map((item) => <tr key={item.id} data-backup-id={item.id}>
            <td title={item.file}>{stamp(item.created_at)}</td>
            <td>{item.kind === 'manual' ? '직접' : item.kind === 'automatic' ? `자동${item.reason ? `(${item.reason})` : ''}` : '—'}</td>
            <td>{item.memo ?? ''}</td>
            <td>{item.created_by ?? '—'}</td>
            <td>{item.app_version ?? '—'}{item.commit ? ` · ${item.commit}` : ''}</td>
            <td className="db-backup-number">{sizeText(item.size)}</td>
            <td className={item.status === 'ok' ? '' : 'db-backup-warn'}>{STATUS_TEXT[item.status] ?? item.status}</td>
            <td>{confirmId === item.id
              ? <DeleteConfirm item={item} busy={deleting === item.id} onCancel={() => setConfirmId('')} onConfirm={() => void remove(item)} />
              : <span className="db-backup-actions">
                <a className="shared-button shared-button--sm shared-button--secondary" href={dbBackupApi.downloadUrl(item.id)} download={item.file} aria-label={`${item.id} 내려받기`}><Download aria-hidden="true" /> 내려받기</a>
                <Button size="sm" aria-label={`${item.id} 삭제`} disabled={Boolean(deleting)} onClick={() => setConfirmId(item.id)}><Trash2 aria-hidden="true" /> 삭제</Button>
              </span>}</td>
          </tr>) : <tr><td colSpan={8}>아직 백업이 없습니다.</td></tr>}</tbody>
        </table>
        <section className="db-backup-help" aria-label="복구 안내" data-testid="db-backup-help">
          <h3>복구 방법 (웹 화면에서는 복구하지 않습니다)</h3>
          <p>서비스를 멈춘 뒤 서버에서 소유자 계정으로 빈 DB에 복구합니다. 기존 DB를 덮어쓰기 전에는 반드시 새 백업을 만드세요.</p>
          <pre><code>{`pg_restore --host 127.0.0.1 --port 5434 --username <소유자 계정> --dbname <빈 DB 이름> --no-owner --single-transaction --exit-on-error "<백업 폴더>\\<파일>.dump"`}</code></pre>
          <p>복구 후 앱 계정 권한 재적용과 확인 절차는 <code>docs/deployment-security-backup-guide.md</code> §6을 따르세요. 내려받은 파일에는 모든 데이터가 들어 있으므로 안전한 곳에만 보관하세요.</p>
        </section>
      </>}
    </section>
  </div>
}

/** Entry inside 저장소 설정 (global admin only), next to the SCX drive entry. */
export function DbBackupSettingsEntry() {
  const [open, setOpen] = useState(false)
  const anchor = useRef<HTMLDivElement>(null)
  // Portal into the themed shell (theme tokens are scoped to .app-shell[data-theme]), outside the popover.
  const host = () => anchor.current?.closest<HTMLElement>('.app-shell') ?? document.body
  return <div className="db-backup-entry" ref={anchor}>
    <span>DB 백업</span>
    <button type="button" className="ghost-button" data-testid="db-backup-open" onClick={() => setOpen(true)}><DatabaseBackup aria-hidden="true" /> 백업 관리</button>
    {open ? createPortal(<DbBackupPanel onClose={() => setOpen(false)} />, host()) : null}
  </div>
}
