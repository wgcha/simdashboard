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
  is_active: boolean
  created_at: string
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
