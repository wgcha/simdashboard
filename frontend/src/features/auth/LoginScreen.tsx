import { useState, type FormEvent } from 'react'
import { AlertTriangle, Check, LoaderCircle, Lock, Moon, Sun, UserPlus } from 'lucide-react'

import type { RegisterAccountResult } from '../../shared/api/account'

type LoginScreenProps = {
  mode: 'password' | 'oidc'
  error: string
  onLogin: (username: string, password: string) => Promise<void>
  onRegister?: (payload: { username: string; display_name: string; password: string }) => Promise<RegisterAccountResult>
  registrationEnabled?: boolean
  theme: 'dark' | 'light'
  onThemeChange: (theme: 'dark' | 'light') => void
}

export function LoginScreen({ mode, error, onLogin, onRegister, registrationEnabled = false, theme, onThemeChange }: LoginScreenProps) {
  const [registerMode, setRegisterMode] = useState(false)
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [localError, setLocalError] = useState('')
  const [registrationNotice, setRegistrationNotice] = useState('')

  const switchMode = (nextRegisterMode: boolean) => {
    setRegisterMode(nextRegisterMode)
    setLocalError('')
    setRegistrationNotice('')
    setPassword('')
    setConfirmation('')
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setLocalError('')
    if (registerMode) {
      if (!onRegister) return
      if (password !== confirmation) {
        setLocalError('비밀번호와 확인 비밀번호가 일치하지 않습니다.')
        return
      }
      setSubmitting(true)
      try {
        const result = await onRegister({ username: username.trim(), display_name: displayName.trim(), password })
        setRegisterMode(false)
        setDisplayName('')
        setPassword('')
        setConfirmation('')
        setRegistrationNotice(result.message || '회원가입이 완료되었습니다. 관리자 승인 후 로그인할 수 있습니다.')
      } catch (reason) {
        setLocalError(reason instanceof Error ? reason.message : '회원가입을 완료하지 못했습니다.')
      } finally {
        setSubmitting(false)
      }
      return
    }
    setSubmitting(true)
    try { await onLogin(username, password) } catch { /* App renders the server error */ } finally { setSubmitting(false) }
  }

  const visibleError = localError || (!registerMode ? error : '')
  return <main className={`login-page ${theme === 'light' ? 'light-login' : ''}`}>
    <div className="login-theme-switch theme-switch" role="group" aria-label="화면 테마 선택"><button type="button" className={theme === 'light' ? 'active' : ''} onClick={() => onThemeChange('light')}><Sun /> 라이트</button><button type="button" className={theme === 'dark' ? 'active' : ''} onClick={() => onThemeChange('dark')}><Moon /> 다크</button></div>
    <form className="login-card" onSubmit={submit}>
      <div className="login-mark">{registerMode ? <UserPlus /> : <Lock />}</div>
      <span>VD simulation workbench</span>
      <h1>{registerMode ? '개인 회원가입' : '보안 로그인'}</h1>
      <p>{registerMode ? '개인 계정을 등록하면 전역 관리자의 승인 후 업무 공간을 사용할 수 있습니다.' : mode === 'oidc' ? '회사 인트라넷 계정으로 로그인하세요. 최초 로그인 후 전역 관리자의 승인이 필요합니다.' : '승인된 개인 계정으로 로그인하세요. 처음 방문했다면 회원가입을 신청할 수 있습니다.'}</p>
      {registrationNotice && !registerMode && <div className="login-success" role="status"><Check /> <span>{registrationNotice}<br /><small>승인이 완료되면 이 화면에서 로그인하세요.</small></span></div>}
      {mode === 'password' && registerMode ? <>
        <label><span>사용자 이름</span><input autoFocus autoComplete="username" minLength={3} maxLength={80} pattern="[A-Za-z0-9][A-Za-z0-9._\-]{2,79}" title="영문·숫자로 시작하는 3~80자, 점·밑줄·하이픈을 사용할 수 있습니다." placeholder="영문·숫자 3자 이상" value={username} onChange={(event) => { setUsername(event.target.value); setLocalError('') }} required /></label>
        <label><span>표시 이름</span><input autoComplete="name" maxLength={200} value={displayName} onChange={(event) => { setDisplayName(event.target.value); setLocalError('') }} required /></label>
        <label><span>비밀번호</span><input type="password" autoComplete="new-password" minLength={12} maxLength={256} placeholder="12자 이상" value={password} onChange={(event) => { setPassword(event.target.value); setLocalError('') }} required /></label>
        <label><span>비밀번호 확인</span><input type="password" autoComplete="new-password" minLength={12} maxLength={256} value={confirmation} onChange={(event) => { setConfirmation(event.target.value); setLocalError('') }} required /></label>
      </> : mode === 'password' ? <>
        <label><span>사용자 이름</span><input autoFocus autoComplete="username" value={username} onChange={(event) => { setUsername(event.target.value); setLocalError('') }} required /></label>
        <label><span>비밀번호</span><input type="password" autoComplete="current-password" value={password} onChange={(event) => { setPassword(event.target.value); setLocalError('') }} required /></label>
      </> : null}
      {visibleError && <div className="login-error" role="alert"><AlertTriangle /> {visibleError}</div>}
      {mode === 'oidc' ? <button type="button" onClick={() => window.location.assign('/api/auth/oidc/start')}><Lock /> 회사 SSO 로그인</button> : <button type="submit" disabled={submitting || !username.trim() || !password || (registerMode && (!displayName.trim() || !confirmation))}>{submitting ? <LoaderCircle className="spin" /> : registerMode ? <UserPlus /> : <Lock />} {registerMode ? '회원가입 신청' : '로그인'}</button>}
      {mode === 'password' && registrationEnabled && <button type="button" className="login-secondary-button" disabled={submitting} onClick={() => switchMode(!registerMode)}>{registerMode ? '로그인으로 돌아가기' : '개인 회원가입'}</button>}
      <small>{registerMode ? '가입 신청 후 관리자가 계정을 승인하면 로그인할 수 있습니다.' : '개인 계정으로 로그인한 뒤 내 PC 설정에서 도우미를 연결하세요.'}</small>
    </form>
  </main>
}

export function ApprovalPendingScreen({ displayName, onLogout }: { displayName: string; onLogout: () => void }) {
  return <main className="login-page"><section className="login-card" aria-live="polite"><div className="login-mark"><Lock /></div><span>ACCOUNT APPROVAL</span><h1>계정 승인 대기 중</h1><p>{displayName}님의 회사 계정 인증이 완료되었습니다. 전역 관리자가 계정을 승인하면 프로젝트 메뉴와 업무를 사용할 수 있습니다.</p><button type="button" onClick={onLogout}>로그아웃</button></section></main>
}
