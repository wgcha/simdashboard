import { Check, X } from 'lucide-react'

/** New-password rules (mirror of the server's ``password_policy_problem``, user decision 2026-10-14). */
export const PASSWORD_RULES_TEXT = '8자 이상, 영문·숫자·특수문자를 모두 포함하고 아이디를 포함하지 않아야 합니다.'

export function passwordRuleChecks(password: string, username = '') {
  const name = username.trim().toLowerCase()
  return [
    { id: 'length', label: '8자 이상', ok: [...password].length >= 8 },
    { id: 'letter', label: '영문자 포함', ok: /[A-Za-z]/.test(password) },
    { id: 'digit', label: '숫자 포함', ok: /\d/.test(password) },
    { id: 'special', label: '특수문자 포함 (예: ! @ # $ %)', ok: [...password].some((ch) => !/[\p{L}\p{N}\s]/u.test(ch)) },
    { id: 'username', label: '아이디 포함 금지', ok: !(name.length >= 3 && password.toLowerCase().includes(name)) },
  ]
}

export function passwordMeetsRules(password: string, username = '') {
  return passwordRuleChecks(password, username).every((rule) => rule.ok)
}

/** Rules shown before and while typing a new password. */
export function PasswordRules({ password, username }: { password: string; username?: string }) {
  return <ul className="password-rules" aria-label="비밀번호 규칙" data-testid="password-rules">
    {passwordRuleChecks(password, username).map((rule) => <li key={rule.id} className={password && rule.ok ? 'ok' : password ? 'bad' : ''}>
      {password && rule.ok ? <Check aria-hidden="true" /> : password ? <X aria-hidden="true" /> : <span aria-hidden="true">·</span>}{rule.label}
    </li>)}
  </ul>
}
