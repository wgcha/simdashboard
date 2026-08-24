import { useEffect, useState, type FormEvent } from 'react'
import { AlertTriangle, Check, ChevronDown, ClipboardPlus, Database, Download, LayoutDashboard, Play, Plus, Upload } from 'lucide-react'
import { api } from '../../api'
import type { AnalysisRequest, LoadCase, Project } from '../../types'

export function DataWorkspace({ canCreateProject, projects, initialProjectId, initialRequestId, onDataChanged, onOpenAnalysis, onOpenIntake }: { canCreateProject: boolean; projects: Project[]; initialProjectId: string; initialRequestId?: string; onDataChanged: () => Promise<void>; onOpenAnalysis: (projectId: string, requestId: string, loadCaseId: string) => Promise<void>; onOpenIntake: () => void }) {
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

  useEffect(() => {
    setManagedProjects(projects)
    if (!projectId && projects[0]) setProjectId(initialProjectId || projects[0].id)
  }, [projects, initialProjectId, projectId])

  useEffect(() => {
    if (!projectId) return
    api.requests(projectId).then((items) => {
      setRequests(items)
      setRequestId((current) => items.some((item) => item.id === initialRequestId) ? initialRequestId || '' : items.some((item) => item.id === current) ? current : items[0]?.id || '')
    }).catch((reason) => setFormError(reason instanceof Error ? reason.message : '의뢰 목록을 불러오지 못했습니다.'))
  }, [initialRequestId, projectId])

  useEffect(() => {
    if (!requestId) { setLoadCases([]); return }
    api.loadCases(requestId).then((items) => {
      setLoadCases(items)
      setLoadCaseId((current) => items.some((item) => item.id === current) ? current : items[0]?.id || '')
      setResultFile(null); setImportPreview(null); setImported(false)
    }).catch((reason) => setFormError(reason instanceof Error ? reason.message : '하중 경우 목록을 불러오지 못했습니다.'))
  }, [requestId])

  const complete = async (label: string, action: () => Promise<void>) => {
    setBusy(true); setFormError(''); setMessage('')
    try {
      await action()
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
      setProjectForm({ name: '', product_name: '', manufacturer: '', display_size_inch: 65, description: '' })
    })
  }

  const submitLoadCase = (event: FormEvent) => {
    event.preventDefault()
    if (!requestId) return
    void complete('하중 경우', async () => {
      const parameters: Record<string, string | number | string[]> = caseForm.analysis_type === 'DROP'
        ? { drop_height_mm: Number(caseForm.primary), impact_direction: caseForm.secondary }
        : { clamp_pressure_kpa: Number(caseForm.primary), hold_time_s: Number(caseForm.secondary) || 10 }
      const created = await api.createLoadCase(requestId, { name: caseForm.name, analysis_type: caseForm.analysis_type, parameters })
      setLoadCases((items) => [created, ...items])
      setLoadCaseId(created.id)
      setCaseForm({ name: '', analysis_type: caseForm.analysis_type, primary: caseForm.analysis_type === 'DROP' ? '800' : '25', secondary: caseForm.analysis_type === 'DROP' ? 'BOTTOM' : '10' })
    })
  }

  const selectedRequest = requests.find((item) => item.id === requestId)

  const validateResultFile = async (selected: { filename: string; content: string }) => {
    setResultFile(selected); setBusy(true)
    try {
      setImportPreview(await api.importResults(loadCaseId, { ...selected, author: resultAuthor, validate_only: true }))
    } catch (reason) { setFormError(reason instanceof Error ? reason.message : '결과 파일 검증에 실패했습니다.') }
    finally { setBusy(false) }
  }

  const chooseResultFile = async (file?: File) => {
    setFormError(''); setMessage(''); setImportPreview(null); setImported(false)
    if (!file) { setResultFile(null); return }
    if (!/\.(csv|json)$/i.test(file.name)) { setFormError('CSV 또는 JSON 파일만 선택할 수 있습니다.'); return }
    if (file.size > 4_500_000) { setFormError('파일은 4.5 MB 이하여야 합니다.'); return }
    const selected = { filename: file.name, content: await file.text() }
    await validateResultFile(selected)
  }

  const loadRadiossExample = async () => {
    setFormError(''); setMessage(''); setImportPreview(null); setImported(false); setBusy(true)
    try {
      await validateResultFile({ filename: 'radioss-tv-result-example.csv', content: await api.resultImportTemplate('radioss-csv') })
    } catch (reason) { setFormError(reason instanceof Error ? reason.message : 'Radioss 예제 검증에 실패했습니다.') }
    finally { setBusy(false) }
  }

  const importTypedFolderExample = async () => {
    if (!loadCaseId) return
    setBusy(true); setFormError(''); setMessage(''); setImported(false)
    try {
      const result = await api.importTypedFolderExample(loadCaseId)
      setImported(true)
      setMessage(`예제 폴더 스키마(${result.schema_id})를 적용해 실수·정수·텍스트 ${result.summary.scalar_count}개, 커브 ${result.summary.curve_count}개, 미디어 ${result.summary.media_count}개를 Run #${result.run_no}로 등록했습니다.`)
      await onDataChanged()
    } catch (reason) { setFormError(reason instanceof Error ? reason.message : '예제 폴더를 등록하지 못했습니다.') }
    finally { setBusy(false) }
  }

  const submitResultImport = async () => {
    if (!resultFile || !loadCaseId) return
    setBusy(true); setFormError(''); setMessage('')
    try {
      const result = await api.importResults(loadCaseId, { ...resultFile, author: resultAuthor, validate_only: false })
      setImportPreview(result); setImported(true)
      setMessage(result.status === 'SKIPPED' ? '동일한 결과 파일이 이미 등록되어 있습니다. 기존 결과를 표시합니다.' : `Run #${result.run_no} 결과를 등록했습니다. 결과 검토 단계가 시작되었습니다.`)
      await onDataChanged()
    } catch (reason) { setFormError(reason instanceof Error ? reason.message : '해석 결과 등록에 실패했습니다.') }
    finally { setBusy(false) }
  }

  return <section className="data-workspace">
    <header className="data-workspace-head">
      <div><span>OPERATIONS / FILE DATABASE</span><h1>해석 데이터 등록</h1><p>프로젝트와 하중 경우를 구성하고, 완료된 해석 결과를 검증해 DB에 등록합니다.</p></div>
      <div className="data-count"><strong>{managedProjects.length}</strong><span>PROJECTS</span></div>
    </header>

    <div className="data-hierarchy-bar">
      <label><span>1 · 프로젝트</span><select aria-label="등록 프로젝트 선택" value={projectId} onChange={(event) => setProjectId(event.target.value)}>{managedProjects.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.product_name}</option>)}</select></label>
      <i>›</i>
      <label><span>2 · 접수된 의뢰</span><select aria-label="등록 의뢰 선택" value={requestId} onChange={(event) => setRequestId(event.target.value)} disabled={!requests.length}>{requests.length ? requests.map((item) => <option key={item.id} value={item.id}>{item.title}</option>) : <option>의뢰 접수 탭에서 먼저 접수하세요</option>}</select></label>
      <i>›</i>
      <label><span>3 · 하중 경우</span><select aria-label="등록 하중 경우 선택" value={loadCaseId} onChange={(event) => { setLoadCaseId(event.target.value); setResultFile(null); setImportPreview(null); setImported(false) }} disabled={!loadCases.length}>{loadCases.length ? loadCases.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.analysis_type}</option>) : <option value="">하중 경우 없음</option>}</select></label>
    </div>

    {(message || formError) && <div className={`data-message ${formError ? 'error' : ''}`}>{formError ? <AlertTriangle /> : <Check />}{formError || message}</div>}
    <div className="data-intake-handoff"><ClipboardPlus /><span><strong>새 해석 의뢰는 별도 접수 절차를 사용합니다.</strong>외부 시스템 전달·부서장 지시와 작업 시나리오를 기록한 뒤 이 화면에서 하중 경우와 결과를 연결하세요.</span><button onClick={onOpenIntake}>의뢰 접수 열기 <ChevronDown /></button></div>

    <div className={`data-form-grid ${canCreateProject ? '' : 'single'}`}>
      {canCreateProject && <article className="data-form-card"><header><span>01</span><div><h2>새 프로젝트</h2><p>제품 단위 최상위 분류</p></div></header><form onSubmit={submitProject}>
        <label><span>프로젝트 이름</span><input required value={projectForm.name} onChange={(e) => setProjectForm({ ...projectForm, name: e.target.value })} placeholder="예: 2026 OLED 신뢰성" /></label>
        <label><span>제품 모델명</span><input required value={projectForm.product_name} onChange={(e) => setProjectForm({ ...projectForm, product_name: e.target.value })} placeholder="예: OLED77X26" /></label>
        <label><span>제조사</span><input value={projectForm.manufacturer} onChange={(e) => setProjectForm({ ...projectForm, manufacturer: e.target.value })} placeholder="예: NeoView Display" /></label>
        <label><span>화면 크기 (inch)</span><input type="number" min="1" max="200" value={projectForm.display_size_inch ?? ''} onChange={(e) => setProjectForm({ ...projectForm, display_size_inch: e.target.value ? Number(e.target.value) : null })} /></label>
        <label><span>설명</span><textarea value={projectForm.description} onChange={(e) => setProjectForm({ ...projectForm, description: e.target.value })} placeholder="제품과 해석 목적을 입력하세요." /></label>
        <button className="data-submit" disabled={busy}><Plus /> 프로젝트 등록</button>
      </form></article>}

      <article className="data-form-card"><header><span>02</span><div><h2>새 하중 경우</h2><p>{selectedRequest?.title || '접수된 의뢰를 선택하세요'}</p></div></header><form onSubmit={submitLoadCase}>
        <label><span>하중 경우 이름</span><input required disabled={!requestId} value={caseForm.name} onChange={(e) => setCaseForm({ ...caseForm, name: e.target.value })} placeholder="예: Bottom Drop 800 mm" /></label>
        <label><span>해석 유형</span><select value={caseForm.analysis_type} onChange={(e) => { const type = e.target.value as 'DROP' | 'SIDE_CLAMP'; setCaseForm({ name: caseForm.name, analysis_type: type, primary: type === 'DROP' ? '800' : '25', secondary: type === 'DROP' ? 'BOTTOM' : '10' }) }}><option value="DROP">포장 낙하 (DROP)</option><option value="SIDE_CLAMP">Side Clamp</option></select></label>
        <div className="data-form-row"><label><span>{caseForm.analysis_type === 'DROP' ? '낙하 높이 (mm)' : '압력 (kPa)'}</span><input type="number" min="0" required value={caseForm.primary} onChange={(e) => setCaseForm({ ...caseForm, primary: e.target.value })} /></label><label><span>{caseForm.analysis_type === 'DROP' ? '충격 방향' : '유지 시간 (s)'}</span>{caseForm.analysis_type === 'DROP' ? <select value={caseForm.secondary} onChange={(e) => setCaseForm({ ...caseForm, secondary: e.target.value })}><option>BOTTOM</option><option>TOP</option><option>LEFT</option><option>RIGHT</option></select> : <input type="number" min="0" value={caseForm.secondary} onChange={(e) => setCaseForm({ ...caseForm, secondary: e.target.value })} />}</label></div>
        <button className="data-submit" disabled={busy || !requestId}><Plus /> 하중 경우 등록</button>
      </form></article>
    </div>

    <article className="result-import-card">
      <header>
        <div><span>03 · RESULT INGESTION</span><h2>해석 결과 가져오기</h2><p>선택한 하중 경우에 CSV 또는 JSON 결과를 검증한 뒤 새 Analysis Run으로 저장합니다.</p></div>
        <div className="template-links"><button onClick={() => void loadRadiossExample()} disabled={!loadCaseId || busy}><Play /> 예제로 검증</button><button onClick={() => void importTypedFolderExample()} disabled={!loadCaseId || busy}><Database /> 형식별 폴더 예제 등록</button><a href={api.resultImportTemplateUrl('radioss-csv')} download><Download /> Radioss CSV</a><a href={api.resultImportTemplateUrl('csv')} download><Download /> 요약 CSV</a><a href={api.resultImportTemplateUrl('json')} download><Download /> JSON</a></div>
      </header>
      <div className="result-import-body">
        <div className="result-drop-zone">
          <input id="result-file" type="file" accept=".csv,.json,text/csv,application/json" onChange={(event) => void chooseResultFile(event.target.files?.[0])} disabled={!loadCaseId || busy} />
          <label htmlFor="result-file"><Upload /><strong>{resultFile?.filename || '결과 파일 선택'}</strong><span>CSV / JSON · 최대 4.5 MB · 선택 즉시 사전 검증</span></label>
          <div className="result-import-meta"><label><span>수행자</span><input value={resultAuthor} onChange={(event) => setResultAuthor(event.target.value)} /></label><div><span>등록 대상</span><strong>{loadCases.find((item) => item.id === loadCaseId)?.name || '하중 경우를 선택하세요'}</strong></div></div>
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
      <footer><div><strong>판정 규칙</strong><span>Open Cell 응력 및 Chassis Rear 영구변형 모두 값이 기준 이상이면 FAIL</span></div>{imported ? <button className="open-result-button" onClick={() => void onOpenAnalysis(projectId, requestId, loadCaseId)}><LayoutDashboard /> 분석 대시보드에서 확인</button> : <button className="data-submit import-button" onClick={() => void submitResultImport()} disabled={!importPreview || !resultFile || busy}><Upload /> 검증된 결과 등록</button>}</footer>
    </article>
  </section>
}
