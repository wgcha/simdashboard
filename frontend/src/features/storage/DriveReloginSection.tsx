import { KeyRound, RotateCcw } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Button } from '../../shared/components/Button'
import {
  driveApi, driveCodeMessage, driveErrorDetail, driveErrorText,
  type DriveAuthStatus, type DriveDeviceLogin, type DriveReloginResult,
} from '../../shared/api/drive'
import { DriveDeviceLoginPanel } from './DriveDeviceLogin'

/**
 * Re-login inside 드라이브 관리 (issue #50, docs/plans/scx-drive-relogin-spec.md §3.4):
 * [저장된 토큰으로 다시 연결] and the account login form.  The password lives only in
 * the input until it is sent; the field is cleared right after sending and is never
 * stored, logged or shown again.
 */

function stamp(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('ko-KR', { hour12: false })
}

/** "연결 계정이 A에서 B로 바뀝니다" — asks before a token of another account is saved. */
export function confirmAccountChange(reason: unknown): boolean | null {
  const detail = driveErrorDetail(reason)
  if (detail?.code !== 'DRIVE_ACCOUNT_CHANGE_CONFIRM') return null
  const current = typeof detail.current === 'string' ? detail.current : '(알 수 없음)'
  const received = typeof detail.received === 'string' && detail.received ? detail.received : '(계정 이름 없음)'
  return window.confirm(`연결 계정이 ${current}에서 ${received}(으)로 바뀝니다. 계속할까요?`)
}

function blockedText(status: DriveAuthStatus): string {
  if (!status.adapter_supported) return '설치된 드라이브 어댑터가 계정 로그인을 지원하지 않습니다(어댑터 업데이트 필요, 이슈 #50). 아래 토큰 붙여넣기를 사용하세요.'
  if (status.password_login_blocked === 'OFF') return '계정 로그인이 꺼져 있습니다(SIMDASH_DRIVE_PASSWORD_LOGIN=off). 아래 토큰 붙여넣기를 사용하세요.'
  if (status.password_login_blocked === 'HTTP') return 'HTTPS 접속(또는 서버 PC에서 직접 접속)에서만 계정 로그인을 사용할 수 있습니다. 비밀번호가 사내망에 평문으로 지나가지 않게 하기 위한 설정입니다(SIMDASH_DRIVE_PASSWORD_LOGIN=https_only).'
  if (!status.capabilities.password) return 'HPC SSO가 아이디·비밀번호 로그인을 허용하지 않습니다. 아래 토큰 붙여넣기를 사용하세요.'
  return ''
}

function successText(result: DriveReloginResult): string {
  const queue = result.queue_resumed ? ` · 대기열 ${result.queue_resumed}개 항목을 재개했습니다` : ''
  return `${result.account_hint ?? '(계정 이름 없음)'} 계정으로 연결됨${result.account_changed ? '(연결 계정 변경)' : ''}${queue}.`
}

export function DriveReloginSection({ busy, run, onChanged }: {
  busy: string
  run: (label: string, action: () => Promise<void>) => Promise<void>
  onChanged: () => void
}) {
  const [status, setStatus] = useState<DriveAuthStatus | null>(null)
  const [loadError, setLoadError] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [result, setResult] = useState('')
  const [failure, setFailure] = useState('')
  const [showLogin, setShowLogin] = useState(false)
  const passwordInput = useRef<HTMLInputElement>(null)
  const prefilled = useRef(false)

  const load = useCallback(async () => {
    try {
      const next = await driveApi.authStatus()
      setStatus(next); setLoadError('')
      if (!prefilled.current && next.connected_account) { setUsername(next.connected_account); prefilled.current = true }
    } catch (reason) { setLoadError(driveErrorText(reason, '재연결 상태를 불러오지 못했습니다.')) }
  }, [])
  useEffect(() => { void load() }, [load])

  const finish = async (outcome: DriveReloginResult) => {
    setResult(successText(outcome)); setFailure('')
    onChanged()
    await load()
  }
  const deviceFinished = useCallback((login: DriveDeviceLogin) => {
    if (login.status === 'SUCCEEDED') {
      const queue = login.queue_resumed ? ` · 대기열 ${login.queue_resumed}개 항목을 재개했습니다` : ''
      setResult(`${login.account ?? '(계정 이름 없음)'} 계정으로 연결됨${queue}.`); setFailure('')
      onChanged()
    }
    void load()
  }, [load, onChanged])
  const fail = (reason: unknown, fallback: string) => {
    const detail = driveErrorDetail(reason)
    setResult('')
    setFailure(driveErrorText(reason, fallback))
    if (detail?.code === 'AUTH_REFRESH_EXPIRED' || detail?.code === 'AUTH_REQUIRED') {
      setShowLogin(true)
      window.setTimeout(() => passwordInput.current?.focus(), 0)
    }
  }

  const reconnect = () => void run('다시 연결', async () => {
    try {
      let outcome: DriveReloginResult
      try { outcome = await driveApi.reconnect() } catch (reason) {
        const confirmed = confirmAccountChange(reason)
        if (!confirmed) throw reason
        outcome = await driveApi.reconnect(true)
      }
      await finish(outcome)
    } catch (reason) { fail(reason, '저장된 토큰으로 다시 연결하지 못했습니다.') } finally { void load() }
  })

  const login = () => {
    const id = username.trim()
    let secret = password
    setPassword('')   // spec §3.3-6: cleared as soon as it is sent
    if (!id || !secret) return
    void run('계정 로그인', async () => {
      try {
        let outcome: DriveReloginResult
        try { outcome = await driveApi.login(id, secret) } catch (reason) {
          const confirmed = confirmAccountChange(reason)
          if (!confirmed) throw reason
          outcome = await driveApi.login(id, secret, true)
        }
        await finish(outcome)
      } catch (reason) { fail(reason, '계정으로 로그인하지 못했습니다.') } finally { secret = ''; void load() }
    })
  }

  if (loadError) return <section className="drive-admin-section" aria-labelledby="drive-relogin-title">
    <h3 id="drive-relogin-title"><RotateCcw aria-hidden="true" /> 다시 연결</h3>
    <p className="drive-admin-result bad" role="alert">{loadError}</p>
  </section>
  if (!status) return null

  const blocked = blockedText(status)
  const canRefresh = status.adapter_supported && status.capabilities.refresh && status.credentials_present
  const loginAllowed = status.password_login_allowed && status.attempts_left > 0
  // The HPC SSO refuses passwords for the drive client (issue #52): code login replaces the form.
  const deviceAllowed = Boolean(status.device_login_allowed)
  const passwordFormUsable = status.capabilities.password || !deviceAllowed
  const loginVisible = passwordFormUsable && (showLogin || !canRefresh || Boolean(status.last_error_code))
  return <section className="drive-admin-section" aria-labelledby="drive-relogin-title" data-testid="drive-relogin">
    <h3 id="drive-relogin-title"><RotateCcw aria-hidden="true" /> 다시 연결</h3>
    <dl className="drive-admin-facts">
      <div><dt>연결 계정</dt><dd data-testid="drive-connected-account">{status.connected_account ?? '아직 없음(처음 등록·로그인한 계정이 연결 계정이 됩니다)'}</dd></div>
      <div><dt>마지막 갱신</dt><dd>{stamp(status.last_refresh_at)}</dd></div>
      <div><dt>마지막 오류</dt><dd>{status.last_error_code ? `${driveCodeMessage(status.last_error_code)} · ${stamp(status.last_error_at)}` : '—'}</dd></div>
    </dl>
    {result ? <p className="drive-admin-result ok" role="status" data-testid="drive-relogin-result">{result}</p> : null}
    {failure ? <p className="drive-admin-result bad" role="alert" data-testid="drive-relogin-error">{failure}</p> : null}
    <div className="drive-admin-actions">
      <Button variant="primary" disabled={Boolean(busy) || !canRefresh} onClick={reconnect} data-testid="drive-reconnect">
        <RotateCcw aria-hidden="true" /> {busy === '다시 연결' ? '연결 중…' : '저장된 토큰으로 다시 연결'}
      </Button>
      {passwordFormUsable && !loginVisible ? <Button variant="ghost" disabled={Boolean(busy)} onClick={() => setShowLogin(true)}>계정으로 로그인</Button> : null}
    </div>
    {deviceAllowed ? <DriveDeviceLoginPanel connectedAccount={status.connected_account} initial={status.device_login}
      disabled={Boolean(busy)} onFinished={deviceFinished} /> : null}
    {!status.credentials_present ? <small>저장된 토큰이 없어 계정으로 로그인하거나 토큰을 붙여 넣어야 합니다.</small> : null}
    {loginVisible ? <form className="drive-relogin-form" data-testid="drive-login-form" autoComplete="off"
      onSubmit={(event) => { event.preventDefault(); login() }}>
      <h4><KeyRound aria-hidden="true" /> 계정으로 로그인</h4>
      {blocked ? <p className="drive-admin-result warn" role="status" data-testid="drive-login-blocked">{blocked}</p> : <>
        <p className="drive-admin-note">HPC 공용 계정의 아이디와 비밀번호를 입력하세요. 비밀번호는 드라이브 어댑터에 한 번 전달할 뿐 저장·기록하지 않습니다. 잘못 입력하면 다시 시도하지 않으며, 15분에 {status.max_attempts}회까지 시도할 수 있습니다(남은 횟수 {status.attempts_left}회).</p>
        {status.locked_until ? <p className="drive-admin-result bad" role="alert">시도 한도를 넘어 {stamp(status.locked_until)}까지 잠겼습니다.</p> : null}
        <div className="drive-admin-actions">
          <label className="drive-admin-folder"><span>아이디</span>
            <input aria-label="HPC 아이디" value={username} maxLength={128} autoComplete="off" spellCheck={false}
              disabled={Boolean(busy) || !loginAllowed} onChange={(event) => setUsername(event.target.value)} /></label>
          <label className="drive-admin-folder"><span>비밀번호</span>
            <input ref={passwordInput} aria-label="HPC 비밀번호" type="password" value={password} maxLength={256}
              autoComplete="off" data-lpignore="true" disabled={Boolean(busy) || !loginAllowed}
              onChange={(event) => setPassword(event.target.value)} /></label>
          <Button type="submit" variant="primary" disabled={Boolean(busy) || !loginAllowed || !username.trim() || !password}>
            {busy === '계정 로그인' ? '로그인 중…' : '로그인'}
          </Button>
        </div>
      </>}
    </form> : null}
  </section>
}
