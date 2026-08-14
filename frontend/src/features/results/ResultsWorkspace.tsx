import type { ComponentType } from 'react'
import { GripVertical, LayoutDashboard, LoaderCircle, Plus, Settings2 } from 'lucide-react'
import { Responsive, WidthProvider, type Layout, type Layouts } from 'react-grid-layout'
import type { DashboardDefinition, Overview, QualityThreshold } from '../../types'
import { ComparisonWorkspace, type RunComparisonReportContext, WidgetCard } from './AnalysisWidgets'

const ResponsiveGridLayout = WidthProvider(Responsive) as unknown as ComponentType<any>

type ResultsView = 'open_cell' | 'chassis' | 'custom' | 'compare'

type ResultsWorkspaceProps = {
  activeView: ResultsView
  canEdit: boolean
  canManageThresholds: boolean
  chassisThreshold?: QualityThreshold
  dashboard: DashboardDefinition
  editMode: boolean
  loadCaseId: string
  onBeginEditing: () => void
  onComparisonContext: (context: RunComparisonReportContext | null) => void
  onConfigureWidget: (widgetId: string) => void
  onLayoutChange: (layout: Layout[], layouts: Layouts) => void
  onOpenAssistant: () => void
  onRemoveWidget: (widgetId: string) => void
  onSaveChassisThreshold: (value: number) => void
  onSaveOpenCellThreshold: (value: number) => void
  openCellThreshold?: QualityThreshold
  overview: Overview
  selectedEdges: string[]
}

export function ResultsWorkspace({
  activeView,
  canEdit,
  canManageThresholds,
  chassisThreshold,
  dashboard,
  editMode,
  loadCaseId,
  onBeginEditing,
  onComparisonContext,
  onConfigureWidget,
  onLayoutChange,
  onOpenAssistant,
  onRemoveWidget,
  onSaveChassisThreshold,
  onSaveOpenCellThreshold,
  openCellThreshold,
  overview,
  selectedEdges,
}: ResultsWorkspaceProps) {
  const widgetProps = (widget: DashboardDefinition['widgets'][number]) => ({
    widget,
    overview,
    selectedEdges,
    editMode,
    canManageThresholds,
    threshold: chassisThreshold,
    openCellThreshold,
    onSaveThreshold: onSaveChassisThreshold,
    onSaveOpenCellThreshold,
    onRemove: () => onRemoveWidget(widget.id),
    onConfigure: () => onConfigureWidget(widget.id),
  })
  const layouts = { lg: dashboard.widgets.map((item) => ({ i: item.id, x: item.x, y: item.y, w: item.w, h: item.h })) }

  if (activeView === 'compare' && overview.run && dashboard.page?.analysis_key === 'run_comparison') {
    return <section className="canvas-area"><ResponsiveGridLayout className="layout comparison-dashboard-layout" layouts={layouts} breakpoints={{ lg: 900, md: 600, sm: 0 }} cols={{ lg: 12, md: 8, sm: 1 }} rowHeight={74} margin={[16, 16]} isDraggable={editMode} isResizable={editMode} draggableHandle=".widget-drag-handle" compactType="vertical" onLayoutChange={onLayoutChange}>
      {dashboard.widgets.map((widget) => <div key={widget.id} className="comparison-dashboard-widget"><header className="widget-head"><div>{editMode && <span className="widget-drag-handle"><GripVertical /> 이동</span>}<h3>{widget.title}</h3></div>{editMode && <button onClick={() => onConfigureWidget(widget.id)}><Settings2 /> 설정</button>}</header>{widget.type === 'run_comparison' ? <ComparisonWorkspace loadCaseId={loadCaseId} currentRunId={overview.run ?? ''} onContextChange={onComparisonContext} /> : <WidgetCard {...widgetProps(widget)} />}</div>)}
    </ResponsiveGridLayout></section>
  }

  if (activeView === 'compare') return <section className="canvas-area"><div className="comparison-state"><LoaderCircle className="spin" /> Run 비교 대시보드를 불러오고 있습니다.</div></section>

  if (dashboard.widgets.length === 0) return <section className="canvas-area"><div className="analysis-empty-canvas"><LayoutDashboard /><h2>{dashboard.name}</h2><p>아직 배치된 위젯이 없습니다. 위젯을 추가해 이 분석 페이지를 구성하세요.</p>{canEdit && <button onClick={() => { if (!editMode) onBeginEditing(); onOpenAssistant() }}><Plus /> 첫 위젯 추가</button>}</div></section>

  return <section className="canvas-area"><ResponsiveGridLayout className="layout" layouts={layouts} breakpoints={{ lg: 900, md: 600, sm: 0 }} cols={{ lg: 12, md: 8, sm: 1 }} rowHeight={74} margin={[16, 16]} isDraggable={editMode} isResizable={editMode} draggableHandle=".widget-drag-handle" compactType="vertical" onLayoutChange={onLayoutChange}>
    {dashboard.widgets.map((widget) => <div key={widget.id}><WidgetCard {...widgetProps(widget)} /></div>)}
  </ResponsiveGridLayout></section>
}
