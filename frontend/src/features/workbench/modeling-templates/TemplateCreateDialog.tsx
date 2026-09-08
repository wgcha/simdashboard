import { useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { modelingTemplatesApi, type ModelingTemplateDetail } from '../../../shared/api/modelingTemplates'

type Props = { products: string[]; loadCases: string[]; onClose: () => void; onCreated: (value: ModelingTemplateDetail) => void }
export function TemplateCreateDialog({ products, loadCases, onClose, onCreated }: Props) {
  const dialog = useRef<HTMLDialogElement>(null)
  const mounted = useRef(true)
  const submitting = useRef(false)
  const [draft, setDraft] = useState({ name: '', product_name: '', load_case_name: '', description: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { mounted.current = true; dialog.current?.showModal(); return () => { mounted.current = false } }, [])
  async function create() {
    if (submitting.current) return
    submitting.current = true; setBusy(true); setError('')
    try {
      const value = await modelingTemplatesApi.create(draft)
      if (mounted.current) onCreated(value)
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : '템플릿을 만들지 못했습니다.')
    } finally {
      submitting.current = false
      if (mounted.current) setBusy(false)
    }
  }
  return <dialog ref={dialog} className="template-create-dialog" onCancel={(event) => { if (busy) event.preventDefault(); else onClose() }}>
    <form className="template-modal" onSubmit={(event) => { event.preventDefault(); void create() }}>
      <header><div><span className="eyebrow">NEW TEMPLATE</span><h2>새 모델링 템플릿</h2></div><button disabled={busy} type="button" onClick={onClose} aria-label="닫기"><X /></button></header>
      {error && <p role="alert" className="template-alert error">{error}</p>}
      {([['name', '템플릿 이름'], ['product_name', '제품 이름'], ['load_case_name', '하중 경우 이름']] as const).map(([key, label]) => <label key={key}>{label}<input required maxLength={160} disabled={busy} list={key === 'product_name' ? 'modeling-products' : key === 'load_case_name' ? 'modeling-load-cases' : undefined} value={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} /></label>)}
      <datalist id="modeling-products">{products.map(value => <option key={value} value={value} />)}</datalist>
      <datalist id="modeling-load-cases">{loadCases.map(value => <option key={value} value={value} />)}</datalist>
      <label>설명<textarea disabled={busy} maxLength={2000} rows={3} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} /></label>
      <footer><button type="button" disabled={busy} onClick={onClose}>취소</button><button className="primary-button" type="submit" disabled={busy}>{busy ? '저장 중…' : '저장'}</button></footer>
    </form>
  </dialog>
}
