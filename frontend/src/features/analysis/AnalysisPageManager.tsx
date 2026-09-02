import { useEffect, useState, type FormEvent } from 'react'
import { AlertTriangle, ArrowLeft, ArrowRight, LayoutDashboard, LoaderCircle, Plus, Save, Trash2, X } from 'lucide-react'
import { api } from '../../api'
import type { DashboardDefinition, DashboardPageSummary } from '../../types'

export function AnalysisPageManager({ loadCaseId, activePageId, visiblePageIds, onClose, onPagesChanged, onActiveDefinitionChanged, onActivate, onDeleted }: { loadCaseId: string; activePageId: string; visiblePageIds: string[]; onClose: () => void; onPagesChanged: (pages: DashboardPageSummary[]) => void; onActiveDefinitionChanged: (definition: DashboardDefinition) => void; onActivate: (definition: DashboardDefinition, startEditing?: boolean) => void; onDeleted: (dashboardId: string) => void }) {
  const [pages, setPages] = useState<DashboardPageSummary[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [newName, setNewName] = useState('')
  const [newDescription, setNewDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<DashboardPageSummary | null>(null)
  const [deleteConfirmation, setDeleteConfirmation] = useState('')
  const selected = pages.find((page) => page.id === selectedId)

  const reload = async (preferredId?: string) => {
    const [adminItems, publicItems] = await Promise.all([api.adminDashboardPages(loadCaseId, true), api.dashboardPages(loadCaseId)])
    const sorted = [...adminItems].sort((left, right) => left.page.display_order - right.page.display_order)
    setPages(sorted); onPagesChanged(publicItems)
    const nextId = preferredId ?? selectedId ?? activePageId ?? sorted[0]?.id ?? ''
    const next = sorted.find((page) => page.id === nextId) ?? sorted[0]
    setSelectedId(next?.id ?? ''); setName(next?.name ?? ''); setDescription(next?.description ?? '')
  }

  useEffect(() => { void reload(activePageId).catch((reason) => setError(reason instanceof Error ? reason.message : '분석 페이지를 불러오지 못했습니다.')) }, [loadCaseId])

  const choose = (page: DashboardPageSummary) => { setSelectedId(page.id); setName(page.name); setDescription(page.description) }
  const create = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const definition = await api.createDashboardPage({ load_case_id: loadCaseId, name: newName.trim(), description: newDescription.trim() })
      setNewName(''); setNewDescription(''); await reload(definition.id); onActivate(definition, true)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '분석 페이지를 만들지 못했습니다.') } finally { setBusy(false) }
  }
  const saveMetadata = async () => {
    if (!selected || selected.page.is_system) return
    setBusy(true); setError('')
    try { await api.updateDashboardPage(selected.id, { name: name.trim(), description: description.trim() }); await reload(selected.id); if (selected.id === activePageId) onActiveDefinitionChanged(await api.dashboard(selected.id)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '페이지 정보를 저장하지 못했습니다.') } finally { setBusy(false) }
  }
  const changeStatus = async (status: 'draft' | 'published' | 'archived') => {
    if (!selected || selected.page.is_system) return
    setBusy(true); setError('')
    try { await api.updateDashboardPage(selected.id, { status }); await reload(selected.id); if (selected.id === activePageId) onActiveDefinitionChanged(await api.dashboard(selected.id)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '페이지 상태를 변경하지 못했습니다.') } finally { setBusy(false) }
  }
  const move = async (offset: -1 | 1) => {
    if (!selected || selected.page.is_system || selected.page.status === 'archived') return
    const custom = pages.filter((page) => !page.page.is_system && page.page.status !== 'archived')
    const index = custom.findIndex((page) => page.id === selected.id); const target = index + offset
    if (index < 0 || target < 0 || target >= custom.length) return
    const reordered = [...custom]; [reordered[index], reordered[target]] = [reordered[target], reordered[index]]
    setBusy(true); setError('')
    try { await api.reorderDashboardPages(loadCaseId, reordered.map((page) => page.id)); await reload(selected.id); if (selected.id === activePageId) onActiveDefinitionChanged(await api.dashboard(selected.id)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '페이지 순서를 저장하지 못했습니다.') } finally { setBusy(false) }
  }
  const deletePermanently = async () => {
    if (!deleteTarget || deleteConfirmation !== deleteTarget.name || deleteTarget.page.is_system) return
    const activeCustom = pages.filter((page) => !page.page.is_system && page.page.analysis_key === 'custom' && page.page.status !== 'archived')
    const targetIndex = activeCustom.findIndex((page) => page.id === deleteTarget.id)
    const visibleIds = new Set(visiblePageIds)
    const fallbackIds = [activeCustom[targetIndex + 1]?.id, activeCustom[targetIndex - 1]?.id, pages.find((page) => page.page.analysis_key === 'open_cell' && visibleIds.has(page.id))?.id, pages.find((page) => page.page.analysis_key === 'chassis_rear' && visibleIds.has(page.id))?.id, pages.find((page) => page.page.analysis_key === 'run_comparison' && visibleIds.has(page.id))?.id].filter(Boolean) as string[]
    setBusy(true); setError('')
    try {
      await api.deleteDashboardPage(deleteTarget.id, loadCaseId)
      const [adminItems, publicItems] = await Promise.all([api.adminDashboardPages(loadCaseId, true), api.dashboardPages(loadCaseId)])
      const sorted = [...adminItems].sort((left, right) => left.page.display_order - right.page.display_order)
      setPages(sorted); onPagesChanged(publicItems); onDeleted(deleteTarget.id)
      const fallback = fallbackIds.map((id) => sorted.find((page) => page.id === id)).find(Boolean) ?? sorted.find((page) => visibleIds.has(page.id) && page.id !== deleteTarget.id)
      setDeleteTarget(null); setDeleteConfirmation('')
      if (deleteTarget.id === activePageId && fallback) {
        onActivate(await api.dashboard(fallback.id), false)
        onClose()
      } else {
        setSelectedId(fallback?.id ?? ''); setName(fallback?.name ?? ''); setDescription(fallback?.description ?? '')
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : '분석 페이지를 영구 삭제하지 못했습니다.') } finally { setBusy(false) }
  }

  return <div className="drawer-backdrop" onMouseDown={onClose}><section className="analysis-page-manager" role="dialog" aria-modal="true" aria-labelledby="analysis-page-manager-title" onMouseDown={(event)=>event.stopPropagation()}>
    <header><div><span>ANALYSIS PAGE LIBRARY</span><h2 id="analysis-page-manager-title">상세 분석 페이지 관리</h2><p>페이지 수와 내부 위젯 수에는 제한이 없습니다.</p></div><button aria-label="닫기" onClick={onClose}><X/></button></header>
    {error && <div className="catalog-error"><AlertTriangle/>{error}</div>}
    <div className="analysis-page-manager-grid">
      <aside><strong>페이지 {pages.length}개</strong><div>{pages.map((page)=><button key={page.id} className={page.id===selectedId?'active':''} onClick={()=>choose(page)}><span>{page.name}<small>{page.page.is_system?'SYSTEM':page.page.status.toUpperCase()}</small></span><b>v{page.version}</b></button>)}</div></aside>
      <main>
        <form className="analysis-page-create" onSubmit={(event)=>void create(event)}><h3>빈 페이지 추가</h3><label><span>페이지 이름</span><input required minLength={2} maxLength={120} value={newName} onChange={(event)=>setNewName(event.target.value)}/></label><label><span>설명</span><textarea maxLength={500} value={newDescription} onChange={(event)=>setNewDescription(event.target.value)}/></label><button className="primary-button" disabled={busy||newName.trim().length<2}><Plus/> 생성하고 위젯 배치</button></form>
        {selected && <section className="analysis-page-detail"><header><div><span>{selected.page.analysis_key.replace('_',' ')}</span><h3>{selected.name}</h3></div><button onClick={()=>void api.dashboard(selected.id).then((definition)=>onActivate(definition, false))} disabled={busy}><LayoutDashboard/> 페이지 열기</button></header><label><span>이름</span><input disabled={selected.page.is_system} value={name} onChange={(event)=>setName(event.target.value)}/></label><label><span>설명</span><textarea disabled={selected.page.is_system} value={description} onChange={(event)=>setDescription(event.target.value)}/></label>{!selected.page.is_system && <><div className="analysis-page-detail-actions"><button onClick={()=>void move(-1)} disabled={busy}><ArrowLeft/> 앞</button><button onClick={()=>void move(1)} disabled={busy}>뒤 <ArrowRight/></button><button onClick={()=>void saveMetadata()} disabled={busy||name.trim().length<2}><Save/> 정보 저장</button></div><div className="analysis-page-status-actions">{selected.page.status==='draft'&&<button className="primary-button" onClick={()=>void changeStatus('published')} disabled={busy}>게시</button>}{selected.page.status==='published'&&<button onClick={()=>void changeStatus('draft')} disabled={busy}>게시 해제</button>}{selected.page.status!=='archived'?<button className="danger" onClick={()=>void changeStatus('archived')} disabled={busy}>보관</button>:<button onClick={()=>void changeStatus('draft')} disabled={busy}>초안으로 복원</button>}<button className="danger permanent-delete" onClick={()=>{setDeleteTarget(selected);setDeleteConfirmation('')}} disabled={busy}><Trash2/> 영구 삭제</button></div></>}</section>}
      </main>
    </div>
    {deleteTarget && <div className="analysis-page-delete-confirm" role="alertdialog" aria-modal="true" aria-labelledby="analysis-page-delete-title"><div><AlertTriangle/><h3 id="analysis-page-delete-title">{deleteTarget.name} 영구 삭제</h3><p>이 페이지와 모든 버전이 삭제되며 복구할 수 없습니다. 계속하려면 페이지 이름을 정확히 입력하세요.</p><label><span>페이지 이름 확인</span><input autoFocus value={deleteConfirmation} onChange={(event)=>setDeleteConfirmation(event.target.value)} placeholder={deleteTarget.name}/></label><footer><button onClick={()=>{setDeleteTarget(null);setDeleteConfirmation('')}} disabled={busy}>취소</button><button className="danger" onClick={()=>void deletePermanently()} disabled={busy||deleteConfirmation!==deleteTarget.name}>{busy?<LoaderCircle className="spin"/>:<Trash2/>} 영구 삭제</button></footer></div></div>}
  </section></div>
}
