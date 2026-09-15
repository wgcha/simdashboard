import { Plus, RotateCcw, Save, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { FolderAnalysisTypeOption, FolderDiscoveryCatalog, FolderRoleKind, FolderRoleOption } from '../../shared/api/folderDiscovery'

const ROLE_KINDS: FolderRoleKind[] = ['PROJECT', 'REQUEST', 'LOAD_CASE', 'RESULTS', 'INPUT']
const ROLE_KIND_LABEL: Record<FolderRoleKind, string> = { PROJECT: '프로젝트', REQUEST: '의뢰', LOAD_CASE: '하중 경우', RESULTS: '결과 폴더', INPUT: '입력 폴더' }

type Props = {
  catalog: FolderDiscoveryCatalog
  saving: boolean
  onSave: (catalog: Omit<FolderDiscoveryCatalog, 'revision'>, expectedRevision: number) => Promise<void>
}

const cloneCatalog = (catalog: FolderDiscoveryCatalog): FolderDiscoveryCatalog => ({ revision: catalog.revision, roles: catalog.roles.map((item) => ({ ...item })), analysis_types: catalog.analysis_types.map((item) => ({ ...item })) })

export function FolderDiscoveryCatalogEditor({ catalog, saving, onSave }: Props) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<FolderDiscoveryCatalog>(() => cloneCatalog(catalog))
  const [roleKey, setRoleKey] = useState('')
  const [roleLabel, setRoleLabel] = useState('')
  const [roleKind, setRoleKind] = useState<FolderRoleKind>('RESULTS')
  const [analysisKey, setAnalysisKey] = useState('')
  const [analysisLabel, setAnalysisLabel] = useState('')
  const [error, setError] = useState('')

  useEffect(() => { setDraft(cloneCatalog(catalog)); setError('') }, [catalog])

  const updateRole = (key: string, patch: Partial<FolderRoleOption>) => setDraft((current) => ({ ...current, roles: current.roles.map((item) => item.key === key ? { ...item, ...patch } : item) }))
  const updateAnalysis = (key: string, patch: Partial<FolderAnalysisTypeOption>) => setDraft((current) => ({ ...current, analysis_types: current.analysis_types.map((item) => item.key === key ? { ...item, ...patch } : item) }))
  const addRole = () => {
    const key = roleKey.trim()
    const label = roleLabel.trim()
    if (!key || !label) { setError('새 역할의 키와 표시명을 입력하세요.'); return }
    if (draft.roles.some((item) => item.key === key)) { setError('이미 사용하는 역할 키입니다.'); return }
    setDraft((current) => ({ ...current, roles: [...current.roles, { key, label, kind: roleKind, active: true }] }))
    setRoleKey(''); setRoleLabel(''); setError('')
  }
  const addAnalysis = () => {
    const key = analysisKey.trim()
    const label = analysisLabel.trim()
    if (!key || !label) { setError('새 해석 종류의 키와 표시명을 입력하세요.'); return }
    if (draft.analysis_types.some((item) => item.key === key)) { setError('이미 사용하는 해석 종류 키입니다.'); return }
    setDraft((current) => ({ ...current, analysis_types: [...current.analysis_types, { key, label, active: true }] }))
    setAnalysisKey(''); setAnalysisLabel(''); setError('')
  }
  const save = async () => {
    setError('')
    try { await onSave({ roles: draft.roles, analysis_types: draft.analysis_types }, draft.revision) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '카탈로그 저장에 실패했습니다.') }
  }

  return <details className="folder-discovery-catalog-editor" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary><span><strong>폴더 역할·해석 종류 카탈로그</strong><small>관리자 전용 · 변경 이력 보존</small></span><span className="folder-discovery-catalog-summary">역할 {draft.roles.filter((item) => item.active).length} · 해석 {draft.analysis_types.filter((item) => item.active).length}</span></summary>
    <div className="folder-discovery-catalog-body">
      <p>키는 저장된 규칙과 연결되는 식별자라 유지되고, 삭제는 비활성화로 기록됩니다. 기존 규칙의 비활성 역할도 현재 값으로 계속 표시됩니다.</p>
      <div className="folder-discovery-catalog-columns">
        <section><header><h4>역할 옵션</h4><span>활성 역할을 규칙에서 선택합니다.</span></header>
          <div className="folder-discovery-catalog-list">{draft.roles.map((item) => <div className={`folder-discovery-catalog-row${item.active ? '' : ' inactive'}`} key={item.key}><code>{item.key}</code><input aria-label={`${item.key} 역할 표시명`} value={item.label} onChange={(event) => updateRole(item.key, { label: event.target.value })} /><select aria-label={`${item.key} 역할 종류`} value={item.kind} onChange={(event) => updateRole(item.key, { kind: event.target.value as FolderRoleKind })}>{ROLE_KINDS.map((kind) => <option key={kind} value={kind}>{ROLE_KIND_LABEL[kind]}</option>)}</select><button type="button" className="icon-button" aria-label={`${item.key} 역할 ${item.active ? '삭제' : '복구'}`} onClick={() => updateRole(item.key, { active: !item.active })}>{item.active ? <Trash2 /> : <RotateCcw />}</button></div>)}</div>
          <div className="folder-discovery-catalog-add"><input aria-label="새 역할 키" placeholder="새 역할 키" value={roleKey} onChange={(event) => setRoleKey(event.target.value)} /><input aria-label="새 역할 표시명" placeholder="표시명" value={roleLabel} onChange={(event) => setRoleLabel(event.target.value)} /><select aria-label="새 역할 종류" value={roleKind} onChange={(event) => setRoleKind(event.target.value as FolderRoleKind)}>{ROLE_KINDS.map((kind) => <option key={kind} value={kind}>{ROLE_KIND_LABEL[kind]}</option>)}</select><button type="button" className="ghost-button" onClick={addRole}><Plus /> 추가</button></div>
        </section>
        <section><header><h4>해석 종류</h4><span>하중 경우 역할에 표시됩니다.</span></header>
          <div className="folder-discovery-catalog-list">{draft.analysis_types.map((item) => <div className={`folder-discovery-catalog-row${item.active ? '' : ' inactive'}`} key={item.key}><code>{item.key}</code><input aria-label={`${item.key} 해석 종류 표시명`} value={item.label} onChange={(event) => updateAnalysis(item.key, { label: event.target.value })} /><button type="button" className="icon-button" aria-label={`${item.key} 해석 종류 ${item.active ? '삭제' : '복구'}`} onClick={() => updateAnalysis(item.key, { active: !item.active })}>{item.active ? <Trash2 /> : <RotateCcw />}</button></div>)}</div>
          <div className="folder-discovery-catalog-add"><input aria-label="새 해석 종류 키" placeholder="새 해석 키" value={analysisKey} onChange={(event) => setAnalysisKey(event.target.value)} /><input aria-label="새 해석 종류 표시명" placeholder="표시명" value={analysisLabel} onChange={(event) => setAnalysisLabel(event.target.value)} /><button type="button" className="ghost-button" onClick={addAnalysis}><Plus /> 추가</button></div>
        </section>
      </div>
      {error ? <p className="folder-discovery-catalog-error" role="alert">{error}</p> : null}
      <div className="folder-discovery-actions"><button type="button" className="primary-button" onClick={() => void save()} disabled={saving}><Save />{saving ? '저장 중…' : '카탈로그 저장'}</button></div>
    </div>
  </details>
}
