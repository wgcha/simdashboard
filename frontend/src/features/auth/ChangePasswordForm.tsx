import { useState, type FormEvent } from 'react'
import { AlertTriangle, Check, KeyRound, LoaderCircle } from 'lucide-react'

import { accountApi } from '../../shared/api/account'
import './ChangePasswordForm.css'

export function ChangePasswordForm() {
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    setNotice('')
    if (newPassword !== confirmation) {
      setError('새 비밀번호와 확인 비밀번호가 일치하지 않습니다.')
      return
    }
    if (!currentPassword || !newPassword) {
      setError('현재 비밀번호와 새 비밀번호를 입력하세요.')
      return
    }
    setBusy(true)
    try {
      await accountApi.changePassword({ current_password: currentPassword, new_password: newPassword })
      setCurrentPassword('')
      setNewPassword('')
      setConfirmation('')
      setNotice('비밀번호를 변경했습니다.')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '비밀번호를 변경하지 못했습니다.')
    } finally {
      setBusy(false)
    }
  }

  return <section className="change-password-form" aria-labelledby="change-password-heading">
    <header>
      <div><span className="change-password-eyebrow"><KeyRound aria-hidden="true" /> ACCOUNT SECURITY</span><h2 id="change-password-heading">비밀번호 변경</h2><p>현재 비밀번호를 확인한 뒤 새 비밀번호로 변경합니다.</p></div>
    </header>
    <form onSubmit={submit}>
      <label><span>현재 비밀번호</span><input type="password" autoComplete="current-password" maxLength={256} value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required disabled={busy} /></label>
      <label><span>새 비밀번호</span><input type="password" autoComplete="new-password" minLength={8} maxLength={256} placeholder="8자 이상" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required disabled={busy} /></label>
      <label><span>새 비밀번호 확인</span><input type="password" autoComplete="new-password" minLength={8} maxLength={256} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required disabled={busy} /></label>
      {error && <p className="change-password-feedback error" role="alert"><AlertTriangle aria-hidden="true" />{error}</p>}
      {notice && <p className="change-password-feedback success" role="status"><Check aria-hidden="true" />{notice}</p>}
      <button type="submit" className="change-password-submit" disabled={busy}>{busy ? <LoaderCircle className="spin" aria-hidden="true" /> : <KeyRound aria-hidden="true" />} 비밀번호 변경</button>
    </form>
  </section>
}
