import { useEffect, useState, type FormEvent } from 'react'
import { AlertTriangle, Building2, Check, ChevronRight, ClipboardPlus, LoaderCircle, Network, UserRound } from 'lucide-react'
import { api, type AssigneeCandidate } from '../../api'
import type { AnalysisRequest, Project } from '../../types'
import { workbenchApi } from './api'
import { ExpectedResultsPreview } from './ExpectedResultsPreview'
import { requestTypeLabels, type ResultProfile, type WorkbenchRequestType } from './types'
import { useMemoryQuery } from '../../shared/cache/useMemoryQuery'

type IntakeSource = 'EXTERNAL_SYSTEM' | 'DEPARTMENT_HEAD'
type RequestTypeSelection = Pick<WorkbenchRequestType, 'id' | 'version'>

const loadActiveRequestTypes = async () => (await workbenchApi.requestTypes()).filter((item) => item.is_active)
const EMPTY_REQUEST_TYPES: WorkbenchRequestType[] = []

export function RequestIntakePage({ projects, initialProjectId, createdBy, canCreate, onCreated, onOpenWorkbench }: {
  projects: Project[]
  initialProjectId?: string
  createdBy: string
  canCreate: boolean
  onCreated: (projectId: string, request: AnalysisRequest) => Promise<void>
  onOpenWorkbench: (requestId: string) => void
}) {
  const [projectId, setProjectId] = useState(initialProjectId || projects[0]?.id || '')
  const [sourceType, setSourceType] = useState<IntakeSource>('EXTERNAL_SYSTEM')
  const [sourceReference, setSourceReference] = useState('')
  const [requestedBy, setRequestedBy] = useState('')
  const [title, setTitle] = useState('')
  const [ownerUserId, setOwnerUserId] = useState('')
  const [assignees, setAssignees] = useState<AssigneeCandidate[]>([])
  const [assigneesLoading, setAssigneesLoading] = useState(false)
  const [dueInDays, setDueInDays] = useState(14)
  const [note, setNote] = useState('')
  const [requestTypeSelection, setRequestTypeSelection] = useState<RequestTypeSelection | null>(null)
  const [selectedLabel, setSelectedLabel] = useState<string | null>(null)
  const [resultProfile, setResultProfile] = useState<ResultProfile | null>(null)
  const [resultProfileLoading, setResultProfileLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [created, setCreated] = useState<AnalysisRequest | null>(null)

  const requestTypesQuery = useMemoryQuery({ key: 'workbench:request-types:active', query: loadActiveRequestTypes })
  const requestTypes = requestTypesQuery.data || EMPTY_REQUEST_TYPES
  const loading = requestTypesQuery.isLoading

  useEffect(() => {
    setRequestTypeSelection((current) => {
      if (current && requestTypes.some((item) => item.id === current.id && item.version === current.version)) return current
      const first = requestTypes[0]
      return first ? { id: first.id, version: first.version } : null
    })
    if (requestTypesQuery.error) setError(requestTypesQuery.error.message || '접수 가능한 업무 유형을 불러오지 못했습니다.')
  }, [requestTypes, requestTypesQuery.error])

  useEffect(() => {
    if (initialProjectId && projects.some((item) => item.id === initialProjectId) && initialProjectId !== projectId) {
      setProjectId(initialProjectId)
      return
    }
    if (!projects.some((item) => item.id === projectId)) setProjectId(projects[0]?.id ?? '')
  }, [initialProjectId, projectId, projects])

  useEffect(() => {
    let cancelled = false
    if (!projectId || !canCreate) {
      setAssignees([])
      setOwnerUserId('')
      return () => { cancelled = true }
    }
    setAssigneesLoading(true)
    api.assigneeCandidates(projectId)
      .then((items) => {
        if (cancelled) return
        setAssignees(items)
        setOwnerUserId((current) => items.some((item) => item.user_id === current)
          ? current
          : items.find((item) => item.display_name === createdBy)?.user_id ?? items[0]?.user_id ?? '')
      })
      .catch((reason) => {
        if (cancelled) return
        setAssignees([])
        setOwnerUserId('')
        setError(reason instanceof Error ? reason.message : '담당자 후보를 불러오지 못했습니다.')
      })
      .finally(() => { if (!cancelled) setAssigneesLoading(false) })
    return () => { cancelled = true }
  }, [canCreate, createdBy, projectId])

  const selectedType = requestTypeSelection
    ? requestTypes.find((item) => item.id === requestTypeSelection.id && item.version === requestTypeSelection.version)
    : undefined

  useEffect(() => {
    let cancelled = false
    if (!selectedType) {
      setResultProfile(null)
      setResultProfileLoading(false)
      return () => { cancelled = true }
    }
    setResultProfileLoading(true)
    workbenchApi.resultProfile(selectedType.id, selectedType.version, projectId)
      .then((profile) => { if (!cancelled) setResultProfile(profile) })
      .catch(() => { if (!cancelled) setResultProfile(null) })
      .finally(() => { if (!cancelled) setResultProfileLoading(false) })
    return () => { cancelled = true }
  }, [projectId, selectedType?.id, selectedType?.version])

  const declaredLabels = Array.from(new Set(requestTypes.flatMap(requestTypeLabels))).sort((left, right) => left.localeCompare(right, 'ko'))
  const filteredRequestTypes = selectedLabel
    ? requestTypes.filter((item) => requestTypeLabels(item).includes(selectedLabel))
    : requestTypes
  const selectedProject = projects.find((item) => item.id === projectId)
  const filterByLabel = (label: string | null) => {
    setSelectedLabel(label)
    const visibleTypes = label ? requestTypes.filter((item) => requestTypeLabels(item).includes(label)) : requestTypes
    if (!requestTypeSelection || !visibleTypes.some((item) => item.id === requestTypeSelection.id && item.version === requestTypeSelection.version)) {
      const first = visibleTypes[0]
      setRequestTypeSelection(first ? { id: first.id, version: first.version } : null)
    }
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!canCreate || !projectId || !selectedType) return
    setSaving(true); setError(''); setCreated(null)
    try {
      const request = await api.createRequest(projectId, {
        title: title.trim(), owner_user_id: ownerUserId, due_in_days: dueInDays, overall_note: note.trim(),
        source_type: sourceType, source_reference: sourceReference.trim(), requested_by: requestedBy.trim(),
        request_type_id: selectedType.id, request_type_version: selectedType.version,
      })
      await onCreated(projectId, request)
      setCreated(request)
      setTitle(''); setNote('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '의뢰 접수에 실패했습니다.')
    } finally { setSaving(false) }
  }

  if (loading) return <div className="intake-state"><LoaderCircle className="spin" /> 접수 가능한 업무 유형을 확인하고 있습니다.</div>

  return <section className="request-intake-page" data-testid="request-intake-page">
    <header className="request-intake-hero">
      <div><span><ClipboardPlus /> REQUEST INTAKE</span><h1>해석 의뢰 접수</h1><p>외부 시스템 전달 또는 부서장 지시를 수행자에게 제공된 작업 유형으로 접수합니다.</p></div>
      <aside><strong data-testid="active-request-type-count">{requestTypes.length}</strong><span>접수 가능한 활성 작업 유형</span><small>선택한 유형과 버전은 접수 시 작업계획으로 고정됩니다.</small></aside>
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
        <div className="intake-form-row"><label><span>담당 수행자</span><select aria-label="담당 수행자" required value={ownerUserId} onChange={(event) => setOwnerUserId(event.target.value)} disabled={assigneesLoading || !assignees.length}><option value="">{assigneesLoading ? '담당자 불러오는 중' : '담당자를 선택하세요'}</option>{assignees.map((item) => <option key={item.user_id} value={item.user_id}>{item.display_name}{item.department ? ` · ${item.department}` : ''}{item.employee_id ? ` · ${item.employee_id}` : ''}</option>)}</select><small>현재 프로젝트의 활성 임직원만 선택할 수 있습니다.</small></label><label><span>완료 기한</span><input type="number" min="1" max="365" required value={dueInDays} onChange={(event) => setDueInDays(Number(event.target.value))} /><small>접수일 기준 일수</small></label></div>
        <label><span>요청 사항</span><textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="검증 조건, 설계 범위, 확인할 결과를 입력하세요." /></label>
      </section>

      <section className="intake-scenario-card">
        <header><span>02 · WORK PLAN</span><h2>작업 유형 선택</h2><p>선언된 라벨로 필터한 뒤 수행자에게 제공된 활성 작업 유형을 선택합니다.</p></header>
        {requestTypes.length ? <>
          <div className="intake-label-filter" role="group" aria-label="작업 유형 라벨 필터"><span>선언된 라벨</span><div><button type="button" className={selectedLabel === null ? 'selected' : ''} aria-pressed={selectedLabel === null} onClick={() => filterByLabel(null)}>전체 <b>{requestTypes.length}</b></button>{declaredLabels.map((label) => { const count = requestTypes.filter((item) => requestTypeLabels(item).includes(label)).length; return <button type="button" key={label} className={selectedLabel === label ? 'selected' : ''} aria-pressed={selectedLabel === label} data-testid={`request-type-label-filter-${label}`} onClick={() => filterByLabel(label)}>#{label} <b>{count}</b></button> })}</div></div>
          {filteredRequestTypes.length ? <div className="intake-scenario-options">{filteredRequestTypes.map((item) => {
            const selected = item.id === requestTypeSelection?.id && item.version === requestTypeSelection.version
            return <button
              type="button"
              key={`${item.id}-${item.version}`}
              className={selected ? 'selected' : ''}
              onClick={() => setRequestTypeSelection({ id: item.id, version: item.version })}
              aria-pressed={selected}
              data-testid={`request-type-option-${item.id}-${item.version}`}
            ><span>WORK TYPE · v{item.version}</span><strong>{item.display_name}</strong><div className="request-type-option-labels">{requestTypeLabels(item).map((label) => <em key={label}>#{label}</em>)}</div><small>{item.default_workflow.nodes.length}개 세부 작업</small></button>
          })}</div> : <div className="intake-filter-empty" role="status">#{selectedLabel} 라벨에 해당하는 활성 작업 유형이 없습니다.</div>}
          <ExpectedResultsPreview profile={resultProfile} loading={resultProfileLoading} />
          <ol className="intake-work-preview">{selectedType?.default_workflow.nodes.map((node, index) => {
            const nextNode = selectedType.default_workflow.nodes[index + 1]
            const leadsToNext = nextNode?.depends_on.includes(node.node_key) ?? false
            return <li key={node.node_key}><i>{index + 1}</i><span><strong>{node.display_name ?? node.task_type_id}</strong><small>{node.task_type_id} · v{node.task_type_version}{node.depends_on.length ? ` · 선행 ${node.depends_on.join(', ')}` : ' · 선행 없음'}</small></span>{leadsToNext && <ChevronRight />}</li>
          })}</ol>
        </> : <div className="intake-summary" data-testid="empty-request-types"><strong>접수 가능한 업무 유형이 없습니다.</strong><p>작업 유형 관리에서 활성 업무 유형을 먼저 제공해 주세요.</p></div>}
        <div className="intake-summary"><span>접수 대상</span><strong>{selectedProject?.name ?? '프로젝트 미선택'}</strong><p>{selectedType ? `${selectedType.display_name} · v${selectedType.version} · 첫 작업 READY` : '작업 유형 미선택'}</p></div>
        <button className="intake-submit" disabled={!canCreate || saving || !projectId || !selectedType || !ownerUserId} data-testid="submit-request-intake">{saving ? <LoaderCircle className="spin" /> : <ClipboardPlus />} {canCreate ? '해석 의뢰 접수' : '편집 권한이 필요합니다'}</button>
      </section>
    </form>
  </section>
}
