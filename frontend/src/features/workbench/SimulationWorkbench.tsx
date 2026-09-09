import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Activity, AlertTriangle, Check, ChevronRight, ClipboardList, FileText, FlaskConical, Image, LoaderCircle, Pencil, Play, Plus, RefreshCw, Save, Settings2, ShieldCheck, Tag, Trash2, X } from 'lucide-react'
import type { Workflow } from '../../types'
import { workbenchApi } from './api'
import { RequestResultWidgetConfiguration } from './RequestResultWidgetConfiguration'
import { requestResultDefinitionFromProfile, requestResultDefinitionValidation } from './requestResultDefinition'
import './RequestResultWidgetConfiguration.css'
import { DEFAULT_REQUEST_TYPE_LABELS, requestTypeLabels, type BatchExecutionAttempt, type BatchProfile, type DemoRun, type DemoRunTask, type RequestResultDefinition, type WorkbenchNode, type WorkbenchRequestType, type WorkbenchTaskType } from './types'
import { useMemoryQuery } from '../../shared/cache/useMemoryQuery'
import { LocalProgramPanel } from './local-programs/LocalProgramPanel'

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

function profileTaskTypeId(profile: BatchProfile) {
  return profile.task_type_id || ''
}

function normalizeRequestTypeLabel(value: string) {
  return value.normalize('NFKC').trim().replace(/^#+/, '').replace(/\s+/g, '-').replace(/[,#]/g, '').slice(0, 24)
}

function compositionModeFor(nodes: WorkbenchNode[]): CompositionMode {
  if (nodes.length < 2) return 'sequence'
  return nodes.every((node, index) => index === 0
    ? node.depends_on.length === 0
    : node.depends_on.length === 1 && node.depends_on[0] === nodes[index - 1].node_key)
    ? 'sequence'
    : 'parallel'
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

const loadWorkbenchExecutionCatalog = async () => {
  const [profiles, tasks] = await Promise.all([workbenchApi.batchProfiles(), workbenchApi.taskTypes()])
  return { profiles, tasks }
}
const EMPTY_DEMO_RUNS: DemoRun[] = []
const EMPTY_BATCH_PROFILES: BatchProfile[] = []
const EMPTY_TASK_TYPES: WorkbenchTaskType[] = []

export function SimulationWorkbench({ workflows, initialRequestId, currentUserId, createdBy, canExecute, isAdmin, embedded = false, onChanged, onRequestSelected }: {
  workflows: Workflow[]
  initialRequestId: string
  currentUserId: string
  createdBy: string
  canExecute: boolean
  isAdmin: boolean
  /** The request shell already owns context selection in embedded mode. */
  embedded?: boolean
  onChanged: (message: string) => void | Promise<void>
  onRequestSelected: (requestId: string) => void
}) {
  const assigned = useMemo(() => workflows.filter((workflow) => workflow.work_plan), [workflows])
  const [activeRun, setActiveRun] = useState<DemoRun | null>(null)
  const [activeTaskId, setActiveTaskId] = useState('')
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const [selectedWorkItemId, setSelectedWorkItemId] = useState('')
  const [optimisticRun, setOptimisticRun] = useState<{ requestId: string; run: DemoRun } | null>(null)
  const [batchAttempts, setBatchAttempts] = useState<BatchExecutionAttempt[]>([])
  const [loadingBatchAttempts, setLoadingBatchAttempts] = useState(false)
  const [batchAttemptError, setBatchAttemptError] = useState('')
  const [batchRequestKey, setBatchRequestKey] = useState(() => `batch-${crypto.randomUUID()}`)
  const [progressDraft, setProgressDraft] = useState(10)
  const batchAttemptSequence = useRef(0)

  const explicitWorkflow = initialRequestId ? workflows.find((item) => item.request.id === initialRequestId) : undefined
  const requestId = initialRequestId || (assigned[0]?.request.id ?? '')
  const workflow = initialRequestId ? assigned.find((item) => item.request.id === initialRequestId) : assigned.find((item) => item.request.id === requestId)
  const runQuery = useMemoryQuery<DemoRun[]>({
    key: requestId ? `workbench:demo-runs:${requestId}` : 'workbench:demo-runs:none',
    query: useCallback(() => workbenchApi.demoRuns(requestId), [requestId]),
    enabled: Boolean(requestId && workflow),
  })
  const catalogQuery = useMemoryQuery<{ profiles: BatchProfile[]; tasks: WorkbenchTaskType[] }>({ key: 'workbench:execution-catalog', query: loadWorkbenchExecutionCatalog, enabled: Boolean(workflow) })
  const cachedRuns = runQuery.data || EMPTY_DEMO_RUNS
  const runs = useMemo(() => runQuery.isBlocked ? EMPTY_DEMO_RUNS : optimisticRun?.requestId === requestId
    ? [optimisticRun.run, ...cachedRuns.filter((item) => item.id !== optimisticRun.run.id)]
    : cachedRuns, [cachedRuns, optimisticRun, requestId, runQuery.isBlocked])
  useEffect(() => { if (runQuery.isBlocked) setOptimisticRun(null) }, [runQuery.isBlocked])
  const loadingRuns = runQuery.isLoading || runQuery.isRefreshing
  const batchProfiles = catalogQuery.data?.profiles || EMPTY_BATCH_PROFILES
  const taskTypes = catalogQuery.data?.tasks || EMPTY_TASK_TYPES
  const currentItem = workflow?.steps.find((item) => item.status === 'IN_PROGRESS') ?? workflow?.steps.find((item) => item.status === 'READY') ?? null
  const activeRunForScope = activeRun && runs.some((item) => item.id === activeRun.id) ? activeRun : null
  const activeTask = activeRunForScope?.tasks.find((task) => task.id === activeTaskId) ?? activeRunForScope?.tasks[0] ?? null
  const selectedWorkItem = workflow?.steps.find((item) => item.id === selectedWorkItemId) ?? currentItem
  const selectedTaskType = taskTypes.find((task) => task.id === selectedWorkItem?.task_type_id && task.version === selectedWorkItem?.task_type_version)
  const selectedGuidance = selectedTaskType ? TASK_GUIDANCE[selectedTaskType.kind] : undefined
  const selectedExecution = selectedTaskType ? TASK_EXECUTION[selectedTaskType.kind] ?? FALLBACK_TASK_EXECUTION : FALLBACK_TASK_EXECUTION
  const selectedTaskTypeId = selectedWorkItem?.task_type_id ?? ''
  const compatibleBatchProfiles = batchProfiles.filter((profile) => selectedTaskTypeId && profile.is_active && profileTaskTypeId(profile) === selectedTaskTypeId && Number(profile.task_type_version || 1) === Number(selectedWorkItem?.task_type_version || 1))
  const selectedBatchProfile = compatibleBatchProfiles[0]

  const selectedIsCurrent = selectedWorkItem?.id === currentItem?.id
  const canOperateCurrent = Boolean(canExecute && currentItem?.owner_user_id && (isAdmin || currentItem.owner_user_id === currentUserId))
  const canOperateSelected = Boolean(canExecute && selectedWorkItem?.owner_user_id && (isAdmin || selectedWorkItem.owner_user_id === currentUserId))
  const progressIsValid = Boolean(selectedWorkItem && Number.isFinite(progressDraft) && progressDraft > selectedWorkItem.progress && progressDraft <= 99)
  const taskActionDisabled = !canOperateSelected || working || !selectedIsCurrent || !selectedWorkItem || !['READY', 'IN_PROGRESS'].includes(selectedWorkItem.status)
  const taskActionHelp = !canExecute
    ? '실행 권한이 없어 작업을 수행할 수 없습니다.'
    : selectedWorkItem && !isAdmin && selectedWorkItem.owner_user_id !== currentUserId
      ? `작업 담당자(${selectedWorkItem.owner})만 실행할 수 있습니다.`
    : !selectedIsCurrent
      ? '현재 순서의 작업을 완료한 뒤 실행할 수 있습니다.'
      : selectedWorkItem?.status === 'READY'
        ? '작업을 시작하면 진행도와 데모 실행 기능이 열립니다.'
        : selectedWorkItem?.status === 'IN_PROGRESS'
          ? '데모 결과를 생성하고 이 작업을 완료합니다.'
          : `현재 상태(${runStatusLabel(selectedWorkItem?.status ?? '')})에서는 실행할 수 없습니다.`

  const loadRuns = () => { if (workflow && requestId) runQuery.retry() }

  const loadBatchAttempts = async (workItemId = selectedWorkItem?.id ?? '') => {
    const sequence = ++batchAttemptSequence.current
    if (!workItemId) { setBatchAttempts([]); setBatchAttemptError(''); setLoadingBatchAttempts(false); return }
    setBatchAttempts([])
    setLoadingBatchAttempts(true); setBatchAttemptError('')
    try {
      const items = await workbenchApi.batchAttempts(workItemId)
      if (sequence === batchAttemptSequence.current) setBatchAttempts(items)
    } catch (reason) {
      if (sequence === batchAttemptSequence.current) { setBatchAttempts([]); setBatchAttemptError(friendlyWorkbenchError(reason)) }
    } finally {
      if (sequence === batchAttemptSequence.current) setLoadingBatchAttempts(false)
    }
  }

  useEffect(() => {
    setOptimisticRun(null)
    setActiveRun(null)
    setActiveTaskId('')
  }, [requestId])
  useEffect(() => {
    if (runQuery.error) setError(friendlyWorkbenchError(runQuery.error))
  }, [runQuery.error])
  useEffect(() => {
    setActiveRun((current) => current && runs.some((item) => item.id === current.id) ? current : runs[0] ?? null)
    setActiveTaskId((current) => current || runs[0]?.tasks[0]?.id || '')
  }, [requestId, runs])
  useEffect(() => { void loadBatchAttempts(selectedWorkItem?.id) }, [selectedWorkItem?.id])
  useEffect(() => {
    if (catalogQuery.error) setError(friendlyWorkbenchError(catalogQuery.error))
  }, [catalogQuery.error])
  useEffect(() => {
    if (!selectedWorkItem) return
    setSelectedWorkItemId(selectedWorkItem.id)
    setProgressDraft(Math.min(99, Math.max(1, Number(selectedWorkItem.progress || 0) + 10)))
  }, [selectedWorkItem?.id, selectedWorkItem?.progress])
  useEffect(() => { setBatchRequestKey(`batch-${crypto.randomUUID()}`) }, [selectedWorkItem?.id, selectedBatchProfile?.id])

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
      setOptimisticRun({ requestId, run })
      setActiveRun(run); setActiveTaskId(run.tasks[0]?.id ?? '')
      await onChanged(`${currentItem.name} 작업을 완료했습니다. 다음 작업은 시작 대기 상태입니다.`)
      runQuery.retry()
      setSelectedWorkItemId('')
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
    if (!selectedWorkItem || selectedWorkItem.status !== 'IN_PROGRESS' || !selectedBatchProfile || !canOperateSelected) return
    const workItemId = selectedWorkItem.id
    setWorking(true); setError('')
    try {
      const run = await workbenchApi.dispatchBatch(workItemId, createdBy, batchRequestKey)
      setOptimisticRun({ requestId, run })
      setActiveRun(run); setActiveTaskId(run.tasks[0]?.id ?? '')
      setBatchRequestKey(`batch-${crypto.randomUUID()}`)
      await onChanged(`${selectedWorkItem.name} 배치 구성을 검증하고 DEMO_ONLY 실행 기록을 생성했습니다.`)
      runQuery.retry()
    } catch (reason) { setError(friendlyWorkbenchError(reason)) }
    finally { await loadBatchAttempts(workItemId); setWorking(false) }
  }

  const executeSelectedTask = async () => {
    if (!selectedIsCurrent || !selectedWorkItem) return
    if (selectedWorkItem.status === 'READY') await startCurrent()
    else if (selectedWorkItem.status === 'IN_PROGRESS') await completeCurrent()
  }

  if (!workflow) {
    const title = explicitWorkflow?.request.title ?? '선택한 의뢰'
    const currentValue = initialRequestId || ''
    return <div className="workbench-page assigned-only" data-testid="simulation-workbench"><section className="workbench-state workbench-no-plan-state" style={{ minHeight: '260px', flexDirection: 'column', padding: '32px', color: 'var(--focus-muted)', background: 'transparent' }}><ClipboardList aria-hidden="true" />{!embedded && <h1 style={{ margin: 0, color: 'var(--focus-navy)', fontSize: '24px' }}>{title}</h1>}<p>{explicitWorkflow ? '이 의뢰에는 배정된 작업 계획이 없어 실행할 수 없습니다.' : '선택한 의뢰를 확인할 수 없습니다.'}</p>{!embedded && <label><span>배정 작업 대상 의뢰</span><select aria-label="배정 작업 대상 의뢰" value={currentValue} onChange={(event) => { if (event.target.value) changeRequest(event.target.value) }}><option value={currentValue}>{explicitWorkflow ? `${title} · 작업 계획 없음` : '선택한 의뢰 확인 필요'}</option>{assigned.map((item) => <option key={item.request.id} value={item.request.id}>{item.request.project_name} / {item.request.title}</option>)}</select></label>}</section></div>
  }

  const firstReady = currentItem?.status === 'READY' && currentItem.sequence_no === 1 && workflow.completed_count === 0
  return <div className="workbench-page assigned-only" data-testid="simulation-workbench">
    {!embedded && <section className="workbench-hero operator"><div><span className="demo-only-badge"><ShieldCheck /> 작업 실행</span><h1>배정 작업 실행</h1><p>작업 계획에 따라 현재 작업을 시작하고 완료합니다.</p></div><details className="workbench-source workbench-collapsible"><summary><span><strong>작업 원칙</strong><small>시작 → 데모 수행 → 완료</small></span><ChevronRight aria-hidden="true" /></summary><p>완료 후 다음 작업은 시작 대기 상태로 열립니다.</p></details></section>}
    {error && <section className="workbench-service-error" role="alert"><AlertTriangle /><div><strong>작업 요청을 처리하지 못했습니다.</strong><p>{error}</p></div></section>}

    {!embedded && <section className="assigned-request-card">
      <header><div><span>배정 의뢰</span><h2>{workflow.request.title}</h2><p>{workflow.request.project_name} · 담당 {workflow.request.owner}</p></div>{!embedded && <label><span>대상 의뢰</span><select aria-label="배정 작업 대상 의뢰" value={workflow.request.id} onChange={(event) => changeRequest(event.target.value)}>{assigned.map((item) => <option key={item.request.id} value={item.request.id}>{item.request.project_name} / {item.request.title}</option>)}</select></label>}</header>
      <details className="assigned-request-meta workbench-collapsible"><summary><span><strong>의뢰 정보</strong><small>시나리오·요청자·완료 작업</small></span><ChevronRight aria-hidden="true" /></summary><div className="assigned-request-meta-content"><div><span>시나리오</span><strong>{workflow.work_plan?.scenario_name}</strong></div><div><span>접수 출처</span><strong>{workflow.work_plan?.source_type === 'EXTERNAL_SYSTEM' ? '외부 시스템' : '부서장 지시'}</strong><small>{workflow.work_plan?.source_reference}</small></div><div><span>요청자</span><strong>{workflow.work_plan?.requested_by}</strong></div><div><span>완료 작업</span><strong>{workflow.completed_count ?? 0} / {workflow.total_count ?? workflow.steps.length}</strong></div><div className="assigned-progress"><span>전체 진행률</span><strong>{workflow.progress}%</strong><i><b style={{ width: `${workflow.progress}%` }} /></i></div></div></details>
    </section>}

    <section className="assigned-work-list"><header><div><span>작업 순서</span><h2>배정 작업 순서</h2></div><b>{currentItem ? `현재 · ${currentItem.name}` : '모든 작업 완료'}</b></header><div>{workflow.steps.map((item) => {
      const isCurrent = item.id === currentItem?.id
      const statusLabel = { READY: '시작 대기', IN_PROGRESS: '진행 중', COMPLETED: '완료', WAITING: '선행 작업 대기', BLOCKED: '차단', FAILED: '실패' }[item.status]
      return <article key={item.id} role="button" tabIndex={0} aria-pressed={selectedWorkItem?.id === item.id} className={`${item.status.toLowerCase()} ${isCurrent ? 'current' : ''} ${selectedWorkItem?.id === item.id ? 'selected' : ''}`} onClick={() => setSelectedWorkItemId(item.id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelectedWorkItemId(item.id) } }}><i>{item.status === 'COMPLETED' ? <Check /> : item.sequence_no}</i><div><span>{item.display_name ?? item.task_type_id} {item.task_type_version ? `· v${item.task_type_version}` : ''}</span><strong>{item.name}</strong><small>{item.started_at ? `${item.started_by} 시작 · ${new Date(item.started_at).toLocaleString('ko-KR')}` : '아직 시작하지 않음'}</small></div><b>{statusLabel} · {item.progress}%</b></article>
    })}</div></section>

    {selectedWorkItem && <section className="work-item-detail" data-testid="work-item-detail" aria-busy={working}>
      <header><div><span>작업 상세</span><h2>{selectedWorkItem.name}</h2><p>{selectedWorkItem.display_name ?? selectedWorkItem.task_type_id} {selectedWorkItem.task_type_version ? `· v${selectedWorkItem.task_type_version}` : ''}</p></div><b>{runStatusLabel(selectedWorkItem.status)} · {selectedWorkItem.progress}%</b></header>
      {selectedGuidance && <details className="work-item-guidance-disclosure workbench-collapsible"><summary><span><strong>업무 안내</strong><small>목적·필요 입력·예상 결과</small></span><ChevronRight aria-hidden="true" /></summary><div className="work-item-guidance"><div><span>업무 목적</span><strong>{selectedGuidance.purpose}</strong></div><div><span>필요 입력</span><strong>{selectedGuidance.requires}</strong></div><div><span>예상 결과</span><strong>{selectedGuidance.result}</strong></div></div></details>}
      <article className={`task-execution-widget group-${selectedGuidance?.group === 'PhysicsAI' ? 'ai' : selectedGuidance?.group === '해석 실행' ? 'run' : selectedGuidance?.group === '결과 활용' ? 'result' : 'prepare'}`} data-testid={`task-execution-widget-${selectedTaskType?.kind ?? 'unknown'}`}>
        <div className="task-execution-mark"><Activity aria-hidden="true" /></div>
        <div><span>작업 시작 · 데모 예제</span><small className="demo-only-badge">DEMO ONLY · 데모 버튼은 예제 결과를 생성합니다.</small><h3>{selectedExecution.title}</h3><p>{selectedExecution.description}</p><small id="task-action-help">{taskActionHelp}</small></div>
        <div data-testid={selectedWorkItem.status === 'READY' ? 'start-current-work' : selectedWorkItem.status === 'IN_PROGRESS' ? 'complete-current-work' : undefined} className="task-execution-action-wrap"><button data-testid="execute-selected-task" aria-describedby="task-action-help" disabled={taskActionDisabled} onClick={() => void executeSelectedTask()}>
          {working ? <LoaderCircle className="spin" aria-hidden="true" /> : selectedWorkItem.status === 'IN_PROGRESS' ? <FlaskConical aria-hidden="true" /> : <Play aria-hidden="true" />}
          {selectedWorkItem.status === 'IN_PROGRESS' ? selectedExecution.runLabel : selectedExecution.startLabel}
        </button></div>
      </article>
      <LocalProgramPanel key={currentUserId} currentUserId={currentUserId} currentUserName={createdBy} requestId={requestId} selectedWorkItem={selectedWorkItem} taskName={selectedWorkItem.name} canExecute={canExecute} isAdmin={isAdmin} isCurrent={selectedIsCurrent} />
      <div className="work-item-detail-grid">
        <details className="progress-update-card workbench-collapsible">
          <summary><span><strong>진행도 업데이트</strong><small>수동 진행 상태 갱신</small></span><ChevronRight aria-hidden="true" /></summary><div className="progress-update-body">
          <h3>진행도 업데이트</h3><p>진행 중 작업의 진행도만 이전 값보다 크게 갱신할 수 있습니다.</p>
          <div className="progress-current"><span>현재 진행도</span><strong>{selectedWorkItem.progress}%</strong><progress aria-label={`${selectedWorkItem.name} 현재 진행도`} max="100" value={selectedWorkItem.progress}>{selectedWorkItem.progress}%</progress></div>
          <label><span>새 진행도</span><input aria-label="작업 진행도" aria-describedby="progress-update-help" aria-invalid={selectedWorkItem.status === 'IN_PROGRESS' && !progressIsValid} type="number" min={Math.min(99, selectedWorkItem.progress + 1)} max="99" value={progressDraft} disabled={selectedWorkItem.status !== 'IN_PROGRESS'} onChange={(event) => setProgressDraft(event.target.value === '' ? Number.NaN : Number(event.target.value))}/><b>%</b></label>
          <small id="progress-update-help" role="status">{!canExecute ? '진행도를 수정할 실행 권한이 없습니다.' : !isAdmin && selectedWorkItem.owner_user_id !== currentUserId ? `작업 담당자(${selectedWorkItem.owner})만 진행도를 수정할 수 있습니다.` : selectedWorkItem.status !== 'IN_PROGRESS' ? '진행 중인 작업에서만 수정할 수 있습니다.' : progressIsValid ? `${selectedWorkItem.progress + 1}~99 사이의 값을 저장할 수 있습니다.` : `현재 값 ${selectedWorkItem.progress}%보다 큰 ${Math.min(99, selectedWorkItem.progress + 1)}~99 사이의 값을 입력하세요.`}</small>
          <button disabled={!canOperateSelected || working || selectedWorkItem.status !== 'IN_PROGRESS' || !progressIsValid} onClick={() => void updateSelectedProgress()}><RefreshCw aria-hidden="true" /> 진행도 저장</button>
          </div></details>
        <details className="batch-execution-card workbench-collapsible" open={selectedWorkItem.status === 'IN_PROGRESS'}>
          <summary><span><strong>배치 실행 구성</strong><small>실행 경로와 기록 설정</small></span><ChevronRight aria-hidden="true" /></summary><p>경로와 명령은 기록·미리보기 전용입니다. 서버는 외부 solver 프로세스를 실행하지 않습니다.</p>
          {selectedBatchProfile ? <><div className="batch-linked-definition"><span>자동 연결된 배치 실행 정의</span><strong>{selectedBatchProfile.name} · v{selectedBatchProfile.version}</strong><small>현재 수행 작업 유형과 1:1로 연결됨 · 시스템 ID {selectedBatchProfile.id}</small></div>{selectedBatchProfile.solver_path ? <pre aria-label="배치 명령 미리보기"><code>{`"${selectedBatchProfile.solver_path}" ${selectedBatchProfile.arguments_template}`}</code></pre> : <p className="batch-profile-redacted" role="note">이 계정에는 실행 경로와 명령 미리보기가 표시되지 않습니다.</p>}</> : <p className="batch-profile-empty" role="status">이 작업 유형에 연결된 활성 배치 실행 정의가 없습니다. 관리자에게 배치 실행 정의 등록을 요청하세요.</p>}
          <small id="batch-action-help" role="status">{!canExecute ? '실행 권한이 필요합니다.' : !isAdmin && selectedWorkItem.owner_user_id !== currentUserId ? `작업 담당자(${selectedWorkItem.owner})만 배치 기록을 생성할 수 있습니다.` : selectedWorkItem.status !== 'IN_PROGRESS' ? '진행 중인 현재 작업에서만 배치 기록을 생성할 수 있습니다.' : !selectedBatchProfile ? '호환되는 활성 프로필이 필요합니다.' : 'DEMO_ONLY 배치 기록을 생성할 준비가 되었습니다.'}</small>
          <button className="batch-dispatch-button" aria-describedby="batch-action-help" disabled={!canOperateSelected || working || selectedWorkItem.status !== 'IN_PROGRESS' || !selectedBatchProfile} onClick={() => void dispatchSelectedBatch()}><Play aria-hidden="true" /> 배치 실행 기록 생성</button>
        </details>
      </div>
      <details className="batch-attempt-history workbench-collapsible" open={Boolean(batchAttemptError || loadingBatchAttempts || batchAttempts.length)}>
        <summary><span><strong>배치 실행 상태·이벤트</strong><small>선택한 작업의 실행 기록</small></span><ChevronRight aria-hidden="true" /></summary>
        <section aria-labelledby="batch-attempt-history-title" aria-live="polite">
        <header><div><span>배치 실행 기록</span><h3 id="batch-attempt-history-title">상태·이벤트</h3><p>preflight부터 완료·거부까지 모든 제어 plane 기록을 표시합니다.</p></div><button aria-label="배치 실행 상태 새로고침" disabled={loadingBatchAttempts} onClick={() => void loadBatchAttempts()}><RefreshCw className={loadingBatchAttempts ? 'spin' : ''} aria-hidden="true" /></button></header>
        {batchAttemptError ? <div className="batch-attempt-error" role="alert"><AlertTriangle aria-hidden="true" /><span>{batchAttemptError}</span></div> : loadingBatchAttempts ? <div className="batch-attempt-empty"><LoaderCircle className="spin" aria-hidden="true" /> 배치 실행 기록을 불러오는 중입니다…</div> : batchAttempts.length === 0 ? <div className="batch-attempt-empty"><ClipboardList aria-hidden="true" /> 이 작업의 배치 실행 기록이 아직 없습니다.</div> : <div className="batch-attempt-list">{batchAttempts.map((attempt) => <article key={attempt.id} className={`status-${attempt.status.toLowerCase()}`}>
          <header><div><span>{attempt.execution_mode} · {attempt.batch_profile_id} v{attempt.batch_profile_version}</span><strong>{attempt.last_message}</strong><small>{attempt.id} · <time dateTime={attempt.created_at}>{new Date(attempt.created_at).toLocaleString('ko-KR')}</time></small></div><b>{attempt.status} · {attempt.progress}%</b></header>
          <progress aria-label={`${attempt.batch_profile_id} 배치 실행 진행도`} max="100" value={attempt.progress}>{attempt.progress}%</progress>
          {attempt.profile_snapshot?.solver_path && attempt.command_preview ? <pre aria-label="실행 시점 배치 명령 snapshot"><code>{attempt.command_preview}</code></pre> : null}
          <ol aria-label="배치 상태 이벤트">{attempt.events.map((event) => <li key={event.id} className={`level-${event.level.toLowerCase()}`}><i aria-hidden="true" /><span><strong>{event.event_type} · {event.progress}%</strong><small>{event.message}</small></span><time dateTime={event.occurred_at}>{new Date(event.occurred_at).toLocaleTimeString('ko-KR')}</time></li>)}</ol>
        </article>)}</div>}
        </section>
      </details>
    </section>}

    <details className="workbench-monitor workbench-collapsible" open={Boolean(activeRunForScope)}><summary><span><strong>실행 이력</strong><small>현재 의뢰의 DEMO 결과와 로그</small></span><ChevronRight aria-hidden="true" /></summary><section className="workbench-monitor-content"><div className="workbench-run-list"><header><div><span>실행 이력</span><h2>현재 의뢰의 데모 결과</h2></div><button aria-label="실행 이력 새로고침" disabled={loadingRuns} onClick={() => void loadRuns()}><RefreshCw className={loadingRuns ? 'spin' : ''} /></button></header>{runs.length === 0 ? <p className="workbench-empty">완료한 데모 작업이 없습니다.</p> : runs.map((run) => <button key={run.id} className={activeRunForScope?.id === run.id ? 'active' : ''} onClick={() => { setActiveRun(run); setActiveTaskId(run.tasks[0]?.id ?? '') }}><span><strong>{run.name}</strong><small>{new Date(run.created_at).toLocaleString('ko-KR')}</small></span><b>{runStatusLabel(run.status)} · {run.progress}%</b></button>)}</div><div className="workbench-run-detail">{activeRunForScope ? <><header><div><span>{activeRunForScope.execution_mode}</span><h2>{activeRunForScope.name}</h2><p>Run ID {activeRunForScope.id}</p></div><strong>{runStatusLabel(activeRunForScope.status)} · {activeRunForScope.progress}%</strong></header><div className="workbench-run-progress"><i><b style={{ width: `${activeRunForScope.progress}%` }} /></i><span>갱신 {new Date(activeRunForScope.completed_at || activeRunForScope.created_at).toLocaleString('ko-KR')}</span></div><div className="workbench-run-body"><nav>{activeRunForScope.tasks.map((task) => <button key={task.id} className={activeTask?.id === task.id ? 'active' : ''} onClick={() => setActiveTaskId(task.id)}><Activity /><span><strong>{task.display_name}</strong><small>{runStatusLabel(task.status)} · {task.progress}%</small></span></button>)}</nav>{activeTask && <DemoTaskDetail task={activeTask} />}</div></> : <div className="workbench-empty-detail"><Image /><strong>로그·검증·결과 확인</strong><p>현재 작업을 완료하면 데모 실행 결과가 표시됩니다.</p></div>}</div></section></details>
  </div>
}

export function WorkbenchTypeAdmin() {
  const [adminView, setAdminView] = useState<'request-types' | 'batch-paths'>('request-types')
  const [taskTypes, setTaskTypes] = useState<WorkbenchTaskType[]>([])
  const [requestTypes, setRequestTypes] = useState<WorkbenchRequestType[]>([])
  const [resultDefinition, setResultDefinition] = useState<RequestResultDefinition | null>(null)
  const [resultDefinitionLoading, setResultDefinitionLoading] = useState(false)
  const resultDefinitionRequest = useRef(0)
  const [selectedTaskKeys, setSelectedTaskKeys] = useState<string[]>([])
  const [compositionMode, setCompositionMode] = useState<CompositionMode>('sequence')
  const [typeName, setTypeName] = useState('사용자 정의 해석')
  const [typeDescription, setTypeDescription] = useState('의뢰 수행자가 선택할 수 있는 작업 시나리오입니다.')
  const [matchAnalysisType, setMatchAnalysisType] = useState('')
  const [typeLabels, setTypeLabels] = useState<string[]>([...DEFAULT_REQUEST_TYPE_LABELS])
  const [labelDraft, setLabelDraft] = useState('')
  const [editingTypeId, setEditingTypeId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [batchProfiles, setBatchProfiles] = useState<BatchProfile[]>([])
  const [editingBatchProfileId, setEditingBatchProfileId] = useState('')
  const [batchDraft, setBatchDraft] = useState<Omit<BatchProfile, 'version' | 'created_at' | 'updated_at'>>({ id: '', name: 'Radioss 로컬 배치', solver_path: 'C:\\Altair\\hwsolvers\\radioss.exe', working_directory: 'C:\\Simulation\\runs\\{request_id}', arguments_template: '-i {input} -nt {cores}', environment: { OMP_NUM_THREADS: '{cores}' }, task_type_id: 'hpc-submit', task_type_version: 1, task_type_ids: ['hpc-submit'], is_active: true, updated_by: '관리자' })

  useEffect(() => () => {
    resultDefinitionRequest.current += 1
  }, [])

  useEffect(() => {
    Promise.all([workbenchApi.taskTypes(), workbenchApi.requestTypes(), workbenchApi.batchProfiles(true)])
      .then(([tasks, types, profiles]) => { setTaskTypes(tasks); setRequestTypes(types); setBatchProfiles(profiles) })
      .catch((reason) => setError(friendlyWorkbenchError(reason)))
  }, [])

  const selectedTasks = selectedTaskKeys.flatMap((key) => {
    const task = taskTypes.find((candidate) => `${candidate.id}:${candidate.version}` === key)
    return task ? [task] : []
  })
  const batchTaskTypes = taskTypes.filter((task, index, items) => items.findIndex((candidate) => candidate.id === task.id) === index)
  const toggle = (task: WorkbenchTaskType) => {
    const key = `${task.id}:${task.version}`
    setSelectedTaskKeys((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key])
  }
  const resetTypeDraft = () => {
    setEditingTypeId(null)
    setTypeName('사용자 정의 해석')
    setTypeDescription('의뢰 수행자가 선택할 수 있는 작업 시나리오입니다.')
    setMatchAnalysisType('')
    setTypeLabels([...DEFAULT_REQUEST_TYPE_LABELS])
    setLabelDraft('')
    setSelectedTaskKeys([])
    setResultDefinition(null)
    resultDefinitionRequest.current += 1
    setResultDefinitionLoading(false)
    setCompositionMode('sequence')
  }
  const addLabel = () => {
    const clean = normalizeRequestTypeLabel(labelDraft)
    if (!clean || typeLabels.some((label) => label.localeCompare(clean, undefined, { sensitivity: 'accent' }) === 0)) return
    setTypeLabels((current) => [...current, clean])
    setLabelDraft('')
  }
  const editType = (requestType: WorkbenchRequestType) => {
    setEditingTypeId(requestType.id)
    setTypeName(requestType.display_name)
    setTypeDescription(requestType.description)
    setMatchAnalysisType(typeof requestType.match_rules.analysis_type === 'string' ? requestType.match_rules.analysis_type : '')
    setTypeLabels(requestTypeLabels(requestType))
    setLabelDraft('')
    setCompositionMode(compositionModeFor(requestType.default_workflow.nodes))
    setSelectedTaskKeys(requestType.default_workflow.nodes.map((node) => `${node.task_type_id}:${node.task_type_version}`))
    setResultDefinition(null)
    const requestToken = resultDefinitionRequest.current + 1
    resultDefinitionRequest.current = requestToken
    setResultDefinitionLoading(true)
    void workbenchApi.resultProfile(requestType.id, requestType.version)
      .then((profile) => { if (resultDefinitionRequest.current === requestToken) setResultDefinition(requestResultDefinitionFromProfile(profile)) })
      .catch((reason) => { if (resultDefinitionRequest.current === requestToken) setError(friendlyWorkbenchError(reason)) })
      .finally(() => { if (resultDefinitionRequest.current === requestToken) setResultDefinitionLoading(false) })
    document.querySelector('.workbench-admin-form')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  const deactivateType = async (requestType: WorkbenchRequestType) => {
    if (!window.confirm(`${requestType.display_name} 작업 유형을 삭제할까요? 과거 의뢰 이력은 유지되고 신규 접수에서만 숨겨집니다.`)) return
    setSaving(true); setError(''); setNotice('')
    try {
      await workbenchApi.deactivateRequestType(requestType.id)
      setRequestTypes((current) => current.filter((item) => item.id !== requestType.id))
      if (editingTypeId === requestType.id) resetTypeDraft()
      setNotice(`${requestType.display_name} 작업 유형을 삭제했습니다. 과거 버전 이력은 보존됩니다.`)
    } catch (reason) { setError(friendlyWorkbenchError(reason)) }
    finally { setSaving(false) }
  }
  const saveType = async () => {
    if (typeName.trim().length < 2 || !selectedTasks.length || !typeLabels.length) return
    const resultDefinitionError = requestResultDefinitionValidation(resultDefinition, selectedTasks)
    if (resultDefinitionLoading || resultDefinitionError) {
      setError(resultDefinitionError || '기존 요청 결과 구성을 불러오는 동안 저장할 수 없습니다.')
      return
    }

    setSaving(true); setError(''); setNotice('')
    try {
      resultDefinitionRequest.current += 1
      const payload = { display_name: typeName.trim(), description: typeDescription.trim(),
        allowed_task_types: selectedTasks.map((task) => ({ id: task.id, version: task.version })),
        default_workflow: { nodes: nodesFor(selectedTasks, compositionMode) },
        match_rules: { ...(matchAnalysisType.trim() ? { analysis_type: matchAnalysisType.trim() } : {}), labels: typeLabels },
        result_definition: resultDefinition?.widgets.length ? {
          page_name: resultDefinition.page_name?.trim() || undefined,
          page_description: resultDefinition.page_description?.trim() || '',
          widgets: resultDefinition.widgets,
        } : undefined,
        is_active: true }
      const saved = editingTypeId ? await workbenchApi.updateRequestType(editingTypeId, payload) : await workbenchApi.createRequestType(payload)
      setRequestTypes((current) => [saved, ...current.filter((item) => item.id !== saved.id)])
      setNotice(`${saved.display_name} v${saved.version}을 저장했습니다. 시스템 ID는 ${saved.id}입니다.`)
      resetTypeDraft()
    } catch (reason) { setError(friendlyWorkbenchError(reason)) } finally { setSaving(false) }
  }

  const resetBatchDraft = () => {
    const firstTask = batchTaskTypes[0]
    setEditingBatchProfileId('')
    setBatchDraft({ ...batchDraft, id: '', task_type_id: firstTask?.id || '', task_type_version: firstTask?.version || 1, task_type_ids: firstTask ? [firstTask.id] : [] })
  }

  const saveBatchProfile = async () => {
    const taskTypeId = batchDraft.task_type_id || batchDraft.task_type_ids[0] || ''
    if (!batchDraft.name || !batchDraft.solver_path || !batchDraft.working_directory || !taskTypeId) return
    setSaving(true); setError(''); setNotice('')
    try {
      const { id: _systemId, migration_required: _migrationRequired, ...draftWithoutId } = batchDraft
      const payload = { ...draftWithoutId, task_type_id: taskTypeId, task_type_version: Number(batchDraft.task_type_version || 1), task_type_ids: [taskTypeId] }
      const saved = editingBatchProfileId ? await workbenchApi.saveBatchProfile(editingBatchProfileId, payload) : await workbenchApi.createBatchProfile(payload)
      setBatchProfiles((items) => [saved, ...items.filter((item) => item.id !== saved.id)])
      setEditingBatchProfileId(saved.id)
      setBatchDraft((current) => ({ ...current, ...saved, task_type_id: saved.task_type_id, task_type_version: saved.task_type_version, task_type_ids: [saved.task_type_id || taskTypeId] }))
      setNotice(`${saved.name} 배치 실행 정의 v${saved.version}을 저장했습니다. 시스템 ID는 ${saved.id}입니다.`)
    } catch (reason) { setError(friendlyWorkbenchError(reason)) }
    finally { setSaving(false) }
  }
  const editBatchProfile = (profile: BatchProfile) => {
    const taskTypeId = profileTaskTypeId(profile)
    setEditingBatchProfileId(profile.id)
    setBatchDraft({ id: profile.id, name: profile.name, solver_path: profile.solver_path, working_directory: profile.working_directory, arguments_template: profile.arguments_template, environment: { ...profile.environment }, task_type_id: taskTypeId, task_type_version: Number(profile.task_type_version || 1), task_type_ids: taskTypeId ? [taskTypeId] : [], migration_required: profile.migration_required, is_active: profile.is_active, updated_by: '관리자' })
  }
  const deactivateBatchProfile = async (profile: BatchProfile) => {
    if (!window.confirm(`${profile.name} 배치 실행 정의를 비활성화할까요? 기존 실행 이력은 유지됩니다.`)) return
    setSaving(true); setError('')
    try {
      await workbenchApi.deactivateBatchProfile(profile.id)
      setBatchProfiles((items) => items.map((item) => item.id === profile.id ? { ...item, is_active: false } : item))
      setNotice(`${profile.name} 배치 실행 정의를 비활성화했습니다.`)
    } catch (reason) { setError(friendlyWorkbenchError(reason)) } finally { setSaving(false) }
  }


  return <div className="workbench-admin-page" data-testid="workbench-type-admin">
    <section className="workbench-admin-hero"><div><span><Settings2 /> ADMIN ONLY</span><h1>작업 유형 관리</h1><p>수행자에게 보여 줄 실행 시나리오와 라벨, 허용 작업, 기본 순서를 불변 버전으로 정의합니다.</p></div><aside><strong>{requestTypes.length}</strong><span>활성 작업 유형</span></aside></section>
    {error && <div className="workbench-service-error" role="alert"><AlertTriangle aria-hidden="true" /><div><strong>관리 화면을 불러오지 못했습니다.</strong><p>{error}</p></div></div>}
    {notice && <div className="workbench-admin-notice" role="status" aria-live="polite"><Check aria-hidden="true" /> {notice}</div>}
    <nav className="workbench-admin-tabs" role="tablist" aria-label="작업 유형 관리 내부 창">
      <button type="button" id="request-types-tab" role="tab" aria-selected={adminView === 'request-types'} aria-controls="request-types-panel" tabIndex={adminView === 'request-types' ? 0 : -1} onClick={() => setAdminView('request-types')} onKeyDown={(event) => { if (event.key === 'ArrowRight' || event.key === 'ArrowDown') { event.preventDefault(); setAdminView('batch-paths'); (event.currentTarget.nextElementSibling as HTMLButtonElement | null)?.focus() } }}><ClipboardList aria-hidden="true" /><span><strong>작업 유형</strong><small>시나리오·라벨·기본 작업 순서</small></span></button>
      <button type="button" id="batch-paths-tab" role="tab" aria-selected={adminView === 'batch-paths'} aria-controls="batch-paths-panel" tabIndex={adminView === 'batch-paths' ? 0 : -1} onClick={() => setAdminView('batch-paths')} onKeyDown={(event) => { if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') { event.preventDefault(); setAdminView('request-types'); (event.currentTarget.previousElementSibling as HTMLButtonElement | null)?.focus() } }}><Settings2 aria-hidden="true" /><span><strong>배치 경로 정의</strong><small>HyperStudy 스타일 실행 프로필</small></span></button>
    </nav>
    {adminView === 'request-types' && <div id="request-types-panel" className="workbench-admin-panel" role="tabpanel" aria-labelledby="request-types-tab" tabIndex={0}>
    <section className="workbench-admin-layout">
      <form className="workbench-admin-form" aria-labelledby="request-type-form-heading" onSubmit={(event) => { event.preventDefault(); void saveType() }}>
        <header>
          <div><span>{editingTypeId ? 'EDIT AS NEW VERSION' : 'NEW IMMUTABLE VERSION'}</span><h2 id="request-type-form-heading">{editingTypeId ? '작업 유형 편집' : '새 작업 유형 작성'}</h2></div>
          {editingTypeId && <button type="button" className="workbench-edit-cancel" onClick={resetTypeDraft}><X aria-hidden="true" /> 편집 취소</button>}
        </header>
        <label htmlFor="request-type-name"><span>수행자에게 보일 이름</span><input id="request-type-name" name="requestTypeName" required minLength={2} maxLength={120} aria-label="관리 유형 표시 이름" value={typeName} onChange={(event) => setTypeName(event.target.value)} /></label>
        <label htmlFor="request-type-description"><span>시나리오 설명</span><textarea id="request-type-description" name="requestTypeDescription" maxLength={500} aria-label="관리 유형 설명" value={typeDescription} onChange={(event) => setTypeDescription(event.target.value)} /></label>
        <div className="workbench-label-editor">
          <label htmlFor="request-type-label">작업 유형 라벨 *</label>
          <div className="workbench-label-chips" aria-label="선택된 작업 유형 라벨">{typeLabels.map((label) => <span key={label}><Tag aria-hidden="true" />#{label}<button type="button" aria-label={`${label} 라벨 삭제`} disabled={typeLabels.length === 1} onClick={() => setTypeLabels((current) => current.filter((item) => item !== label))}><X aria-hidden="true" /></button></span>)}</div>
          <div className="workbench-label-input-row"><input id="request-type-label" value={labelDraft} maxLength={24} onChange={(event) => setLabelDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ',') { event.preventDefault(); addLabel() } }} placeholder="라벨 입력 후 Enter (예: 충돌해석)" /><button type="button" onClick={addLabel} disabled={!normalizeRequestTypeLabel(labelDraft)}>라벨 추가</button></div>
          <small>최소 1개 · 기본값 #SPDM, #부서 · 라벨은 의뢰 접수 필터에 바로 표시됩니다.</small>
        </div>
        <label htmlFor="request-type-analysis"><span>자동 추천 분석 유형</span><input id="request-type-analysis" name="requestTypeAnalysis" aria-label="관리 자동 추천 분석 유형" value={matchAnalysisType} onChange={(event) => setMatchAnalysisType(event.target.value)} placeholder="예: DROP · 비워두면 수동 선택" /></label>
        <fieldset className="workbench-composition-options"><legend>기본 실행 방식</legend><label><input type="radio" name="compositionMode" value="sequence" checked={compositionMode === 'sequence'} onChange={() => setCompositionMode('sequence')} /><span><strong>순차 실행</strong><small>선택한 순서대로, 이전 작업 완료 후 다음 작업을 시작합니다.</small></span></label><label><input type="radio" name="compositionMode" value="parallel" checked={compositionMode === 'parallel'} onChange={() => setCompositionMode('parallel')} /><span><strong>독립·병렬 실행</strong><small>선행 관계 없이 선택한 작업을 각각 시작할 수 있습니다.</small></span></label></fieldset>
        <div className="workbench-type-plan"><strong>{selectedTasks.length}개 작업 선택</strong><p>{selectedTasks.map((task) => task.display_name).join(' → ') || '오른쪽에서 수행자에게 허용할 작업을 선택하세요.'}</p></div>
        <RequestResultWidgetConfiguration value={resultDefinition} tasks={selectedTasks} onChange={setResultDefinition} />
        <button type="submit" className="workbench-admin-save" disabled={saving || resultDefinitionLoading || Boolean(requestResultDefinitionValidation(resultDefinition, selectedTasks)) || !selectedTasks.length || !typeLabels.length || typeName.trim().length < 2}>{saving ? <LoaderCircle className="spin" aria-hidden="true" /> : <Save aria-hidden="true" />} {editingTypeId ? '변경 내용을 새 버전으로 저장' : '새 작업 유형 저장'}</button>
      </form>
      <div className="workbench-admin-task-picker"><header><span>ALLOWED TASKS</span><h2>수행자에게 허용할 작업</h2><p>카드를 선택한 순서가 기본 실행 순서가 됩니다.</p></header><div>{taskTypes.map((task) => { const selected = selectedTaskKeys.includes(`${task.id}:${task.version}`); const guidance = TASK_GUIDANCE[task.kind]; return <button type="button" key={`${task.id}:${task.version}`} className={selected ? 'selected' : ''} aria-pressed={selected} onClick={() => toggle(task)}><i>{selected ? <Check aria-hidden="true" /> : <Plus aria-hidden="true" />}</i><span><strong>{task.display_name}</strong><small>{guidance?.purpose ?? task.description}</small></span><b>v{task.version}</b></button> })}</div></div>
    </section>
    <section className="workbench-admin-types"><header><span>ACTIVE WORK TYPES</span><h2>정의된 작업 유형</h2></header><div>{requestTypes.map((item) => <article key={`${item.id}:${item.version}`} className={editingTypeId === item.id ? 'editing' : ''}>
      <header><div><strong>{item.display_name}</strong><code>{item.id} · v{item.version}</code></div><b>{item.default_workflow.nodes.length}개 작업</b></header>
      <div className="workbench-type-labels">{requestTypeLabels(item).map((label) => <span key={label}>#{label}</span>)}</div>
      <p>{item.description}</p>
      <div className="workbench-type-steps">{item.default_workflow.nodes.map((node, index) => <span key={node.node_key}>{index + 1}. {taskTypes.find((task) => task.id === node.task_type_id)?.display_name ?? node.task_type_id}</span>)}</div>
      <footer><button type="button" onClick={() => editType(item)}><Pencil aria-hidden="true" /> 편집</button><button type="button" className="danger" disabled={saving} onClick={() => void deactivateType(item)}><Trash2 aria-hidden="true" /> 삭제</button></footer>
    </article>)}</div></section>
    </div>}
    {adminView === 'batch-paths' && <section id="batch-paths-panel" className="batch-profile-admin workbench-admin-panel" role="tabpanel" aria-labelledby="batch-paths-tab" tabIndex={0}><header><div><span>HYPERSTUDY STYLE BATCH PATHS</span><h2>배치 경로 정의</h2><p>수행 작업 유형 버전 하나에 실행 파일·작업 폴더·인수 템플릿·환경 변수를 1:1로 저장합니다. 실제 외부 프로세스는 실행하지 않습니다.</p></div><strong>{batchProfiles.length}개 실행 정의</strong></header><div className="batch-profile-layout"><form aria-label="배치 경로 프로필 편집" onSubmit={(event) => { event.preventDefault(); void saveBatchProfile() }}><div className="batch-form-heading"><strong>{editingBatchProfileId ? '배치 실행 정의 편집' : '새 배치 실행 정의'}</strong>{editingBatchProfileId && <button type="button" onClick={resetBatchDraft}>새 실행 정의</button>}</div><label><span>이름</span><input required aria-label="배치 프로필 이름" value={batchDraft.name} onChange={(event) => setBatchDraft({ ...batchDraft, name: event.target.value })}/></label><label><span>대상 수행 작업 유형 *</span><select required aria-label="대상 수행 작업 유형" value={batchDraft.task_type_id || batchDraft.task_type_ids[0] || ''} disabled={Boolean(editingBatchProfileId && !batchDraft.migration_required)} onChange={(event) => setBatchDraft({ ...batchDraft, task_type_id: event.target.value, task_type_version: Number(batchTaskTypes.find((task) => task.id === event.target.value)?.version || 1), task_type_ids: [event.target.value] })}>{batchTaskTypes.map((task) => { const assignedProfile = batchProfiles.find((profile) => profile.id !== editingBatchProfileId && profileTaskTypeId(profile) === task.id && Number(profile.task_type_version || 1) === task.version); const assigned = Boolean(assignedProfile); return <option key={`${task.id}:${task.version}`} value={task.id} disabled={assigned}>{task.display_name} · v{task.version}{assigned ? ` · ${assignedProfile?.is_active ? '이미 연결됨' : '기존 정의 있음'}` : ''}</option> })}</select><small>수행 작업 유형 버전 하나에 배치 실행 정의 하나만 연결할 수 있습니다.</small></label><label><span>Solver 실행 파일</span><input required aria-label="Solver 실행 파일" value={batchDraft.solver_path} onChange={(event) => setBatchDraft({ ...batchDraft, solver_path: event.target.value })}/></label><label><span>Working directory</span><input required aria-label="배치 작업 폴더" value={batchDraft.working_directory} onChange={(event) => setBatchDraft({ ...batchDraft, working_directory: event.target.value })}/></label><label><span>Arguments template</span><textarea aria-label="배치 인수 템플릿" value={batchDraft.arguments_template} onChange={(event) => setBatchDraft({ ...batchDraft, arguments_template: event.target.value })}/><small>{'{input}, {cores}, {request_id} 같은 자리표시자를 사용할 수 있습니다.'}</small></label><label><span>Environment (KEY=VALUE)</span><textarea aria-label="배치 환경 변수" value={Object.entries(batchDraft.environment).map(([key, value]) => `${key}=${value}`).join('\n')} onChange={(event) => setBatchDraft({ ...batchDraft, environment: Object.fromEntries(event.target.value.split(/\r?\n/).filter(Boolean).map((line) => { const index = line.indexOf('='); return index > 0 ? [line.slice(0, index).trim(), line.slice(index + 1).trim()] : [line.trim(), ''] })) })}/></label><label className="batch-active"><input type="checkbox" checked={batchDraft.is_active} onChange={(event) => setBatchDraft({ ...batchDraft, is_active: event.target.checked })}/><span>활성 실행 정의</span></label><button className="workbench-admin-save" aria-describedby="batch-task-types-help" disabled={saving || !(batchDraft.task_type_id || batchDraft.task_type_ids[0])}>{saving ? <LoaderCircle className="spin" aria-hidden="true" /> : <Save aria-hidden="true" />} 배치 실행 정의 저장</button></form><div className="batch-profile-list" aria-label="저장된 배치 실행 정의">{batchProfiles.map((profile) => <article key={profile.id} className={batchDraft.id === profile.id ? 'active' : ''}><button type="button" aria-pressed={batchDraft.id === profile.id} onClick={() => editBatchProfile(profile)}><span><strong>{profile.name}</strong><code>{profile.id}</code></span><small>{profile.solver_path}</small><small>대상 · {batchTaskTypes.find((task) => task.id === profileTaskTypeId(profile) && task.version === Number(profile.task_type_version || 1))?.display_name ?? (profile.migration_required ? '마이그레이션 필요' : (profileTaskTypeId(profile) || '미지정'))} · v{profile.task_type_version || 1}</small><b>{profile.is_active ? 'ACTIVE' : 'INACTIVE'}</b></button>{profile.is_active && <button type="button" className="danger" aria-label={`${profile.name} 배치 실행 정의 비활성화`} onClick={() => void deactivateBatchProfile(profile)}><Trash2 aria-hidden="true" /></button>}</article>)}</div></div></section>}
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
