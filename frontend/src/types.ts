export type ScalarResult = {
  id: string
  variable_key: string
  display_name: string
  value_double: number
  unit: string
  threshold_double: number
  verdict: 'PASS' | 'FAIL'
}

export type TimeSeriesPoint = {
  variable_key: string
  display_name: string
  time_value: number
  value: number
  time_unit: string
  value_unit: string
}

export type Overview = {
  load_case: {
    id: string
    name: string
    analysis_type: string
    project_name: string
    product_name: string
    request_title: string
    request_id: string
    project_id: string
    created_at: string
    parameters: Record<string, string | number | string[]>
  }
  run: string | null
  overall_verdict: 'PASS' | 'FAIL' | 'NO_DATA'
  analysis_verdicts: {
    open_cell: 'PASS' | 'FAIL' | 'NO_DATA'
    chassis_rear: 'PASS' | 'FAIL' | 'NO_DATA'
  }
  threshold: number | null
  product_information: Array<{
    category: string
    name: string
    value_text?: string
    file_path?: string
    metadata?: Record<string, string | number | boolean>
  }>
  scalar_results: ScalarResult[]
  time_series: TimeSeriesPoint[]
  notes: Array<{ id: string; author: string; body: string; created_at: string }>
  media: Array<{ id: string; title: string; file_path: string; mime_type: string }>
  template_execution: {
    template_name: string
    template_version: string
    generated_model: { elements: number; model: string }
  } | null
}

export type Project = {
  id: string
  name: string
  product_name: string
  description: string
}

export type AnalysisRequest = {
  id: string
  project_id: string
  title: string
  status: string
  owner: string
  requested_at: string
}

export type LoadCase = {
  id: string
  request_id: string
  name: string
  analysis_type: 'DROP' | 'SIDE_CLAMP' | string
  status: string
  parameters: Record<string, string | number | string[]>
}

export type QualityThreshold = {
  criterion_key: string
  project_id: string
  analysis_key: string
  label: string
  threshold_double: number
  unit: string
  updated_by: string
  updated_at: string
}

export type WorkflowStep = {
  id: string
  sequence_no: number
  name: string
  status: 'COMPLETED' | 'IN_PROGRESS' | 'WAITING' | 'BLOCKED' | 'FAILED'
  owner: string
  progress: number
  planned_end: string
  is_optional: boolean
  note?: string
}

export type Workflow = {
  request: {
    id: string
    project_id: string
    title: string
    status: string
    owner: string
    due_at: string
    requested_at: string
    project_name: string
    product_name: string
    category: string
    load_case_name: string
  }
  steps: WorkflowStep[]
  progress: number
}

export type WidgetType = 'open_cell_map' | 'verdict' | 'edge_bar' | 'summary' | 'time_series' | 'note' | 'result_table' | 'contour'

export type DashboardWidget = {
  id: string
  type: WidgetType
  title: string
  x: number
  y: number
  w: number
  h: number
  settings?: Record<string, unknown>
}

export type DashboardDefinition = {
  id: string
  name: string
  description: string
  widgets: DashboardWidget[]
  version?: number
  updated_at?: string
}
