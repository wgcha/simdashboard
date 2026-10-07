import { HardDrive, PlugZap, RefreshCw, ShieldCheck, Trash2, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Button } from '../../shared/components/Button'
import {
  driveApi, driveCodeMessage, driveErrorText, driveStateLabel,
  type DriveAdminStatus, type DriveCheckReport, type DriveTestResult,
} from '../../shared/api/drive'
import './DriveAdminPanel.css'

const PROBLEM_TEXT: Record<string, string> = {
  MISSING: '등록된 토큰이 없습니다. 관리자 PC에서 login 명령으로 받은 토큰 JSON을 등록하세요.',
  KEY_CHANGED: '암호화 키가 바뀌었습니다. 토큰을 다시 등록하세요.',
  DECRYPT_FAILED: '저장된 토큰을 복호화할 수 없습니다. 토큰을 다시 등록하세요.',
  DB_ERROR: '토큰을 DB에서 읽지 못했습니다.',
}

function stamp(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('ko-KR', { hour12: false })
}

const TOKEN_KIND_TEXT: Record<string, string> = { sha1: 'sha1(목록에 포함)', size_mtime: '크기·수정 시각(목록에 sha1 없음)', mixed: '혼합(일부 파일만 sha1)' }

function tokenKindText(observed: DriveAdminStatus['version_tokens']) {
  if (!observed?.last_kind) return '아직 목록 조회 없음'
  return `${TOKEN_KIND_TEXT[observed.last_kind] ?? observed.last_kind} · sha1 ${observed.sha1_files ?? 0} / 크기·시각 ${observed.size_mtime_files ?? 0}`
}

export function DriveStateBadge({ state }: { state: string | null | undefined }) {
  const tone = state === 'OK' ? 'ok' : state === 'DEGRADED' ? 'warn' : state === 'UNKNOWN' || !state ? 'idle' : 'bad'
  return <span className={`drive-state-badge drive-state-${tone}`} data-testid="drive-state-badge" data-state={state ?? ''}>{driveStateLabel(state)}</span>
}

function TestResultLine({ result }: { result: DriveTestResult }) {
  return result.ok
    ? <p className="drive-admin-result ok" role="status">연결 성공 · {result.latency_ms ?? '—'} ms · {result.root_identity}</p>
    : <p className="drive-admin-result bad" role="status">연결 실패 · {driveCodeMessage(result.error?.code)} {result.error?.message ? `(${result.error.message})` : ''}</p>
}

function CheckTable({ report }: { report: DriveCheckReport }) {
  return <div className="drive-check-report">
    <p className={report.ok ? 'drive-admin-result ok' : 'drive-admin-result bad'} role="status">
      {report.ok ? '점검 통과' : '점검에서 실패한 단계가 있습니다'} · {report.drive_path}
    </p>
    <table aria-label="드라이브 점검 결과">
      <thead><tr><th>단계</th><th>결과</th><th>코드</th><th>지연(ms)</th><th>관찰</th></tr></thead>
      <tbody>{report.steps.map((step) => <tr key={step.step} data-step={step.step}>
        <td>{step.label}</td>
        <td>{step.skipped ? '건너뜀' : step.ok ? '성공' : '실패'}</td>
        <td title={driveCodeMessage(step.code)}>{step.code ?? ''}</td>
        <td>{step.latency_ms ?? ''}</td>
        <td>{(step.observations ?? []).join(' · ')}</td>
      </tr>)}</tbody>
    </table>
    <small>{report.note}</small>
  </div>
}

export function DriveAdminPanel({ onClose }: { onClose: () => void }) {
  const [status, setStatus] = useState<DriveAdminStatus | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [bundleText, setBundleText] = useState('')
  const [testResult, setTestResult] = useState<DriveTestResult | null>(null)
  const [testFolder, setTestFolder] = useState('')
  const [report, setReport] = useState<DriveCheckReport | null>(null)

  const load = useCallback(async () => {
    try { setStatus(await driveApi.adminStatus()) } catch (reason) { setError(driveErrorText(reason, '드라이브 상태를 불러오지 못했습니다.')) }
  }, [])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape' && !busy) onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onClose])

  const run = async (label: string, action: () => Promise<void>) => {
    if (busy) return
    setBusy(label); setError(''); setMessage('')
    try { await action() } catch (reason) { setError(driveErrorText(reason, `${label}에 실패했습니다.`)) } finally { setBusy(''); void load() }
  }
  const scx = status?.mode === 'scx'
  const credentials = status?.credentials
  const problem = status?.credentials_problem ? PROBLEM_TEXT[status.credentials_problem] ?? status.credentials_problem : ''

  return <div className="drive-admin-backdrop" role="presentation" onMouseDown={() => { if (!busy) onClose() }}>
    <section className="drive-admin-dialog" role="dialog" aria-modal="true" aria-labelledby="drive-admin-title" onMouseDown={(event) => event.stopPropagation()}>
      <header className="drive-admin-heading">
        <h2 id="drive-admin-title"><HardDrive aria-hidden="true" /> SCX 드라이브</h2>
        {scx ? <DriveStateBadge state={status?.state} /> : null}
        <Button variant="ghost" size="sm" aria-label="닫기" disabled={Boolean(busy)} onClick={onClose}><X aria-hidden="true" /></Button>
      </header>
      {error ? <p className="drive-admin-result bad" role="alert">{error}</p> : null}
      {message ? <p className="drive-admin-result ok" role="status">{message}</p> : null}
      {!status ? <p role="status">드라이브 상태를 불러오는 중…</p> : !scx ? <p className="drive-admin-note" data-testid="drive-mode-none">
        드라이브 모드가 꺼져 있습니다(<code>SIMDASH_DRIVE_GATEWAY=none</code>). 지금처럼 서버 폴더를 SPDM 원본으로 사용합니다.
      </p> : <>
        <dl className="drive-admin-facts">
          <div><dt>모드</dt><dd>scx</dd></div>
          <div><dt>서버</dt><dd>{status.server_url}</dd></div>
          <div><dt>SPDM 루트</dt><dd>{status.drive_root ? `~/${status.drive_root}` : '미설정'}{status.drive_root_locked ? ' (환경 설정 고정)' : ''}</dd></div>
          <div><dt>워커 버전</dt><dd>{status.worker_version ?? '—'}</dd></div>
          <div><dt>드라이브 쓰기</dt><dd data-testid="drive-writes">{status.writes_available ? '사용 가능' : '읽기 전용'} · 쓰기 허용 설정 {status.writes_enabled ? '켜짐' : '꺼짐'}<small> (SIMDASH_DRIVE_WRITES_ENABLED, 쓰기는 D3 이후)</small></dd></div>
          <div><dt>원본 지문</dt><dd data-testid="drive-version-tokens">{tokenKindText(status.version_tokens)}</dd></div>
          <div><dt>공용 계정</dt><dd>{credentials?.present ? `${credentials.account_hint ?? '(이름 없음)'} · 발급 ${stamp(credentials.obtained_at)} · 갱신 ${stamp(credentials.updated_at)} (${credentials.updated_by === 'worker' ? '자동 회전' : credentials.updated_by ?? '—'})` : '등록 안 됨'}</dd></div>
          <div><dt>마지막 성공</dt><dd>{stamp(status.health?.last_success_at)}</dd></div>
          <div><dt>마지막 오류</dt><dd>{status.health?.last_error_code ? `${status.health.last_error_code} · ${stamp(status.health.last_error_at)}` : '—'}</dd></div>
        </dl>
        {problem ? <p className="drive-admin-result warn" role="status">{problem}</p> : null}
        {status.token_save_failed_at ? <p className="drive-admin-result bad" role="alert">갱신된 토큰을 저장하지 못했습니다({stamp(status.token_save_failed_at)}). 재시작 전에 토큰을 다시 등록하세요.</p> : null}

        <section className="drive-admin-section" aria-labelledby="drive-token-title">
          <h3 id="drive-token-title"><ShieldCheck aria-hidden="true" /> 공용 계정 토큰</h3>
          <p className="drive-admin-note">관리자 PC에서 <code>python -m scx_drive_adapter login --server {status.server_url}</code>을 실행해 출력된 JSON 전체를 붙여 넣으세요. 토큰은 암호화해 저장하며 화면에 다시 표시하지 않습니다.</p>
          <textarea aria-label="토큰 JSON" value={bundleText} spellCheck={false} autoComplete="off" rows={4} disabled={Boolean(busy)}
            placeholder='{"server_url": "...", "access_token": "...", "refresh_token": "...", "obtained_at": "...", "account_hint": "..."}'
            onChange={(event) => setBundleText(event.target.value)} />
          <div className="drive-admin-actions">
            <Button variant="primary" disabled={Boolean(busy) || !bundleText.trim()} onClick={() => void run('토큰 등록', async () => {
              const saved = await driveApi.registerCredentials(bundleText)
              setBundleText(''); setTestResult(saved.test)
              setMessage(`토큰을 등록했습니다(${saved.account_hint ?? '계정 이름 없음'}).${saved.test.ok ? '' : ' 새 토큰은 최대 10초 안에 워커에 반영됩니다. 잠시 후 연결 시험을 다시 하세요.'}`)
            })}>{busy === '토큰 등록' ? '등록 중…' : '토큰 등록'}</Button>
            <Button disabled={Boolean(busy) || !credentials?.present} onClick={() => {
              if (!window.confirm('등록된 공용 계정 토큰을 삭제할까요? 이후 모든 드라이브 호출은 인증 필요 상태가 됩니다.')) return
              void run('토큰 삭제', async () => { await driveApi.deleteCredentials(); setTestResult(null); setMessage('토큰을 삭제했습니다.') })
            }}><Trash2 aria-hidden="true" /> 토큰 삭제</Button>
            <Button disabled={Boolean(busy)} onClick={() => void run('연결 시험', async () => { setTestResult(await driveApi.test()) })}>
              <PlugZap aria-hidden="true" /> {busy === '연결 시험' ? '시험 중…' : '연결 시험'}
            </Button>
          </div>
          {testResult ? <TestResultLine result={testResult} /> : null}
        </section>

        <section className="drive-admin-section" aria-labelledby="drive-check-title">
          <h3 id="drive-check-title"><RefreshCw aria-hidden="true" /> 드라이브 점검</h3>
          <p className="drive-admin-note" data-testid="drive-check-note">조회 전용 점검입니다. SPDM 루트 아래 시험 폴더의 폴더 조회·목록 조회·작은 파일 조회·다운로드(8 MiB 이하, 서버 사본은 바로 삭제)·어댑터 상태를 차례로 확인합니다. 드라이브에는 아무것도 만들거나 바꾸거나 지우지 않습니다.</p>
          <div className="drive-admin-actions">
            <label className="drive-admin-folder"><span>시험 폴더 (SPDM 루트 기준)</span>
              <input aria-label="시험 폴더" value={testFolder} placeholder="예: _simdash_test" disabled={Boolean(busy) || !status.drive_root}
                onChange={(event) => setTestFolder(event.target.value)} /></label>
            <Button variant="primary" disabled={Boolean(busy) || !testFolder.trim() || !status.drive_root}
              onClick={() => void run('드라이브 점검', async () => { setReport(null); setReport(await driveApi.check(testFolder.trim())) })}>
              {busy === '드라이브 점검' ? '점검 중…' : '점검 실행'}
            </Button>
          </div>
          {!status.drive_root ? <small>SIMDASH_SCX_DRIVE_ROOT를 설정한 뒤 사용할 수 있습니다.</small> : null}
          {report ? <CheckTable report={report} /> : null}
        </section>
      </>}
    </section>
  </div>
}

/** Entry inside 저장소 설정 (global admin only). */
export function DriveSettingsEntry() {
  const [open, setOpen] = useState(false)
  const anchor = useRef<HTMLDivElement>(null)
  // Portal into the themed shell (theme tokens are scoped to .app-shell[data-theme]), outside the popover.
  const host = () => anchor.current?.closest<HTMLElement>('.app-shell') ?? document.body
  return <div className="drive-settings-entry" ref={anchor}>
    <span>SCX 드라이브</span>
    <button type="button" className="ghost-button" data-testid="drive-admin-open" onClick={() => setOpen(true)}><HardDrive aria-hidden="true" /> 드라이브 관리</button>
    {open ? createPortal(<DriveAdminPanel onClose={() => setOpen(false)} />, host()) : null}
  </div>
}
