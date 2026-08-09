import { useState, type FormEvent } from 'react'
import { AlertTriangle, LoaderCircle, Lock, Moon, Sun } from 'lucide-react'


export function LoginScreen({ error, onLogin, theme, onThemeChange }: { error: string; onLogin: (username: string, password: string) => Promise<void>; theme: 'dark' | 'light'; onThemeChange: (theme: 'dark' | 'light') => void }) {
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
      <p>관리자가 발급한 계정으로 로그인하세요. 권한에 따라 조회·편집·관리 기능이 구분됩니다.</p>
      <label><span>사용자 이름</span><input autoFocus autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} /></label>
      <label><span>비밀번호</span><input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
      {error && <div className="login-error"><AlertTriangle /> {error}</div>}
      <button type="submit" disabled={submitting || !username.trim() || !password}>{submitting ? <LoaderCircle className="spin" /> : <Lock />} 로그인</button>
      <small>브라우저는 스크립트에서 읽을 수 없는 HttpOnly 보안 쿠키로 세션을 유지합니다.</small>
    </form>
  </main>
}
