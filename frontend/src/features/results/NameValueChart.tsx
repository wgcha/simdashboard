import { useMemo } from 'react'
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import type { ScalarResult } from '../../types'
import './NameValueChart.css'

type ChartStyle = 'bar' | 'dot' | 'line'

type ChartRow = {
  id: string
  name: string
  value: number
}

type UnitGroup = {
  unit: string
  rows: ChartRow[]
}

const CHART_COLORS = ['var(--color-chart-series-1)', 'var(--color-chart-series-3)', 'var(--color-chart-series-4)', 'var(--color-chart-series-5)']

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function normalizeStyle(value: unknown): ChartStyle {
  return value === 'dot' || value === 'line' ? value : 'bar'
}

function unitLabel(unit: string): string {
  return unit.trim() || '단위 없음'
}

function CategoryTick({ x = 0, y = 0, payload }: { x?: number; y?: number; payload?: { value?: unknown } }) {
  const label = String(payload?.value ?? '')
  const firstLine = label.slice(0, 9)
  const remainder = label.slice(9)
  const secondLine = remainder ? `${remainder.slice(0, 8)}${remainder.length > 8 ? '…' : ''}` : ''
  return <g transform={`translate(${x},${y})`}>
    <title>{label}</title>
    <text fill="var(--color-chart-axis)" fontSize={11} textAnchor="middle" y={12}>
      <tspan x={0}>{firstLine}</tspan>
      {secondLine ? <tspan x={0} dy={12}>{secondLine}</tspan> : null}
    </text>
  </g>
}

function exactNumber(value: number): string {
  return String(value)
}

function valueDomain(rows: ChartRow[]): [number, number] {
  let minimum = 0
  let maximum = 0
  for (const row of rows) {
    minimum = Math.min(minimum, row.value)
    maximum = Math.max(maximum, row.value)
  }
  if (minimum === maximum) {
    const padding = Math.abs(maximum) || 1
    return [minimum - padding * 0.15, maximum + padding * 0.15]
  }
  const padding = (maximum - minimum) * 0.1
  return [minimum - padding, maximum + padding]
}

function UnitChart({ group, style, color }: { group: UnitGroup; style: ChartStyle; color: string }) {
  const domain = valueDomain(group.rows)
  const tooltip = <Tooltip
    contentStyle={{ background: 'var(--color-surface-raised)', color: 'var(--color-text)', border: '1px solid var(--color-border-strong)', borderRadius: 8 }}
    formatter={(value: number) => [exactNumber(value), group.unit]}
    labelFormatter={(name) => `항목: ${String(name)}`}
  />
  const xAxis = <XAxis type="category" dataKey="name" interval={0} height={52} padding={{ left:48, right:48 }} tick={<CategoryTick />} axisLine={false} tickLine={false} />
  const yAxis = <YAxis type="number" dataKey="value" domain={domain} width={80} tick={{ fill: 'var(--color-chart-axis)', fontSize: 11 }} axisLine={false} tickLine={false} />
  const minimumWidth = Math.max(360, group.rows.length * 180 + 160)

  return <section className="name-value-chart__unit" aria-label={`${group.unit} 항목-값 그래프`}>
    <header><span>{group.unit}</span><small>{group.rows.length}개 항목</small></header>
    <div className="name-value-chart__plot">
      <div className="name-value-chart__plot-inner" style={{ minWidth: minimumWidth }}>
        <ResponsiveContainer width="100%" height="100%">
          {style === 'bar' ? <BarChart data={group.rows} margin={{ top: 4, right: 20, left: 12, bottom: 2 }}>
          <CartesianGrid vertical={false} stroke="var(--color-chart-grid)" strokeDasharray="3 3" />
          {xAxis}{yAxis}{tooltip}
          <Bar dataKey="value" fill={color} radius={[4, 4, 0, 0]} isAnimationActive={false} />
        </BarChart> : <LineChart data={group.rows} margin={{ top: 4, right: 20, left: 12, bottom: 2 }}>
          <CartesianGrid vertical={false} stroke="var(--color-chart-grid)" strokeDasharray="3 3" />
          {xAxis}{yAxis}{tooltip}
          <Line dataKey="value" type="linear" name={group.unit} stroke={style === 'dot' ? 'transparent' : color} strokeWidth={2} dot={{ r: 4, fill: color, stroke: 'var(--color-surface-raised)', strokeWidth: 1.5 }} activeDot={{ r: 6 }} isAnimationActive={false} />
          </LineChart>}
        </ResponsiveContainer>
      </div>
    </div>
  </section>
}

export function NameValueChart({ results, chartStyle, color }: { results: ScalarResult[]; chartStyle?: unknown; color?: unknown }) {
  const groups = useMemo<UnitGroup[]>(() => {
    const byUnit = new Map<string, ChartRow[]>()
    for (const item of results) {
      if (!isFiniteNumber(item.value_double)) continue
      const unit = unitLabel(item.unit ?? '')
      const rows = byUnit.get(unit) ?? []
      rows.push({ id: item.id, name: item.display_name || item.variable_key, value: item.value_double })
      byUnit.set(unit, rows)
    }
    return [...byUnit].map(([unit, rows]) => ({ unit, rows }))
  }, [results])

  if (!groups.length) return <div className="widget-empty">표시할 숫자 결과가 없습니다.</div>

  const style = normalizeStyle(chartStyle)
  const accentColor = typeof color === 'string' && color.trim() ? color : CHART_COLORS[0]
  return <div className={`name-value-chart ${groups.length === 1 ? 'name-value-chart--single' : ''}`}>
    <div className="name-value-chart__groups">
      {groups.map((group, index) => <UnitChart key={group.unit} group={group} style={style} color={groups.length === 1 ? accentColor : CHART_COLORS[index % CHART_COLORS.length]} />)}
    </div>
  </div>
}
