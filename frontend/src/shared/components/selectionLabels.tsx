import { useMemo, useState } from 'react'

export type SelectionMetadata = {
  code?: string | null
  name?: string | null
  project?: { id?: string; name?: string | null } | null
  request?: { id?: string; name?: string | null } | null
  analysis_type?: string | null
  relative_path?: string | null
}

type SelectionRecord = { id: string; name?: string | null; title?: string | null; product_name?: string | null; code?: string | null; analysis_type?: string | null; selection_metadata?: SelectionMetadata | null }
export type SelectionKind = 'project' | 'request' | 'load_case'

function clean(value: unknown) { return typeof value === 'string' ? value.trim() : '' }
function shortId(value: string) { return value.length <= 10 ? value : `${value.slice(0, 6)}…${value.slice(-3)}` }

export function selectionMetadata(item: SelectionRecord): SelectionMetadata {
  const metadata = item.selection_metadata ?? {}
  return { ...metadata, code: clean(metadata.code) || clean(item.code) || null, name: clean(metadata.name) || clean(item.name) || clean(item.title), analysis_type: clean(metadata.analysis_type) || clean(item.analysis_type) || null }
}

function baseLabel(item: SelectionRecord, kind: SelectionKind) {
  const meta = selectionMetadata(item)
  const code = clean(meta.code)
  const name = clean(meta.name) || item.id
  if (code && code !== name) return `${code} · ${name}`
  if (code) return code
  if (name) return name
  return kind === 'load_case' ? item.id : item.id
}

function contextLabel(item: SelectionRecord, kind: SelectionKind) {
  const meta = selectionMetadata(item)
  const context = kind === 'request' ? clean(meta.project?.name) : kind === 'load_case' ? clean(meta.request?.name) || clean(meta.project?.name) : ''
  const analysis = kind === 'load_case' ? clean(meta.analysis_type) : ''
  const path = clean(meta.relative_path)
  return [context, analysis, path].filter(Boolean).join(' · ')
}

export function selectionLabels<T extends SelectionRecord>(items: readonly T[], kind: SelectionKind): Map<string, string> {
  const bases = new Map<string, number>()
  items.forEach((item) => bases.set(baseLabel(item, kind), (bases.get(baseLabel(item, kind)) ?? 0) + 1))
  const labels = new Map<string, string>()
  items.forEach((item) => {
    const base = baseLabel(item, kind)
    if ((bases.get(base) ?? 0) < 2) { labels.set(item.id, base); return }
    const context = contextLabel(item, kind)
    const contextual = context ? `${base} · ${context}` : base
    labels.set(item.id, contextual || `${base} · ID ${shortId(item.id)}`)
  })
  // A parent/name/path can still collide. Grow the fallback ID until each option is unique.
  const seen = new Map<string, number>()
  labels.forEach((label, id) => { seen.set(label, (seen.get(label) ?? 0) + 1); labels.set(id, label) })
  labels.forEach((label, id) => { if ((seen.get(label) ?? 0) > 1) labels.set(id, `${label} · ID ${id}`) })
  return labels
}

export function SearchableSelect<T extends SelectionRecord>({ ariaLabel, items, kind, value, onChange, disabled = false, required = false, placeholder, className }: {
  ariaLabel: string; items: readonly T[]; kind: SelectionKind; value: string; onChange: (value: string) => void; disabled?: boolean; required?: boolean; placeholder: string; className?: string
}) {
  const [query, setQuery] = useState('')
  const labels = useMemo(() => selectionLabels(items, kind), [items, kind])
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    return needle ? items.filter((item) => `${labels.get(item.id) ?? ''} ${item.id} ${JSON.stringify(selectionMetadata(item))}`.toLocaleLowerCase().includes(needle)) : items
  }, [items, labels, query])
  const visibleItems = value && !filtered.some((item) => item.id === value) ? [items.find((item) => item.id === value), ...filtered].filter((item): item is T => Boolean(item)) : filtered
  return <span className={className ? `selection-control ${className}` : 'selection-control'}>
    <input type="search" aria-label={`${ariaLabel} 검색`} placeholder={`${ariaLabel} 검색`} value={query} onChange={(event) => setQuery(event.target.value)} disabled={disabled} />
    <select aria-label={ariaLabel} title={labels.get(value) ?? undefined} value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled} required={required}>
      <option value="">{placeholder}</option>
      {value && !visibleItems.some((item) => item.id === value) ? <option value={value}>{value}</option> : null}
      {visibleItems.map((item) => <option key={item.id} value={item.id} title={labels.get(item.id) ?? item.id}>{labels.get(item.id) ?? item.id}</option>)}
    </select>
  </span>
}

export function selectionOptionLabel(item: SelectionRecord, kind: SelectionKind, items: readonly SelectionRecord[] = [item]) { return selectionLabels(items, kind).get(item.id) ?? baseLabel(item, kind) }
