import { useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts'
import type { PreviewWidget } from '../api/semanticMapping'
import './semanticResults.css'

type Observation = Record<string, unknown>

export function SemanticWidgetGrid({ widgets }: { widgets: PreviewWidget[] }) {
  return <div className="semantic-result-grid">{widgets.map((widget) => <SemanticWidgetCard key={widget.id} widget={widget} />)}</div>
}

function scalarValue(value: unknown) {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number' && !Number.isFinite(value)) return '—'
  return String(value)
}

function chartSeries(data: unknown[]) {
  const series = new Map<string, { name: string; key: string; rows: Array<{ x: number; [key: string]: number }> }>()
  data.forEach((entry, observationIndex) => {
    const row = (entry && typeof entry === 'object' ? entry : {}) as Observation
    const dimensions = row.dimensions && typeof row.dimensions === 'object' ? JSON.stringify(row.dimensions) : ''
    const itemKey = String(row.item_id ?? row.series_key ?? row.series ?? 'series')
    const label = String(row.label ?? row.series_key ?? row.series ?? row.item_id ?? `series-${observationIndex}`)
    const name = dimensions ? `${label} · ${dimensions}` : label
    const seriesKey = `${itemKey}:${dimensions}:${observationIndex}`
    const points = Array.isArray(row.points) ? row.points : [{ x: observationIndex, y: row.value }]
    const existing = series.get(seriesKey)
    const key = existing?.key ?? `series_${series.size}`
    const rows = points.flatMap((point) => {
      if (!point || typeof point !== 'object') return []
      const candidate = point as { x?: unknown; y?: unknown }
      if (typeof candidate.x !== 'number' || typeof candidate.y !== 'number') return []
      return [{ x: candidate.x, [key]: candidate.y }]
    })
    series.set(seriesKey, existing ? { ...existing, rows: [...existing.rows, ...rows] } : { name, key, rows })
  })
  return Array.from(series.values())
}

function safeMediaUrl(value: unknown) {
  if (typeof value !== 'string' || !value) return null
  try {
    const parsed = new URL(value, window.location.origin)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : null
  } catch { return null }
}

export function SemanticWidgetCard({ widget }: { widget: PreviewWidget }) {
  const isReady = widget.status === 'READY'
  const data = Array.isArray(widget.data) ? widget.data : []
  const first = data[0] && typeof data[0] === 'object' ? data[0] as Observation : null
  const decimals = typeof (widget as PreviewWidget & { decimals?: number }).decimals === 'number' ? (widget as PreviewWidget & { decimals?: number }).decimals! : 2
  const unit = widget.unit ? ` ${widget.unit}` : ''
  const mediaUrl = safeMediaUrl(first?.asset_url ?? first?.url)
  return <article className={`semantic-result-widget type-${widget.type} ${isReady ? 'ready' : 'invalid'}`}><header><span>{widget.type}</span><strong>{widget.title}</strong><b>{widget.status}</b></header>{widget.message ? <p className="semantic-result-message"><AlertTriangle />{widget.message}</p> : null}{isReady && (widget.type === 'image' || widget.type === 'video') ? mediaUrl ? widget.type === 'image' ? <img className="semantic-result-media" src={mediaUrl} alt={widget.title} /> : <video className="semantic-result-media" src={mediaUrl} controls /> : <p className="semantic-result-message">지원된 안전한 미디어 URL이 없습니다.</p> : null}{isReady && (widget.type === 'line' || widget.type === 'scatter' || widget.type === 'bar') ? <Chart widget={widget} data={data} /> : null}{isReady && (widget.type === 'kpi' || widget.type === 'gauge') ? <div className="semantic-result-kpi"><strong>{first && 'value' in first ? scalarValue(typeof first.value === 'number' ? first.value.toFixed(decimals) : first.value) : '—'}</strong><span>{unit}{widget.type === 'gauge' && (widget as PreviewWidget & { threshold?: number }).threshold !== undefined ? ` · 기준 ${(widget as PreviewWidget & { threshold?: number }).threshold}${unit}` : ''}</span>{widget.type === 'gauge' && (widget as PreviewWidget & { verdict?: string }).verdict ? <b>{(widget as PreviewWidget & { verdict?: string }).verdict}</b> : null}</div> : null}{isReady && widget.type === 'table' ? <ResultTable data={data} decimals={decimals} /> : null}{isReady && (widget.type === 'bar' || widget.type === 'scatter' || widget.type === 'line') && !data.length ? <span className="semantic-result-empty">데이터 없음</span> : null}</article>
}

function Chart({ widget, data }: { widget: PreviewWidget; data: unknown[] }) {
  if (widget.type === 'scatter') {
    const points = data.flatMap((entry) => { const row = entry as Observation; return typeof row.x === 'number' && typeof row.y === 'number' ? [{ x: row.x, y: row.y, label: row.label ?? row.item_id ?? widget.title }] : [] })
    return <div className="semantic-result-chart"><ResponsiveContainer width="100%" height={180}><ScatterChart><CartesianGrid strokeDasharray="3 3"/><XAxis type="number" dataKey="x" label={{ value: `${(widget as PreviewWidget & { x_unit?: string }).x_unit ?? ''}`, position: 'insideBottomRight', offset: -3 }} /><YAxis type="number" dataKey="y" label={{ value: `${(widget as PreviewWidget & { y_unit?: string }).y_unit ?? widget.unit ?? ''}`, angle: -90, position: 'insideLeft' }} /><Tooltip/><Scatter isAnimationActive={false} name={widget.title} data={points} fill="#51d3ff" /></ScatterChart></ResponsiveContainer></div>
  }
  if (widget.type === 'bar') {
    const bars = data.flatMap((entry) => {
      const row = entry as Observation
      if (typeof row.value !== 'number') return []
      const dimensions = row.dimensions && Object.keys(row.dimensions as object).length ? ` · ${JSON.stringify(row.dimensions)}` : ''
      return [{ name: `${row.label ?? row.item_id}${dimensions}`, value: row.value }]
    })
    return <div className="semantic-result-chart"><ResponsiveContainer width="100%" height={220}><BarChart data={bars}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="name" /><YAxis label={{ value: widget.unit ?? '', angle: -90, position: 'insideLeft' }} /><Tooltip /><Bar isAnimationActive={false} dataKey="value" name={widget.title} fill="#51d3ff" /></BarChart></ResponsiveContainer></div>
  }
  const series = chartSeries(data)
  const points = new Map<number, Record<string, number>>()
  for (const item of series) for (const row of item.rows) points.set(row.x, { ...points.get(row.x), ...row })
  const combined = Array.from(points.values()).sort((a, b) => a.x - b.x)
  return <div className="semantic-result-chart"><ResponsiveContainer width="100%" height={220}><LineChart data={combined}><CartesianGrid strokeDasharray="3 3" /><XAxis type="number" dataKey="x" domain={['dataMin', 'dataMax']} label={{ value: widget.x_unit ?? '', position: 'insideBottomRight', offset: -3 }} /><YAxis label={{ value: widget.unit ?? '', angle: -90, position: 'insideLeft' }} /><Tooltip />{series.map((item, index) => <Line isAnimationActive={false} key={item.key} dataKey={item.key} name={item.name} type="linear" stroke={['#51d3ff', '#4fd6a0', '#f8bd60', '#cf99f0'][index % 4]} dot={item.rows.length <= 12} connectNulls />)}</LineChart></ResponsiveContainer></div>
}

function ResultTable({ data, decimals }: { data: unknown[]; decimals: number }) {
  const [page, setPage] = useState(0)
  const rows = data.filter((entry): entry is Observation => Boolean(entry && typeof entry === 'object'))
  const pageCount = Math.max(1, Math.ceil(rows.length / 50))
  const current = Math.min(page, pageCount - 1)
  const cell = (row: Observation, key: string) => key === 'dimensions' && row[key] && typeof row[key] === 'object' ? Object.entries(row[key] as Record<string, unknown>).map(([name, value]) => `${name}: ${String(value)}`).join(' · ') || '—' : key === 'value' && typeof row[key] === 'number' && Number.isFinite(row[key]) ? row[key].toFixed(decimals) : scalarValue(row[key])
  const columns = [['label', '결과 항목'], ['dimensions', '측정 위치'], ['value', '값'], ['unit', '단위']]
  return <><table className="semantic-result-table"><thead><tr>{columns.map(([key, title]) => <th key={key}>{title}</th>)}</tr></thead><tbody>{rows.slice(current * 50, (current + 1) * 50).map((row, index) => <tr key={index}>{columns.map(([key]) => <td key={key}>{cell(row, key)}</td>)}</tr>)}</tbody></table><div className="semantic-result-count">총 {rows.length}개 · {current + 1}/{pageCount}쪽 <button disabled={current === 0} onClick={() => setPage(current - 1)}>이전</button><button disabled={current + 1 >= pageCount} onClick={() => setPage(current + 1)}>다음</button></div></>
}
