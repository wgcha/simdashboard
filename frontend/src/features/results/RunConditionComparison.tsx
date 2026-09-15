import { useMemo, useState } from 'react'
import type { RunConditionComparisonData } from '../../types'
import './RunConditionComparison.css'

type ConditionFilter = 'DIFFS' | 'ALL'

function valueLabel(value: unknown, unit: string | null) {
  if (value === null || value === undefined || value === '') return '기록 없음'
  let text: string
  if (typeof value === 'object') {
    try { text = JSON.stringify(value, null, 2) } catch { text = String(value) }
  } else if (typeof value === 'boolean') text = value ? 'true' : 'false'
  else text = String(value)
  return unit ? `${text} ${unit}` : text
}

function statusLabel(status: 'CHANGED' | 'SAME' | 'UNKNOWN') {
  return status === 'CHANGED' ? '차이' : status === 'SAME' ? '동일' : '확인 필요'
}

export function RunConditionComparison({ comparison, baselineRunNo, targetRunNo }: { comparison: RunConditionComparisonData | undefined; baselineRunNo: number; targetRunNo: number }) {
  const [filter, setFilter] = useState<ConditionFilter>('DIFFS')
  const rows = comparison?.rows ?? []
  const filteredRows = useMemo(() => filter === 'ALL' ? rows : rows.filter((row) => row.status !== 'SAME'), [filter, rows])
  const hasRecordedRows = rows.length > 0
  const allSame = hasRecordedRows && comparison?.summary.changed === 0 && comparison.summary.unknown === 0

  return <section className="run-condition-comparison" data-testid="run-condition-comparison">
    <header className="run-condition-header">
      <div><span>RUN INPUT EVIDENCE</span><h3>기록된 입력 조건 비교</h3><p>Run {baselineRunNo} 기준 · Run {targetRunNo} 대상</p></div>
      {comparison && <div className="run-condition-at-glance"><strong>{comparison.summary.changed}</strong><span>차이</span><strong className="unknown">{comparison.summary.unknown}</strong><span>확인 필요</span></div>}
    </header>
    <p className="run-condition-note">차이는 원인 확정이 아닙니다. 기록되지 않은 조건은 확인이 필요합니다.</p>
    {comparison && <nav className="run-condition-filters" aria-label="입력 조건 표시 필터">
      <button type="button" className={filter === 'DIFFS' ? 'active' : ''} aria-pressed={filter === 'DIFFS'} onClick={() => setFilter('DIFFS')}>차이·확인 필요 <b>{comparison.summary.changed + comparison.summary.unknown}</b></button>
      <button type="button" className={filter === 'ALL' ? 'active' : ''} aria-pressed={filter === 'ALL'} onClick={() => setFilter('ALL')}>전체 <b>{rows.length}</b></button>
    </nav>}
    {!comparison ? <div className="run-condition-empty" data-testid="run-condition-empty"><strong>입력 조건을 확인할 수 없습니다.</strong><span>현재 비교 조건 상태는 확인 필요입니다.</span></div> : !hasRecordedRows ? <div className="run-condition-empty" data-testid="run-condition-empty"><strong>기록된 입력 조건이 없습니다.</strong><span>입력 조건이 없다는 사실은 두 Run이 동일하다는 뜻이 아닙니다.</span></div> : allSame && filter === 'DIFFS' ? <div className="run-condition-empty same" data-testid="run-condition-all-same"><strong>기록된 입력 조건은 모두 동일합니다.</strong><span>전체 보기를 선택하면 확인된 조건을 볼 수 있습니다.</span></div> : filteredRows.length === 0 ? <div className="run-condition-empty" data-testid="run-condition-empty"><strong>현재 필터에 해당하는 조건이 없습니다.</strong><span>전체 보기를 선택하면 동일 조건도 확인할 수 있습니다.</span></div> : <div className="run-condition-body">
      <div className="run-condition-columns" aria-hidden="true"><span>조건</span><span>기준 Run</span><span>대상 Run</span><span>상태</span></div>
      <div className="run-condition-rows">{filteredRows.map((row) => <article key={row.key} className={`run-condition-row status-${row.status.toLowerCase()}`} data-testid="condition-row" data-condition-key={row.key} data-status={row.status}>
        <header><strong>{row.label}</strong><code>{row.key}</code></header>
        <div className="run-condition-value baseline"><span>{row.baseline ? valueLabel(row.baseline.value, row.baseline.unit) : '기록 없음'}</span></div>
        <div className="run-condition-value target"><span>{row.target ? valueLabel(row.target.value, row.target.unit) : '기록 없음'}</span></div>
        <div className="run-condition-status"><b>{statusLabel(row.status)}</b><small>{row.reason}</small></div>
        {(row.baseline?.source || row.target?.source || row.reason) && <details><summary>기록 출처·확인 메모</summary><dl>{row.baseline?.source && <div><dt>기준 출처</dt><dd>{row.baseline.source}</dd></div>}{row.target?.source && <div><dt>대상 출처</dt><dd>{row.target.source}</dd></div>}<div><dt>상태 근거</dt><dd>{row.reason}</dd></div></dl></details>}
      </article>)}</div>
    </div>}
  </section>
}
