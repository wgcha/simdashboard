import { AlertTriangle, ArrowRightLeft, CheckCircle2, CircleHelp, XCircle } from 'lucide-react'

import type { Overview } from '../../types'
import './RequestResultSummary.css'

function edgeForResult(key: string) {
  const normalized = key.toLowerCase()
  if (normalized.includes('top')) return 'top'
  if (normalized.includes('bottom')) return 'bottom'
  if (normalized.includes('left')) return 'left'
  if (normalized.includes('right')) return 'right'
  return null
}

export function RequestResultSummary({ activeView, comparisonAvailable, overview, onOpenComparison, onSelectEdges, runNo }: {
  activeView: 'open_cell' | 'chassis' | 'custom'
  comparisonAvailable: boolean
  overview: Overview
  onOpenComparison: () => void
  onSelectEdges: (edges: string[]) => void
  runNo?: number
}) {
  const resultGroup = activeView === 'open_cell' ? 'OPEN_CELL' : activeView === 'chassis' ? 'CHASSIS_REAR' : 'CUSTOM'
  const scalarResults = overview.scalar_results.filter((result) => (activeView === 'custom' || result.result_group === resultGroup) && (activeView !== 'open_cell' || result.unit.toLowerCase() === 'mpa') && (activeView !== 'open_cell' || result.variable_key.toLowerCase().includes('stress')))
  const failing = scalarResults.filter((result) => result.verdict === 'FAIL')
  const unit = (failing[0] ?? scalarResults[0])?.unit
  const comparableResults = unit ? scalarResults.filter((result) => result.unit === unit) : []
  const primary = failing[0] ?? (activeView === 'custom' ? scalarResults[0] : comparableResults.reduce<typeof scalarResults[number] | undefined>((maximum, result) => !maximum || result.value_double > maximum.value_double ? result : maximum, undefined))
  const primaryEdge = activeView === 'open_cell' && primary && /(?:top|bottom|left|right).*stress|stress.*(?:top|bottom|left|right)/.test(primary.variable_key.toLowerCase()) ? edgeForResult(primary.variable_key) : null
  const domainVerdict = activeView === 'open_cell' ? overview.analysis_verdicts.open_cell : activeView === 'chassis' ? overview.analysis_verdicts.chassis_rear : overview.overall_verdict
  const verdictLabel = domainVerdict === 'NO_DATA' ? '판정 없음' : domainVerdict
  const VerdictIcon = domainVerdict === 'FAIL' ? XCircle : domainVerdict === 'PASS' ? CheckCircle2 : CircleHelp

  return <section className="request-result-summary" aria-label="현재 Run 핵심 결과">
    <div className={`result-summary-verdict ${domainVerdict.toLowerCase()}`}><VerdictIcon /><div><span>{activeView === 'custom' ? 'Run 전체 판정' : '현재 분석 판정'}</span><strong>{verdictLabel}</strong></div></div>
    {primary ? <div className="result-summary-value"><span>{failing.length ? `기준 미충족 ${failing.length}개 중 첫 항목` : activeView === 'custom' ? '등록된 측정 항목' : '최대 측정값'}</span><strong>{primary.value_double.toFixed(2)} <small>{primary.unit}</small></strong><b>{primary.display_name}</b>{primaryEdge && failing.length > 0 && <button type="button" onClick={() => onSelectEdges([primaryEdge])}><AlertTriangle /> 해당 엣지 보기</button>}</div> : <div className="result-summary-value no-data"><span>측정 결과</span><strong>이 분석에 등록된 수치 없음</strong><b>결과를 가져온 뒤 판정합니다.</b></div>}
    <div className="result-summary-meta"><span>표시 항목의 기준</span><strong>{primary && Number.isFinite(primary.threshold_double) ? `${primary.threshold_double.toFixed(2)} ${primary.unit}` : '이 Run에 기록된 기준 없음'}</strong><b>{runNo != null ? `Run #${runNo}` : overview.run ? 'Run 확인 중' : 'Run 미등록'}</b></div>
    <button className="result-summary-compare" type="button" onClick={onOpenComparison} disabled={!comparisonAvailable}><ArrowRightLeft /> 이전 Run 비교</button>
  </section>
}
