import { lazy, Suspense, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { KeyRound, X } from 'lucide-react'
import './change-password-dialog.css'

const ChangePasswordForm = lazy(() => import('./ChangePasswordForm').then((module) => ({ default: module.ChangePasswordForm })))

export function ChangePasswordDialog({ theme }: { theme: 'dark' | 'light' }) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [open, setOpen] = useState(false)
  const show = () => {
    dialogRef.current?.showModal()
    setOpen(true)
  }
  const close = () => dialogRef.current?.close()

  return <>
    <button type="button" className="account-password-trigger" aria-haspopup="dialog" onClick={show}><KeyRound /><span>계정 비밀번호 변경</span></button>
    {createPortal(<dialog ref={dialogRef} className={`account-password-dialog ${theme}`} aria-label="계정 보안 설정" onClose={() => setOpen(false)}>
      <header><strong>계정 보안 설정</strong><button type="button" className="account-password-close" aria-label="계정 보안 설정 닫기" onClick={close}><X /></button></header>
      {open && <Suspense fallback={<div className="full-state">비밀번호 변경을 준비하고 있습니다.</div>}><ChangePasswordForm /></Suspense>}
    </dialog>, document.body)}
  </>
}
