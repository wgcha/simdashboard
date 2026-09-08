import { useCallback, useEffect, useRef, useState } from 'react'
import { Archive, ChevronRight, FileText, Folder, Plus, RefreshCw, Search, X } from 'lucide-react'
import { modelingTemplatesApi, type ModelingTemplateDetail } from '../../../shared/api/modelingTemplates'
import { useMemoryQuery } from '../../../shared/cache/useMemoryQuery'
import { TemplateCreateDialog } from './TemplateCreateDialog'
import { TemplateFileTree, formatBytes } from './TemplateFileTree'
import { TemplateUploadPanel } from './TemplateUploadPanel'
import './modeling-templates.css'

type Selection = { id: string; version?: number }
export function ModelingTemplatesPage() {
  const [q, setQ] = useState('')
  const [search, setSearch] = useState('')
  const [product, setProduct] = useState('')
  const [loadCase, setLoadCase] = useState('')
  const [selection, setSelection] = useState<Selection>()
  const [creating, setCreating] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [downloading, setDownloading] = useState(false)
  const selectionRef = useRef(selection)
  selectionRef.current = selection
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => { const timer = window.setTimeout(() => setSearch(q), 220); return () => window.clearTimeout(timer) }, [q])
  const listQuery = useCallback(() => modelingTemplatesApi.list({ q: search, product_name: product, load_case_name: loadCase }), [search, product, loadCase])
  const catalog = useMemoryQuery({ key: `modeling-templates:list:${JSON.stringify([search, product, loadCase])}`, query: listQuery })
  const detailQuery = useCallback(() => modelingTemplatesApi.detail(selection!.id, selection!.version), [selection?.id, selection?.version])
  const snapshot = useMemoryQuery({ key: `modeling-templates:detail:${selection?.id}:${selection?.version ?? 'latest'}`, query: detailQuery, enabled: Boolean(selection) })
  const catalogData = catalog.isBlocked ? undefined : catalog.data
  const detail = snapshot.isBlocked ? undefined : snapshot.data
  const canManage = Boolean(catalogData?.can_manage)
  // Keep search controls and their options mounted while a new filter is fetched.
  const options = useRef({ products: [] as string[], load_cases: [] as string[] })
  if (catalogData) options.current = catalogData
  if (catalog.isBlocked) options.current = { products: [], load_cases: [] }
  const cards = q === search ? catalogData?.items ?? [] : []
  const pendingSearch = catalog.isLoading || q !== search
  const visibleError = error || catalog.error?.message || snapshot.error?.message
  const choose = (id: string, version?: number) => { setSelection({ id, version }); setError(''); setNotice('') }
  const onSaved = (value: ModelingTemplateDetail) => {
    if (selectionRef.current?.id !== value.id) return
    setSelection({ id: value.id, version: value.latest_version })
    setNotice(`v${value.latest_version}을 저장했습니다. 이전 버전도 다시 다운로드할 수 있습니다.`)
    catalog.retry()
  }
  const created = (value: ModelingTemplateDetail) => {
    setCreating(false); setQ(''); setSearch(''); setProduct(''); setLoadCase('')
    setSelection({ id: value.id }); setNotice('템플릿을 만들었습니다. CSV를 선택해 새 버전으로 저장하세요.')
    catalog.retry()
  }
  async function saveDownload(promise: Promise<Blob>, name: string) {
    setDownloading(true); setError('')
    try {
      const blob = await promise
      if (!mounted.current) return
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url; link.download = name; document.body.append(link); link.click(); link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : '다운로드하지 못했습니다.')
    } finally { if (mounted.current) setDownloading(false) }
  }
  return <section className="modeling-templates-page">
    <header className="modeling-hero"><div><span className="eyebrow">MODELING LIBRARY</span><h1>모델링 템플릿</h1><p>제품·하중 경우별 CSV 설정을 저장하고, 필요한 버전을 다시 내려받습니다.</p></div>{canManage && <button className="primary-button" onClick={() => setCreating(true)}><Plus size={17} /> 새 템플릿</button>}</header>
    <section className="template-toolbar" aria-label="템플릿 찾기">
      <label className="template-search"><Search size={18} /><input aria-label="템플릿 검색" placeholder="템플릿 이름, 제품, 하중 경우 검색" value={q} onChange={event => setQ(event.target.value)} /></label>
      <select aria-label="제품 필터" value={product} onChange={event => setProduct(event.target.value)}><option value="">모든 제품</option>{options.current.products.map(item => <option key={item}>{item}</option>)}</select>
      <select aria-label="하중 경우 필터" value={loadCase} onChange={event => setLoadCase(event.target.value)}><option value="">모든 하중 경우</option>{options.current.load_cases.map(item => <option key={item}>{item}</option>)}</select>
      <button className="icon-button" aria-label="새로고침" onClick={() => { catalog.retry(); if (selection) snapshot.retry() }} disabled={catalog.isLoading}><RefreshCw size={17} /></button>
    </section>
    {visibleError && <div className="template-alert error" role="alert">{visibleError}<button onClick={() => { setError(''); catalog.retry(); if (selection) snapshot.retry() }}>다시 시도</button></div>}
    {notice && <div className="template-alert success" role="status">{notice}<button onClick={() => setNotice('')} aria-label="알림 닫기"><X size={15} /></button></div>}
    <div className={`template-layout ${selection ? 'has-selection' : 'no-selection'}`}>
      <section className="template-card-grid" aria-label="저장된 템플릿">
        {cards.map(card => <button key={card.id} className={`modeling-card ${selection?.id === card.id ? 'selected' : ''}`} aria-pressed={selection?.id === card.id} onClick={() => choose(card.id)}><span className="card-icon"><FileText size={19} /></span><span className="card-copy"><span>{card.product_name} · {card.load_case_name}</span><strong>{card.name}</strong><small>{card.description || 'CSV 모델링 설정'}</small></span><span className="card-stats"><b>v{card.latest_version}</b><small>{card.file_count}개 · {formatBytes(card.total_bytes)}</small></span><ChevronRight size={17} /></button>)}
        {pendingSearch && <p className="template-loading">템플릿을 불러오는 중…</p>}
        {!pendingSearch && !cards.length && <div className="template-empty"><Folder size={26} /><h2>{q || product || loadCase ? '검색 결과가 없습니다' : '저장된 템플릿이 없습니다'}</h2><p>{q || product || loadCase ? '검색어나 제품·하중 경우 조건을 바꿔 보세요.' : '새 템플릿에서 제품과 하중 경우를 입력해 시작하세요.'}</p></div>}
      </section>
      {selection && <aside className="template-detail" aria-busy={snapshot.isLoading}>
        {!detail ? <p className="template-loading">{snapshot.error ? '상세 정보를 불러오지 못했습니다.' : '선택한 버전을 불러오는 중…'}</p> : <>
          <header><div><span className="eyebrow">TEMPLATE DETAIL</span><h2>{detail.name}</h2><p>{detail.product_name} · {detail.load_case_name}</p></div><button className="icon-button" aria-label="템플릿 상세 닫기" onClick={() => setSelection(undefined)}><X size={16} /></button></header>
          <div className="detail-version"><label>버전<select aria-label="버전" value={detail.selected_version.version} onChange={event => choose(detail.id, Number(event.target.value))}>{detail.versions.map(item => <option key={item.version} value={item.version}>v{item.version} · {item.file_count}개</option>)}</select></label><button disabled={downloading || !detail.files.length} onClick={() => void saveDownload(modelingTemplatesApi.zip(detail.id, detail.selected_version.version), `${detail.name}_v${detail.selected_version.version}.zip`)}><Archive size={16} /> 전체 ZIP</button></div>
          {detail.selected_version.version === detail.latest_version
            ? <TemplateUploadPanel key={`${detail.id}:${detail.selected_version.version}`} detail={detail} canManage={canManage} onSaved={onSaved} />
            : <p className="template-version-note">이전 버전을 보고 있습니다. <button onClick={() => choose(detail.id)}>최신 버전에서 파일 추가</button></p>}
          <TemplateFileTree files={detail.files} disabled={downloading} onDownload={file => void saveDownload(modelingTemplatesApi.file(detail.id, detail.selected_version.version, file.id), file.relative_path.split('/').pop()!)} />
        </>}
      </aside>}
    </div>
    {creating && canManage && <TemplateCreateDialog products={options.current.products} loadCases={options.current.load_cases} onClose={() => setCreating(false)} onCreated={created} />}
  </section>
}
