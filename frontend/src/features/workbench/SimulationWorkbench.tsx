import { useEffect, useMemo, useState } from 'react'
import { Activity, AlertTriangle, Check, ClipboardList, FileText, FlaskConical, Image, LoaderCircle, Play, Plus, RefreshCw, Save, Settings2, ShieldCheck } from 'lucide-react'
import type { Workflow } from '../../types'
import { workbenchApi } from './api'
import type { BatchExecutionAttempt, BatchProfile, DemoRun, DemoRunTask, WorkbenchNode, WorkbenchRequestType, WorkbenchTaskType } from './types'

type CompositionMode = 'parallel' | 'sequence'

type TaskGuidance = {
  group: '준비·설계' | '해석 실행' | '결과 활용' | 'PhysicsAI'
  purpose: string
  requires: string
  result: string
}

type TaskExecution = {
  eyebrow: string
  title: string
  description: string
  startLabel: string
  runLabel: string
}

const TASK_GUIDANCE: Record<string, TaskGuidance> = {
  CAD_PREPARE: { group: '준비·설계', purpose: 'CAD 또는 기준 형상을 확인하고 해석에 쓸 형상으로 준비합니다.', requires: 'CAD 파일 또는 등록 형상', result: '준비된 형상과 형상 검증 리포트' },
  DOE_GENERATE: { group: '준비·설계', purpose: '설계변수와 범위를 바탕으로 비교할 설계점을 만듭니다.', requires: '설계변수·범위·DOE 방법', result: '설계점 목록과 DOE manifest' },
  ANALYSIS_MODELING: { group: '준비·설계', purpose: '형상에 재료·경계조건·하중을 적용해 해석 모델을 준비합니다.', requires: '준비 형상과 모델링 조건', result: '검증된 Solver Deck' },
  HPC_SUBMIT: { group: '해석 실행', purpose: '준비된 해석 모델을 계산 자원에 제출하는 과정을 시연합니다.', requires: 'Solver Deck과 실행 프로파일', result: '작업 접수 정보와 제출 기록' },
  EXECUTION_MONITOR: { group: '해석 실행', purpose: '실행 상태와 로그를 읽어 진행·완료·실패를 감시합니다.', requires: '작업 접수 정보 또는 로그', result: '진행 상태와 실행 로그' },
  RESULT_COLLECT: { group: '결과 활용', purpose: '완료된 작업에서 필요한 결과 파일을 찾아 수집합니다.', requires: '결과 위치와 파일 규칙', result: '결과 파일 목록과 수집 검증' },
  POST_PROCESS: { group: '결과 활용', purpose: '원시 결과에서 KPI·판정·그래프용 값을 계산합니다.', requires: '수집된 해석 결과', result: 'KPI, 판정, 결과 요약' },
  RELIABILITY_EVALUATION: { group: '결과 활용', purpose: 'Open Cell 파손과 Chassis 영구변형을 관리 기준으로 평가합니다.', requires: '응력 MPa·영구변형 mm 결과와 관리 기준', result: '항목별 판정과 신뢰성 평가 요약' },
  OPTIMIZATION_ANALYSIS: { group: '결과 활용', purpose: 'DOE 결과에서 목표와 제약을 비교해 최적 설계 후보를 선정합니다.', requires: 'DOE 결과·목표함수·제약조건', result: '최적 설계 후보와 트레이드오프' },
  PERFORMANCE_RANKING: { group: '결과 활용', purpose: '설계점별 성능을 공통 지표로 비교해 우선순위를 계산합니다.', requires: '설계점별 KPI·가중치·판정', result: '설계 성능 순위와 근거 지표' },
  ANALYSIS_DB_PUBLISH: { group: '결과 활용', purpose: '검증된 결과를 해석 DB에 발행하는 과정을 시연합니다.', requires: '검증된 후처리 결과', result: '해석 Run 참조와 발행 리포트' },
  TRAINING_DATASET_PUBLISH: { group: 'PhysicsAI', purpose: '승인된 해석 결과를 학습용 데이터셋 버전으로 구성합니다.', requires: '승인 결과와 데이터 기준', result: '학습 데이터셋 manifest' },
  PHYSICSAI_TRAIN_VALIDATE: { group: 'PhysicsAI', purpose: '데이터셋으로 PhysicsAI 학습과 성능 검증 과정을 시연합니다.', requires: '학습 데이터셋과 학습 설정', result: '모델 후보와 검증 지표' },
  MODEL_APPROVAL: { group: 'PhysicsAI', purpose: '검증 지표를 기준으로 사용할 모델 후보를 승인합니다.', requires: '모델 후보와 승인 기준', result: '승인 결정과 모델 버전' },
  REALTIME_PREDICT: { group: 'PhysicsAI', purpose: '승인 모델과 새 형상으로 실시간 성능 예측을 시연합니다.', requires: '승인 모델과 입력 형상', result: '예측 KPI와 신뢰도' },
  DASHBOARD_VISUALIZE: { group: '결과 활용', purpose: '해석 DB 또는 예측 결과를 대시보드에 연결합니다.', requires: '해석 Run 또는 예측 결과', result: '대시보드 연결 정보' },
}

const TASK_EXECUTION: Record<string, TaskExecution> = {
  CAD_PREPARE: { eyebrow: 'GEOMETRY PREP', title: '형상 준비 실행 위젯', description: 'CAD 입력을 점검하고 형상 준비 데모 결과를 생성합니다.', startLabel: '형상 준비 시작', runLabel: '형상 준비 데모 실행·완료' },
  DOE_GENERATE: { eyebrow: 'DOE GENERATOR', title: 'DOE 생성 실행 위젯', description: '설계변수 범위로 설계점과 manifest를 생성하는 데모를 수행합니다.', startLabel: 'DOE 생성 시작', runLabel: 'DOE 생성 데모 실행·완료' },
  ANALYSIS_MODELING: { eyebrow: 'MODEL BUILD', title: '해석 모델링 실행 위젯', description: '재료·경계조건·하중 적용과 Solver Deck 검증을 시연합니다.', startLabel: '모델링 시작', runLabel: '모델링 검증 실행·완료' },
  HPC_SUBMIT: { eyebrow: 'SOLVER SUBMIT', title: 'HPC 배치 제출 위젯', description: '연결된 배치 프로필로 Solver 제출 기록을 만든 뒤 작업을 완료하세요.', startLabel: 'HPC 제출 준비 시작', runLabel: 'HPC 제출 데모 실행·완료' },
  EXECUTION_MONITOR: { eyebrow: 'RUN MONITOR', title: '실행 모니터 위젯', description: '실행 상태와 이벤트 로그를 갱신하고 모니터링 결과를 남깁니다.', startLabel: '실행 모니터링 시작', runLabel: '모니터링 데모 실행·완료' },
  RESULT_COLLECT: { eyebrow: 'RESULT COLLECT', title: '결과 수집 위젯', description: '완료된 해석의 결과 파일 목록과 수집 검증 결과를 만듭니다.', startLabel: '결과 수집 시작', runLabel: '결과 수집 데모 실행·완료' },
  POST_PROCESS: { eyebrow: 'POST PROCESS', title: '후처리 실행 위젯', description: '원시 결과에서 KPI·판정·차트용 값을 생성하는 데모를 수행합니다.', startLabel: '결과 후처리 시작', runLabel: '후처리 데모 실행·완료' },
  RELIABILITY_EVALUATION: { eyebrow: 'RELIABILITY CHECK', title: '파손·휘 신뢰성 평가 위젯', description: 'Open Cell 응력과 Chassis 영구변형을 각 단위 기준으로 평가하는 데모를 수행합니다.', startLabel: '신뢰성 평가 시작', runLabel: '신뢰성 평가 데모 실행·완료' },
  OPTIMIZATION_ANALYSIS: { eyebrow: 'OPTIMIZATION', title: '최적화 분석 위젯', description: '목표와 제약을 비교해 최적 설계 후보를 생성하는 데모를 수행합니다.', startLabel: '최적화 분석 시작', runLabel: '최적화 데모 실행·완료' },
  PERFORMANCE_RANKING: { eyebrow: 'PERFORMANCE RANK', title: '설계 성능 순위 위젯', description: '설계점별 KPI와 판정을 비교해 성능 순위를 생성하는 데모를 수행합니다.', startLabel: '성능 순위 평가 시작', runLabel: '성능 순위 데모 실행·완료' },
  ANALYSIS_DB_PUBLISH: { eyebrow: 'DB PUBLISH', title: '해석 DB 발행 위젯', description: '검증된 결과를 해석 Run 참조와 발행 리포트로 연결합니다.', startLabel: 'DB 발행 시작', runLabel: 'DB 발행 데모 실행·완료' },
  TRAINING_DATASET_PUBLISH: { eyebrow: 'DATASET PUBLISH', title: '학습 데이터셋 발행 위젯', description: '승인 결과를 버전화된 학습 데이터셋 manifest로 구성합니다.', startLabel: '데이터셋 구성 시작', runLabel: '데이터셋 발행 데모 실행·완료' },
  PHYSICSAI_TRAIN_VALIDATE: { eyebrow: 'TRAIN & VALIDATE', title: 'PhysicsAI 학습·검증 위젯', description: '학습과 성능 검증 결과를 데모 아티팩트로 생성합니다.', startLabel: 'PhysicsAI 학습 시작', runLabel: '학습·검증 데모 실행·완료' },
  MODEL_APPROVAL: { eyebrow: 'MODEL APPROVAL', title: '모델 승인 위젯', description: '검증 지표를 기준으로 모델 후보 승인 결정을 시연합니다.', startLabel: '모델 승인 검토 시작', runLabel: '모델 승인 데모 실행·완료' },
  REALTIME_PREDICT: { eyebrow: 'REALTIME PREDICT', title: '실시간 예측 위젯', description: '승인 모델에 입력 형상을 적용해 예측 KPI와 신뢰도를 생성합니다.', startLabel: '실시간 예측 시작', runLabel: '실시간 예측 데모 실행·완료' },
  DASHBOARD_VISUALIZE: { eyebrow: 'DASHBOARD LINK', title: '대시보드 연결 위젯', description: '해석 Run 또는 예측 결과를 대시보드 연결 정보로 생성합니다.', startLabel: '대시보드 연결 시작', runLabel: '대시보드 연결 데모 실행·완료' },
}

const FALLBACK_TASK_EXECUTION: TaskExecution = {
  eyebrow: 'TASK EXECUTION', title: '관련 업무 실행 위젯', description: '선택한 작업 유형의 데모 실행 결과를 생성합니다.', startLabel: '작업 시작', runLabel: '업무 데모 실행·완료',
}

function runStatusLabel(status: string) {
  return ({ SUCCEEDED: '완료', COMPLETED: '완료', RUNNING: '진행 중', IN_PROGRESS: '진행 중', PENDING: '대기', WAITING: '선행 작업 대기', READY: '실행 준비', BLOCKED: '차단', FAILED: '실패', SKIPPED: '건너뜀', CANCELLED: '취소' } as Record<string, string>)[status] ?? status
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

export function SimulationWorkbench({ workflows, initialRequestId, createdBy, canExecute, isAdmin, onChanged, onRequestSelected }: {
  workflows: Workflow[]
  initialRequestId: string
  createdBy: string
  canExecute: boolean
  isAdmin: boolean
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
  const [selectedWorkItemId, setSelectedWorkItemId] = useState('')
  const [batchProfiles, setBatchProfiles] = useState<BatchProfile[]>([])
  const [batchAttempts, setBatchAttempts] = useState<BatchExecutionAttempt[]>([])
  const [loadingBatchAttempts, setLoadingBatchAttempts] = useState(false)
  const [batchAttemptError, setBatchAttemptError] = useState('')
  const [taskTypes, setTaskTypes] = useState<WorkbenchTaskType[]>([])
  const [batchProfileId, setBatchProfileId] = useState('')
  const [batchRequestKey, setBatchRequestKey] = useState(() => `batch-${crypto.randomUUID()}`)
  const [progressDraft, setProgressDraft] = useState(10)

  const requestId = assigned.some((item) => item.request.id === initialRequestId) ? initialRequestId : assigned[0]?.request.id ?? ''
  const workflow = assigned.find((item) => item.request.id === requestId)
  const currentItem = workflow?.steps.find((item) => item.status === 'IN_PROGRESS') ?? workflow?.steps.find((item) => item.status === 'READY') ?? null
  const activeTask = activeRun?.tasks.find((task) => task.id === activeTaskId) ?? activeRun?.tasks[0] ?? null
  const selectedWorkItem = workflow?.steps.find((item) => item.id === selectedWorkItemId) ?? currentItem
  const selectedTaskType = taskTypes.find((task) => task.id === selectedWorkItem?.task_type_id && task.version === selectedWorkItem?.task_type_version)
  const selectedGuidance = selectedTaskType ? TASK_GUIDANCE[selectedTaskType.kind] : undefined
  const selectedExecution = selectedTaskType ? TASK_EXECUTION[selectedTaskType.kind] ?? FALLBACK_TASK_EXECUTION : FALLBACK_TASK_EXECUTION
  const selectedTaskTypeId = selectedWorkItem?.task_type_id ?? ''
  const compatibleBatchProfiles = batchProfiles.filter((profile) => selectedTaskTypeId && profile.task_type_ids.includes(selectedTaskTypeId))
  const selectedBatchProfile = compatibleBatchProfiles.find((profile) => profile.id === batchProfileId)
  const selectedIsCurrent = selectedWorkItem?.id === currentItem?.id
  const canOperateCurrent = Boolean(canExecute && currentItem && (isAdmin || currentItem.owner === createdBy))
  const canOperateSelected = Boolean(canExecute && selectedWorkItem && (isAdmin || selectedWorkItem.owner === createdBy))
  const progressIsValid = Boolean(selectedWorkItem && Number.isFinite(progressDraft) && progressDraft > selectedWorkItem.progress && progressDraft <= 99)
  const taskActionDisabled = !canOperateSelected || working || !selectedIsCurrent || !selectedWorkItem || !['READY', 'IN_PROGRESS'].includes(selectedWorkItem.status)
  const taskActionHelp = !canExecute
    ? '실행 권한이 없어 작업을 수행할 수 없습니다.'
    : selectedWorkItem && !isAdmin && selectedWorkItem.owner !== createdBy
      ? `작업 담당자(${selectedWorkItem.owner}) 또는 관리자만 실행할 수 있습니다.`
    : !selectedIsCurrent
      ? '현재 순서의 작업을 완료한 뒤 실행할 수 있습니다.'
      : selectedWorkItem?.status === 'READY'
        ? '작업을 시작하면 진행도와 데모 실행 기능이 열립니다.'
        : selectedWorkItem?.status === 'IN_PROGRESS'
          ? '데모 결과를 생성하고 이 작업을 완료합니다.'
          : `현재 상태(${runStatusLabel(selectedWorkItem?.status ?? '')})에서는 실행할 수 없습니다.`

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

  const loadBatchAttempts = async (workItemId = selectedWorkItem?.id ?? '') => {
    if (!workItemId) { setBatchAttempts([]); return }
    setLoadingBatchAttempts(true); setBatchAttemptError('')
    try { setBatchAttempts(await workbenchApi.batchAttempts(workItemId)) }
    catch (reason) { setBatchAttempts([]); setBatchAttemptError(friendlyWorkbenchError(reason)) }
    finally { setLoadingBatchAttempts(false) }
  }

  useEffect(() => { void loadRuns(requestId) }, [requestId])
  useEffect(() => { void loadBatchAttempts(selectedWorkItem?.id) }, [selectedWorkItem?.id])
  useEffect(() => {
    Promise.all([workbenchApi.batchProfiles(), workbenchApi.taskTypes()])
      .then(([profiles, tasks]) => {
        setBatchProfiles(profiles)
        setTaskTypes(tasks)
      })
      .catch((reason) => setError(friendlyWorkbenchError(reason)))
  }, [])
  useEffect(() => {
    if (!selectedWorkItem) return
    setSelectedWorkItemId(selectedWorkItem.id)
    setProgressDraft(Math.min(99, Math.max(1, Number(selectedWorkItem.progress || 0) + 10)))
  }, [selectedWorkItem?.id, selectedWorkItem?.progress])
  useEffect(() => {
    setBatchProfileId((current) => compatibleBatchProfiles.some((profile) => profile.id === current) ? current : compatibleBatchProfiles[0]?.id ?? '')
  }, [selectedTaskTypeId, batchProfiles])
  useEffect(() => { setBatchRequestKey(`batch-${crypto.randomUUID()}`) }, [selectedWorkItem?.id, batchProfileId])

  const changeRequest = (nextRequestId: string) => {
    onRequestSelected(nextRequestId); setError(''); setActiveRun(null); setActiveTaskId('')
  }

  const startCurrent = async () => {
    if (!currentItem || currentItem.status !== 'READY' || !canOperateCurrent) return
    setWorking(true); setError('')
    try {
      await workbenchApi.startWorkItem(currentItem.id, createdBy)
      await onChanged(currentItem.sequence_no === 1 ? '의뢰를 수령하고 첫 작업을 시작했습니다.' : `${currentItem.name} 작업을 시작했습니다.`)
    } catch (reason) { setError(friendlyWorkbenchError(reason)) }
    finally { setWorking(false) }
  }

  const completeCurrent = async () => {
    if (!currentItem || currentItem.status !== 'IN_PROGRESS' || !canOperateCurrent || !currentItem.node_key || !currentItem.task_type_id || !currentItem.task_type_version) return
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

  const updateSelectedProgress = async () => {
    if (!selectedWorkItem || selectedWorkItem.status !== 'IN_PROGRESS' || !canOperateSelected) return
    setWorking(true); setError('')
    try {
      await workbenchApi.updateWorkItemProgress(selectedWorkItem.id, progressDraft, createdBy)
      await onChanged(`${selectedWorkItem.name} 진행도를 ${progressDraft}%로 갱신했습니다.`)
    } catch (reason) { setError(friendlyWorkbenchError(reason)) }
    finally { setWorking(false) }
  }

  const dispatchSelectedBatch = async () => {
    if (!selectedWorkItem || selectedWorkItem.status !== 'IN_PROGRESS' || !batchProfileId || !canOperateSelected) return
    const workItemId = selectedWorkItem.id
    setWorking(true); setError('')
    try {
      const run = await workbenchApi.dispatchBatch(workItemId, batchProfileId, createdBy, batchRequestKey)
      setRuns((items) => [run, ...items.filter((item) => item.id !== run.id)])
      setActiveRun(run); setActiveTaskId(run.tasks[0]?.id ?? '')
      setBatchRequestKey(`batch-${crypto.randomUUID()}`)
      await onChanged(`${selectedWorkItem.name} 배치 구성을 검증하고 DEMO_ONLY 실행 기록을 생성했습니다.`)
    } catch (reason) { setError(friendlyWorkbenchError(reason)) }
    finally { await loadBatchAttempts(workItemId); setWorking(false) }
  }

  const executeSelectedTask = async () => {
    if (!selectedIsCurrent || !selectedWorkItem) return
    if (selectedWorkItem.status === 'READY') await startCurrent()
    else if (selectedWorkItem.status === 'IN_PROGRESS') await completeCurrent()
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
      return <article key={item.id} role="button" tabIndex={0} aria-pressed={selectedWorkItem?.id === item.id} className={`${item.status.toLowerCase()} ${isCurrent ? 'current' : ''} ${selectedWorkItem?.id === item.id ? 'selected' : ''}`} onClick={() => setSelectedWorkItemId(item.id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelectedWorkItemId(item.id) } }}><i>{item.status === 'COMPLETED' ? <Check /> : item.sequence_no}</i><div><span>{item.task_type_id} · v{item.task_type_version}</span><strong>{item.name}</strong><small>{item.started_at ? `${item.started_by} 시작 · ${new Date(item.started_at).toLocaleString('ko-KR')}` : '아직 시작하지 않음'}</small></div><b>{statusLabel} · {item.progress}%</b>{isCurrent && item.status === 'READY' && <button data-testid="start-current-work" aria-describedby="task-action-help" disabled={!canOperateCurrent || working} onClick={(event) => { event.stopPropagation(); void startCurrent() }}>{working ? <LoaderCircle className="spin" /> : <Play />}{firstReady ? '의뢰 수령 · 작업 시작' : '작업 시작'}</button>}{isCurrent && item.status === 'IN_PROGRESS' && <button data-testid="complete-current-work" aria-describedby="task-action-help" disabled={!canOperateCurrent || working} onClick={(event) => { event.stopPropagation(); void completeCurrent() }}>{working ? <LoaderCircle className="spin" /> : <FlaskConical />} 작업 완료</button>}</article>
    })}</div></section>

    {selectedWorkItem && <section className="work-item-detail" data-testid="work-item-detail" aria-busy={working}>
      <header><div><span>WORK ITEM DETAIL</span><h2>{selectedWorkItem.name}</h2><p>{selectedWorkItem.task_type_id} · v{selectedWorkItem.task_type_version}</p></div><b>{runStatusLabel(selectedWorkItem.status)} · {selectedWorkItem.progress}%</b></header>
      {selectedGuidance && <div className="work-item-guidance"><div><span>업무 목적</span><strong>{selectedGuidance.purpose}</strong></div><div><span>필요 입력</span><strong>{selectedGuidance.requires}</strong></div><div><span>예상 결과</span><strong>{selectedGuidance.result}</strong></div></div>}
      <article className={`task-execution-widget group-${selectedGuidance?.group === 'PhysicsAI' ? 'ai' : selectedGuidance?.group === '해석 실행' ? 'run' : selectedGuidance?.group === '결과 활용' ? 'result' : 'prepare'}`} data-testid={`task-execution-widget-${selectedTaskType?.kind ?? 'unknown'}`}>
        <div className="task-execution-mark"><Activity aria-hidden="true" /></div>
        <div><span>{selectedExecution.eyebrow}</span><h3>{selectedExecution.title}</h3><p>{selectedExecution.description}</p><small id="task-action-help">{taskActionHelp}</small></div>
        <button data-testid="execute-selected-task" aria-describedby="task-action-help" disabled={taskActionDisabled} onClick={() => void executeSelectedTask()}>
          {working ? <LoaderCircle className="spin" aria-hidden="true" /> : selectedWorkItem.status === 'IN_PROGRESS' ? <FlaskConical aria-hidden="true" /> : <Play aria-hidden="true" />}
          {selectedWorkItem.status === 'IN_PROGRESS' ? selectedExecution.runLabel : selectedExecution.startLabel}
        </button>
      </article>
      <div className="work-item-detail-grid">
        <article className="progress-update-card">
          <h3>진행도 업데이트</h3><p>진행 중 작업의 진행도만 이전 값보다 크게 갱신할 수 있습니다.</p>
          <div className="progress-current"><span>현재 진행도</span><strong>{selectedWorkItem.progress}%</strong><progress aria-label={`${selectedWorkItem.name} 현재 진행도`} max="100" value={selectedWorkItem.progress}>{selectedWorkItem.progress}%</progress></div>
          <label><span>새 진행도</span><input aria-label="작업 진행도" aria-describedby="progress-update-help" aria-invalid={selectedWorkItem.status === 'IN_PROGRESS' && !progressIsValid} type="number" min={Math.min(99, selectedWorkItem.progress + 1)} max="99" value={progressDraft} disabled={selectedWorkItem.status !== 'IN_PROGRESS'} onChange={(event) => setProgressDraft(event.target.value === '' ? Number.NaN : Number(event.target.value))}/><b>%</b></label>
          <small id="progress-update-help" role="status">{!canExecute ? '진행도를 수정할 실행 권한이 없습니다.' : !isAdmin && selectedWorkItem.owner !== createdBy ? `작업 담당자(${selectedWorkItem.owner}) 또는 관리자만 진행도를 수정할 수 있습니다.` : selectedWorkItem.status !== 'IN_PROGRESS' ? '진행 중인 작업에서만 수정할 수 있습니다.' : progressIsValid ? `${selectedWorkItem.progress + 1}~99 사이의 값을 저장할 수 있습니다.` : `현재 값 ${selectedWorkItem.progress}%보다 큰 ${Math.min(99, selectedWorkItem.progress + 1)}~99 사이의 값을 입력하세요.`}</small>
          <button disabled={!canOperateSelected || working || selectedWorkItem.status !== 'IN_PROGRESS' || !progressIsValid} onClick={() => void updateSelectedProgress()}><RefreshCw aria-hidden="true" /> 진행도 저장</button>
        </article>
        <article className="batch-execution-card">
          <h3>배치 실행 구성</h3><p>경로와 명령은 기록·미리보기 전용입니다. 서버는 외부 solver 프로세스를 실행하지 않습니다.</p>
          {compatibleBatchProfiles.length ? <><label><span>배치 경로 프로필</span><select aria-label="배치 경로 프로필" value={batchProfileId} disabled={selectedWorkItem.status !== 'IN_PROGRESS' || !canOperateSelected} onChange={(event) => setBatchProfileId(event.target.value)}>{compatibleBatchProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></label>{selectedBatchProfile?.solver_path ? <pre aria-label="배치 명령 미리보기"><code>{`"${selectedBatchProfile.solver_path}" ${selectedBatchProfile.arguments_template}`}</code></pre> : <p className="batch-profile-redacted" role="note">이 계정에는 실행 경로와 명령 미리보기가 표시되지 않습니다.</p>}</> : <p className="batch-profile-empty" role="status">이 작업 유형에 연결된 활성 배치 경로가 없습니다. 작업 유형 관리에서 호환 작업을 지정하세요.</p>}
          <small id="batch-action-help" role="status">{!canExecute ? '실행 권한이 필요합니다.' : !isAdmin && selectedWorkItem.owner !== createdBy ? `작업 담당자(${selectedWorkItem.owner}) 또는 관리자만 배치 기록을 생성할 수 있습니다.` : selectedWorkItem.status !== 'IN_PROGRESS' ? '진행 중인 현재 작업에서만 배치 기록을 생성할 수 있습니다.' : !selectedBatchProfile ? '호환되는 활성 프로필이 필요합니다.' : 'DEMO_ONLY 배치 기록을 생성할 준비가 되었습니다.'}</small>
          <button className="batch-dispatch-button" aria-describedby="batch-action-help" disabled={!canOperateSelected || working || selectedWorkItem.status !== 'IN_PROGRESS' || !selectedBatchProfile} onClick={() => void dispatchSelectedBatch()}><Play aria-hidden="true" /> 배치 실행 기록 생성</button>
        </article>
      </div>
      <section className="batch-attempt-history" aria-labelledby="batch-attempt-history-title" aria-live="polite">
        <header><div><span>BATCH ATTEMPTS</span><h3 id="batch-attempt-history-title">배치 실행 상태·이벤트</h3><p>선택한 작업의 preflight부터 완료·거부까지 모든 제어 plane 기록을 표시합니다.</p></div><button aria-label="배치 실행 상태 새로고침" disabled={loadingBatchAttempts} onClick={() => void loadBatchAttempts()}><RefreshCw className={loadingBatchAttempts ? 'spin' : ''} aria-hidden="true" /></button></header>
        {batchAttemptError ? <div className="batch-attempt-error" role="alert"><AlertTriangle aria-hidden="true" /><span>{batchAttemptError}</span></div> : loadingBatchAttempts ? <div className="batch-attempt-empty"><LoaderCircle className="spin" aria-hidden="true" /> 배치 실행 기록을 불러오는 중입니다…</div> : batchAttempts.length === 0 ? <div className="batch-attempt-empty"><ClipboardList aria-hidden="true" /> 이 작업의 배치 실행 기록이 아직 없습니다.</div> : <div className="batch-attempt-list">{batchAttempts.map((attempt) => <article key={attempt.id} className={`status-${attempt.status.toLowerCase()}`}>
          <header><div><span>{attempt.execution_mode} · {attempt.batch_profile_id} v{attempt.batch_profile_version}</span><strong>{attempt.last_message}</strong><small>{attempt.id} · <time dateTime={attempt.created_at}>{new Date(attempt.created_at).toLocaleString('ko-KR')}</time></small></div><b>{attempt.status} · {attempt.progress}%</b></header>
          <progress aria-label={`${attempt.batch_profile_id} 배치 실행 진행도`} max="100" value={attempt.progress}>{attempt.progress}%</progress>
          {attempt.profile_snapshot?.solver_path && attempt.command_preview ? <pre aria-label="실행 시점 배치 명령 snapshot"><code>{attempt.command_preview}</code></pre> : null}
          <ol aria-label="배치 상태 이벤트">{attempt.events.map((event) => <li key={event.id} className={`level-${event.level.toLowerCase()}`}><i aria-hidden="true" /><span><strong>{event.event_type} · {event.progress}%</strong><small>{event.message}</small></span><time dateTime={event.occurred_at}>{new Date(event.occurred_at).toLocaleTimeString('ko-KR')}</time></li>)}</ol>
        </article>)}</div>}
      </section>
    </section>}

    <section className="workbench-monitor"><div className="workbench-run-list"><header><div><span>실행 이력</span><h2>현재 의뢰의 데모 결과</h2></div><button aria-label="실행 이력 새로고침" disabled={loadingRuns} onClick={() => void loadRuns()}><RefreshCw className={loadingRuns ? 'spin' : ''} /></button></header>{runs.length === 0 ? <p className="workbench-empty">완료한 데모 작업이 없습니다.</p> : runs.map((run) => <button key={run.id} className={activeRun?.id === run.id ? 'active' : ''} onClick={() => { setActiveRun(run); setActiveTaskId(run.tasks[0]?.id ?? '') }}><span><strong>{run.name}</strong><small>{new Date(run.created_at).toLocaleString('ko-KR')}</small></span><b>{runStatusLabel(run.status)} · {run.progress}%</b></button>)}</div><div className="workbench-run-detail">{activeRun ? <><header><div><span>{activeRun.execution_mode}</span><h2>{activeRun.name}</h2><p>Run ID {activeRun.id}</p></div><strong>{runStatusLabel(activeRun.status)} · {activeRun.progress}%</strong></header><div className="workbench-run-progress"><i><b style={{ width: `${activeRun.progress}%` }} /></i><span>갱신 {new Date(activeRun.completed_at || activeRun.created_at).toLocaleString('ko-KR')}</span></div><div className="workbench-run-body"><nav>{activeRun.tasks.map((task) => <button key={task.id} className={activeTask?.id === task.id ? 'active' : ''} onClick={() => setActiveTaskId(task.id)}><Activity /><span><strong>{task.display_name}</strong><small>{runStatusLabel(task.status)} · {task.progress}%</small></span></button>)}</nav>{activeTask && <DemoTaskDetail task={activeTask} />}</div></> : <div className="workbench-empty-detail"><Image /><strong>로그·검증·결과 확인</strong><p>현재 작업을 완료하면 데모 실행 결과가 표시됩니다.</p></div>}</div></section>
  </div>
}

export function WorkbenchTypeAdmin() {
  const [adminView, setAdminView] = useState<'request-types' | 'batch-paths'>('request-types')
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
  const [batchProfiles, setBatchProfiles] = useState<BatchProfile[]>([])
  const [batchDraft, setBatchDraft] = useState<Omit<BatchProfile, 'version' | 'created_at' | 'updated_at'>>({ id: 'radioss-local', name: 'Radioss 로컬 배치', solver_path: 'C:\\Altair\\hwsolvers\\radioss.exe', working_directory: 'C:\\Simulation\\runs\\{request_id}', arguments_template: '-i {input} -nt {cores}', environment: { OMP_NUM_THREADS: '{cores}' }, task_type_ids: ['hpc-submit'], is_active: true, updated_by: '관리자' })

  useEffect(() => {
    Promise.all([workbenchApi.taskTypes(), workbenchApi.requestTypes(), workbenchApi.batchProfiles(true)])
      .then(([tasks, types, profiles]) => { setTaskTypes(tasks); setRequestTypes(types); setBatchProfiles(profiles) })
      .catch((reason) => setError(friendlyWorkbenchError(reason)))
  }, [])

  const selectedTasks = taskTypes.filter((task) => selectedTaskKeys.includes(`${task.id}:${task.version}`))
  const batchTaskTypes = taskTypes.filter((task, index, items) => items.findIndex((candidate) => candidate.id === task.id) === index)
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
  const saveBatchProfile = async () => {
    if (!batchDraft.id || !batchDraft.name || !batchDraft.solver_path || !batchDraft.working_directory || !batchDraft.task_type_ids.length) return
    setSaving(true); setError(''); setNotice('')
    try {
      const saved = await workbenchApi.saveBatchProfile(batchDraft)
      setBatchProfiles((items) => [saved, ...items.filter((item) => item.id !== saved.id)])
      setNotice(`${saved.name} 배치 경로 프로필 v${saved.version}을 저장했습니다.`)
    } catch (reason) { setError(friendlyWorkbenchError(reason)) }
    finally { setSaving(false) }
  }
  const editBatchProfile = (profile: BatchProfile) => setBatchDraft({ id: profile.id, name: profile.name, solver_path: profile.solver_path, working_directory: profile.working_directory, arguments_template: profile.arguments_template, environment: { ...profile.environment }, task_type_ids: [...profile.task_type_ids], is_active: profile.is_active, updated_by: '관리자' })

  return <div className="workbench-admin-page" data-testid="workbench-type-admin">
    <section className="workbench-admin-hero"><div><span><Settings2 /> ADMIN ONLY</span><h1>작업 유형 관리</h1><p>의뢰 수행자에게 보여 줄 실행 시나리오와 허용 작업, 기본 순서를 불변 버전으로 정의합니다.</p></div><aside><strong>{requestTypes.length}</strong><span>활성 의뢰 유형</span></aside></section>
    {error && <div className="workbench-service-error"><AlertTriangle /><div><strong>관리 화면을 불러오지 못했습니다.</strong><p>{error}</p></div></div>}
    {notice && <div className="workbench-admin-notice" role="status" aria-live="polite"><Check /> {notice}</div>}
    <nav className="workbench-admin-tabs" role="tablist" aria-label="작업 유형 관리 내부 창">
      <button id="request-types-tab" role="tab" aria-selected={adminView === 'request-types'} aria-controls="request-types-panel" tabIndex={adminView === 'request-types' ? 0 : -1} onClick={() => setAdminView('request-types')} onKeyDown={(event) => { if (event.key === 'ArrowRight' || event.key === 'ArrowDown') { event.preventDefault(); setAdminView('batch-paths'); (event.currentTarget.nextElementSibling as HTMLButtonElement | null)?.focus() } }}><ClipboardList aria-hidden="true" /><span><strong>의뢰·작업 유형</strong><small>시나리오와 기본 작업 순서</small></span></button>
      <button id="batch-paths-tab" role="tab" aria-selected={adminView === 'batch-paths'} aria-controls="batch-paths-panel" tabIndex={adminView === 'batch-paths' ? 0 : -1} onClick={() => setAdminView('batch-paths')} onKeyDown={(event) => { if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') { event.preventDefault(); setAdminView('request-types'); (event.currentTarget.previousElementSibling as HTMLButtonElement | null)?.focus() } }}><Settings2 aria-hidden="true" /><span><strong>배치 경로 정의</strong><small>HyperStudy 스타일 실행 프로필</small></span></button>
    </nav>
    {adminView === 'request-types' && <div id="request-types-panel" className="workbench-admin-panel" role="tabpanel" aria-labelledby="request-types-tab" tabIndex={0}>
    <section className="workbench-admin-layout">
      <div className="workbench-admin-form"><header><span>NEW IMMUTABLE VERSION</span><h2>새 의뢰 유형 작성</h2></header><label><span>유형 ID</span><input aria-label="관리 유형 ID" value={typeId} onChange={(event) => setTypeId(event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, '-'))} /></label><label><span>수행자에게 보일 이름</span><input aria-label="관리 유형 표시 이름" value={typeName} onChange={(event) => setTypeName(event.target.value)} /></label><label><span>언제 사용하는 작업인지</span><textarea aria-label="관리 유형 설명" value={typeDescription} onChange={(event) => setTypeDescription(event.target.value)} /></label><label><span>자동 추천 분석 유형</span><input aria-label="관리 자동 추천 분석 유형" value={matchAnalysisType} onChange={(event) => setMatchAnalysisType(event.target.value)} placeholder="예: DROP · 비워두면 수동 선택" /></label><fieldset><legend>기본 실행 방식</legend><label><input type="radio" checked={compositionMode === 'sequence'} onChange={() => setCompositionMode('sequence')} /> 선택 순서대로</label><label><input type="radio" checked={compositionMode === 'parallel'} onChange={() => setCompositionMode('parallel')} /> 독립·병렬</label></fieldset><div className="workbench-type-plan"><strong>{selectedTasks.length}개 작업 선택</strong><p>{selectedTasks.map((task) => task.display_name).join(' → ') || '오른쪽에서 수행자에게 허용할 작업을 선택하세요.'}</p></div><button className="workbench-admin-save" disabled={saving || !selectedTasks.length || typeName.trim().length < 2} onClick={() => void saveType()}>{saving ? <LoaderCircle className="spin" /> : <Save />} 새 유형 버전 저장</button></div>
      <div className="workbench-admin-task-picker"><header><span>ALLOWED TASKS</span><h2>수행자에게 허용할 작업</h2><p>카드를 선택한 순서가 기본 실행 순서가 됩니다.</p></header><div>{taskTypes.map((task) => { const selected = selectedTaskKeys.includes(`${task.id}:${task.version}`); const guidance = TASK_GUIDANCE[task.kind]; return <button key={`${task.id}:${task.version}`} className={selected ? 'selected' : ''} onClick={() => toggle(task)}><i>{selected ? <Check /> : <Plus />}</i><span><strong>{task.display_name}</strong><small>{guidance?.purpose ?? task.description}</small></span><b>v{task.version}</b></button> })}</div></div>
    </section>
    <section className="workbench-admin-types"><header><span>ACTIVE REQUEST TYPES</span><h2>현재 수행자에게 제공되는 시나리오</h2></header><div>{requestTypes.map((item) => <article key={`${item.id}:${item.version}`}><header><div><strong>{item.display_name}</strong><code>{item.id} · v{item.version}</code></div><b>{item.default_workflow.nodes.length}개 작업</b></header><p>{item.description}</p><div>{item.default_workflow.nodes.map((node, index) => <span key={node.node_key}>{index + 1}. {taskTypes.find((task) => task.id === node.task_type_id)?.display_name ?? node.task_type_id}</span>)}</div></article>)}</div></section>
    </div>}
    {adminView === 'batch-paths' && <section id="batch-paths-panel" className="batch-profile-admin workbench-admin-panel" role="tabpanel" aria-labelledby="batch-paths-tab" tabIndex={0}><header><div><span>HYPERSTUDY STYLE BATCH PATHS</span><h2>배치 경로 정의</h2><p>실행 파일·작업 폴더·인수 템플릿·환경 변수와 호환 작업 유형을 저장합니다. 실제 외부 프로세스는 실행하지 않습니다.</p></div><strong>{batchProfiles.length}개 프로필</strong></header><div className="batch-profile-layout"><form aria-label="배치 경로 프로필 편집" onSubmit={(event) => { event.preventDefault(); void saveBatchProfile() }}><label><span>프로필 ID</span><input required aria-label="배치 프로필 ID" value={batchDraft.id} onChange={(event) => setBatchDraft({ ...batchDraft, id: event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, '-') })}/></label><label><span>이름</span><input required aria-label="배치 프로필 이름" value={batchDraft.name} onChange={(event) => setBatchDraft({ ...batchDraft, name: event.target.value })}/></label><label><span>Solver 실행 파일</span><input required aria-label="Solver 실행 파일" value={batchDraft.solver_path} onChange={(event) => setBatchDraft({ ...batchDraft, solver_path: event.target.value })}/></label><label><span>Working directory</span><input required aria-label="배치 작업 폴더" value={batchDraft.working_directory} onChange={(event) => setBatchDraft({ ...batchDraft, working_directory: event.target.value })}/></label><label><span>Arguments template</span><textarea aria-label="배치 인수 템플릿" value={batchDraft.arguments_template} onChange={(event) => setBatchDraft({ ...batchDraft, arguments_template: event.target.value })}/><small>{'{input}, {cores}, {request_id} 같은 자리표시자를 사용할 수 있습니다.'}</small></label><label><span>Environment (KEY=VALUE)</span><textarea aria-label="배치 환경 변수" value={Object.entries(batchDraft.environment).map(([key, value]) => `${key}=${value}`).join('\n')} onChange={(event) => setBatchDraft({ ...batchDraft, environment: Object.fromEntries(event.target.value.split(/\r?\n/).filter(Boolean).map((line) => { const index = line.indexOf('='); return index > 0 ? [line.slice(0, index).trim(), line.slice(index + 1).trim()] : [line.trim(), ''] })) })}/></label><fieldset className="batch-task-types"><legend>호환 작업 유형 *</legend>{batchTaskTypes.map((task) => <label key={task.id}><input type="checkbox" checked={batchDraft.task_type_ids.includes(task.id)} onChange={() => setBatchDraft((current) => ({ ...current, task_type_ids: current.task_type_ids.includes(task.id) ? current.task_type_ids.filter((id) => id !== task.id) : [...current.task_type_ids, task.id] }))}/><span>{task.display_name}</span><code>{task.id}</code></label>)}<small id="batch-task-types-help">선택한 작업의 상세 화면에서만 이 배치 프로필을 사용할 수 있습니다.</small></fieldset><label className="batch-active"><input type="checkbox" checked={batchDraft.is_active} onChange={(event) => setBatchDraft({ ...batchDraft, is_active: event.target.checked })}/><span>활성 프로필</span></label><button className="workbench-admin-save" aria-describedby="batch-task-types-help" disabled={saving || !batchDraft.task_type_ids.length}>{saving ? <LoaderCircle className="spin" aria-hidden="true" /> : <Save aria-hidden="true" />} 배치 경로 저장</button></form><div className="batch-profile-list" aria-label="저장된 배치 프로필">{batchProfiles.map((profile) => <button key={profile.id} aria-pressed={batchDraft.id === profile.id} className={batchDraft.id === profile.id ? 'active' : ''} onClick={() => editBatchProfile(profile)}><span><strong>{profile.name}</strong><code>{profile.id}</code></span><small>{profile.solver_path}</small><small>호환 · {profile.task_type_ids.map((id) => batchTaskTypes.find((task) => task.id === id)?.display_name ?? id).join(', ') || '미지정'}</small><b>{profile.is_active ? 'ACTIVE' : 'INACTIVE'}</b></button>)}</div></div></section>}
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
