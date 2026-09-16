import { useId, useMemo, useRef, useState } from 'react'
import './ItemTransferPicker.css'

export type ItemTransferOption = {
  id: string
  label: string
  description?: string
  disabled?: boolean
}

type Props = {
  options: ItemTransferOption[]
  value: string[]
  onChange: (ids: string[]) => void
  label: string
  emptyMessage?: string
}

function matches(option: ItemTransferOption, query: string) {
  if (!query) return true
  const searchable = `${option.label} ${option.description ?? ''} ${option.id}`.toLocaleLowerCase()
  return searchable.includes(query.toLocaleLowerCase())
}

/** A keyboard-accessible, source-order-preserving alternative to multi-select controls. */
export function ItemTransferPicker({ options, value, onChange, label, emptyMessage = '표시할 항목이 없습니다.' }: Props) {
  const [query, setQuery] = useState('')
  const searchId = useId()
  const rootRef = useRef<HTMLElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const selected = useMemo(() => [...new Set(value)], [value])
  const selectedSet = useMemo(() => new Set(selected), [selected])
  const optionById = useMemo(() => new Map(options.map((option) => [option.id, option])), [options])
  const available = options.filter((option) => !selectedSet.has(option.id) && matches(option, query))
  const applied = selected.map((id) => optionById.get(id) ?? { id, label: id, description: '현재 목록에서 찾을 수 없는 항목' })
  const focusAction = (actionLabel: string) => requestAnimationFrame(() => {
    const button = Array.from(rootRef.current?.querySelectorAll('button') ?? []).find((candidate) => candidate.getAttribute('aria-label') === actionLabel)
    const target = button ?? searchRef.current
    target?.focus()
  })
  const add = (option: ItemTransferOption) => {
    if (!selectedSet.has(option.id)) onChange([...selected, option.id])
    focusAction(`${option.label} 제외`)
  }
  const remove = (option: ItemTransferOption) => {
    onChange(selected.filter((selectedId) => selectedId !== option.id))
    focusAction(`${option.label} 추가`)
  }
  const addAll = () => {
    const next = available.filter((option) => !option.disabled).map((option) => option.id)
    if (next.length) onChange([...selected, ...next])
  }
  return <section ref={rootRef} className="item-transfer-picker" aria-label={label}>
    <div className="item-transfer-picker-heading"><label htmlFor={searchId}>{label} 검색</label><input ref={searchRef} id={searchId} aria-label={`${label} 검색`} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="이름 또는 설명 검색" /><span>{selected.length}개 적용</span></div>
    <div className="item-transfer-picker-actions"><button type="button" onClick={addAll} disabled={!available.some((option) => !option.disabled)}>검색 결과 모두 추가</button><button type="button" onClick={() => onChange([])} disabled={!selected.length}>전체 제외</button></div>
    <div className="item-transfer-picker-lists">
      <fieldset><legend>미적용 항목</legend><ul>{available.length ? available.map((option) => <li key={option.id}><div><strong title={option.label}>{option.label}</strong>{option.description ? <small title={option.description}>{option.description}</small> : null}</div><button type="button" aria-label={`${option.label} 추가`} disabled={option.disabled} onClick={() => add(option)}>추가</button></li>) : <li className="item-transfer-picker-empty">{emptyMessage}</li>}</ul></fieldset>
      <fieldset><legend>적용 항목</legend><ul>{applied.length ? applied.map((option) => <li key={option.id}><div><strong title={option.label}>{option.label}</strong>{option.description ? <small title={option.description}>{option.description}</small> : null}</div><button type="button" aria-label={`${option.label} 제외`} onClick={() => remove(option)}>제외</button></li>) : <li className="item-transfer-picker-empty">적용한 항목이 없습니다.</li>}</ul></fieldset>
    </div>
  </section>
}
