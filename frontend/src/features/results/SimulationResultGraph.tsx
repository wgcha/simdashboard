import { useEffect, useMemo, useRef, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Expand } from 'lucide-react'
import type { DashboardMember } from '../../shared/api/simulationDashboard'

type ChartMode = 'bar' | 'dot' | 'line'
export type SimulationChartPoint = { scene_id: string; member_id: string; scene: string; scene_sequence_number: number | null; order_status?: string | null; member: string; color: string; value: number | null; unit: string | null }
type ChartRow = { scene_id: string; scene: string; points: Record<string, SimulationChartPoint>; lineValues: Record<string, number | null> }
type Props = { chartId: string; title: string; points: SimulationChartPoint[]; members: DashboardMember[]; onSelect: (sceneId: string, memberId: string) => void; valueName: string }

export function SimulationResultGraph({ chartId, title, points, members, onSelect, valueName }: Props) {
  const [mode, setMode] = useState<ChartMode>('bar')
  const [expanded, setExpanded] = useState(false)
  const dialog = useRef<HTMLDialogElement>(null)
  // All lines share one categorical axis. Per-Line data makes Recharts concatenate
  // domains, duplicating Scene labels and displacing the path relative to its dots.
  const chartRows = useMemo(() => {
    const rows = new Map<string, ChartRow>()
    for (const point of points) {
      const row = rows.get(point.scene_id) ?? { scene_id: point.scene_id, scene: point.scene, points: {}, lineValues: {} }
      row.points[point.member_id] = point
      row.lineValues[point.member_id] = point.scene_sequence_number == null || point.order_status !== 'CONFIRMED' ? null : point.value
      rows.set(point.scene_id, row)
    }
    return [...rows.values()]
  }, [points])
  const labels = new Map(chartRows.map((row) => [row.scene_id, row.scene]))
  const colors = new Map(points.map((point) => [point.member_id, point.color]))
  const hasUnconfirmedOrder = points.some((point) => point.scene_sequence_number == null || point.order_status !== 'CONFIRMED')
  const close = () => { dialog.current?.close(); setExpanded(false) }
  useEffect(() => { if (expanded && dialog.current && !dialog.current.open) dialog.current.showModal() }, [expanded])

  const graph = (large = false) => <div className={`simulation-dashboard__chart${large ? ' expanded' : ''}`} data-testid={`simulation-chart-${chartId}`}>
    <ResponsiveContainer width="100%" height="100%">
      {mode === 'bar' ? <BarChart data={points}>
        <CartesianGrid vertical={false} strokeDasharray="3 3" /><XAxis dataKey="scene" /><YAxis />
        <Tooltip<number, string> formatter={(value, _name, item) => [`${value} ${item?.payload?.unit ?? '단위 미확인'}`, `${item?.payload?.member ?? ''} ${valueName}`]} />
        <Bar dataKey="value" isAnimationActive={false} onClick={(_, index) => { const point = points[index]; if (point?.value != null) onSelect(point.scene_id, point.member_id) }}>
          {points.map((point) => <Cell key={`${point.scene_id}:${point.member_id}`} fill={point.color} />)}
        </Bar>
      </BarChart> : <LineChart data={chartRows}>
        <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="scene_id" tickFormatter={(id: string) => labels.get(id) ?? id} /><YAxis />
        <Tooltip<number, string> labelFormatter={(id) => labels.get(String(id)) ?? id} formatter={(value, name, item) => {
          const point = (item?.payload as ChartRow | undefined)?.points[String(name)]
          return [`${value} ${point?.unit ?? '단위 미확인'}`, `${point?.member ?? name} ${valueName}`]
        }} />
        {mode === 'line' ? <Legend /> : null}
        {mode === 'line' ? members.map((member) => <Line key={`${member.id}-path`} dataKey={(row: ChartRow) => row.lineValues[member.id] ?? null} name={member.label} tooltipType="none" type="linear" connectNulls={false} stroke={colors.get(member.id)} dot={false} activeDot={false} isAnimationActive={false} />) : null}
        {members.map((member) => <Line key={`${member.id}-points`} dataKey={(row: ChartRow) => row.points[member.id]?.value ?? null} name={member.id} legendType="none" type="linear" connectNulls={false} stroke="transparent" activeDot={false} isAnimationActive={false} dot={(props) => {
          const dot = props as { cx?: number; cy?: number; payload?: ChartRow }
          const point = dot.payload?.points[member.id]
          return <SelectableDot key={`${member.id}:${dot.payload?.scene_id}`} cx={dot.cx} cy={dot.cy} point={point} onSelect={onSelect} />
        }} />)}
      </LineChart>}
    </ResponsiveContainer>
  </div>

  return <>
    <div className="simulation-dashboard__chart-controls">
      <label>{title} 그래프 종류<select aria-label={`${title} 그래프 종류`} value={mode} onChange={(event) => setMode(event.target.value as ChartMode)}><option value="bar">막대</option><option value="dot">점</option><option value="line">점과 선</option></select></label>
      <button type="button" aria-label={`${title} 그래프 확대`} onClick={() => setExpanded(true)}><Expand /> 확대</button>
    </div>
    {graph()}
    {hasUnconfirmedOrder && mode === 'line' ? <small className="simulation-dashboard__chart-notice">순번 또는 정렬 상태가 미확인인 Scene은 선으로 연결하지 않았습니다.</small> : null}
    {expanded ? <dialog ref={dialog} className="simulation-dashboard__lightbox simulation-dashboard__chart-lightbox" onCancel={(event) => { event.preventDefault(); close() }} onClose={() => setExpanded(false)}>
      <button type="button" onClick={close} aria-label="그래프 확대 닫기">닫기</button><h3>{title}</h3>{graph(true)}
    </dialog> : null}
  </>
}

function SelectableDot({ cx, cy, point, onSelect }: { cx?: number; cy?: number; point?: SimulationChartPoint; onSelect: Props['onSelect'] }) {
  if (cx == null || cy == null || !point || point.value == null) return null
  return <circle className="recharts-line-dot" cx={cx} cy={cy} r={3} fill={point.color} stroke="var(--color-surface-raised)" strokeWidth={1} role="button" tabIndex={0} aria-label={`${point.member} Scene ${point.scene}: ${point.value}`} onClick={() => onSelect(point.scene_id, point.member_id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(point.scene_id, point.member_id) } }} />
}
