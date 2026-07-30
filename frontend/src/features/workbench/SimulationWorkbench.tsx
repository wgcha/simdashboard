import { useEffect, useMemo, useState } from 'react'
import { Activity, AlertTriangle, Check, ClipboardList, FileText, FlaskConical, Image, LoaderCircle, Play, Plus, RefreshCw, Save, Settings2, ShieldCheck } from 'lucide-react'
import type { Workflow } from '../../types'
import { workbenchApi } from './api'
import type { DemoRun, DemoRunTask, WorkbenchNode, WorkbenchRequestType, WorkbenchTaskType } from './types'

type CompositionMode = 'parallel' | 'sequence'

type TaskGuidance = {
  group: '준비·설계' | '해석 실행' | '결과 활용' | 'PhysicsAI'
  purpose: string
  requires: string
  result: string
}

const TASK_GUIDANCE: Record<string, TaskGuidance> = {
  CAD_PREPARE: { group: '준비·설계', purpose: 'CAD 또는 기준 형상을 확인하고 해석에 쓸 형상으로 준비합니다.', requires: 'CAD 파일 또는 등록 형상', result: '준비된 형상과 형상 검증 리포트' },
  DOE_GENERATE: { group: '준비·설계', purpose: '설계변수와 범위를 바탕으로 비교할 설계점을 만듭니다.', requires: '설계변수·범위·DOE 방법', result: '설계점 목록과 DOE manifest' },
  ANALYSIS_MODELING: { group: '준비·설계', purpose: '형상에 재료·경계조건·하중을 적용해 해석 모델을 준비합니다.', requires: '준비 형상과 모델링 조건', result: '검증된 Solver Deck' },
  HPC_SUBMIT: { group: '해석 실행', purpose: '준비된 해석 모델을 계산 자원에 제출하는 과정을 시연합니다.', requires: 'Solver Deck과 실행 프로파일', result: '작업 접수 정보와 제출 기록' },
  EXECUTION_MONITOR: { group: '해석 실행', purpose: '실행 상태와 로그를 읽어 진행·완료·실패를 감시합니다.', requires: '작업 접수 정보 또는 로그', result: '진행 상태와 실행 로그' },
  RESULT_COLLECT: { group: '결과 활용', purpose: '완료된 작업에서 필요한 결과 파일을 찾아 수집합니다.', requires: '결과 위치와 파일 규칙', result: '결과 파일 목록과 수집 검증' },
  POST_PROCESS: { group: '결과 활용', purpose: '원시 결과에서 KPI·판정·그래프용 값을 계산합니다.', requires: '수집된 해석 결과', result: 'KPI, 판정, 결과 요약' },
  ANALYSIS_DB_PUBLISH: { group: '결과 활용', purpose: '검증된 결과를 해석 DB에 발행하는 과정을 시연합니다.', requires: '검증된 후처리 결과', result: '해석 Run 참조와 발행 리포트' },
  TRAINING_DATASET_PUBLISH: { group: 'PhysicsAI', purpose: '승인된 해석 결과를 학습용 데이터셋 버전으로 구성합니다.', requires: '승인 결과와 데이터 기준', result: '학습 데이터셋 manifest' },
  PHYSICSAI_TRAIN_VALIDATE: { group: 'PhysicsAI', purpose: '데이터셋으로 PhysicsAI 학습과 성능 검증 과정을 시연합니다.', requires: '학습 데이터셋과 학습 설정', result: '모델 후보와 검증 지표' },
  MODEL_APPROVAL: { group: 'PhysicsAI', purpose: '검증 지표를 기준으로 사용할 모델 후보를 승인합니다.', requires: '모델 후보와 승인 기준', result: '승인 결정과 모델 버전' },
  REALTIME_PREDICT: { group: 'PhysicsAI', purpose: '승인 모델과 새 형상으로 실시간 성능 예측을 시연합니다.', requires: '승인 모델과 입력 형상', result: '예측 KPI와 신뢰도' },
  DASHBOARD_VISUALIZE: { group: '결과 활용', purpose: '해석 DB 또는 예측 결과를 대시보드에 연결합니다.', requires: '해석 Run 또는 예측 결과', result: '대시보드 연결 정보' },
}

function runStatusLabel(status: string) {
  return ({ SUCCEEDED: '완료', RUNNING: '진행 중', PENDING: '대기', READY: '실행 준비', FAILED: '실패', SKIPPED: '건너뜀', CANCELLED: '취소' } as Record<string, string>)[status] ?? status
}

function taskNodeKey(task: WorkbenchTaskType, index: number) {
  return `${task.kind.toLowerCase().replace(/[^a-z0-9]+/g, '-') || 'task'}-${index + 1}`
}

function nodesFor(tasks: WorkbenchTaskType[], mode: CompositionMode): WorkbenchNode[] {
  const keys = tasks.map(taskNodeKey)
  return tasks.map((task, index) => ({
    node_key: keys[index],
    task_type_id: task.id,
    task_type_version: task.version,
    depends_on: mode === 'sequence' && index > 0 ? [keys[index - 1]] : [],
  }))
}

function friendlyWorkbenchError(reason: unknown) {
  const message = reason instanceof Error ? reason.message : String(reason)
  if (/not found/i.test(message)) return '작업 실행 API가 현재 백엔드에 로드되지 않았습니다. 최신 백엔드로 다시 시작한 뒤 이 화면을 새로고침하세요.'
  return message || '작업 실행 정보를 불러오지 못했습니다.'
}

export function SimulationWorkbench({ workflows, initialRequestId, createdBy, canExecute, onChanged, onRequestSelected }: {
  workflows: Workflow[]
  initialRequestId: string
  createdBy: string
  canExecute: boolean
  onChanged: (message: string) => void | Promise<void>
  onRequestSelected: (requestId: string) => void
}) {
  const assigned = useMemo(() => workflows.filter((workflow) => workflow.work_plan), [workflows])
  const [runs, setRuns] = useState<DemoRun[]>([])
  const [activeRun, setActiveRun] = useState<DemoRun | null>(null)
  const [activeTaskId, setActiveTaskId] = useState('')
  const [loadingRuns, setLoadingRuns] = useState(false)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')

  const requestId = assigned.some((item) => item.request.id === initialRequestId) ? initialRequestId : assigned[0]?.request.id ?? ''
  const workflow = assigned.find((item) => item.request.id === requestId)
  const currentItem = workflow?.steps.find((item) => item.status === 'IN_PROGRESS') ?? workflow?.steps.find((item) => item.status === 'READY') ?? null
  const activeTask = activeRun?.tasks.find((task) => task.id === activeTaskId) ?? activeRun?.tasks[0] ?? null

  const loadRuns = async (targetRequestId = requestId) => {
    if (!targetRequestId) return
    setLoadingRuns(true)
    try {
      const items = await workbenchApi.demoRuns(targetRequestId)
      setRuns(items)
      setActiveRun((current) => current && items.some((item) => item.id === current.id) ? current : items[0] ?? null)
      setActiveTaskId((current) => current || items[0]?.tasks[0]?.id || '')
    } catch (reason) { setError(friendlyWorkbenchError(reason)) }
    finally { setLoadingRuns(false) }
  }

  useEffect(() => { void loadRuns(requestId) }, [requestId])

  const changeRequest = (nextRequestId: string) => {
    onRequestSelected(nextRequestId); setError(''); setActiveRun(null); setActiveTaskId('')
  }

  const startCurrent = async () => {
    if (!currentItem || currentItem.status !== 'READY') return
    setWorking(true); setError('')
    try {
      await workbenchApi.startWorkItem(currentItem.id, createdBy)
      await onChanged(currentItem.sequence_no === 1 ? '의뢰를 수령하고 첫 작업을 시작했습니다.' : `${currentItem.name} 작업을 시작했습니다.`)
    } catch (reason) { setError(friendlyWorkbenchError(reason)) }
    finally { setWorking(false) }
  }

  const completeCurrent = async () => {
    if (!currentItem || currentItem.status !== 'IN_PROGRESS' || !currentItem.node_key || !currentItem.task_type_id || !currentItem.task_type_version) return
    setWorking(true); setError('')
    try {
      const run = await workbenchApi.createDemoRun({
        name: `${currentItem.name} 데모 수행`, request_id: requestId, execution_mode: 'DEMO_ONLY', created_by: createdBy,
        nodes: [{ node_key: currentItem.node_key, task_type_id: currentItem.task_type_id, task_type_version: currentItem.task_type_version, depends_on: [] }],
      })
      await workbenchApi.completeWorkItem(currentItem.id, createdBy, run.id)
      setRuns((items) => [run, ...items.filter((item) => item.id !== run.id)])
      setActiveRun(run); setActiveTaskId(run.tasks[0]?.id ?? '')
      await onChanged(`${currentItem.name} 작업을 완료했습니다. 다음 작업은 시작 대기 상태입니다.`)
    } catch (reason) { setError(friendlyWorkbenchError(reason)) }
    finally { setWorking(false) }
  }

  if (!workflow) return <div className="workbench-state"><ClipboardList /> 접수되어 배정된 작업 계획이 없습니다.</div>

  const firstReady = currentItem?.status === 'READY' && currentItem.sequence_no === 1 && workflow.completed_count === 0
  return <div className="workbench-page assigned-only" data-testid="simulation-workbench">
    <section className="workbench-hero operator"><div><span className="demo-only-badge"><ShieldCheck /> DEMO ONLY</span><h1>배정 작업 실행</h1><p>접수 시 고정된 작업 계획을 순서대로 시작하고 완료합니다. 실행 중인 현재 작업 외에는 수행할 수 없습니다.</p></div><div className="workbench-source"><span>작업 원칙</span><strong>시작 → 데모 수행 → 완료</strong><small>완료 후 다음 작업은 자동 실행되지 않고 READY로 열립니다.</small></div></section>
    {error && <section className="workbench-service-error" role="alert"><AlertTriangle /><div><strong>작업 요청을 처리하지 못했습니다.</strong><p>{error}</p></div></section>}

    <section className="assigned-request-card">
      <header><div><span>ASSIGNED WORK PLAN</span><h2>{workflow.request.title}</h2><p>{workflow.request.project_name} · 담당 {workflow.request.owner}</p></div><label><span>대상 의뢰</span><select aria-label="배정 작업 대상 의뢰" value={workflow.request.id} onChange={(event) => changeRequest(event.target.value)}>{assigned.map((item) => <option key={item.request.id} value={item.request.id}>{item.request.project_name} / {item.request.title}</option>)}</select></label></header>
      <div className="assigned-request-meta"><div><span>시나리오</span><strong>{workflow.work_plan?.scenario_name}</strong></div><div><span>접수 출처</span><strong>{workflow.work_plan?.source_type === 'EXTERNAL_SYSTEM' ? '외부 시스템' : '부서장 지시'}</strong><small>{workflow.work_plan?.source_reference}</small></div><div><span>요청자</span><strong>{workflow.work_plan?.requested_by}</strong></div><div><span>완료 작업</span><strong>{workflow.completed_count ?? 0} / {workflow.total_count ?? workflow.steps.length}</strong></div><div className="assigned-progress"><span>전체 진행률</span><strong>{workflow.progress}%</strong><i><b style={{ width: `${workflow.progress}%` }} /></i></div></div>
    </section>

    <section className="assigned-work-list"><header><div><span>SEQUENTIAL WORK ITEMS</span><h2>배정 작업 순서</h2></div><b>{currentItem ? `현재 · ${currentItem.name}` : '모든 작업 완료'}</b></header><div>{workflow.steps.map((item) => {
      const isCurrent = item.id === currentItem?.id
      const statusLabel = { READY: '시작 대기', IN_PROGRESS: '진행 중', COMPLETED: '완료', WAITING: '선행 작업 대기', BLOCKED: '차단', FAILED: '실패' }[item.status]
      return <article key={item.id} className={`${item.status.toLowerCase()} ${isCurrent ? 'current' : ''}`}><i>{item.status === 'COMPLETED' ? <Check /> : item.sequence_no}</i><div><span>{item.task_type_id} · v{item.task_type_version}</span><strong>{item.name}</strong><small>{item.started_at ? `${item.started_by} 시작 · ${new Date(item.started_at).toLocaleString('ko-KR')}` : '아직 시작하지 않음'}</small></div><b>{statusLabel}</b>{isCurrent && item.status === 'READY' && <button data-testid="start-current-work" disabled={!canExecute || working} onClick={() => void startCurrent()}>{working ? <LoaderCircle className="spin" /> : <Play />}{firstReady ? '의뢰 수령 · 작업 시작' : '작업 시작'}</button>}{isCurrent && item.status === 'IN_PROGRESS' && <button data-testid="complete-current-work" disabled={!canExecute || working} onClick={() => void completeCurrent()}>{working ? <LoaderCircle className="spin" /> : <FlaskConical />} 작업 완료</button>}</article>
    })}</div></section>

    <section className="workbench-monitor"><div className="workbench-run-list"><header><div><span>실행 이력</span><h2>현재 의뢰의 데모 결과</h2></div><button aria-label="실행 이력 새로고침" disabled={loadingRuns} onClick={() => void loadRuns()}><RefreshCw className={loadingRuns ? 'spin' : ''} /></button></header>{runs.length === 0 ? <p className="workbench-empty">완료한 데모 작업이 없습니다.</p> : runs.map((run) => <button key={run.id} className={activeRun?.id === run.id ? 'active' : ''} onClick={() => { setActiveRun(run); setActiveTaskId(run.tasks[0]?.id ?? '') }}><span><strong>{run.name}</strong><small>{new Date(run.created_at).toLocaleString('ko-KR')}</small></span><b>{runStatusLabel(run.status)} · {run.progress}%</b></button>)}</div><div className="workbench-run-detail">{activeRun ? <><header><div><span>{activeRun.execution_mode}</span><h2>{activeRun.name}</h2><p>Run ID {activeRun.id}</p></div><strong>{runStatusLabel(activeRun.status)} · {activeRun.progress}%</strong></header><div className="workbench-run-progress"><i><b style={{ width: `${activeRun.progress}%` }} /></i><span>갱신 {new Date(activeRun.completed_at || activeRun.created_at).toLocaleString('ko-KR')}</span></div><div className="workbench-run-body"><nav>{activeRun.tasks.map((task) => <button key={task.id} className={activeTask?.id === task.id ? 'active' : ''} onClick={() => setActiveTaskId(task.id)}><Activity /><span><strong>{task.display_name}</strong><small>{runStatusLabel(task.status)} · {task.progress}%</small></span></button>)}</nav>{activeTask && <DemoTaskDetail task={activeTask} />}</div></> : <div className="workbench-empty-detail"><Image /><strong>로그·검증·결과 확인</strong><p>현재 작업을 완료하면 데모 실행 결과가 표시됩니다.</p></div>}</div></section>
  </div>
}

export function WorkbenchTypeAdmin() {
  const [taskTypes, setTaskTypes] = useState<WorkbenchTaskType[]>([])
  const [requestTypes, setRequestTypes] = useState<WorkbenchRequestType[]>([])
  const [selectedTaskKeys, setSelectedTaskKeys] = useState<string[]>([])
  const [compositionMode, setCompositionMode] = useState<CompositionMode>('sequence')
  const [typeId, setTypeId] = useState('custom-analysis')
  const [typeName, setTypeName] = useState('사용자 정의 해석')
  const [typeDescription, setTypeDescription] = useState('의뢰 수행자가 선택할 수 있는 작업 시나리오입니다.')
  const [matchAnalysisType, setMatchAnalysisType] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    Promise.all([workbenchApi.taskTypes(), workbenchApi.requestTypes()])
      .then(([tasks, types]) => { setTaskTypes(tasks); setRequestTypes(types) })
      .catch((reason) => setError(friendlyWorkbenchError(reason)))
  }, [])

  const selectedTasks = taskTypes.filter((task) => selectedTaskKeys.includes(`${task.id}:${task.version}`))
  const toggle = (task: WorkbenchTaskType) => {
    const key = `${task.id}:${task.version}`
    setSelectedTaskKeys((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key])
  }
  const saveType = async () => {
    if (!typeId.trim() || typeName.trim().length < 2 || !selectedTasks.length) return
    setSaving(true); setError(''); setNotice('')
    try {
      const created = await workbenchApi.createRequestType({
        id: typeId.trim(), display_name: typeName.trim(), description: typeDescription.trim(),
        allowed_task_types: selectedTasks.map((task) => ({ id: task.id, version: task.version })),
        default_workflow: { nodes: nodesFor(selectedTasks, compositionMode) },
        match_rules: matchAnalysisType.trim() ? { analysis_type: matchAnalysisType.trim() } : {}, is_active: true,
      })
      setRequestTypes((current) => [created, ...current.filter((item) => item.id !== created.id)])
      setNotice(`${created.display_name} v${created.version}을 저장했습니다.`)
      setSelectedTaskKeys([])
    } catch (reason) { setError(friendlyWorkbenchError(reason)) } finally { setSaving(false) }
  }

  return <div className="workbench-admin-page" data-testid="workbench-type-admin">
    <section className="workbench-admin-hero"><div><span><Settings2 /> ADMIN ONLY</span><h1>작업 유형 관리</h1><p>의뢰 수행자에게 보여 줄 실행 시나리오와 허용 작업, 기본 순서를 불변 버전으로 정의합니다.</p></div><aside><strong>{requestTypes.length}</strong><span>활성 의뢰 유형</span></aside></section>
    {error && <div className="workbench-service-error"><AlertTriangle /><div><strong>관리 화면을 불러오지 못했습니다.</strong><p>{error}</p></div></div>}
    {notice && <div className="workbench-admin-notice"><Check /> {notice}</div>}
    <section className="workbench-admin-layout">
      <div className="workbench-admin-form"><header><span>NEW IMMUTABLE VERSION</span><h2>새 의뢰 유형 작성</h2></header><label><span>유형 ID</span><input aria-label="관리 유형 ID" value={typeId} onChange={(event) => setTypeId(event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, '-'))} /></label><label><span>수행자에게 보일 이름</span><input aria-label="관리 유형 표시 이름" value={typeName} onChange={(event) => setTypeName(event.target.value)} /></label><label><span>언제 사용하는 작업인지</span><textarea aria-label="관리 유형 설명" value={typeDescription} onChange={(event) => setTypeDescription(event.target.value)} /></label><label><span>자동 추천 분석 유형</span><input aria-label="관리 자동 추천 분석 유형" value={matchAnalysisType} onChange={(event) => setMatchAnalysisType(event.target.value)} placeholder="예: DROP · 비워두면 수동 선택" /></label><fieldset><legend>기본 실행 방식</legend><label><input type="radio" checked={compositionMode === 'sequence'} onChange={() => setCompositionMode('sequence')} /> 선택 순서대로</label><label><input type="radio" checked={compositionMode === 'parallel'} onChange={() => setCompositionMode('parallel')} /> 독립·병렬</label></fieldset><div className="workbench-type-plan"><strong>{selectedTasks.length}개 작업 선택</strong><p>{selectedTasks.map((task) => task.display_name).join(' → ') || '오른쪽에서 수행자에게 허용할 작업을 선택하세요.'}</p></div><button className="workbench-admin-save" disabled={saving || !selectedTasks.length || typeName.trim().length < 2} onClick={() => void saveType()}>{saving ? <LoaderCircle className="spin" /> : <Save />} 새 유형 버전 저장</button></div>
      <div className="workbench-admin-task-picker"><header><span>ALLOWED TASKS</span><h2>수행자에게 허용할 작업</h2><p>카드를 선택한 순서가 기본 실행 순서가 됩니다.</p></header><div>{taskTypes.map((task) => { const selected = selectedTaskKeys.includes(`${task.id}:${task.version}`); const guidance = TASK_GUIDANCE[task.kind]; return <button key={`${task.id}:${task.version}`} className={selected ? 'selected' : ''} onClick={() => toggle(task)}><i>{selected ? <Check /> : <Plus />}</i><span><strong>{task.display_name}</strong><small>{guidance?.purpose ?? task.description}</small></span><b>v{task.version}</b></button> })}</div></div>
    </section>
    <section className="workbench-admin-types"><header><span>ACTIVE REQUEST TYPES</span><h2>현재 수행자에게 제공되는 시나리오</h2></header><div>{requestTypes.map((item) => <article key={`${item.id}:${item.version}`}><header><div><strong>{item.display_name}</strong><code>{item.id} · v{item.version}</code></div><b>{item.default_workflow.nodes.length}개 작업</b></header><p>{item.description}</p><div>{item.default_workflow.nodes.map((node, index) => <span key={node.node_key}>{index + 1}. {taskTypes.find((task) => task.id === node.task_type_id)?.display_name ?? node.task_type_id}</span>)}</div></article>)}</div></section>
  </div>
}

function DemoTaskDetail({ task }: { task: DemoRunTask }) {
  const [textArtifactUrl, setTextArtifactUrl] = useState(task.demo_text_artifacts[0]?.url ?? '')
  const [textContent, setTextContent] = useState('')
  const [textLoading, setTextLoading] = useState(false)

  useEffect(() => { setTextArtifactUrl(task.demo_text_artifacts[0]?.url ?? '') }, [task.id, task.demo_text_artifacts])
  useEffect(() => {
    let active = true
    if (!textArtifactUrl) { setTextContent(''); return () => { active = false } }
    setTextLoading(true)
    workbenchApi.demoTextArtifact(textArtifactUrl)
      .then((content) => { if (active) setTextContent(content) })
      .catch((reason) => { if (active) setTextContent(reason instanceof Error ? reason.message : '텍스트 데모 파일을 읽지 못했습니다.') })
      .finally(() => { if (active) setTextLoading(false) })
    return () => { active = false }
  }, [textArtifactUrl])

  return <article className="workbench-task-result">
    <figure><img src={task.demo_artifact_url} alt={`${task.display_name} 데모 결과`} /><figcaption><span><Image /> 데모 결과 이미지</span><b>실제 해석 결과 아님</b></figcaption></figure>
    <div className="workbench-event-log"><header><span>DEMO EVENT LOG</span><strong>{task.display_name}</strong></header>{task.events.map((event) => <div key={event.event_index}><i className={event.level.toLowerCase()} /><span><strong>{event.message}</strong><small>{event.event_type} · {event.progress}% · {new Date(event.occurred_at).toLocaleTimeString('ko-KR')}</small></span></div>)}</div>
    <section className="workbench-text-artifacts"><header><div><span>STATIC TEXT FIXTURES</span><strong><FileText /> 로그·검증·결과 텍스트</strong></div><b>DEMO ONLY · 저장소 예제 파일</b></header><nav aria-label="텍스트 데모 파일 선택">{task.demo_text_artifacts.map((artifact) => <button key={artifact.id} className={textArtifactUrl === artifact.url ? 'active' : ''} onClick={() => setTextArtifactUrl(artifact.url)}>{artifact.label}</button>)}</nav><pre aria-label="텍스트 데모 파일 내용">{textLoading ? '텍스트 파일을 불러오는 중입니다…' : textContent}</pre></section>
  </article>
}

export function RequestDemoRunSummary({ run }: { run: Workflow['latest_demo_run'] }) {
  if (!run) return <div className="request-demo-summary empty"><FlaskConical /><span><strong>연결 실행 없음</strong><small>해석 작업 실행 탭에서 DEMO_ONLY 작업을 연결할 수 있습니다.</small></span></div>
  return <div className="request-demo-summary"><img src="/assets/demo-workbench.svg" alt="최근 데모 실행 결과" /><span><strong>{run.name}</strong><small>DEMO_ONLY · {runStatusLabel(run.status)} · {run.progress}%</small></span><time>{new Date(run.completed_at || run.created_at).toLocaleString('ko-KR')}</time></div>
}
