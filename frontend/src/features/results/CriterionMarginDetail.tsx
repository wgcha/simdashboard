import type { CriterionMargin } from '../../types'
import './CriterionMarginDetail.css'

const reasons: Record<string, string> = {
  RESULT_NOT_RECORDED: '이 Run에 해당 결과가 없습니다.',
  CRITERION_NOT_RECORDED: '이 Run에 판정 기준이 기록되지 않았습니다.',
  CRITERION_AMBIGUOUS: '기록된 기준의 연산자나 범위를 확인해야 합니다.',
  CRITERION_UNIT_MISMATCH: '결과와 기록 기준의 단위가 다릅니다.',
  NON_NUMERIC_VALUE: '계산 가능한 결과 값이 없습니다.',
}

export function marginLabel(margin?: CriterionMargin) {
  if (margin?.status !== 'AVAILABLE' || margin.value == null || !Number.isFinite(margin.value)) return '계산 불가'
  const value = margin.value.toLocaleString('ko-KR', { maximumSignificantDigits: 6 })
  return `${margin.value > 0 ? '+' : ''}${value}${margin.unit ? ` ${margin.unit}` : ''}${margin.value === 0 ? ' · 경계' : ''}`
}

function MarginCard({ title, margin, verdict }: { title: string; margin?: CriterionMargin; verdict: string | null }) {
  const available = margin?.status === 'AVAILABLE' && margin.value != null && Number.isFinite(margin.value)
  const knownVerdict = verdict === 'PASS' || verdict === 'FAIL'
  const differs = available && knownVerdict && typeof margin.meets_criterion === 'boolean' && (verdict === 'PASS') !== margin.meets_criterion
  return <div className="criterion-margin-card" data-testid="criterion-margin-card">
    <h5>{title}</h5><strong className={available && margin.value! < 0 ? 'outside' : ''}>{marginLabel(margin)}</strong>
    {available ? <>
      <span>기록 기준: {margin.criterion_label}</span>
      {typeof margin.meets_criterion === 'boolean' && <span className={margin.meets_criterion ? undefined : 'criterion-not-met'}>기록 기준 {margin.meets_criterion ? '충족' : '미충족'}</span>}
      {differs && <p className="criterion-margin-warning">저장된 판정({verdict})과 기록 기준의 충족 여부가 다릅니다. 적용 기준을 확인하세요.</p>}
      <details><summary>기록 출처</summary><code>{margin.source ?? '출처 확인 필요'}</code></details>
    </> : <p>{reasons[margin?.reason ?? 'CRITERION_NOT_RECORDED'] ?? '기록 기준과 결과 값을 확인해야 합니다.'}</p>}
  </div>
}

export function CriterionMarginDetail({ baseline, target, baselineVerdict, targetVerdict }: { baseline?: CriterionMargin; target?: CriterionMargin; baselineVerdict: string | null; targetVerdict: string | null }) {
  return <section className="criterion-margin-detail" data-testid="criterion-margin-detail" aria-label="기록 기준 여유">
    <h4>기록 기준 여유</h4>
    <p>양수는 기준 안쪽, 음수는 기준 바깥쪽, 0은 경계입니다. 경계 포함 여부는 기록 기준을 따릅니다.</p>
    <div className="criterion-margin-pair"><MarginCard title="기준 Run" margin={baseline} verdict={baselineVerdict} /><MarginCard title="대상 Run" margin={target} verdict={targetVerdict} /></div>
  </section>
}
