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
  result_locations: Array<{
    analysis_run_id: string
    variable_key: string
    entity_type: 'NODE' | 'ELEMENT'
    entity_id: string
    x: number
    y: number
    z: number
    time_value: number
    time_unit: string
    method: string
  }>
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

export type WidgetType = 'open_cell_map' | 'kpi' | 'verdict' | 'gauge' | 'edge_bar' | 'summary' | 'time_series' | 'scatter' | 'note' | 'result_table' | 'contour' | 'video' | 'model3d' | 'workflow' | 'chassis_summary' | 'chassis_diagram' | 'chassis_bar' | 'chassis_table'

export type VariableDefinition = { id: string; definition_id: string; variable_key: string; display_name: string; data_type: 'NUMBER' | 'TIME_SERIES'; unit: string; filterable: boolean; source: string; allowed_widgets: string[]; allowed_aggregations: string[]; description: string; threshold?: number | null; analysis_type: string; result_group: 'OPEN_CELL' | 'CHASSIS_REAR' | 'CUSTOM'; has_data: boolean; dashboard_usage_count: number; updated_at: string; updated_by: string }
export type VariableDefinitionInput = { variable_key: string; display_name: string; data_type: 'NUMBER' | 'TIME_SERIES'; unit: string; description: string; filterable: boolean; threshold: number | null; allowed_widgets: string[]; allowed_aggregations: string[]; result_group: 'OPEN_CELL' | 'CHASSIS_REAR' | 'CUSTOM'; updated_by: string }
export type WidgetCatalogItem = { type: WidgetType; label: string; category: string; allowed_data_types: string[]; default_size: [number, number] }
export type DashboardVersion = { dashboard_id: string; version: number; created_by: string; created_at: string; is_valid: boolean }
export type DashboardSummary = { id: string; project_id: string; request_id?: string; load_case_id?: string; name: string; description: string; version: number; updated_at: string }
export type AutomationTemplate = { id: string; load_case_id: string; template_name: string; template_version: string; status: string; executed_at: string; load_case_name: string; analysis_type: string; request_id: string; request_title: string; project_id: string; project_name: string; input: Record<string, unknown>; generated_model: Record<string, unknown> }

export type PortfolioOverview = {
  grain: string
  source: string
  freshness: string | null
  kpis: { load_cases: number; requests: number; in_progress: number; completed_runs: number; failed: number; pass_rate: number | null }
  trend: Array<{ date: string; requests: number; completed: number; failed: number }>
  status_distribution: Array<{ name: string; value: number }>
  type_distribution: Array<{ name: string; value: number }>
  quality_by_type: Array<{ type: string; pass: number; fail: number; no_data: number }>
  records: Array<{ project_id: string; project_name: string; product_name: string; request_id: string; request_title: string; owner: string; request_status: string; requested_at: string; load_case_id: string; load_case_name: string; analysis_type: string; load_case_status: string; run_id: string | null; completed_at: string | null; verdict: string; result_count: number }>
  filter_options: { projects: Array<{ id: string; name: string }>; analysis_types: string[]; statuses: string[] }
}

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
