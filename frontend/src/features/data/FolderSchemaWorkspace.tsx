import { useEffect, useRef, useState } from 'react'
import { GripVertical, Save } from 'lucide-react'
import { api } from '../../api'
import type { ImportSchema } from '../../types'; import { MasterResultFolderRefresh } from './MasterResultFolderRefresh'

type DiscoveredFolderFile = { path: string; kind: 'typed_scalars' | 'curve_csv' | 'media'; dataType: 'FLOAT' | 'CURVE' | 'IMAGE' | 'VIDEO' | 'MODEL_3D'; variableKey: string }

export function FolderSchemaWorkspace() {
  const [files, setFiles] = useState<DiscoveredFolderFile[]>([])
  const [schemas, setSchemas] = useState<ImportSchema[]>([])
  const [name, setName] = useState('새 해석 결과 폴더')
  const [description, setDescription] = useState('')
  const [message, setMessage] = useState('')
  const [saving, setSaving] = useState(false)
  const [contextMode, setContextMode] = useState<'folder_levels' | 'manifest'>('folder_levels')
  const [projectLevel, setProjectLevel] = useState(0)
  const [requestLevel, setRequestLevel] = useState(1)
  const [loadCaseLevel, setLoadCaseLevel] = useState(2)
  const directoryInput = useRef<HTMLInputElement>(null)
  useEffect(() => { api.importSchemas().then(setSchemas).catch(() => setSchemas([])) }, [])
  useEffect(() => { directoryInput.current?.setAttribute('webkitdirectory', '') }, [])
  const discover = (selected: FileList | null) => {
    const discovered = Array.from(selected ?? []).map((file) => {
      const path = file.webkitRelativePath || file.name
      const extension = path.toLowerCase().split('.').pop() ?? ''
      const stem = path.split('/').pop()?.replace(/\.[^.]+$/, '') ?? 'result'
      if (['png', 'jpg', 'jpeg', 'webp', 'svg'].includes(extension)) return { path, kind: 'media' as const, dataType: 'IMAGE' as const, variableKey: stem.replace(/[^a-z0-9]+/gi, '_').toLowerCase() }
      if (['mp4', 'webm'].includes(extension)) return { path, kind: 'media' as const, dataType: 'VIDEO' as const, variableKey: stem.replace(/[^a-z0-9]+/gi, '_').toLowerCase() }
      if (['glb', 'gltf'].includes(extension)) return { path, kind: 'media' as const, dataType: 'MODEL_3D' as const, variableKey: stem.replace(/[^a-z0-9]+/gi, '_').toLowerCase() }
      if (extension === 'csv') return { path, kind: 'curve_csv' as const, dataType: 'CURVE' as const, variableKey: stem.replace(/[^a-z0-9]+/gi, '_').toLowerCase() }
      return { path, kind: 'typed_scalars' as const, dataType: 'FLOAT' as const, variableKey: stem.replace(/[^a-z0-9]+/gi, '_').toLowerCase() }
    })
    setFiles(discovered); setMessage(discovered.length ? `${discovered.length}개 파일을 발견했습니다. 각 항목의 유형과 변수 키를 확인한 뒤 스키마를 저장하세요.` : '')
  }
  const save = async () => {
    if (!files.length) return
    setSaving(true); setMessage('')
    try {
      const definition = { context_mapping: { mode: contextMode, project_level: projectLevel, request_level: requestLevel, load_case_level: loadCaseLevel, sample_path: files[0]?.path }, mappings: files.map((file) => file.kind === 'curve_csv' ? { kind: file.kind, path: file.path, variable_key: file.variableKey, display_name: file.variableKey, x_column: 'time_ms', y_column: 'value', x_unit: 'ms', y_unit: '-' } : file.kind === 'media' ? { kind: file.kind, path: file.path, variable_key: file.variableKey, display_name: file.variableKey, asset_type: file.dataType, mime_type: file.dataType === 'VIDEO' ? 'video/mp4' : file.dataType === 'IMAGE' ? 'image/png' : 'model/gltf-binary' } : { kind: file.kind, path: file.path, variable_key: file.variableKey, data_type: file.dataType }) }
      const created = await api.createImportSchema({ name, description, definition, updated_by: '관리자' })
      setSchemas((current) => [created, ...current]); setMessage(`스키마 v1을 저장했습니다. 다음 단계에서 이 스키마를 선택해 실제 폴더를 업로드합니다.`)
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : '스키마 저장에 실패했습니다.') }
    finally { setSaving(false) }
  }
  const sampleParts = (files[0]?.path ?? '').split('/').filter(Boolean)
  return <section className="catalog-page">
    <header><div><span>FOLDER INGESTION DESIGN</span><h1>폴더 스키마</h1><p>최상위 결과 폴더의 하위 파일을 탐색하고 변수 카탈로그로 연결할 규칙을 저장합니다.</p></div><div className="catalog-header-actions"><strong>{schemas.length}개 저장됨</strong></div></header><MasterResultFolderRefresh />
    <SchemaCatalog schemas={schemas} onUpdate={(updated) => setSchemas((current) => current.map((item) => item.id === updated.id ? updated : item))} onDelete={(schemaId) => setSchemas((current) => current.filter((item) => item.id !== schemaId))} />
    <div className="data-form-card"><header><div><span>HIERARCHY MAPPING</span><h2>프로젝트·의뢰·하중 경우 매핑</h2></div></header><label><span>메타데이터 출처</span><select value={contextMode} onChange={(event) => setContextMode(event.target.value as 'folder_levels' | 'manifest')}><option value="folder_levels">폴더 이름 단계</option><option value="manifest">manifest.json context</option></select></label>{contextMode === 'folder_levels' ? <><div className="data-form-row"><label><span>프로젝트(제품) 단계</span><input type="number" min="0" value={projectLevel} onChange={(event) => setProjectLevel(Number(event.target.value))}/></label><label><span>의뢰 단계</span><input type="number" min="0" value={requestLevel} onChange={(event) => setRequestLevel(Number(event.target.value))}/></label><label><span>하중 경우 단계</span><input type="number" min="0" value={loadCaseLevel} onChange={(event) => setLoadCaseLevel(Number(event.target.value))}/></label></div>{sampleParts.length > 0 && <div className="result-preview-kpis"><div><strong>{sampleParts[projectLevel] ?? '미지정'}</strong><span>프로젝트(제품)</span></div><div><strong>{sampleParts[requestLevel] ?? '미지정'}</strong><span>의뢰</span></div><div><strong>{sampleParts[loadCaseLevel] ?? '미지정'}</strong><span>하중 경우</span></div></div>}</> : <p>선택 폴더의 manifest.json 안 `context.project`, `context.request`, `context.load_case`를 사용합니다.</p>}<small>0은 선택한 최상위 폴더입니다. 예: 제품/의뢰/하중경우/results에서 0·1·2로 설정합니다.</small></div>
    <div className="data-form-card"><label><span>스키마 이름</span><input value={name} onChange={(event) => setName(event.target.value)} /></label><label><span>설명</span><input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="TV 낙하 결과 폴더 v1" /></label><label><span>최상위 결과 폴더 선택</span><input ref={directoryInput} type="file" multiple onChange={(event) => discover(event.target.files)} /><small>브라우저가 선택한 폴더의 하위 파일 목록만 읽어 트리 규칙 초안을 만듭니다.</small></label></div>
    {files.length ? <div className="variable-table"><div className="variable-row head"><span>발견 경로</span><span>적재 방식</span><span>자료형</span><span>변수 키</span><span>작업</span><span>상태</span></div>{files.map((file, index) => <article className="variable-row" key={file.path}><span><code>{file.path}</code></span><span>{file.kind}</span><span><b>{file.dataType}</b></span><span><input value={file.variableKey} onChange={(event) => setFiles((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, variableKey: event.target.value } : item))} /></span><span><button onClick={() => setFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}>제외</button></span><span><small className="catalog-data-wait">매핑 확인 필요</small></span></article>)}</div> : <div className="portfolio-empty"><GripVertical/><h2>결과 폴더를 선택하세요.</h2><p>CSV, JSON, 이미지, 영상, GLB/GLTF를 자료형별 후보로 자동 분류합니다.</p></div>}
    <footer className="catalog-header-actions"><span>{message}</span><button className="primary-button" disabled={!files.length || saving} onClick={() => void save()}><Save /> {saving ? '저장 중' : '스키마 JSON 저장'}</button></footer>
  </section>
}

function SchemaCatalog({ schemas, onUpdate, onDelete }: { schemas: ImportSchema[]; onUpdate: (schema: ImportSchema) => void; onDelete: (schemaId: string) => void }) {
  const [selectedId, setSelectedId] = useState('')
  const selected = schemas.find((item) => item.id === selectedId) ?? schemas[0]
  const [editName, setEditName] = useState('')
  const [editDescription, setEditDescription] = useState('')
  const [editJson, setEditJson] = useState('')
  const [editing, setEditing] = useState(false)
  const [status, setStatus] = useState('')
  useEffect(() => {
    if (!selected) return
    setSelectedId(selected.id); setEditName(selected.name); setEditDescription(selected.description); setEditJson(JSON.stringify(selected.definition, null, 2)); setEditing(false); setStatus('')
  }, [selected?.id, selected?.updated_at])
  if (!schemas.length) return <section className="data-form-card"><h2>저장된 폴더 스키마</h2><p>아직 저장된 스키마가 없습니다.</p></section>
  const saveChanges = async () => {
    if (!selected) return
    try {
      const definition = JSON.parse(editJson)
      if (!Array.isArray(definition.mappings)) throw new Error('JSON에는 mappings 배열이 필요합니다.')
      const updated = await api.updateImportSchema(selected.id, { name: editName, description: editDescription, definition, updated_by: '관리자' })
      onUpdate(updated); setEditing(false); setStatus(`v${updated.definition.version}으로 저장했습니다.`)
    } catch (reason) { setStatus(reason instanceof Error ? reason.message : '스키마 수정에 실패했습니다.') }
  }
  const remove = async () => {
    if (!selected || !window.confirm(`${selected.name} 스키마를 목록에서 삭제할까요?`)) return
    try { await api.deleteImportSchema(selected.id); onDelete(selected.id); setSelectedId(''); setStatus('삭제했습니다.') }
    catch (reason) { setStatus(reason instanceof Error ? reason.message : '스키마 삭제에 실패했습니다.') }
  }
  return <section className="data-form-card"><header><div><span>SCHEMA LIBRARY</span><h2>저장된 폴더 스키마</h2></div><div className="catalog-row-actions"><button onClick={() => setEditing((value) => !value)}>{editing ? '편집 취소' : '편집'}</button><button className="danger" onClick={() => void remove()}>삭제</button></div></header><label><span>스키마 선택</span><select value={selected?.id ?? ''} onChange={(event) => setSelectedId(event.target.value)}>{schemas.map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.definition.version ?? 1}</option>)}</select></label>{selected && <div className="result-preview"><div className="result-preview-head"><div><span>SCHEMA DETAIL</span><strong>{selected.name}</strong></div><small>{new Date(selected.updated_at).toLocaleString('ko-KR')}</small></div><div className="result-preview-kpis"><div><strong>{selected.definition.mappings.length}</strong><span>파일 매핑</span></div><div><strong>v{selected.definition.version ?? 1}</strong><span>스키마 버전</span></div></div>{editing ? <div className="schema-editor-fields"><label><span>이름</span><input value={editName} onChange={(event) => setEditName(event.target.value)}/></label><label><span>설명</span><input value={editDescription} onChange={(event) => setEditDescription(event.target.value)}/></label><label><span>스키마 JSON</span><textarea className="schema-json-editor" value={editJson} onChange={(event) => setEditJson(event.target.value)}/></label><button className="primary-button" onClick={() => void saveChanges()}><Save/> 변경 저장</button></div> : <><p>{selected.description || '설명 없음'}</p><pre className="schema-json">{JSON.stringify(selected.definition, null, 2)}</pre></>}{status && <p>{status}</p>}</div>}</section>
}
