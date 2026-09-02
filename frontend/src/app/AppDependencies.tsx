import { lazy } from 'react'
import { LoaderCircle } from 'lucide-react'
import type { WidgetCatalogItem } from '../types'

export const DataWorkspace = lazy(() => import('../features/data/DataWorkspace').then(({ DataWorkspace }) => ({ default: DataWorkspace })))
export const FolderSchemaWorkspace = lazy(() => import('../features/data/FolderSchemaWorkspace').then(({ FolderSchemaWorkspace }) => ({ default: FolderSchemaWorkspace })))
export const VariableCatalogPage = lazy(() => import('../features/data/VariableCatalogPage').then(({ VariableCatalogPage }) => ({ default: VariableCatalogPage })))
export const AutomationTemplatesPage = lazy(() => import('../features/workbench/AutomationTemplatesPage').then(({ AutomationTemplatesPage }) => ({ default: AutomationTemplatesPage })))
export const FeatureExampleGallery = lazy(() => import('../features/examples/FeatureExampleGallery').then(({ FeatureExampleGallery }) => ({ default: FeatureExampleGallery })))
export const HelpCenter = lazy(() => import('../features/help/HelpCenter').then(({ HelpCenter }) => ({ default: HelpCenter })))
export const WorkflowView = lazy(() => import('../features/requests/WorkflowView').then(({ WorkflowView }) => ({ default: WorkflowView })))
export const ResultsWorkspace = lazy(() => import('../features/results/ResultsWorkspace').then(({ ResultsWorkspace }) => ({ default: ResultsWorkspace })))
export const PendingAnalysisWorkspace = lazy(() => import('../features/results/PendingAnalysisWorkspace').then(({ PendingAnalysisWorkspace }) => ({ default: PendingAnalysisWorkspace })))
export const AnalysisPageManager = lazy(() => import('../features/analysis/AnalysisPageManager').then(({ AnalysisPageManager }) => ({ default: AnalysisPageManager })))

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
