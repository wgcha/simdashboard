import { useEffect, useMemo, useState } from 'react'
import { CartesianGrid, Dot, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import './ComparisonSeriesChart.css'
import { comparisonXDomain, comparisonYAxisDomain, prepareComparisonSeries, type ComparisonSeriesPoint, type ChartPoint } from './comparisonSeriesAxes'

export { comparisonXDomain, comparisonYAxisDomain, finiteComparisonValue, prepareComparisonSeries } from './comparisonSeriesAxes'

function isolatedDot(points: ChartPoint[], dataKey: 'baseline_value' | 'target_value') {
  return (props: { index?: number; cx?: number; cy?: number }) => {
    const index = props.index ?? -1
    if (index < 0 || points[index]?.[dataKey] == null || points[index - 1]?.[dataKey] != null || points[index + 1]?.[dataKey] != null) return <g />
    return <Dot {...props} r={3} />
  }
}

export function ComparisonSeriesChart({ series, baselineRunNo, targetRunNo, baselineRunId, targetRunId }: { series: { variable_key: string; display_name: string; unit: string; points: ComparisonSeriesPoint[] }; baselineRunNo: number; targetRunNo: number; baselineRunId: string; targetRunId: string }) {
  const [includeZero, setIncludeZero] = useState(true)
  const points = useMemo(() => prepareComparisonSeries(series.points), [series.points])
  useEffect(() => { setIncludeZero(true) }, [series.variable_key, series.unit, baselineRunId, targetRunId])
  const xDomain = useMemo(() => comparisonXDomain(points), [points])
  const domain = useMemo(() => comparisonYAxisDomain(points, includeZero), [points, includeZero])
  const timeUnit = points.find((point) => point.time_unit)?.time_unit ?? ''

  return <div className="comparison-series-axes" data-testid="comparison-series-axes" data-x-domain={xDomain.join(',')} data-y-domain={domain.join(',')}>
    <div className="comparison-series-toolbar"><span className="comparison-series-axis-mode">공통 축 · {includeZero ? '0 포함' : '데이터 범위'}</span><label className="comparison-series-scale-toggle"><input type="checkbox" checked={!includeZero} onChange={(event) => setIncludeZero(!event.target.checked)} /> 데이터 범위만 보기</label></div>
    <div className="comparison-series-plot"><ResponsiveContainer width="100%" height="100%"><LineChart data={points} margin={{ top: 12, right: 20, left: 8, bottom: 8 }}>
      <CartesianGrid vertical={false} stroke="var(--color-chart-grid)" strokeDasharray="3 3" />
      <XAxis type="number" dataKey="time_value" domain={xDomain} name="시간" unit={timeUnit} tick={{ fill: 'var(--color-chart-axis)', fontSize: 10.8 }} axisLine={false} />
      <YAxis width={85} type="number" domain={domain} name={series.display_name} unit={series.unit} tick={{ fill: 'var(--color-chart-axis)', fontSize: 10.8 }} axisLine={false} />
      <Tooltip contentStyle={{ background: 'var(--color-surface-raised)', color: 'var(--color-text)', border: '1px solid var(--color-border-strong)', borderRadius: 8 }} />
      <Legend />
      <Line isAnimationActive={false} type="linear" dataKey="baseline_value" name={`기준 Run ${baselineRunNo} (${series.unit})`} stroke="var(--color-chart-series-1)" dot={isolatedDot(points, 'baseline_value')} strokeWidth={2} connectNulls={false} />
      <Line isAnimationActive={false} type="linear" dataKey="target_value" name={`대상 Run ${targetRunNo} (${series.unit})`} stroke="var(--color-chart-series-target)" dot={isolatedDot(points, 'target_value')} strokeWidth={2} connectNulls={false} />
    </LineChart></ResponsiveContainer></div>
  </div>
}
