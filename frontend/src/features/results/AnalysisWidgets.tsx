import { useEffect, useMemo, useState } from 'react'
import { Activity, AlertTriangle, Check, Database, LayoutDashboard, Lock, MessageSquareText, Settings2, X } from 'lucide-react'
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { DropVideoGrid } from './DropVideoGrid'
import { WidgetFocusFrame } from './WidgetFocusFrame'
import { VideoGridSettings } from './VideoGridSettings'
import type { DashboardWidget, Overview, QualityThreshold, VariableDefinition } from '../../types'

const SERIES_COLORS = [1, 2, 3, 4].map((index) => `var(--color-chart-series-${index})`)

function hasNumericValue(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}
export { ComparisonWorkspace } from './ComparisonWorkspace'

export function WidgetCard({ widget, overview, selectedEdges, editMode, canManageThresholds, threshold, openCellThreshold, onSaveThreshold, onSaveOpenCellThreshold, onRemove, onConfigure }: { widget: DashboardWidget; overview: Overview; selectedEdges: string[]; editMode: boolean; canManageThresholds: boolean; threshold?: QualityThreshold; openCellThreshold?: QualityThreshold; onSaveThreshold: (value: number) => void; onSaveOpenCellThreshold: (value: number) => void; onRemove: () => void; onConfigure: () => void }) {
  const customFontSize = Number(widget.settings?.fontSize)
  return <WidgetFocusFrame className={`widget-card widget-${widget.type} ${editMode ? 'editable' : ''}`} customFontSize={customFontSize} editMode={editMode} onConfigure={onConfigure} onRemove={onRemove} title={widget.title} type={widget.type} widgetId={widget.id}>
    <WidgetContent widget={widget} overview={overview} selectedEdges={selectedEdges} canManageThresholds={canManageThresholds} threshold={threshold} openCellThreshold={openCellThreshold} onSaveThreshold={onSaveThreshold} onSaveOpenCellThreshold={onSaveOpenCellThreshold} />
  </WidgetFocusFrame>
}

function WidgetContent({ widget, overview, selectedEdges, canManageThresholds, threshold, openCellThreshold, onSaveThreshold, onSaveOpenCellThreshold }: { widget: DashboardWidget; overview: Overview; selectedEdges: string[]; canManageThresholds: boolean; threshold?: QualityThreshold; openCellThreshold?: QualityThreshold; onSaveThreshold: (value: number) => void; onSaveOpenCellThreshold: (value: number) => void }) {
  const type = widget.type
  const edgeOrder = ['top', 'bottom', 'left', 'right']
  const openCellScalars = overview.scalar_results.filter((item) => item.result_group === 'OPEN_CELL' && item.unit.toLowerCase() === 'mpa' && item.variable_key.toLowerCase().includes('stress') && hasNumericValue(item.value_double))
  const filteredScalars = openCellScalars
    .filter((item) => selectedEdges.some((edge) => item.variable_key.startsWith(edge)))
    .sort((a, b) => edgeOrder.indexOf(a.variable_key.split('_')[0]) - edgeOrder.indexOf(b.variable_key.split('_')[0]))
  const edgeLabel = (key: string) => ({ top: '상단', bottom: '하단', left: '좌측', right: '우측' }[key.split('_')[0]] ?? key)
  const configuredScalars = widget.settings?.variableId ? overview.scalar_results.filter((item) => item.variable_key === widget.settings?.variableId && hasNumericValue(item.value_double)) : filteredScalars
  const barData = configuredScalars.map((item) => ({ name: edgeLabel(item.variable_key), value: item.value_double, verdict: item.verdict }))
  const openCellLimit = openCellThreshold?.threshold_double ?? openCellScalars.find((item) => hasNumericValue(item.threshold_double))?.threshold_double ?? 75
  const openCellVerdict = openCellScalars.some((item) => item.verdict === 'FAIL') ? 'FAIL' : openCellScalars.length ? 'PASS' : 'NO_DATA'
  const boundScalar = widget.settings?.variableId ? configuredScalars[0] : undefined
  const widgetThreshold = widget.settings?.variableId ? (hasNumericValue(boundScalar?.threshold_double) ? boundScalar.threshold_double : null) : openCellLimit
  const widgetUnit = boundScalar?.unit ?? configuredScalars[0]?.unit ?? 'MPa'
  const widgetVerdict = boundScalar?.verdict ?? openCellVerdict
  const chartMaximum = Math.max(...barData.map((item) => item.value), widgetThreshold ?? 0, 1)
  const resultLocation = (key: string) => overview.result_locations.find((item) => item.variable_key === key)
  const seriesData = useMemo(() => {
    const grouped = new Map<number, Record<string, number>>()
    overview.time_series.filter((item) => hasNumericValue(item.time_value) && hasNumericValue(item.value) && (widget.settings?.variableId ? item.variable_key === widget.settings.variableId : selectedEdges.some((edge) => item.variable_key.startsWith(edge)))).forEach((item) => {
      const point = grouped.get(item.time_value) ?? { time: item.time_value }
      point[item.variable_key] = item.value
      grouped.set(item.time_value, point)
    })
    return [...grouped.values()]
  }, [overview.time_series, selectedEdges, widget.settings?.variableId])

  if (type.startsWith('chassis_')) return <ChassisWidgetContent type={type} overview={overview} threshold={threshold} canManageThreshold={canManageThresholds} onSaveThreshold={onSaveThreshold} variableId={String(widget.settings?.variableId ?? '')} />
  if (widget.settings?.variableId && type === 'time_series' && !overview.time_series.some((item)=>item.variable_key===widget.settings?.variableId)) return <div className="widget-empty"><Database/><strong>선언된 변수에 결과 데이터가 없습니다.</strong><small>{String(widget.settings.variableId)} 키의 시간 이력을 가져오면 자동 표시됩니다.</small></div>
  if (widget.settings?.variableId && ['kpi','gauge','edge_bar','scatter','result_table'].includes(type) && !overview.scalar_results.some((item)=>item.variable_key===widget.settings?.variableId)) return <div className="widget-empty"><Database/><strong>선언된 변수에 결과 데이터가 없습니다.</strong><small>{String(widget.settings.variableId)} 키의 숫자 결과를 가져오면 자동 표시됩니다.</small></div>

  if (type === 'open_cell_map') return <OpenCellMap overview={overview} />
  if (type === 'open_cell_summary') return <OpenCellSummary overview={overview} threshold={openCellThreshold} canManageThreshold={canManageThresholds} onSaveThreshold={onSaveOpenCellThreshold} />
  if (type === 'verdict') return <div className={`verdict-block ${widgetVerdict.toLowerCase()}`}><div className="verdict-icon">{widgetVerdict === 'PASS' ? <Check /> : <X />}</div><div><strong>{widgetVerdict}</strong><span>{widgetVerdict === 'PASS' ? '허용 기준 만족' : widgetVerdict === 'FAIL' ? '기준 초과 감지' : '판정 데이터 없음'}</span></div><small>{widgetThreshold == null ? widgetUnit : `LIMIT ${widgetThreshold} ${widgetUnit}`}</small></div>
  if (type === 'summary') return overview.load_case.analysis_type === 'SIDE_CLAMP' ? <div className="summary-grid"><div><span>클램프 압력</span><strong>{overview.load_case.parameters.pressure_mpa ?? overview.load_case.parameters.clamp_pressure_kpa ?? '-'}<em>MPa</em></strong></div><div><span>유지 시간</span><strong>{overview.load_case.parameters.hold_time_sec ?? overview.load_case.parameters.hold_time_s ?? '-'}<em>s</em></strong></div><div><span>클램프 면</span><strong>{Array.isArray(overview.load_case.parameters.faces) ? overview.load_case.parameters.faces.join(' / ') : 'LEFT / RIGHT'}</strong></div><div><span>요소 수</span><strong>{overview.template_execution?.generated_model.elements.toLocaleString() ?? '-'}</strong></div></div> : <div className="summary-grid"><div><span>낙하 높이</span><strong>{overview.load_case.parameters.drop_height_mm}<em>mm</em></strong></div><div><span>낙하 방향</span><strong>{overview.load_case.parameters.direction ?? overview.load_case.parameters.impact_direction ?? '-'}</strong></div><div><span>자동화 템플릿</span><strong>{overview.template_execution?.template_version ?? '-'}</strong></div><div><span>요소 수</span><strong>{overview.template_execution?.generated_model.elements.toLocaleString() ?? '-'}</strong></div></div>
  if (type === 'edge_bar') return <ResponsiveContainer width="100%" height="100%"><BarChart data={barData} margin={{ top: 12, right: 18, left: -12, bottom: 0 }}><CartesianGrid vertical={false} stroke="var(--color-chart-grid)" strokeDasharray="3 3"/><XAxis dataKey="name" tick={{ fill: 'var(--color-chart-axis)', fontSize: 14.4 }} axisLine={false} tickLine={false}/><YAxis domain={[0, chartMaximum * 1.15]} tick={{ fill: 'var(--color-chart-axis)', fontSize: 13.2 }} axisLine={false} tickLine={false} unit=""/><Tooltip contentStyle={{ background: 'var(--color-surface-raised)', color: 'var(--color-text)', border: '1px solid var(--color-border-strong)', borderRadius: 10 }} formatter={(value: number) => [`${value} ${widgetUnit}`, '결과']}/>{widget.settings?.showThreshold !== false && widgetThreshold != null && <ReferenceLine y={widgetThreshold} stroke="var(--color-warning)" strokeDasharray="5 5" label={{ value: `기준 ${widgetThreshold}`, fill: 'var(--color-warning)', fontSize: 13.2, position: 'insideTopRight' }}/>}<Bar dataKey="value" radius={[5,5,1,1]}>{barData.map((entry) => <Cell key={entry.name} fill={entry.verdict === 'FAIL' ? 'var(--color-danger)' : 'var(--color-success)'} />)}</Bar></BarChart></ResponsiveContainer>
  if (type === 'time_series') { const seriesKeys = widget.settings?.variableId ? [String(widget.settings.variableId)] : selectedEdges.map((edge) => `${edge}_edge_stress_time`); return <ResponsiveContainer width="100%" height="100%"><LineChart data={seriesData} margin={{ top: 10, right: 22, left: -8, bottom: 2 }}><CartesianGrid stroke="var(--color-chart-grid)" strokeDasharray="3 3"/><XAxis dataKey="time" tick={{ fill: 'var(--color-chart-axis)', fontSize: 13.2 }} axisLine={{ stroke: 'var(--color-border-strong)' }} tickLine={false} label={{ value: `TIME (${overview.time_series.find((item)=>seriesKeys.includes(item.variable_key))?.time_unit ?? 'ms'})`, fill: 'var(--color-chart-axis)', fontSize: 12, position: 'insideBottomRight', offset: -2 }}/><YAxis tick={{ fill: 'var(--color-chart-axis)', fontSize: 13.2 }} axisLine={false} tickLine={false}/><Tooltip contentStyle={{ background: 'var(--color-surface-raised)', color: 'var(--color-text)', border: '1px solid var(--color-border-strong)', borderRadius: 10 }}/><Legend wrapperStyle={{ fontSize: 13.2, paddingTop: 5 }}/>{widget.settings?.showThreshold !== false && widgetThreshold != null && <ReferenceLine y={widgetThreshold} stroke="var(--color-warning)" strokeDasharray="6 4"/>}{seriesKeys.map((key, index) => <Line key={key} type="monotone" dataKey={key} name={overview.time_series.find((item) => item.variable_key === key)?.display_name ?? edgeLabel(key)} dot={false} stroke={String(widget.settings?.color ?? SERIES_COLORS[index % SERIES_COLORS.length])} strokeWidth={2}/>)}</LineChart></ResponsiveContainer> }
  if (type === 'note') return <div className="note-block"><MessageSquareText /><blockquote>{overview.notes[0]?.body ?? '등록된 의견이 없습니다.'}</blockquote><footer><span>{overview.notes[0]?.author ?? '-'}</span><small>ANALYSIS ENGINEER</small></footer></div>
  if (type === 'result_table') return <div className="result-table"><div className="table-head"><span>측정 위치</span><span>결과</span><span>허용 기준</span><span>여유율</span><span>판정</span></div>{configuredScalars.map((item) => { const location = resultLocation(item.variable_key); const hasThreshold = hasNumericValue(item.threshold_double) && item.threshold_double !== 0; return <div className="table-row" key={item.id}><strong><i className={`edge-${item.variable_key.split('_')[0]}`} /><span>{item.display_name.replace(' 최대 응력','')}{location && <small>{location.entity_type} {location.entity_id} · ({location.x.toFixed(1)}, {location.y.toFixed(1)}, {location.z.toFixed(1)})</small>}</span></strong><span>{item.value_double.toFixed(1)} <small>{item.unit}</small></span><span>{hasThreshold ? item.threshold_double.toFixed(1) : ''} <small>{hasThreshold ? item.unit : ''}</small></span><span className={item.verdict === 'FAIL' ? 'negative' : 'positive'}>{hasThreshold ? `${((item.threshold_double - item.value_double) / item.threshold_double * 100).toFixed(1)}%` : ''}</span><b className={item.verdict.toLowerCase()}>{item.verdict}</b></div> })}</div>
  if (type === 'contour') { const variableId = String(widget.settings?.variableId ?? ''); const asset = overview.media.find((item) => item.asset_type === 'IMAGE' && (!variableId || item.metadata?.variable_key === variableId)) ?? overview.media.find((item) => item.asset_type === 'IMAGE'); return asset?.asset_url ? <div className="contour"><img src={asset.asset_url} alt={asset.title} /></div> : <div className="empty-widget">등록된 컨투어 이미지가 없습니다.</div> }
  if (type === 'kpi' || type === 'gauge') {
    const bound = overview.scalar_results.find((item) => item.variable_key === widget.settings?.variableId && hasNumericValue(item.value_double)) ?? openCellScalars[0]
    return bound ? <div className="verdict-card"><span>{bound.display_name}</span><strong>{bound.value_double.toFixed(1)} {bound.unit}</strong><small>{hasNumericValue(bound.threshold_double) ? `기준 ${bound.threshold_double.toFixed(1)} ${bound.unit} · ` : ''}{bound.verdict}</small></div> : <div className="empty-widget">선택한 변수의 데이터가 없습니다.</div>
  }
  if (type === 'scatter') return <ResponsiveContainer width="100%" height="100%"><LineChart data={barData}><CartesianGrid stroke="var(--color-chart-grid)" /><XAxis dataKey="name" /><YAxis /><Tooltip /><Line dataKey="value" stroke="var(--color-chart-series-1)" /></LineChart></ResponsiveContainer>
  if (type === 'video_grid') return <DropVideoGrid loadCaseId={overview.load_case.id} pageSize={Number(widget.settings?.pageSize ?? 20)} columns={Number(widget.settings?.videoColumns ?? 2)} rows={Number(widget.settings?.videoRows ?? 2)} />
  if (type === 'video') { const variableId = String(widget.settings?.variableId ?? ''); const asset = overview.media.find((item) => item.asset_type === 'VIDEO' && (!variableId || item.metadata?.variable_key === variableId)) ?? overview.media.find((item) => item.asset_type === 'VIDEO'); return asset?.asset_url ? <video controls className="result-video" src={asset.asset_url} /> : <div className="empty-widget">등록된 안전한 영상 파일이 없습니다.</div> }
  if (type === 'model3d') return <div className="empty-widget">GLB/glTF 경량 파일을 등록하면 여기에 표시됩니다.</div>
  return <div className="empty-widget">표시할 데이터가 없습니다.</div>
}

function OpenCellMap({ overview }: { overview: Overview }) {
  const productValue = (category: string) => overview.product_information.find((item) => item.category === category)?.value_text ?? '-'
  const edgeResult = (edge: string) => overview.scalar_results.find((item) => item.variable_key.startsWith(`${edge}_`) && hasNumericValue(item.value_double))
  const edgeLabels = { top: '상', bottom: '하', left: '좌', right: '우' }
  const isClamp = overview.load_case.analysis_type === 'SIDE_CLAMP'
  const scene = isClamp ? `SIDE CLAMP · ${overview.load_case.parameters.pressure_mpa ?? '-'} MPa` : `${overview.load_case.parameters.direction ?? '-'} FACE · ${overview.load_case.parameters.drop_height_mm ?? '-'} mm`
  const threshold = overview.scalar_results.find((item) => !item.variable_key.startsWith('chassis_rear_') && hasNumericValue(item.threshold_double))?.threshold_double ?? 75

  return <div className="open-cell-layout">
    <div className="open-cell-visual">
      <div className="open-cell-frame">
        {Object.entries(edgeLabels).map(([edge, label]) => {
          const result = edgeResult(edge)
          return <div key={edge} className={`open-cell-edge ${edge} ${(result?.verdict ?? 'PASS').toLowerCase()}`}><span>{label}</span><strong>{result?.value_double.toFixed(1) ?? '-'}<small> MPa</small></strong></div>
        })}
        <div className="open-cell-glass"><span>OPEN CELL</span><strong>{productValue('SPEC').replace(' inch', '\"')}</strong><small>GLASS PANEL · 16:9</small></div>
      </div>
      <div className="edge-legend"><span><i className="pass" />기준 이내</span><span><i className="fail" />기준 초과</span><b>LIMIT {threshold} MPa</b></div>
    </div>
    <div className="open-cell-meta">
      <div><span>인치</span><strong>{productValue('SPEC')}</strong></div>
      <div><span>하중 씬</span><strong>{scene}</strong></div>
      <div><span>일자</span><strong>{new Date(overview.load_case.created_at).toLocaleDateString('ko-KR')}</strong></div>
      <div><span>제조사</span><strong>{productValue('MANUFACTURER')}</strong></div>
      <div><span>제품 모델명</span><strong>{productValue('MODEL')}</strong></div>
    </div>
  </div>
}

export function WidgetSettingsPanel({ widget, variables, onChange, onClose }: { widget: DashboardWidget; variables: VariableDefinition[]; onChange: (patch: Partial<DashboardWidget>) => void; onClose: () => void }) {
  const chartOptions: Array<[DashboardWidget['type'],string]> = [...(widget.type === 'run_comparison' ? [['run_comparison','Run 비교·검토 패널'] as [DashboardWidget['type'], string]] : []),['summary','하중 조건 요약'],['kpi','KPI 카드'],['verdict','패스/실패 카드'],['gauge','임계값 게이지'],['edge_bar','막대그래프'],['time_series','시계열 그래프'],['scatter','산점도'],['result_table','데이터 테이블'],['open_cell_map','Open Cell 맵'],['open_cell_summary','Open Cell 판정 요약'],['chassis_summary','Chassis 판정 요약'],['chassis_diagram','Chassis 위치도'],['chassis_bar','Chassis 비교 그래프'],['chassis_table','Chassis 상세 표'],['contour','컨투어 이미지'],['video','영상 플레이어'],['video_grid','낙하 영상 비교'],['note','수행자 의견']]
  const currentVariable = variables.find((item) => item.id === widget.settings?.variableId)
  const compatibleVariables = variables.filter((item) => item.allowed_widgets.includes(widget.type) || item.id === currentVariable?.id)
  const aggregations = currentVariable?.allowed_aggregations ?? ['MAX','MIN','AVG','LATEST','RAW']
  return <div className="drawer-backdrop" onMouseDown={onClose}><aside className="widget-settings-drawer" onMouseDown={(e)=>e.stopPropagation()}><header><div><span>WIDGET SETTINGS</span><h2>위젯 설정</h2></div><button onClick={onClose}><X/></button></header><label><span>제목</span><input value={widget.title} onChange={(e)=>onChange({title:e.target.value})}/></label><label><span>글자 크기 (px)</span><input type="number" min="8" max="24" step="1" value={Number(widget.settings?.fontSize ?? 10)} onChange={(e)=>onChange({settings:{fontSize:Math.min(24,Math.max(8,Number(e.target.value)||10))}})}/></label><label><span>시각화 유형</span><select value={widget.type} disabled={widget.type === 'run_comparison'} onChange={(e)=>onChange({type:e.target.value as DashboardWidget['type']})}>{chartOptions.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>{widget.type === 'video_grid' && <VideoGridSettings settings={widget.settings} onChange={onChange} />}{widget.type !== 'run_comparison' && <><label><span>데이터 변수</span><select value={String(widget.settings?.variableId ?? '')} onChange={(e)=>onChange({settings:{variableId:e.target.value||undefined}})}><option value="">전체/위젯 기본 변수</option>{compatibleVariables.map((item)=><option key={item.id} value={item.id}>{item.display_name} ({item.unit}){item.has_data?'':' · 데이터 대기'}</option>)}</select></label><label><span>집계 방식</span><select value={String(widget.settings?.aggregation ?? aggregations[0])} onChange={(e)=>onChange({settings:{aggregation:e.target.value}})}>{aggregations.map((item)=><option key={item}>{item}</option>)}</select></label></>}<label><span>강조 색상</span><div className="color-setting"><input type="color" value={String(widget.settings?.color ?? '#50d5ff')} onChange={(e)=>onChange({settings:{color:e.target.value}})}/><code>{String(widget.settings?.color ?? '#50d5ff')}</code></div></label><label className="check-setting"><input type="checkbox" checked={widget.settings?.showThreshold !== false} onChange={(e)=>onChange({settings:{showThreshold:e.target.checked}})}/><span>기준선 표시</span></label><label className="check-setting"><input type="checkbox" checked={widget.settings?.includeInReport !== false} onChange={(e)=>onChange({settings:{includeInReport:e.target.checked}})}/><span>보고서 포함</span></label><div className="settings-note"><Lock/><p>카탈로그에 선언되고 현재 그래프에 허용된 변수만 표시됩니다. 변수 키로 실제 결과 테이블과 연결됩니다.</p></div><button className="primary-button" onClick={onClose}><Check/> 설정 완료</button></aside></div>
}


function OpenCellSummary({ overview, threshold, canManageThreshold, onSaveThreshold }: { overview: Overview; threshold?: QualityThreshold; canManageThreshold: boolean; onSaveThreshold: (value: number) => void }) {
  const results = overview.scalar_results.filter((item) => item.result_group === 'OPEN_CELL' && item.unit.toLowerCase() === 'mpa' && item.variable_key.toLowerCase().includes('stress') && hasNumericValue(item.value_double))
  const limit = threshold?.threshold_double ?? results.find((item) => hasNumericValue(item.threshold_double))?.threshold_double ?? 75
  const [draftLimit, setDraftLimit] = useState(limit)
  useEffect(() => setDraftLimit(limit), [limit])
  const maximum = Math.max(...results.map((item) => item.value_double), 0)
  const minimum = results.length ? Math.min(...results.map((item) => item.value_double)) : 0
  const verdict = results.length ? (maximum >= limit ? 'FAIL' : 'PASS') : 'NO_DATA'
  return <div className="chassis-widget-summary open-cell-widget-summary"><div><span>전체 판정</span><strong className={verdict.toLowerCase()}>{verdict}</strong></div><div><span>최대 응력</span><strong>{maximum.toFixed(2)} MPa</strong></div>{canManageThreshold ? <label><span>응력 관리 기준</span><div><input aria-label="Open Cell 응력 기준값" type="number" min="0.1" step="0.1" value={draftLimit} onChange={(event) => setDraftLimit(Number(event.target.value))}/><button disabled={!Number.isFinite(draftLimit) || draftLimit <= 0} onClick={() => onSaveThreshold(draftLimit)}>저장</button></div></label> : <div><span>응력 관리 기준</span><strong>{limit.toFixed(1)} MPa</strong></div>}<p>적용 대상 {results.length}개 · MPa 범위 {minimum.toFixed(2)}~{maximum.toFixed(2)} · 기준 이상은 FAIL입니다.</p></div>
}

function ChassisWidgetContent({ type, overview, threshold, canManageThreshold, onSaveThreshold, variableId }: { type: DashboardWidget['type']; overview: Overview; threshold?: QualityThreshold; canManageThreshold: boolean; onSaveThreshold: (value: number) => void; variableId: string }) {
  const allResults = overview.scalar_results.filter((item) => item.result_group === 'CHASSIS_REAR' && item.unit.toLowerCase() === 'mm' && item.variable_key.includes('permanent_deformation') && hasNumericValue(item.value_double))
  const results = type === 'chassis_summary' ? allResults : variableId ? allResults.filter((item)=>item.variable_key===variableId) : allResults
  const limit = threshold?.threshold_double ?? results.find((item) => hasNumericValue(item.threshold_double))?.threshold_double ?? 5
  const [draftLimit, setDraftLimit] = useState(limit)
  useEffect(() => setDraftLimit(limit), [limit])
  const verdict = results.some((item) => item.verdict === 'FAIL') ? 'FAIL' : results.length ? 'PASS' : 'NO_DATA'
  const maximum = Math.max(...results.map((item) => item.value_double), 0)
  const minimum = results.length ? Math.min(...results.map((item) => item.value_double)) : 0
  if (type !== 'chassis_summary' && variableId && !results.length) return <div className="widget-empty"><Database/><strong>선언된 변수에 결과 데이터가 없습니다.</strong><small>{variableId} 키의 숫자 결과를 가져오면 자동 표시됩니다.</small></div>
  const markerClasses: Record<string, string> = { chassis_rear_top_edge_gap_permanent_deformation:'top-edge', chassis_rear_bottom_edge_gap_permanent_deformation:'bottom-edge', chassis_rear_corner_top_left_permanent_deformation:'top-left', chassis_rear_corner_top_right_permanent_deformation:'top-right', chassis_rear_corner_bottom_left_permanent_deformation:'bottom-left', chassis_rear_corner_bottom_right_permanent_deformation:'bottom-right' }
  const labels: Record<string,string> = { top_edge_gap:'상단 엣지', bottom_edge_gap:'하단 엣지', corner_top_left:'좌상단', corner_top_right:'우상단', corner_bottom_left:'좌하단', corner_bottom_right:'우하단' }
  const chartData = results.map((item) => ({ name: labels[item.variable_key.replace('chassis_rear_','').replace('_permanent_deformation','')] ?? item.display_name, value:item.value_double, verdict:item.verdict }))
  const location = (key:string) => overview.result_locations.find((item) => item.variable_key === key)
  if (!results.length) return <div className="empty-widget">Chassis Rear 결과가 없습니다.</div>
  if (type === 'chassis_summary') return <div className="chassis-widget-summary"><div><span>전체 판정</span><strong className={verdict.toLowerCase()}>{verdict}</strong></div><div><span>최대 영구변형</span><strong>{maximum.toFixed(2)} mm</strong></div>{canManageThreshold ? <label><span>관리 기준</span><div><input aria-label="Chassis Rear 영구변형 기준값" type="number" min="0.1" step="0.1" value={draftLimit} onChange={(e)=>setDraftLimit(Number(e.target.value))}/><button disabled={!Number.isFinite(draftLimit) || draftLimit <= 0} onClick={()=>onSaveThreshold(draftLimit)}>저장</button></div></label> : <div><span>관리 기준</span><strong>{limit.toFixed(1)} mm</strong></div>}<p>적용 대상 {results.length}개 · mm 범위 {minimum.toFixed(2)}~{maximum.toFixed(2)} · 기준 이상은 FAIL입니다.</p></div>
  if (type === 'chassis_diagram') return <div className="chassis-diagram-body compact"><div className="chassis-shell"><div className="chassis-ribs"/><div className="chassis-center"><i/><i/><i/><i/></div>{results.map((item)=><div key={item.id} className={`chassis-marker ${markerClasses[item.variable_key] ?? ''} ${item.verdict.toLowerCase()}`}><span>{item.value_double.toFixed(1)} mm</span><small>{chartData.find((entry)=>entry.value===item.value_double)?.name}</small></div>)}</div><div className="chassis-legend"><span><i className="pass"/>기준 미만</span><span><i className="fail"/>기준 이상</span></div></div>
  if (type === 'chassis_bar') return <ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} layout="vertical" margin={{top:8,right:24,left:8,bottom:4}}><CartesianGrid horizontal={false} stroke="var(--color-chart-grid)"/><XAxis type="number" domain={[0,Math.max(8,limit+2)]}/><YAxis type="category" dataKey="name" width={60} tick={{fill:'#8fa6bb',fontSize:10.8}}/><Tooltip formatter={(value:number)=>[`${value} mm`,'영구변형']}/><ReferenceLine x={limit} stroke="var(--color-warning)" strokeDasharray="5 4"/><Bar dataKey="value">{chartData.map((entry)=><Cell key={entry.name} fill={entry.verdict==='FAIL'?'#ff5d73':'#4fd6a0'}/>)}</Bar></BarChart></ResponsiveContainer>
  if (type === 'chassis_table') return <div className="chassis-result-table"><div className="chassis-result-head"><span>측정 위치</span><span>영구변형</span><span>기준</span><span>판정</span></div>{results.map((item)=>{const point=location(item.variable_key); return <div className="chassis-result-row" key={item.id}><strong>{item.display_name}{point&&<small>NODE {point.entity_id} · ({point.x.toFixed(1)}, {point.y.toFixed(1)}, {point.z.toFixed(1)})</small>}</strong><span>{item.value_double.toFixed(1)} mm</span><span>{hasNumericValue(item.threshold_double) ? `${item.threshold_double.toFixed(1)} mm` : ''}</span><b className={item.verdict.toLowerCase()}>{item.verdict}</b></div>})}</div>
  return <div className="empty-widget">지원하지 않는 Chassis 위젯입니다.</div>
}

function ChassisRearDashboard({ overview, threshold, onSaveThreshold }: { overview: Overview; threshold?: QualityThreshold; onSaveThreshold: (value: number) => void }) {
  const results = overview.scalar_results.filter((item) => item.result_group === 'CHASSIS_REAR' && item.unit.toLowerCase() === 'mm' && item.variable_key.includes('permanent_deformation') && hasNumericValue(item.value_double))
  const limit = threshold?.threshold_double ?? results.find((item) => hasNumericValue(item.threshold_double))?.threshold_double ?? 5
  const [draftLimit, setDraftLimit] = useState(limit)
  useEffect(() => setDraftLimit(limit), [limit])
  const verdict = results.some((item) => item.verdict === 'FAIL') ? 'FAIL' : 'PASS'
  const maximum = Math.max(...results.map((item) => item.value_double), 0)
  const markerClasses: Record<string, string> = {
    chassis_rear_top_edge_gap_permanent_deformation: 'top-edge',
    chassis_rear_bottom_edge_gap_permanent_deformation: 'bottom-edge',
    chassis_rear_corner_top_left_permanent_deformation: 'top-left',
    chassis_rear_corner_top_right_permanent_deformation: 'top-right',
    chassis_rear_corner_bottom_left_permanent_deformation: 'bottom-left',
    chassis_rear_corner_bottom_right_permanent_deformation: 'bottom-right',
  }
  const shortLabels: Record<string, string> = {
    top_edge_gap: '상단 엣지', bottom_edge_gap: '하단 엣지', corner_top_left: '좌상단', corner_top_right: '우상단', corner_bottom_left: '좌하단', corner_bottom_right: '우하단',
  }
  const chartData = results.map((item) => {
    const key = item.variable_key.replace('chassis_rear_', '').replace('_permanent_deformation', '')
    return { name: shortLabels[key] ?? item.display_name, value: item.value_double, verdict: item.verdict }
  })
  const resultLocation = (key: string) => overview.result_locations.find((item) => item.variable_key === key)

  return <div className="chassis-dashboard">
    <section className="chassis-summary-strip"><div><span>CHASSIS REAR RESULT</span><strong className={verdict.toLowerCase()}>{verdict}</strong></div><div><span>최대 영구변형</span><strong>{maximum.toFixed(1)} <small>mm</small></strong></div><div><span>관리 기준값</span><strong>{limit.toFixed(1)} <small>mm</small></strong></div><p>해석 결과는 응력이 아닌 영구변형만 판정에 사용합니다. 측정값이 기준 이상이면 FAIL입니다.</p></section>
    <div className="chassis-dashboard-grid">
      <article className="chassis-card chassis-diagram-card"><header><div><span className="widget-kicker">PERMANENT DEFORMATION MAP</span><h3>Chassis Rear 변형 위치</h3></div><span className="diagram-scene">{overview.load_case.analysis_type.replace('_', ' ')}</span></header><div className="chassis-diagram-body"><div className="chassis-shell"><div className="chassis-ribs"/><div className="chassis-center"><i/><i/><i/><i/></div>{results.map((item) => <div key={item.id} className={`chassis-marker ${markerClasses[item.variable_key] ?? ''} ${item.verdict.toLowerCase()}`}><span>{item.value_double.toFixed(1)} mm</span><small>{chartData.find((entry) => entry.value === item.value_double)?.name}</small></div>)}</div><div className="chassis-legend"><span><i className="pass"/>기준 미만</span><span><i className="fail"/>기준 이상</span><b>Open Cell 기준면 대비 거리 / 모서리 영구변형</b></div></div></article>
      <article className="chassis-card chassis-chart-card"><header><div><span className="widget-kicker">LOCATION COMPARISON</span><h3>위치별 영구변형</h3></div></header><div className="chassis-chart-body"><ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} layout="vertical" margin={{ top: 7, right: 22, left: 10, bottom: 4 }}><CartesianGrid horizontal={false} stroke="var(--color-chart-grid)"/><XAxis type="number" domain={[0, Math.max(8, limit + 2)]} tick={{ fill: 'var(--color-chart-axis)', fontSize: 10.8 }} axisLine={false} tickLine={false}/><YAxis type="category" dataKey="name" width={58} tick={{ fill: 'var(--color-chart-axis)', fontSize: 10.8 }} axisLine={false} tickLine={false}/><Tooltip contentStyle={{ background: 'var(--color-surface-raised)', color: 'var(--color-text)', border: '1px solid var(--color-border-strong)', borderRadius: 8 }} formatter={(value: number) => [`${value} mm`, '영구변형']}/><ReferenceLine x={limit} stroke="var(--color-warning)" strokeDasharray="5 4" label={{ value: `기준 ${limit}`, fill: 'var(--color-warning)', fontSize: 10.8 }}/><Bar dataKey="value" radius={[0,4,4,0]}>{chartData.map((entry) => <Cell key={entry.name} fill={entry.verdict === 'FAIL' ? 'var(--color-danger)' : 'var(--color-success)'}/>)}</Bar></BarChart></ResponsiveContainer></div></article>
      <article className="chassis-card threshold-card"><header><div><span className="widget-kicker">ADMIN CRITERION</span><h3>관리자 판정 기준</h3></div></header><div className="threshold-body"><label><span>목표값</span><div><input aria-label="Chassis Rear 영구변형 기준값" type="number" min="0.1" step="0.1" value={draftLimit} onChange={(event) => setDraftLimit(Number(event.target.value))}/><b>mm</b></div></label><button onClick={() => onSaveThreshold(draftLimit)} disabled={!Number.isFinite(draftLimit) || draftLimit <= 0}>기준값 저장</button><p>상·하 엣지 이격 및 네 모서리 영구변형에 동일 기준을 적용합니다.</p></div></article>
      <article className="chassis-card chassis-result-card"><header><div><span className="widget-kicker">FAILURE JUDGEMENT 02</span><h3>Chassis Rear 상세 판정</h3></div></header><div className="chassis-result-table"><div className="chassis-result-head"><span>측정 위치</span><span>영구변형</span><span>기준</span><span>판정</span></div>{results.map((item) => { const location = resultLocation(item.variable_key); return <div className="chassis-result-row" key={item.id}><strong>{item.display_name}{location && <small>NODE {location.entity_id} · ({location.x.toFixed(1)}, {location.y.toFixed(1)}, {location.z.toFixed(1)})</small>}</strong><span>{item.value_double.toFixed(1)} mm</span><span>{hasNumericValue(item.threshold_double) ? `${item.threshold_double.toFixed(1)} mm` : ''}</span><b className={item.verdict.toLowerCase()}>{item.verdict}</b></div> })}</div></article>
    </div>
  </div>
}
