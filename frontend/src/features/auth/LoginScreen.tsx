import { useState, type FormEvent } from 'react'
import { AlertTriangle, LoaderCircle, Lock, Moon, Sun } from 'lucide-react'


export function LoginScreen({ mode, error, onLogin, theme, onThemeChange }: { mode: 'password' | 'oidc'; error: string; onLogin: (username: string, password: string) => Promise<void>; theme: 'dark' | 'light'; onThemeChange: (theme: 'dark' | 'light') => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    try { await onLogin(username, password) } catch { /* App renders the server error */ } finally { setSubmitting(false) }
  }
  return <main className={`login-page ${theme === 'light' ? 'light-login' : ''}`}>
    <div className="login-theme-switch theme-switch" role="group" aria-label="화면 테마 선택"><button type="button" className={theme === 'light' ? 'active' : ''} onClick={() => onThemeChange('light')}><Sun /> 라이트</button><button type="button" className={theme === 'dark' ? 'active' : ''} onClick={() => onThemeChange('dark')}><Moon /> 다크</button></div>
    <form className="login-card" onSubmit={submit}>
      <div className="login-mark"><Lock /></div>
      <span>VD simulation workbench</span>
      <h1>보안 로그인</h1>
      <p>{mode === 'oidc' ? '회사 인트라넷 계정으로 로그인하세요. 최초 로그인 후 전역 관리자의 승인이 필요합니다.' : '관리자가 발급한 계정으로 로그인하세요. 권한에 따라 조회·편집·관리 기능이 구분됩니다.'}</p>
      {mode === 'password' ? <><label><span>사용자 이름</span><input autoFocus autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} /></label>
      <label><span>비밀번호</span><input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} /></label></> : null}
      {error && <div className="login-error"><AlertTriangle /> {error}</div>}
      {mode === 'oidc' ? <button type="button" onClick={() => window.location.assign('/api/auth/oidc/start')}><Lock /> 회사 SSO 로그인</button> : <button type="submit" disabled={submitting || !username.trim() || !password}>{submitting ? <LoaderCircle className="spin" /> : <Lock />} 로그인</button>}
      <small>브라우저는 스크립트에서 읽을 수 없는 HttpOnly 보안 쿠키로 세션을 유지합니다.</small>
    </form>
  </main>
}

export function ApprovalPendingScreen({ displayName, onLogout }: { displayName: string; onLogout: () => void }) {
  return <main className="login-page"><section className="login-card" aria-live="polite"><div className="login-mark"><Lock /></div><span>ACCOUNT APPROVAL</span><h1>계정 승인 대기 중</h1><p>{displayName}님의 회사 계정 인증이 완료되었습니다. 전역 관리자가 계정을 승인하면 프로젝트 메뉴와 업무를 사용할 수 있습니다.</p><button type="button" onClick={onLogout}>로그아웃</button></section></main>
}
