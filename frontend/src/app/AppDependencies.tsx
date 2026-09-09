import { LoaderCircle } from 'lucide-react'
import type { WidgetCatalogItem } from '../types'

export { DataWorkspace, FolderSchemaWorkspace, VariableCatalogPage, AutomationTemplatesPage, FeatureExampleGallery, HelpCenter, WorkflowView, ResultsWorkspace, PendingAnalysisWorkspace, AnalysisPageManager } from './routing/workspaceScreenModules'

export function FeatureScreenFallback() {
  return <div className="full-state"><LoaderCircle className="spin" /> 화면을 준비하고 있습니다.</div>
}

export const SPECIAL_WIDGET_CATALOG: WidgetCatalogItem[] = [
  { type: 'summary', label: '하중 조건 요약', category: '요약', allowed_data_types: [], default_size: [6, 2] },
  { type: 'open_cell_map', label: 'Open Cell 맵', category: '전용 평가', allowed_data_types: [], default_size: [5, 4] },
  { type: 'open_cell_summary', label: 'Open Cell 판정 요약', category: '전용 평가', allowed_data_types: [], default_size: [12, 2] },
  { type: 'chassis_summary', label: 'Chassis 판정 요약', category: '전용 평가', allowed_data_types: [], default_size: [12, 2] },
  { type: 'chassis_diagram', label: 'Chassis 위치도', category: '전용 평가', allowed_data_types: [], default_size: [7, 5] },
  { type: 'chassis_bar', label: 'Chassis 비교 그래프', category: '전용 평가', allowed_data_types: [], default_size: [5, 5] },
  { type: 'chassis_table', label: 'Chassis 상세 표', category: '전용 평가', allowed_data_types: [], default_size: [8, 4] },
]
