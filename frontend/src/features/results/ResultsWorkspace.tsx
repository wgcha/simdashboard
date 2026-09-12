import { useEffect, useRef, useState } from 'react'
import type { Layout, Layouts } from 'react-grid-layout'
import type { DashboardDefinition, Overview, QualityThreshold, RunComparisonReportContext } from '../../types'
import { suggestLayout } from './recommendedLayout'
import { RecommendedLayoutPreview, applyPositionOnly, layoutFingerprint, type LayoutSuggestion } from './RecommendedLayoutPreview'
import { ResultsWorkspaceGrid } from './ResultsWorkspaceGrid'

type Props = { activeView: 'open_cell' | 'chassis' | 'custom' | 'compare'; canEdit: boolean; canManageThresholds: boolean; chassisThreshold?: QualityThreshold; dashboard: DashboardDefinition; editMode: boolean; loadCaseId: string; onBeginEditing: () => void; onComparisonContext: (context: RunComparisonReportContext | null) => void; onConfigureWidget: (widgetId: string) => void; onLayoutChange: (layout: Layout[], layouts: Layouts) => void; onOpenAssistant: () => void; onRemoveWidget: (widgetId: string) => void; onSaveChassisThreshold: (value: number) => void; onSaveOpenCellThreshold: (value: number) => void; openCellThreshold?: QualityThreshold; overview: Overview; selectedEdges: string[]; resultSummary?: React.ReactNode }
type Applied = { before: Layout[]; expected: Layout[]; fingerprint: string }

export function ResultsWorkspace(props: Props) {
  const { canEdit, dashboard, editMode, loadCaseId, onLayoutChange } = props
  const [open, setOpen] = useState(false); const [suggestion, setSuggestion] = useState<LayoutSuggestion | null>(null); const [source, setSource] = useState(''); const [stale, setStale] = useState(false); const [applied, setApplied] = useState<Applied | null>(null)
  const fingerprint = layoutFingerprint(dashboard.widgets, dashboard.id, loadCaseId)
  const previousFingerprint = useRef(fingerprint)
  useEffect(() => { if (!canEdit || !editMode) { setOpen(false); setSuggestion(null); setApplied(null); setStale(false) } }, [canEdit, editMode])
  useEffect(() => { setOpen(false); setSuggestion(null); setApplied(null); setStale(false); setSource('') }, [dashboard.id, loadCaseId])
  useEffect(() => { if (fingerprint !== previousFingerprint.current) { if (open && source && source !== fingerprint) setStale(true); if (applied && applied.fingerprint !== fingerprint) setApplied(null); previousFingerprint.current = fingerprint } }, [fingerprint, open, source, applied])
  const change = (layout: Layout[], layouts: Layouts) => { onLayoutChange(layout, layouts) }
  const preview = () => { const next = suggestLayout(dashboard.widgets); setSuggestion(next); setSource(fingerprint); setStale(false); setOpen(true) }
  const apply = () => { if (!canEdit || !editMode || source !== fingerprint || !suggestion?.available || !suggestion.changedCount || stale) return; const before = dashboard.widgets.map(({ id, x, y, w, h }) => ({ i: id, x, y, w, h })); const expected = applyPositionOnly(dashboard.widgets, suggestion.widgets); const nextWidgets = dashboard.widgets.map((widget) => { const next = expected.find((item) => item.i === widget.id); return next ? { ...widget, x: next.x, y: next.y } : widget }); onLayoutChange(expected, { lg: expected }); setApplied({ before, expected, fingerprint: layoutFingerprint(nextWidgets, dashboard.id, loadCaseId) }); setOpen(false) }
  const restore = () => { if (!canEdit || !editMode || !applied || applied.fingerprint !== fingerprint) return; onLayoutChange(applied.before, { lg: applied.before }); setApplied(null) }
  return <><div className="recommended-layout-controls">{canEdit && editMode && <button type="button" data-testid="recommended-layout-open" onClick={preview}>추천 배치 미리보기</button>}{canEdit && editMode && applied && <button type="button" data-testid="recommended-layout-restore" onClick={restore}>적용 전 배치로</button>}</div><ResultsWorkspaceGrid {...props} onLayoutChange={change} /><RecommendedLayoutPreview open={open} current={dashboard.widgets} suggestion={suggestion} stale={stale} onApply={apply} onClose={() => setOpen(false)} /></>
}
