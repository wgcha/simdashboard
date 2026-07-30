import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { AlertTriangle, Building2, Check, ChevronRight, ClipboardPlus, LoaderCircle, Network, UserRound } from 'lucide-react'
import { api } from '../../api'
import type { AnalysisRequest, Project } from '../../types'
import { workbenchApi } from './api'
import type { WorkbenchRequestType } from './types'

type IntakeSource = 'EXTERNAL_SYSTEM' | 'DEPARTMENT_HEAD'

export function RequestIntakePage({ projects, createdBy, canCreate, onCreated, onOpenWorkbench }: {
  projects: Project[]
  createdBy: string
  canCreate: boolean
  onCreated: (projectId: string, request: AnalysisRequest) => Promise<void>
  onOpenWorkbench: (requestId: string) => void
}) {
  const [requestTypes, setRequestTypes] = useState<WorkbenchRequestType[]>([])
  const [projectId, setProjectId] = useState(projects[0]?.id ?? '')
  const [sourceType, setSourceType] = useState<IntakeSource>('EXTERNAL_SYSTEM')
  const [sourceReference, setSourceReference] = useState('')
  const [requestedBy, setRequestedBy] = useState('')
  const [title, setTitle] = useState('')
  const [owner, setOwner] = useState('')
  const [dueInDays, setDueInDays] = useState(14)
  const [note, setNote] = useState('')
  const [requestTypeId, setRequestTypeId] = useState<'design-reliability-validation' | 'design-doe-exploration'>('design-reliability-validation')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [created, setCreated] = useState<AnalysisRequest | null>(null)

  useEffect(() => {
    workbenchApi.requestTypes()
      .then((items) => setRequestTypes(items.filter((item) => item.id === 'design-reliability-validation' || item.id === 'design-doe-exploration')))
      .catch((reason) => setError(reason instanceof Error ? reason.message : '의뢰 시나리오를 불러오지 못했습니다.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!projects.some((item) => item.id === projectId)) setProjectId(projects[0]?.id ?? '')
  }, [projectId, projects])

  const selectedType = useMemo(() => requestTypes.find((item) => item.id === requestTypeId), [requestTypeId, requestTypes])
  const selectedProject = projects.find((item) => item.id === projectId)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!canCreate || !projectId || !selectedType) return
    setSaving(true); setError(''); setCreated(null)
    try {
      const request = await api.createRequest(projectId, {
        title: title.trim(), owner: owner.trim(), due_in_days: dueInDays, overall_note: note.trim(),
        source_type: sourceType, source_reference: sourceReference.trim(), requested_by: requestedBy.trim(),
        request_type_id: requestTypeId, request_type_version: selectedType.version, assigned_by: createdBy,
      })
      await onCreated(projectId, request)
      setCreated(request)
      setTitle(''); setNote('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '의뢰 접수에 실패했습니다.')
    } finally { setSaving(false) }
  }

  if (loading) return <div className="intake-state"><LoaderCircle className="spin" /> 접수 가능한 작업 시나리오를 확인하고 있습니다.</div>

  return <section className="request-intake-page" data-testid="request-intake-page">
    <header className="request-intake-hero">
      <div><span><ClipboardPlus /> REQUEST INTAKE</span><h1>해석 의뢰 접수</h1><p>외부 시스템 전달 또는 부서장 지시를 수행 가능한 고정 작업 시나리오로 접수합니다.</p></div>
      <aside><strong>2</strong><span>승인된 접수 시나리오</span><small>접수 후 첫 작업은 시작 대기 상태로 배정됩니다.</small></aside>
    </header>

    {error && <div className="intake-message error" role="alert"><AlertTriangle /><span><strong>접수 요청을 처리하지 못했습니다.</strong>{error}</span></div>}
    {created && <div className="intake-message success" data-testid="intake-success"><Check /><span><strong>{created.title} 접수 완료</strong>{created.scenario_name} · 상태 READY · 진행률 0%</span><button onClick={() => onOpenWorkbench(created.id)}>배정 작업 열기 <ChevronRight /></button></div>}

    <form className="request-intake-layout" onSubmit={submit}>
      <section className="intake-form-card">
        <header><span>01 · REQUEST SOURCE</span><h2>의뢰 기본 정보</h2><p>누가 어떤 경로로 요청했는지 접수 시점의 정보로 고정합니다.</p></header>
        <label><span>대상 프로젝트</span><select aria-label="의뢰 프로젝트" required value={projectId} onChange={(event) => setProjectId(event.target.value)}>{projects.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.product_name}</option>)}</select></label>
        <fieldset className="intake-source-options"><legend>접수 출처</legend><label className={sourceType === 'EXTERNAL_SYSTEM' ? 'selected' : ''}><input type="radio" name="source" checked={sourceType === 'EXTERNAL_SYSTEM'} onChange={() => { setSourceType('EXTERNAL_SYSTEM'); setSourceReference('') }} /><Network /><span><strong>외부 시스템 전달</strong><small>PLM·PDM·업무 시스템에서 전달</small></span></label><label className={sourceType === 'DEPARTMENT_HEAD' ? 'selected' : ''}><input type="radio" name="source" checked={sourceType === 'DEPARTMENT_HEAD'} onChange={() => { setSourceType('DEPARTMENT_HEAD'); setSourceReference('') }} /><Building2 /><span><strong>부서장 지시</strong><small>조직 책임자의 직접 수행 지시</small></span></label></fieldset>
        <label><span>{sourceType === 'EXTERNAL_SYSTEM' ? '전달 시스템명' : '지시 부서장 또는 부서'}</span><input aria-label="의뢰 출처 상세" required minLength={2} value={sourceReference} onChange={(event) => setSourceReference(event.target.value)} placeholder={sourceType === 'EXTERNAL_SYSTEM' ? '예: PLM Gateway' : '예: 구조해석팀장'} /></label>
        <label><span>요청자</span><div className="intake-icon-input"><UserRound /><input required minLength={2} value={requestedBy} onChange={(event) => setRequestedBy(event.target.value)} placeholder="요청자 이름 또는 시스템 계정" /></div></label>
        <label><span>의뢰 제목</span><input required minLength={2} value={title} onChange={(event) => setTitle(event.target.value)} placeholder="예: 신규 Bracket 설계 신뢰성 검증" /></label>
        <div className="intake-form-row"><label><span>담당 수행자</span><input required minLength={2} value={owner} onChange={(event) => setOwner(event.target.value)} placeholder="수행자 이름" /></label><label><span>완료 기한</span><input type="number" min="1" max="365" required value={dueInDays} onChange={(event) => setDueInDays(Number(event.target.value))} /><small>접수일 기준 일수</small></label></div>
        <label><span>요청 사항</span><textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="검증 조건, 설계 범위, 확인할 결과를 입력하세요." /></label>
      </section>

      <section className="intake-scenario-card">
        <header><span>02 · WORK PLAN</span><h2>작업 시나리오 선택</h2><p>접수 후 변경되지 않는 작업 순서입니다.</p></header>
        <div className="intake-scenario-options">{requestTypes.map((item) => <button type="button" key={item.id} className={item.id === requestTypeId ? 'selected' : ''} onClick={() => setRequestTypeId(item.id as typeof requestTypeId)} aria-pressed={item.id === requestTypeId}><span>{item.id === 'design-reliability-validation' ? 'RELIABILITY' : 'DOE EXPLORATION'}</span><strong>{item.display_name}</strong><small>{item.default_workflow.nodes.length}개 순차 작업</small></button>)}</div>
        <ol className="intake-work-preview">{selectedType?.default_workflow.nodes.map((node, index) => <li key={node.node_key}><i>{index + 1}</i><span><strong>{node.display_name ?? node.task_type_id}</strong><small>{node.task_type_id} · v{node.task_type_version}</small></span>{index < selectedType.default_workflow.nodes.length - 1 && <ChevronRight />}</li>)}</ol>
        <div className="intake-summary"><span>접수 대상</span><strong>{selectedProject?.name ?? '프로젝트 미선택'}</strong><p>{selectedType?.display_name ?? '시나리오 미선택'} · 첫 작업 READY</p></div>
        <button className="intake-submit" disabled={!canCreate || saving || !projectId || !selectedType} data-testid="submit-request-intake">{saving ? <LoaderCircle className="spin" /> : <ClipboardPlus />} {canCreate ? '해석 의뢰 접수' : '편집 권한이 필요합니다'}</button>
      </section>
    </form>
  </section>
}
