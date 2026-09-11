import { BarChart3, Check, ClipboardCheck, RotateCcw } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { RunComparison } from '../../types'
import './ComparisonEvidenceTable.css'

type EvidenceFilter = 'ALL' | 'TARGET_FAIL' | 'REGRESSION' | 'NOT_COMPARABLE'

const FILTERS: Array<{ key: EvidenceFilter; label: string }> = [
  { key: 'ALL', label: '전체' },
  { key: 'TARGET_FAIL', label: '대상 기준 미충족' },
  { key: 'REGRESSION', label: '이전 대비 악화' },
  { key: 'NOT_COMPARABLE', label: '비교 불가' },
]

const CHANGE_LABEL: Record<string, string> = {
  REGRESSION: '회귀', IMPROVED: '개선', UNCHANGED: '유지', ADDED: '추가', REMOVED: '제거', NOT_COMPARABLE: '비교 불가',
}

function valueLabel(value: number | null, unit: string | null) {
  return value == null ? '—' : `${value.toFixed(Math.abs(value) >= 100 ? 0 : 2)}${unit ? ` ${unit}` : ''}`
}

function baselineUnit(item: RunComparison['scalar_comparison'][number]) {
  return item.change === 'NOT_COMPARABLE' ? null : item.unit
}

function baselineNote(item: RunComparison['scalar_comparison'][number]) {
  if (item.change === 'ADDED') return '기준 Run에 없음'
  return `${item.baseline_verdict ?? '판정 없음'}${item.change === 'NOT_COMPARABLE' ? ' · 단위 확인 필요' : ''}`
}

function targetNote(item: RunComparison['scalar_comparison'][number]) {
  return item.change === 'REMOVED' ? '대상 Run에 없음' : (item.target_verdict ?? '판정 없음')
}

function filterMatches(filter: EvidenceFilter, item: RunComparison['scalar_comparison'][number]) {
  if (filter === 'TARGET_FAIL') return String(item.target_verdict ?? '').toUpperCase() === 'FAIL'
  if (filter === 'REGRESSION') return item.change === 'REGRESSION'
  if (filter === 'NOT_COMPARABLE') return !item.comparable || item.change === 'NOT_COMPARABLE'
  return true
}

export function ComparisonEvidenceTable({ comparison, selectedKey, onSelect, onViewSeries, onPrepareReview }: { comparison: RunComparison; selectedKey: string; onSelect: (key: string) => void; onViewSeries: (key: string) => void; onPrepareReview: (key: string) => void }) {
  const [filter, setFilter] = useState<EvidenceFilter>('ALL')
  const filtered = useMemo(() => comparison.scalar_comparison.filter((item) => filterMatches(filter, item)), [comparison.scalar_comparison, filter])
  const selected = comparison.scalar_comparison.find((item) => item.variable_key === selectedKey) ?? null
  const seriesAvailable = selected ? comparison.available_series.some((item) => item.variable_key === selected.variable_key) : false
  const counts = useMemo(() => Object.fromEntries(FILTERS.map(({ key }) => [key, comparison.scalar_comparison.filter((item) => filterMatches(key, item)).length])) as Record<EvidenceFilter, number>, [comparison.scalar_comparison])
  useEffect(() => {
    if (selectedKey && !filtered.some((item) => item.variable_key === selectedKey)) onSelect('')
  }, [filtered, onSelect, selectedKey])
  const visibleSelected = selected && filtered.some((item) => item.variable_key === selected.variable_key) ? selected : null

  return <section className="comparison-evidence-table" data-testid="comparison-evidence-table">
    <header className="comparison-evidence-header">
      <div><span>SCALAR EVIDENCE</span><h3>정량 결과 근거</h3><p>기준 Run과 대상 Run의 변수별 판정·변화를 확인합니다.</p></div>
      <div className="comparison-evidence-count" aria-label={`필터 결과 ${filtered.length}개`}><strong>{filtered.length}</strong><span>/ {comparison.scalar_comparison.length} 변수</span></div>
    </header>
    <nav className="comparison-evidence-filters" aria-label="정량 결과 필터">
      {FILTERS.map(({ key, label }) => <button key={key} type="button" className={filter === key ? 'active' : ''} aria-pressed={filter === key} data-testid={`comparison-filter-${key.toLowerCase()}`} onClick={() => setFilter(key)}>{label}<b>{counts[key]}</b></button>)}
      {filter !== 'ALL' && <button type="button" className="comparison-evidence-reset" onClick={() => setFilter('ALL')}><RotateCcw /> 필터 초기화</button>}
    </nav>
    {filtered.length > 0 ? <div className="comparison-evidence-body">
      <div className="comparison-evidence-columns" aria-hidden="true"><span>변수</span><span>기준</span><span>대상</span><span>차이</span><span>변화</span></div>
      <div className="comparison-evidence-rows">
        {filtered.map((item) => <button key={item.variable_key} type="button" className={`comparison-evidence-row ${selectedKey === item.variable_key ? 'selected' : ''} change-${item.change.toLowerCase()}`} aria-pressed={selectedKey === item.variable_key} data-testid="comparison-evidence-row" data-variable-key={item.variable_key} onClick={() => onSelect(item.variable_key)}>
          <span><strong>{item.display_name}</strong><code>{item.variable_key}</code></span>
          <span>{valueLabel(item.baseline_value, baselineUnit(item))}<small>{baselineNote(item)}</small></span>
          <span>{valueLabel(item.target_value, item.unit)}<small>{targetNote(item)}</small></span>
          <span>{item.delta == null || !item.comparable ? '—' : `${item.delta >= 0 ? '+' : ''}${item.delta.toFixed(2)}`}<small>{item.delta_percent == null || !item.comparable ? '' : `${item.delta_percent >= 0 ? '+' : ''}${item.delta_percent.toFixed(1)}%`}</small></span>
          <span><b>{CHANGE_LABEL[item.change] ?? item.change}</b></span>
        </button>)}
      </div>
    </div> : <div className="comparison-evidence-empty" data-testid="comparison-evidence-empty"><Check /><strong>조건에 맞는 정량 결과가 없습니다.</strong><span>다른 필터를 선택하면 해당 근거를 확인할 수 있습니다.</span>{filter !== 'ALL' && <button type="button" onClick={() => setFilter('ALL')}>전체 보기</button>}</div>}
    {visibleSelected && <aside className={`comparison-evidence-detail change-${visibleSelected.change.toLowerCase()}`} data-testid="comparison-evidence-detail" aria-live="polite">
      <header><div><span>SELECTED VARIABLE</span><strong>{visibleSelected.display_name}</strong><code>{visibleSelected.variable_key}</code></div><b>{CHANGE_LABEL[visibleSelected.change] ?? visibleSelected.change}</b></header>
      <dl><div><dt>기준 Run</dt><dd>{valueLabel(visibleSelected.baseline_value, baselineUnit(visibleSelected))} · {baselineNote(visibleSelected)}</dd></div><div><dt>대상 Run</dt><dd>{valueLabel(visibleSelected.target_value, visibleSelected.unit)} · {targetNote(visibleSelected)}</dd></div><div><dt>변화</dt><dd>{visibleSelected.delta == null || !visibleSelected.comparable ? '비교 불가' : `${visibleSelected.delta >= 0 ? '+' : ''}${visibleSelected.delta.toFixed(2)} ${visibleSelected.unit ?? ''}`}</dd></div></dl>
      <footer><button type="button" onClick={() => onPrepareReview(visibleSelected.variable_key)}><ClipboardCheck /> 검토 준비</button>{seriesAvailable ? <button type="button" onClick={() => onViewSeries(visibleSelected.variable_key)}><BarChart3 /> 시계열 보기</button> : <span className="comparison-evidence-no-series">연결된 시계열 없음</span>}</footer>
    </aside>}
  </section>
}
