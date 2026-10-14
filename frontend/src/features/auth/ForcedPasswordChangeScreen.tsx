import { KeyRound } from 'lucide-react'

import { ChangePasswordForm } from './ChangePasswordForm'

/** Shown instead of the dashboard while an administrator-reset password must be replaced (2026-10-14). */
export function ForcedPasswordChangeScreen({ username, onLogout }: { username: string; onLogout: () => void }) {
  return <main className="login-page" data-testid="forced-password-change">
    <section className="login-card forced-password-card" aria-live="polite">
      <div className="login-mark"><KeyRound /></div>
      <span>ACCOUNT SECURITY</span>
      <ChangePasswordForm forced username={username} onChanged={() => window.setTimeout(() => window.location.reload(), 600)} />
      <button type="button" className="login-secondary-button" onClick={onLogout}>로그아웃</button>
    </section>
  </main>
}
