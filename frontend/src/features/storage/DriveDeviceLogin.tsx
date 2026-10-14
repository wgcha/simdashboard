import { ExternalLink, QrCode, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Button } from '../../shared/components/Button'
import { driveApi, driveErrorText, type DriveDeviceLogin } from '../../shared/api/drive'
import { ApiError } from '../../shared/api/errors'

/**
 * [코드로 로그인] (2026-10-14, docs/plans/scx-drive-relogin-spec.md §7): the HPC SSO refuses
 * ID/password logins, so the shared drive account is connected with an activation code.  The
 * admin opens the address in any browser (another PC or a phone works too), signs in with the
 * shared account and enters the code; this panel checks every ``interval`` seconds.
 * Nothing secret passes through here: the code is useless once used or expired.
 */

function remaining(expiresAt: string, now: number): string {
  const seconds = Math.max(0, Math.round((new Date(expiresAt).getTime() - now) / 1000))
  return `${Math.floor(seconds / 60)}분 ${String(seconds % 60).padStart(2, '0')}초`
}

export function DriveDeviceLoginPanel({ connectedAccount, initial, disabled, onFinished }: {
  connectedAccount: string | null | undefined
  initial: DriveDeviceLogin | null | undefined
  disabled: boolean
  onFinished: (login: DriveDeviceLogin) => void
}) {
  const [login, setLogin] = useState<DriveDeviceLogin | null>(initial?.status === 'PENDING' ? initial : null)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const timer = useRef<number | null>(null)
  const finished = useRef<string | null>(null)

  const pending = login?.status === 'PENDING'
  const onFinishedRef = useRef(onFinished)
  onFinishedRef.current = onFinished

  const settle = useCallback((next: DriveDeviceLogin) => {
    setLogin(next)
    if (next.status !== 'PENDING' && finished.current !== next.device_id) {
      finished.current = next.device_id
      onFinished(next)
    }
  }, [onFinished])

  useEffect(() => {
    if (!pending || !login) return
    timer.current = window.setTimeout(() => {
      void driveApi.deviceLoginPoll(login.device_id).then((next) => { setError(''); settle(next) }).catch((reason) => {
        const expired = new Date(login.expires_at).getTime() <= Date.now()
        if ((reason instanceof ApiError && reason.status >= 400 && reason.status < 500) || expired) {
          // Restarted by another admin, cancelled elsewhere or signed out: stop checking this code.
          setError(driveErrorText(reason, '코드 로그인이 끝났습니다. 다시 시작하세요.'))
          setLogin(null)
          onFinishedRef.current({ ...login, status: 'FAILED' })
          return
        }
        setError(driveErrorText(reason, '승인 여부를 확인하지 못했습니다. 잠시 후 다시 확인합니다.'))
        setLogin((current) => current ? { ...current } : current)   // schedule the next check
      })
    }, Math.max(2, login.interval) * 1000)
    return () => { if (timer.current !== null) window.clearTimeout(timer.current) }
  }, [login, pending, settle])

  useEffect(() => {
    if (!pending) return
    const tick = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(tick)
  }, [pending])

  const start = async () => {
    setError(''); setStarting(true)
    try { settle(await driveApi.deviceLoginStart()) } catch (reason) {
      setError(driveErrorText(reason, '활성화 코드를 받지 못했습니다.'))
    } finally { setStarting(false); setNow(Date.now()) }
  }
  const cancel = async () => {
    if (!login) return
    try { settle(await driveApi.deviceLoginCancel(login.device_id)) } catch (reason) {
      setError(driveErrorText(reason, '취소하지 못했습니다.'))
    }
  }

  const address = login?.verification_uri_complete || login?.verification_uri || ''
  const shared = connectedAccount || 'HPC 공용 계정'
  return <div className="drive-device-login" data-testid="drive-device-login">
    <h4><QrCode aria-hidden="true" /> 코드로 로그인</h4>
    {!pending ? <>
      <p className="drive-admin-note">
        활성화 코드를 받아 인증 페이지에서 입력하면 드라이브가 <strong>{shared}</strong>(으)로 연결됩니다. 비밀번호는 대시보드에 입력하지 않습니다.
      </p>
      {login && login.status !== 'SUCCEEDED' && login.message
        ? <p className={`drive-admin-result ${login.status === 'CANCELLED' ? 'warn' : 'bad'}`} role="alert" data-testid="drive-device-error">{login.message}</p> : null}
      <div className="drive-admin-actions">
        <Button variant="primary" disabled={disabled || starting} onClick={() => void start()} data-testid="drive-device-start">
          <QrCode aria-hidden="true" /> {starting ? '코드 받는 중…' : login && login.status !== 'SUCCEEDED' ? '코드 다시 받기' : '코드로 로그인'}
        </Button>
      </div>
    </> : <>
      <ol className="drive-device-steps">
        <li>
          인증 페이지를 엽니다.{' '}
          {address ? <a href={address} target="_blank" rel="noopener noreferrer" data-testid="drive-device-link">
            인증 페이지 열기 <ExternalLink aria-hidden="true" />
          </a> : <span>(어댑터가 주소를 주지 않았습니다. HPC SSO의 장치 로그인 페이지를 여세요.)</span>}
          {address ? <code className="drive-device-address">{login.verification_uri || address}</code> : null}
          {address.startsWith('http://') ? <span className="drive-admin-result warn" role="status">
            암호화되지 않은(http) 주소입니다. 이 페이지에 입력하는 SSO 비밀번호가 사내망에 그대로 지나갑니다.</span> : null}
        </li>
        <li><strong>{shared}</strong>(으)로 로그인하고 아래 코드를 입력합니다.</li>
      </ol>
      <div className="drive-device-code">
        <input aria-label="활성화 코드" readOnly value={login.user_code ?? ''} data-testid="drive-device-code"
          onFocus={(event) => event.currentTarget.select()} />
        <span>남은 시간 {remaining(login.expires_at, now)} · 승인을 기다리는 중…</span>
      </div>
      <p className="drive-admin-note">
        이 PC의 브라우저가 개인 계정으로 SSO에 로그인되어 있으면 개인 계정으로 승인됩니다(그 경우 저장하지 않습니다).
        주소는 휴대폰 등 다른 기기에서 열어도 됩니다.
      </p>
      <div className="drive-admin-actions">
        <Button variant="ghost" onClick={() => void cancel()} data-testid="drive-device-cancel"><X aria-hidden="true" /> 취소</Button>
      </div>
    </>}
    {error ? <p className="drive-admin-result bad" role="alert">{error}</p> : null}
  </div>
}
