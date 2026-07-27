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
  curves: Array<{ id: string; variable_key: string; display_name: string; series_key: string; x_label: string; x_unit: string; y_label: string; y_unit: string; point_count: number }>
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
  media: Array<{ id: string; title: string; file_path: string; mime_type: string; asset_type?: 'IMAGE' | 'VIDEO' | 'MODEL_3D' | string; asset_url?: string; metadata?: Record<string, unknown> }>
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

export type FeatureExample = {
  id: string
  order: number
  category: string
  title: string
  summary: string
  badge: string
  workspace_page: 'portfolio' | 'dashboard' | 'data' | 'schemas' | 'variables' | 'templates' | 'help'
  preferred_view?: 'open_cell' | 'chassis' | 'workflow' | 'compare'
  project_id?: string
  request_id?: string
  load_case_id?: string
  features: string[]
  checks: string[]
  action_hint?: string
  data_profile: { runs: number; scalars: number; series: number; curves: number; media: number; reviews: number }
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

export type VariableDataType = 'NUMBER' | 'TIME_SERIES' | 'FLOAT' | 'INTEGER' | 'TEXT' | 'CURVE' | 'IMAGE' | 'VIDEO' | 'MODEL_3D' | 'VERDICT' | 'STATUS' | 'BOOLEAN'
export type VariableDefinition = { id: string; definition_id: string; variable_key: string; display_name: string; data_type: VariableDataType; unit: string; filterable: boolean; source: string; allowed_widgets: string[]; allowed_aggregations: string[]; description: string; threshold?: number | null; analysis_type: string; result_group: 'OPEN_CELL' | 'CHASSIS_REAR' | 'CUSTOM'; has_data: boolean; dashboard_usage_count: number; updated_at: string; updated_by: string }
export type VariableDefinitionInput = { variable_key: string; display_name: string; data_type: VariableDataType; unit: string; description: string; filterable: boolean; threshold: number | null; allowed_widgets: string[]; allowed_aggregations: string[]; result_group: 'OPEN_CELL' | 'CHASSIS_REAR' | 'CUSTOM'; updated_by: string }
export type WidgetCatalogItem = { type: WidgetType; label: string; category: string; allowed_data_types: string[]; default_size: [number, number] }
export type DashboardVersion = { dashboard_id: string; version: number; created_by: string; created_at: string; is_valid: boolean }
export type ReportSection = 'series' | 'scalar' | 'media'
export type ReportVariablePresentation = 'chart' | 'table' | 'both'
export type ReportVariablePlacement = { variableKey: string; presentation: ReportVariablePresentation; order: number }
export type ReportSlideKind = 'cover' | 'series' | 'scalar' | 'media' | 'custom'
export type ReportElementType = 'title' | 'text' | 'verdict' | 'scalar-card' | 'chart' | 'table' | 'image'
export type ReportElementBinding = {
  source: 'field' | 'variable' | 'series' | 'scalar' | 'media' | 'static'
  key?: string
  variableKey?: string
}
export type ReportElementDefinition = {
  id: string
  type: ReportElementType
  label: string
  x: number
  y: number
  w: number
  h: number
  z: number
  binding?: ReportElementBinding
  style?: {
    fontSize?: number
    color?: string
    fill?: string
    align?: 'left' | 'center' | 'right'
  }
  rules?: {
    visibleWhenData?: boolean
    maxRows?: number
  }
}
export type ReportSlideDefinition = {
  id: string
  name: string
  kind: ReportSlideKind
  repeat: 'none' | 'series-variable' | 'media-item'
  elements: ReportElementDefinition[]
}
export type ReportTemplatePlaceholder = {
  id: string
  slideIndex: number
  shapeName: string
  token: string
  kind: 'variable' | 'field' | 'text' | 'chart' | 'image'
  x: number
  y: number
  w: number
  h: number
}
export type ReportTemplateAsset = {
  id: string
  name: string
  filename: string
  slide_count: number
  definition: {
    slideWidth: number
    slideHeight: number
    placeholders: ReportTemplatePlaceholder[]
  }
  created_at: string
  updated_by: string
}
export type ReportLayoutDefinition = {
  id: string
  name: string
  description: string
  version: number
  coverVariant: 'balanced' | 'executive' | 'evidence'
  accentColor: string
  sectionOrder: ReportSection[]
  variablePlacements: ReportVariablePlacement[]
  includeMedia: boolean
  canvas?: { columns: 32; rows: 18; widthInches: number; heightInches: number }
  slides?: ReportSlideDefinition[]
  templateSource?: 'native' | 'pptx_upload'
  templateAssetId?: string
  templateBindings?: Record<string, string>
}
export type ReportLayout = {
  id: string
  name: string
  description: string
  version: number
  definition: ReportLayoutDefinition
  is_system: boolean
  updated_at: string
  updated_by: string
}
export type ReportLayoutVersion = { layout_id: string; version: number; created_by: string; created_at: string; is_valid: boolean }
export type DashboardSummary = { id: string; project_id: string; request_id?: string; load_case_id?: string; name: string; description: string; version: number; updated_at: string }
export type AutomationTemplate = { id: string; load_case_id: string; template_name: string; template_version: string; status: string; executed_at: string; load_case_name: string; analysis_type: string; request_id: string; request_title: string; project_id: string; project_name: string; input: Record<string, unknown>; generated_model: Record<string, unknown> }
export type ImportSchemaDefinition = { schema_id?: string; version?: number; mappings: Array<Record<string, unknown>>; context_mapping?: { mode: 'folder_levels' | 'manifest'; project_level: number; request_level: number; load_case_level: number; sample_path?: string }; context?: Record<string, unknown>; [key: string]: unknown }
export type ImportSchema = { id: string; name: string; description: string; definition: ImportSchemaDefinition; created_at: string; updated_at: string; updated_by: string }

export type AnalysisRunSummary = {
  id: string
  load_case_id: string
  run_no: number
  solver: string
  status: string
  started_at: string
  completed_at: string
  overall_verdict: 'PASS' | 'FAIL' | 'NO_DATA'
  scalar_count: number
  series_count: number
  trust_status: 'TRUSTED' | 'WARN' | 'FAIL'
  is_latest: boolean
}

export type RunComparison = {
  baseline_run: AnalysisRunSummary
  target_run: AnalysisRunSummary
  summary: { regression: number; improved: number; unchanged: number; comparable: number }
  scalar_comparison: Array<{
    variable_key: string
    display_name: string
    unit: string | null
    baseline_value: number | null
    target_value: number | null
    baseline_verdict: string | null
    target_verdict: string | null
    delta: number | null
    delta_percent: number | null
    change: 'REGRESSION' | 'IMPROVED' | 'UNCHANGED' | 'ADDED' | 'REMOVED' | 'NOT_COMPARABLE'
    comparable: boolean
  }>
  available_series: Array<{ variable_key: string; display_name: string; unit: string }>
  time_series: null | {
    variable_key: string
    display_name: string
    unit: string
    points: Array<{ time_value: number; time_unit: string; baseline_value: number | null; target_value: number | null }>
  }
}

export type RunTrust = {
  run: AnalysisRunSummary
  trust_status: 'TRUSTED' | 'WARN' | 'FAIL'
  is_latest: boolean
  age_days: number | null
  metadata: null | { source_type: string; source_name: string | null; source_checksum: string | null; schema_id: string | null; schema_version: number | null; parser_version: string; metadata: Record<string, unknown> }
  import_job: null | { schema_id: string; schema_version: number; source_folder: string; status: string; summary: Record<string, unknown> }
  counts: { scalar: number; time_series: number; curve: number; media: number; location: number }
  coverage: { result_variables: number; catalog_variables: number; unmapped: string[]; missing: string[] }
  unit_mismatches: Array<{ variable_key: string; expected: string; actual: string }>
  validations: Array<{ validation_type: string; verdict: string; created_at: string }>
  checks: Array<{ code: string; label: string; status: 'PASS' | 'WARN' | 'FAIL'; detail: string }>
}

export type ReviewItem = {
  id: string
  bookmark_id: string
  analysis_run_id: string
  variable_key: string | null
  title: string
  time_value: number | null
  entity_type: 'NODE' | 'ELEMENT' | null
  entity_id: string | null
  body: string
  review_status: 'OPEN' | 'IN_REVIEW' | 'RESOLVED'
  created_by: string
  created_at: string
  updated_at: string
}

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

export type PortfolioLayout = {
  fontSize: number
  chartOrder: string[]
}

export type WorkflowDashboardLayout = {
  fontSize: number
  accentColor: string
  items: Array<{ requestId: string; x: number; y: number; w: number; h: number }>
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
