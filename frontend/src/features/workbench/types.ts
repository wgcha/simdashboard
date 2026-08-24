import type { WidgetType } from '../../types'

export type { AnalysisTemplateVersion, RequestResultLayout, ResultLayoutSnapshot, ResultProfile } from '../../shared/api/resultLayouts'

export type ResultProfileInput = {
  template_id: string
  template_version: number
  included_widget_ids: string[] | null
  overrides: Record<string, unknown>
  required_data_contracts: string[]
}

export type RequestResultWidget = {
  id: string
  type: WidgetType
  title: string
  variable_key: string | null
  data_contracts: string[]
  required: boolean
}

export type RequestResultDefinition = {
  page_name?: string
  page_description?: string
  widgets: RequestResultWidget[]
}

export type WorkbenchTaskRef = { id: string; version: number }

export type WorkbenchTaskType = {
  id: string
  version: number
  kind: string
  display_name: string
  description: string
  supports_standalone: boolean
  input_artifact_types: string[]
  output_artifact_types: string[]
  parameter_schema: Record<string, unknown>
  demo_artifact_url: string
  is_active: boolean
  created_at: string
}

export type WorkbenchNode = {
  node_key: string
  task_type_id: string
  task_type_version: number
  depends_on: string[]
  display_name?: string
}

export type WorkbenchRequestType = {
  id: string
  version: number
  display_name: string
  description: string
  allowed_task_types: WorkbenchTaskRef[]
  default_workflow: { nodes: WorkbenchNode[] }
  match_rules: Record<string, unknown>
  result_profile?: ResultProfileInput | null
  result_definition?: RequestResultDefinition | null
  is_active: boolean
  created_at: string
}

export const DEFAULT_REQUEST_TYPE_LABELS = ['SPDM', '부서'] as const

export function requestTypeLabels(requestType: Pick<WorkbenchRequestType, 'match_rules'>): string[] {
  const labels = requestType.match_rules.labels
  if (!Array.isArray(labels)) return [...DEFAULT_REQUEST_TYPE_LABELS]
  const cleanLabels = labels.filter((label): label is string => typeof label === 'string' && Boolean(label.trim()))
  return cleanLabels.length ? cleanLabels : [...DEFAULT_REQUEST_TYPE_LABELS]
}

export type RequestTypeResolution = {
  resolution: 'ASSIGNED' | 'RECOMMENDED' | 'REVIEW_REQUIRED' | 'USER_SELECTION'
  request_id: string
  source: 'ADMIN' | 'RULE' | 'USER' | 'DEFAULT' | null
  reason: string
  request_type: WorkbenchRequestType | null
  candidates: WorkbenchRequestType[]
  decided_by: string | null
  decided_at: string | null
}

export type DemoRunEvent = {
  event_index: number
  event_type: string
  level: string
  message: string
  progress: number
  occurred_at: string
}

export type DemoTextArtifact = {
  id: string
  kind: 'LOG' | 'VALIDATION' | 'RESULT'
  label: string
  url: string
  media_type: 'text/plain'
}

export type DemoRunTask = {
  id: string
  node_key: string
  task_type_id: string
  task_type_version: number
  kind: string
  display_name: string
  status: string
  progress: number
  depends_on: string[]
  demo_artifact_url: string
  started_at: string
  completed_at: string
  events: DemoRunEvent[]
  demo_text_artifacts: DemoTextArtifact[]
}

export type DemoRun = {
  id: string
  name: string
  request_id: string | null
  request_type_id: string | null
  request_type_version: number | null
  execution_mode: 'DEMO_ONLY'
  status: string
  progress: number
  created_by: string
  created_at: string
  started_at: string
  completed_at: string
  tasks: DemoRunTask[]
  batch_dispatch?: BatchDispatch | null
}

export type BatchProfile = {
  id: string
  version: number
  name: string
  solver_path: string
  working_directory: string
  arguments_template: string
  environment: Record<string, string>
  /** New 1:1 contract. */
  task_type_id?: string
  task_type_version?: number
  /** Legacy API compatibility. */
  task_type_ids: string[]
  migration_required?: boolean
  is_active: boolean
  updated_by: string
  created_at: string
  updated_at: string
}

export type BatchDispatch = {
  id: string
  work_item_id: string
  workflow_run_id: string
  batch_profile_id: string
  command_preview: string
  status: 'RECORDED_DEMO'
  created_by: string
  created_at: string
  profile_snapshot: Partial<BatchProfile>
}

export type BatchExecutionEvent = {
  id: string
  attempt_id: string
  event_index: number
  event_type: string
  level: 'INFO' | 'WARN' | 'ERROR' | string
  message: string
  progress: number
  occurred_at: string
}

export type BatchExecutionAttempt = {
  id: string
  work_item_id: string
  workflow_run_id: string | null
  batch_profile_id: string
  batch_profile_version: number
  profile_snapshot: BatchProfile
  command_preview: string
  idempotency_key: string
  execution_mode: 'DEMO_ONLY'
  status: 'PREFLIGHT' | 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'REJECTED' | 'FAILED' | 'CANCELLED'
  progress: number
  last_message: string
  created_by: string
  created_at: string
  started_at: string | null
  completed_at: string | null
  events: BatchExecutionEvent[]
}

export type CreateDemoRunInput = {
  name: string
  request_id?: string
  request_type_id?: string
  request_type_version?: number
  execution_mode: 'DEMO_ONLY'
  nodes: WorkbenchNode[]
  created_by: string
}
