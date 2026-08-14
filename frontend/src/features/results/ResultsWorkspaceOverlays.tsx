import { lazy, Suspense } from 'react'
import { AlertTriangle, Check, Lock, Plus, Sparkles, Trash2, X } from 'lucide-react'

import { api } from '../../api'

import type { DashboardDefinition, DashboardSummary, DashboardVersion, DashboardWidget, VariableDefinition, WidgetCatalogItem } from '../../types'

const WidgetSettingsPanel = lazy(() => import('./AnalysisWidgets').then(({ WidgetSettingsPanel: Component }) => ({ default: Component })))

type AssistantProposal = Awaited<ReturnType<typeof api.previewCommand>> | null

type Props = {
  assistantOpen: boolean
  canManagePages: boolean
  catalogVariable: string
  command: string
  dashboard: DashboardDefinition | null
  proposal: AssistantProposal
  savedDashboards: DashboardSummary[]
  selectedWidgetId: string | null
  variables: VariableDefinition[]
  versions: DashboardVersion[]
  widgetCatalog: WidgetCatalogItem[]
  onAddCatalogWidget: (item: WidgetCatalogItem) => void
  onApplyProposal: () => void
  onCloneLayout: () => void
  onCloseAssistant: () => void
  onCloseWidgetSettings: () => void
  onCommandChange: (value: string) => void
  onLoadSavedDashboard: (id: string) => void
  onLoadVersion: (version: number) => void
  onPreviewCommand: () => void
  onRemoveVersion: (version: number) => void
  onRestorePrevious: () => void
  onSelectCatalogVariable: (id: string) => void
  onUpdateWidget: (id: string, patch: Partial<DashboardWidget>) => void
}

const SPECIAL_TYPES = new Set(['summary', 'open_cell_map', 'open_cell_summary', 'kpi', 'verdict', 'gauge', 'edge_bar', 'time_series', 'scatter', 'note', 'result_table', 'contour', 'video', 'video_grid', 'chassis_summary', 'chassis_diagram', 'chassis_bar', 'chassis_table'])

/** Results-owned temporary UI; its open state remains controlled by the workspace. */
export function ResultsWorkspaceOverlays({
  assistantOpen, canManagePages, catalogVariable, command, dashboard, proposal, savedDashboards,
  selectedWidgetId, variables, versions, widgetCatalog, onAddCatalogWidget, onApplyProposal,
  onCloneLayout, onCloseAssistant, onCloseWidgetSettings, onCommandChange, onLoadSavedDashboard,
  onLoadVersion, onPreviewCommand, onRemoveVersion, onRestorePrevious, onSelectCatalogVariable,
  onUpdateWidget,
}: Props) {
  const selectedWidget = dashboard?.widgets.find((item) => item.id === selectedWidgetId)
  return <>
    {assistantOpen && dashboard && <div className="drawer-backdrop" onMouseDown={onCloseAssistant}>
      <aside className="assistant-drawer" onMouseDown={(event) => event.stopPropagation()}>
        <div className="drawer-head"><div><span><Sparkles /></span><div><strong>Canvas Copilot</strong><small>자연어 대시보드 편집</small></div></div><button onClick={onCloseAssistant}><X /></button></div>
        <div className="assistant-copy"><h2>어떤 시각화가 필요하세요?</h2><p>등록된 변수와 허용된 위젯만 사용해 안전한 변경안을 만듭니다. 적용 전 내용을 확인할 수 있습니다.</p></div>
        <div className="suggestions">{['응력-시간 그래프를 추가해', '상하좌우 최대 응력 막대그래프를 추가해', '패스/실패 판정 카드를 추가해'].map((text) => <button key={text} onClick={() => onCommandChange(text)}>{text}<Plus /></button>)}</div>
        <div className="catalog-editor"><strong>위젯 카탈로그</strong><select aria-label="위젯 변수" value={catalogVariable} onChange={(event) => onSelectCatalogVariable(event.target.value)}>{variables.map((item) => <option key={item.id} value={item.id}>{item.display_name} ({item.unit})</option>)}</select><div>{widgetCatalog.filter((item) => SPECIAL_TYPES.has(item.type)).map((item) => <button key={item.type} onClick={() => onAddCatalogWidget(item)}><Plus /> {item.label}</button>)}</div></div>
        <textarea value={command} onChange={(event) => onCommandChange(event.target.value)} placeholder="예: 응력-시간 그래프에 기준선을 넣어줘" />
        <button className="assistant-submit" onClick={onPreviewCommand} disabled={!command.trim()}><Sparkles /> 변경안 만들기</button>
        {proposal && <div className={`proposal ${proposal.recognized ? 'recognized' : ''}`}><span>{proposal.recognized ? <Check /> : <AlertTriangle />}</span><div><strong>{proposal.recognized ? '적용 전 미리보기' : '요청 확인 필요'}</strong><p>{proposal.message}</p>{proposal.proposal && <code>{proposal.proposal.action === 'add_widget' ? `${proposal.proposal.widget.title} · ${proposal.proposal.widget.type}` : `${proposal.proposal.updates.length}개 위젯 설정 변경`}</code>}</div>{proposal.recognized && <button onClick={onApplyProposal}>변경안 적용</button>}</div>}
        <div className="assistant-safe"><Lock /><span><strong>안전한 변경</strong>자연어 명령은 SQL이나 코드를 직접 실행하지 않습니다.</span></div>
        <div className="layout-history"><button onClick={onCloneLayout}>다른 이름으로 복제</button><button onClick={onRestorePrevious}>최근 정상 버전 복구</button><small>현재 v{dashboard.version ?? 1} · 유효 저장 이력 {versions.length}개</small><div className="dashboard-version-list">{versions.map((item) => <article key={item.version}><span><strong>v{item.version}</strong><small>{new Date(item.created_at).toLocaleString('ko-KR')} · {item.created_by}</small></span><button onClick={() => onLoadVersion(item.version)} disabled={item.version === dashboard.version}>초안으로 불러오기</button>{canManagePages && item.version !== dashboard.version && !(dashboard.page?.is_system && item.version === 1) && <button className="danger" aria-label={`v${item.version} 버전 삭제`} onClick={() => onRemoveVersion(item.version)}><Trash2 /> 삭제</button>}</article>)}</div></div>
        <label className="saved-layouts"><span>저장된 레이아웃 불러오기</span><select value={dashboard.id} onChange={(event) => onLoadSavedDashboard(event.target.value)}>{savedDashboards.map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.version}</option>)}</select></label>
      </aside>
    </div>}
    {selectedWidget && <Suspense fallback={null}><WidgetSettingsPanel widget={selectedWidget} variables={variables} onChange={(patch) => onUpdateWidget(selectedWidget.id, patch)} onClose={onCloseWidgetSettings} /></Suspense>}
  </>
}
