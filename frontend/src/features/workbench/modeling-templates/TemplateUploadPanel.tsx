import { useEffect, useRef, useState } from 'react'
import { Check, FileText, FolderOpen, Upload, X } from 'lucide-react'
import { modelingTemplatesApi, type ModelingTemplateDetail } from '../../../shared/api/modelingTemplates'

const MAX_FILES = 200
const MAX_BYTES = 25 * 1024 * 1024
type Mode = 'merge' | 'replace'
type Picked = { file: File; path: string }

async function encode(file: File) {
  const data = new Uint8Array(await file.arrayBuffer())
  let binary = ''
  for (let offset = 0; offset < data.length; offset += 32768) binary += String.fromCharCode(...data.subarray(offset, offset + 32768))
  return btoa(binary)
}

export function TemplateUploadPanel({ detail, canManage, onSaved, disabled = false }: { detail: ModelingTemplateDetail; canManage: boolean; onSaved: (value: ModelingTemplateDetail) => void; disabled?: boolean }) {
  const [pending, setPending] = useState<Picked[]>([])
  const [destination, setDestination] = useState('')
  const [mode, setMode] = useState<Mode>('merge')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const directory = useRef<HTMLInputElement>(null)
  const individual = useRef<HTMLInputElement>(null)
  const mounted = useRef(true)
  const savingRef = useRef(false)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const prefix = destination.trim().replace(/\\/g, '/').replace(/\/+$/g, '')
  const targetPath = (item: Picked) => prefix && !item.path.includes('/') ? `${prefix}/${item.path}` : item.path
  if (!canManage) return null
  const choose = (files: FileList | null, isDirectory: boolean) => {
    const picked = [...(files ?? [])].filter((file) => file.name.toLowerCase().endsWith('.csv')).map((file) => ({ file, path: isDirectory ? ((file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name) : file.name }))
    const total = picked.reduce((sum, item) => sum + item.file.size, 0)
    if (!picked.length) { setError('CSV 파일을 하나 이상 선택하세요.'); return }
    if (picked.length > MAX_FILES || total > MAX_BYTES) { setError('CSV는 한 번에 200개, 25MiB까지 선택할 수 있습니다.'); return }
    setError(''); setPending(picked)
  }
  const save = async () => {
    if (!pending.length) { setError('저장할 CSV 파일을 먼저 선택하세요.'); return }
    if (savingRef.current || disabled || !canManage) return
    if (prefix.startsWith('/') || /^[A-Za-z]:/.test(prefix) || prefix.split('/').includes('..')) { setError('저장 폴더는 상대 경로로 입력하세요.'); return }
    savingRef.current = true
    setSaving(true); setError('')
    try {
      const files = []
      for (const item of pending) files.push({ relative_path: targetPath(item), content_base64: await encode(item.file) })
      if (!mounted.current) return
      const result = await modelingTemplatesApi.uploadVersion(detail.id, detail.latest_version, mode, files)
      if (mounted.current) { onSaved(result); setPending([]); setDestination('') }
    } catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : '버전을 저장하지 못했습니다.') } finally { savingRef.current = false; if (mounted.current) setSaving(false) }
  }
  return <section className="template-upload-panel" aria-label="CSV 버전 업로드">
    <div className="upload-panel-heading"><div><strong>CSV 추가</strong><small>파일을 확인한 뒤 새 버전으로 저장하세요.</small></div><span>{pending.length ? `${pending.length}개 선택됨` : '선택된 파일 없음'}</span></div>
    <div className="upload-panel-actions"><button type="button" disabled={disabled || saving} onClick={() => directory.current?.click()}><FolderOpen size={15}/> 폴더 선택</button><button type="button" disabled={disabled || saving} onClick={() => individual.current?.click()}><Upload size={15}/> 개별 CSV</button><label>저장 모드<select disabled={disabled || saving} value={mode} onChange={(event) => setMode(event.target.value as Mode)}><option value="merge">추가 · 같은 경로 갱신</option><option value="replace">전체 파일 교체</option></select></label></div>
    <label className="upload-destination">저장 폴더 <input disabled={disabled || saving} value={destination} onChange={(event) => setDestination(event.target.value)} placeholder="개별 CSV에 적용할 폴더 (선택)" /></label>
    <p className="upload-mode-hint">{mode === 'replace' ? '선택한 CSV만 담은 새 버전을 만듭니다. 이전 버전은 보존됩니다.' : '같은 경로의 CSV를 갱신하고, 나머지 파일은 그대로 포함합니다.'}</p>
    {pending.length > 0 && <div className="pending-files">{pending.map((item) => <div key={item.path}><FileText size={14}/><span>{targetPath(item)}</span><small>{item.file.size.toLocaleString()} B</small></div>)}</div>}
    {error && <p className="upload-error" role="alert">{error}</p>}
    <button type="button" className="primary-button upload-save" disabled={disabled || saving || !pending.length} onClick={() => void save()}><Check size={15}/>{saving ? '저장 중…' : '새 버전 저장'}</button>
    <input ref={directory} className="visually-hidden" type="file" accept=".csv,text/csv" multiple {...{ webkitdirectory: '', directory: '' } as Record<string, string>} onChange={(event) => { choose(event.target.files, true); event.currentTarget.value = '' }}/><input ref={individual} className="visually-hidden" type="file" accept=".csv,text/csv" multiple onChange={(event) => { choose(event.target.files, false); event.currentTarget.value = '' }}/>
    {pending.length > 0 && <button type="button" className="clear-pending" disabled={disabled || saving} onClick={() => setPending([])}><X size={14}/> 선택 취소</button>}
  </section>
}
