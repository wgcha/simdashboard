import { useCallback, useEffect, useRef, useState, type ComponentType, type FormEvent } from 'react'
import { AlertTriangle, Check, ChevronDown, ClipboardPlus, Database, Download, LayoutDashboard, Play, Plus, Upload } from 'lucide-react'
import { api } from '../../api'
import type { AnalysisRequest, LoadCase, Project } from '../../types'
import { ResultImportHistory } from './ResultImportHistory'
import { useDataWorkspaceContext } from './useDataWorkspaceContext'
import { uploadStructuredResult, type StoragePanelProps } from '../../shared/api/storage'
import './DataWorkspace.css'
export function DataWorkspace({ canCreateProject, canRetryImports, canUploadStorage = false, canBindStorage = false, canManageStorageRoot = false, contextChanging = false, embedded = false, projects, initialProjectId, initialRequestId, initialLoadCaseId, refreshToken = 0, storagePanel: StoragePanel, onContextChange, onLoadCaseCreated, onDataChanged, onOpenAnalysis, onOpenIntake }: { canCreateProject: boolean; canRetryImports: boolean; canUploadStorage?: boolean; canBindStorage?: boolean; canManageStorageRoot?: boolean; contextChanging?: boolean; embedded?: boolean; projects: Project[]; initialProjectId: string; initialRequestId?: string; initialLoadCaseId?: string; refreshToken?: number; storagePanel?: ComponentType<StoragePanelProps>; onContextChange?: (context: { projectId: string; requestId: string; loadCaseId: string }) => void; onLoadCaseCreated?: (loadCase: LoadCase) => void; onDataChanged: () => Promise<void>; onOpenAnalysis: (projectId: string, requestId: string, loadCaseId: string) => Promise<void>; onOpenIntake: () => void }) {
  const [managedProjects, setManagedProjects] = useState(projects)
  const [projectId, setProjectId] = useState(initialProjectId || projects[0]?.id || '')
  const [requests, setRequests] = useState<AnalysisRequest[]>([])
  const [requestId, setRequestId] = useState(initialRequestId || '')
  const [loadCases, setLoadCases] = useState<LoadCase[]>([])
  const [loadCaseId, setLoadCaseId] = useState('')
  const [message, setMessage] = useState('')
  const [formError, setFormError] = useState('')
  const [busy, setBusy] = useState(false)
  const [projectForm, setProjectForm] = useState({ name: '', product_name: '', manufacturer: '', display_size_inch: 65 as number | null, description: '' })
  const [caseForm, setCaseForm] = useState({ name: '', analysis_type: 'DROP' as 'DROP' | 'SIDE_CLAMP', primary: '800', secondary: 'BOTTOM' })
  const [resultFile, setResultFile] = useState<{ filename: string; content: string } | null>(null)
  const [resultAuthor, setResultAuthor] = useState('해석 담당자')
  const [importPreview, setImportPreview] = useState<Awaited<ReturnType<typeof api.importResults>> | null>(null)
  const [imported, setImported] = useState(false)
  const [storageBound, setStorageBound] = useState(false)
  const [historyRefreshToken, setHistoryRefreshToken] = useState(0)
  const resetImportState = useCallback(() => { setResultFile(null); setImportPreview(null); setImported(false) }, [])
  useDataWorkspaceContext({ embedded, initialLoadCaseId, initialProjectId, initialRequestId, projectId, projects, requestId, setFormError, setLoadCaseId, setLoadCases, setManagedProjects, setProjectId, setRequestId, setRequests, resetImportState })
  useEffect(() => {
    if (!embedded) onContextChange?.({ projectId, requestId, loadCaseId })
  }, [embedded, loadCaseId, onContextChange, projectId, requestId])
  const complete = async (label: string, action: () => Promise<boolean | void>) => {
    setBusy(true); setFormError(''); setMessage('')
    try {
      if (await action() === false) return
      setMessage(`${label} 등록이 완료되었습니다.`)
      await onDataChanged()
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : `${label} 등록에 실패했습니다.`)
    } finally { setBusy(false) }
  }
  const submitProject = (event: FormEvent) => {
    event.preventDefault()
    void complete('프로젝트', async () => {
      const created = await api.createProject(projectForm)
      setManagedProjects((items) => [created, ...items])
      setProjectId(created.id); setRequests([]); setRequestId(''); setLoadCases([])
      if (embedded) onContextChange?.({ projectId: created.id, requestId: '', loadCaseId: '' })
      setProjectForm({ name: '', product_name: '', manufacturer: '', display_size_inch: 65, description: '' })
    })
  }
  const submitLoadCase = (event: FormEvent) => {
    event.preventDefault()
    const activeRequestId = embedded ? initialRequestId || '' : requestId
    if (!activeRequestId) return
    const operationContext = contextKey
    void complete('하중 경우', async () => {
      const parameters: Record<string, string | number | string[]> = caseForm.analysis_type === 'DROP'
        ? { drop_height_mm: Number(caseForm.primary), impact_direction: caseForm.secondary }
        : { clamp_pressure_kpa: Number(caseForm.primary), hold_time_s: Number(caseForm.secondary) || 10 }
      const created = await api.createLoadCase(activeRequestId, { name: caseForm.name, analysis_type: caseForm.analysis_type, parameters })
      if (!isCurrentContext(operationContext)) return false
      setLoadCases((items) => [created, ...items])
      setLoadCaseId(created.id)
      onLoadCaseCreated?.(created)
      setCaseForm({ name: '', analysis_type: caseForm.analysis_type, primary: caseForm.analysis_type === 'DROP' ? '800' : '25', secondary: caseForm.analysis_type === 'DROP' ? 'BOTTOM' : '10' })
    })
  }
  const activeRequestId = embedded ? initialRequestId || '' : requestId; const activeLoadCaseId = embedded ? initialLoadCaseId || '' : loadCaseId
  const contextReady = !contextChanging && (!embedded || (projectId === initialProjectId && requestId === activeRequestId && loadCaseId === activeLoadCaseId)); const contextKey = [contextChanging, initialProjectId, initialRequestId, initialLoadCaseId, projectId, requestId, loadCaseId].join('|')
  const contextKeyRef = useRef(contextKey); contextKeyRef.current = contextKey; const isCurrentContext = (key: string) => contextKeyRef.current === key
  useEffect(() => { resetImportState() }, [contextKey, resetImportState]); const selectedRequest = requests.find((item) => item.id === activeRequestId)
  const validateResultFile = async (selected: { filename: string; content: string }, operationContext = contextKey) => {
    if (!activeLoadCaseId || !contextReady || !isCurrentContext(operationContext)) return
    setResultFile(selected); setBusy(true)
    try {
      const preview = await api.importResults(activeLoadCaseId, { ...selected, author: resultAuthor, validate_only: true })
      if (isCurrentContext(operationContext)) setImportPreview(preview)
    } catch (reason) { if (isCurrentContext(operationContext)) setFormError(reason instanceof Error ? reason.message : '결과 파일 검증에 실패했습니다.') }
    finally { setBusy(false) }
  }
  const chooseResultFile = async (file?: File) => {
    const operationContext = contextKey
    setFormError(''); setMessage(''); setImportPreview(null); setImported(false)
    if (!file) { setResultFile(null); return }
    if (!/\.(csv|json)$/i.test(file.name)) { setFormError('CSV 또는 JSON 파일만 선택할 수 있습니다.'); return }
    if (file.size > 4_500_000) { setFormError('파일은 4.5 MB 이하여야 합니다.'); return }
    const selected = { filename: file.name, content: await file.text() }
    if (isCurrentContext(operationContext)) await validateResultFile(selected, operationContext)
  }
  const loadRadiossExample = async () => {
    const operationContext = contextKey
    setFormError(''); setMessage(''); setImportPreview(null); setImported(false); setBusy(true)
    try {
      const content = await api.resultImportTemplate('radioss-csv')
      if (isCurrentContext(operationContext)) await validateResultFile({ filename: 'radioss-tv-result-example.csv', content }, operationContext)
    } catch (reason) { if (isCurrentContext(operationContext)) setFormError(reason instanceof Error ? reason.message : 'Radioss 예제 검증에 실패했습니다.') }
    finally { setBusy(false) }
  }
  const submitResultImport = async () => {
    if (!resultFile || !activeLoadCaseId || !contextReady) return
    const operationContext = contextKey
    setBusy(true); setFormError(''); setMessage('')
    try {
      if (!storageBound) { setFormError('결과 저장 폴더를 먼저 연결하세요.'); return }
      const result = await uploadStructuredResult(activeLoadCaseId, { ...resultFile, author: resultAuthor })
      if (!isCurrentContext(operationContext)) return
      const outcome = result.result ?? result as { status?: string; run_id?: string | null; run_no?: number | null }
      if (!outcome || !['IMPORTED', 'SKIPPED'].includes(outcome.status || '') || !outcome.run_id) {
        setFormError(outcome?.status === 'FAILED' ? '원본 파일은 저장됐지만 수치 결과 등록에 실패했습니다. 파일 상태를 확인하세요.' : '수치 결과 등록 응답을 확인하지 못했습니다. 현재 폴더를 새로고침해 주세요.')
        return
      }
      setImported(true)
      setMessage(outcome?.status === 'SKIPPED' ? '동일한 결과 파일이 이미 등록되어 있습니다. 기존 결과를 표시합니다.' : `Run #${outcome?.run_no ?? '신규'} 결과를 등록했습니다. 결과 검토 단계가 시작되었습니다.`)
      await onDataChanged()
    } catch (reason) { if (isCurrentContext(operationContext)) setFormError(reason instanceof Error ? reason.message : '해석 결과 등록에 실패했습니다.') }
    finally { setHistoryRefreshToken((value) => value + 1); setBusy(false) }
  }

  return <section className="data-workspace">
    {!embedded && <header className="data-workspace-head">
      <div><span>OPERATIONS / FILE DATABASE</span><h1>해석 데이터 등록</h1><p>프로젝트와 하중 경우를 구성하고, 완료된 해석 결과를 검증해 DB에 등록합니다.</p></div>
      <div className="data-count"><strong>{managedProjects.length}</strong><span>PROJECTS</span></div>
    </header>}
    {!embedded && <div className="data-hierarchy-bar">
      <label><span>1 · 프로젝트</span><select aria-label="등록 프로젝트 선택" value={projectId} onChange={(event) => setProjectId(event.target.value)}>{managedProjects.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.product_name}</option>)}</select></label>
      <i>›</i>
      <label><span>2 · 접수된 의뢰</span><select aria-label="등록 의뢰 선택" value={requestId} onChange={(event) => setRequestId(event.target.value)} disabled={!requests.length}>{requests.length ? requests.map((item) => <option key={item.id} value={item.id}>{item.title}</option>) : <option>의뢰 접수 탭에서 먼저 접수하세요</option>}</select></label>
      <i>›</i>
      <label><span>3 · 하중 경우</span><select aria-label="등록 하중 경우 선택" value={loadCaseId} onChange={(event) => { setLoadCaseId(event.target.value); setResultFile(null); setImportPreview(null); setImported(false) }} disabled={!loadCases.length}>{loadCases.length ? loadCases.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.analysis_type}</option>) : <option value="">하중 경우 없음</option>}</select></label>
    </div>}
    {(message || formError) && <div className={`data-message ${formError ? 'error' : ''}`}>{formError ? <AlertTriangle /> : <Check />}{formError || message}</div>}
    <details className="data-setup-details" open={projects.length === 0}>
      <summary>프로젝트와 하중 경우 설정</summary>
      <div className="data-intake-handoff"><ClipboardPlus /><span><strong>새 해석 의뢰는 별도 접수 절차를 사용합니다.</strong>외부 시스템 전달·부서장 지시와 작업 시나리오를 기록한 뒤 이 화면에서 하중 경우와 결과를 연결하세요.</span><button type="button" onClick={onOpenIntake}>의뢰 접수 열기 <ChevronDown /></button></div>
    <div className={`data-form-grid ${canCreateProject ? '' : 'single'}`}>
      {canCreateProject && <article className="data-form-card"><header><span>01</span><div><h2>새 프로젝트</h2><p>제품 단위 최상위 분류</p></div></header><form onSubmit={submitProject}>
        <label><span>프로젝트 이름</span><input required value={projectForm.name} onChange={(e) => setProjectForm({ ...projectForm, name: e.target.value })} placeholder="예: 2026 OLED 신뢰성" /></label>
        <label><span>제품 모델명</span><input required value={projectForm.product_name} onChange={(e) => setProjectForm({ ...projectForm, product_name: e.target.value })} placeholder="예: OLED77X26" /></label>
        <label><span>제조사</span><input value={projectForm.manufacturer} onChange={(e) => setProjectForm({ ...projectForm, manufacturer: e.target.value })} placeholder="예: NeoView Display" /></label>
        <label><span>화면 크기 (inch)</span><input type="number" min="1" max="200" value={projectForm.display_size_inch ?? ''} onChange={(e) => setProjectForm({ ...projectForm, display_size_inch: e.target.value ? Number(e.target.value) : null })} /></label>
        <label><span>설명</span><textarea value={projectForm.description} onChange={(e) => setProjectForm({ ...projectForm, description: e.target.value })} placeholder="제품과 해석 목적을 입력하세요." /></label>
        <button className="data-submit" disabled={busy || !contextReady}><Plus /> 프로젝트 등록</button>
      </form></article>}

      <article className="data-form-card"><header><span>02</span><div><h2>새 하중 경우</h2><p>{selectedRequest?.title || '접수된 의뢰를 선택하세요'}</p></div></header><form onSubmit={submitLoadCase}>
        <label><span>하중 경우 이름</span><input required disabled={!activeRequestId || !contextReady} value={caseForm.name} onChange={(e) => setCaseForm({ ...caseForm, name: e.target.value })} placeholder="예: Bottom Drop 800 mm" /></label>
        <label><span>해석 유형</span><select value={caseForm.analysis_type} onChange={(e) => { const type = e.target.value as 'DROP' | 'SIDE_CLAMP'; setCaseForm({ name: caseForm.name, analysis_type: type, primary: type === 'DROP' ? '800' : '25', secondary: type === 'DROP' ? 'BOTTOM' : '10' }) }}><option value="DROP">포장 낙하 (DROP)</option><option value="SIDE_CLAMP">Side Clamp</option></select></label>
        <div className="data-form-row"><label><span>{caseForm.analysis_type === 'DROP' ? '낙하 높이 (mm)' : '압력 (kPa)'}</span><input type="number" min="0" required value={caseForm.primary} onChange={(e) => setCaseForm({ ...caseForm, primary: e.target.value })} /></label><label><span>{caseForm.analysis_type === 'DROP' ? '충격 방향' : '유지 시간 (s)'}</span>{caseForm.analysis_type === 'DROP' ? <select value={caseForm.secondary} onChange={(e) => setCaseForm({ ...caseForm, secondary: e.target.value })}><option>BOTTOM</option><option>TOP</option><option>LEFT</option><option>RIGHT</option></select> : <input type="number" min="0" value={caseForm.secondary} onChange={(e) => setCaseForm({ ...caseForm, secondary: e.target.value })} />}</label></div>
        <button className="data-submit" disabled={busy || !activeRequestId || !contextReady}><Plus /> 하중 경우 등록</button>
      </form></article>
    </div>
    </details>
    {activeLoadCaseId && StoragePanel ? <StoragePanel refreshToken={refreshToken} loadCaseId={activeLoadCaseId} projectLabel={managedProjects.find((item) => item.id === projectId)?.name} requestLabel={selectedRequest?.title} loadCaseLabel={loadCases.find((item) => item.id === activeLoadCaseId)?.name} canManageRoot={canManageStorageRoot} canBindFolder={canBindStorage} canUpload={canUploadStorage} allowStructuredUpload={false} onBindingStateChange={setStorageBound} onChanged={onDataChanged} /> : null}
    <article className="result-import-card">
      <header>
        <div><span>{embedded ? 'RESULT INGESTION' : '03 · RESULT INGESTION'}</span><h2>해석 결과 가져오기</h2><p>선택한 하중 경우에 CSV 또는 JSON 결과를 검증한 뒤 새 Analysis Run으로 저장합니다.</p></div>
        <div className="template-links"><button onClick={() => void loadRadiossExample()} disabled={!activeLoadCaseId || !contextReady || busy}><Play /> 예제로 검증</button><a href={api.resultImportTemplateUrl('radioss-csv')} download><Download /> Radioss CSV</a><a href={api.resultImportTemplateUrl('csv')} download><Download /> 요약 CSV</a><a href={api.resultImportTemplateUrl('json')} download><Download /> JSON</a></div>
      </header>
      <div className="result-import-body">
        <div className="result-drop-zone">
          <input id="result-file" type="file" accept=".csv,.json,text/csv,application/json" onChange={(event) => void chooseResultFile(event.target.files?.[0])} disabled={!activeLoadCaseId || !contextReady || busy} />
          <label htmlFor="result-file"><Upload /><strong>{resultFile?.filename || '결과 파일 선택'}</strong><span>CSV / JSON · 최대 4.5 MB · 선택 즉시 사전 검증</span></label>
          <div className="result-import-meta"><label><span>수행자</span><input value={resultAuthor} onChange={(event) => setResultAuthor(event.target.value)} /></label><div><span>등록 대상</span><strong>{loadCases.find((item) => item.id === activeLoadCaseId)?.name || '하중 경우를 선택하세요'}</strong></div></div>
        </div>
        <div className="result-preview">
          {importPreview ? <>
            <div className="result-preview-head"><div><span>{imported ? 'IMPORTED' : 'VALIDATED'}</span><strong>{importPreview.overall_verdict}</strong></div><small>{importPreview.filename}</small></div>
            <div className="result-preview-kpis"><div><strong>{importPreview.node_count}</strong><span>노드 행</span></div><div><strong>{importPreview.element_count}</strong><span>요소 행</span></div><div><strong>{importPreview.frame_count}</strong><span>프레임</span></div><div><strong>{importPreview.scalar_count}</strong><span>파생 결과</span></div><div className={importPreview.fail_count ? 'fail' : ''}><strong>{importPreview.fail_count}</strong><span>FAIL 항목</span></div></div>
            <div className="result-preview-list">{importPreview.results.map((item) => <div key={item.variable_key}><span>{item.display_name}</span><strong>{item.value.toFixed(2)} {item.unit}</strong><b className={item.verdict.toLowerCase()}>{item.verdict}</b></div>)}</div>
            {importPreview.warnings.length > 0 && <div className="result-warnings">{importPreview.warnings.map((warning) => <span key={warning}><AlertTriangle />{warning}</span>)}</div>}
          </> : <div className="result-preview-empty"><Database /><strong>검증 결과가 여기에 표시됩니다.</strong><span>허용 변수 외 데이터와 중복 시점은 저장 전에 차단됩니다.</span></div>}
        </div>
      </div>
      <footer><div><strong>판정 규칙</strong><span>Open Cell 응력 및 Chassis Rear 영구변형 모두 값이 기준 이상이면 FAIL</span></div>{imported ? <button className="open-result-button" disabled={!contextReady} onClick={() => void onOpenAnalysis(projectId, activeRequestId, activeLoadCaseId)}><LayoutDashboard /> 분석 대시보드에서 확인</button> : <button className="data-submit import-button" onClick={() => void submitResultImport()} disabled={!importPreview || !resultFile || !contextReady || !storageBound || busy}><Upload /> 검증된 결과 등록</button>}</footer>
    </article>
    <details className="data-history-details"><summary>이전 결과 가져오기 이력</summary><ResultImportHistory loadCaseId={activeLoadCaseId} canRetryImports={canRetryImports} refreshToken={historyRefreshToken} /></details>
  </section>
}
