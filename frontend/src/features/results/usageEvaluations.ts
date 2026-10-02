/**
 * 사용환경 "다섯 평가 종합" rows. Shared by the 사용환경 Case results screen and
 * the usage Case report so both show the same rows, keys, values and states.
 */
import type { DashboardValue, UsageDashboard } from '../../shared/api/simulationDashboard'

export type UsageMetric = 'value' | 'verdict'
export type UsageEvaluation = UsageDashboard['evaluations'][number]
export type UsageEvaluationRow = { evaluation: UsageEvaluation; label: string; key?: string; metric: UsageMetric }
export type UsageCellView = { text: string; label: string; tone: 'ready' | 'review' | 'missing' | 'absent' }

export const USAGE_DIRECTIONS = ['common', 'front', 'rear'] as const
export const USAGE_DIRECTION_LABELS: Record<(typeof USAGE_DIRECTIONS)[number], string> = { common: '공통', front: '전방', rear: '후방' }
export const USAGE_KEYS: Record<string, string> = { Settle: 'Set Tilt Angle @ Settle (deg)', Wobble: 'Wobble Disp. (mm)', Horizontal_Force_Angle: 'Set Tilt Angle Difference (deg)', Slope_Angle: 'Slope Angle (deg)', Slope_Angle_360: 'OK/NG' }

export function usageValueText(value?: Pick<DashboardValue, 'value' | 'unit'> | null) {
  return value?.value == null ? '값 없음' : `${value.value}${value.unit ? ` ${value.unit}` : ' · 단위 미확인'}`
}

export function usageMetricStatus(value: DashboardValue, metric: UsageMetric) {
  return value[`${metric}_status`] ?? (value[metric] != null ? 'READY' : value.status ?? 'MISSING')
}

export function usageStateLabel(status: string) {
  return status === 'READY' ? '확인' : ['MISSING', 'MISSING_SOURCE', 'MISSING_NUMERIC', 'ABSENT', 'NO_DATA', 'EXCLUDED'].includes(status) ? '자료 없음' : status === 'NOT_APPLICABLE' ? '해당 없음' : '검수 필요'
}

/** Text and state of one table cell, exactly as the screen shows it. */
export function usageCell(value: DashboardValue | null | undefined, metric: UsageMetric): UsageCellView {
  if (!value) return { text: '해당 없음', label: '', tone: 'absent' }
  const state = usageMetricStatus(value, metric)
  const label = usageStateLabel(state)
  const text = state === 'READY' && value[metric] != null ? metric === 'verdict' ? String(value.verdict) : usageValueText(value) : '—'
  return { text, label, tone: label === '검수 필요' ? 'review' : state === 'READY' ? 'ready' : 'missing' }
}

export function usageFieldKey(evaluation: UsageEvaluation, metric: UsageMetric) {
  return USAGE_DIRECTIONS.map((direction) => evaluation[direction]?.[`${metric}_key`]).find(Boolean) || (metric === 'verdict' ? 'OK/NG' : USAGE_KEYS[evaluation.id])
}

export function usageEvaluationLabel(evaluation: UsageEvaluation) {
  return USAGE_KEYS[evaluation.id] ? evaluation.id : evaluation.name
}

/** One row per evaluation; Slope_Angle adds its OK/NG row. */
export function usageEvaluationRows(data: UsageDashboard): UsageEvaluationRow[] {
  return data.evaluations.flatMap((evaluation): UsageEvaluationRow[] => {
    const metric: UsageMetric = evaluation.id === 'Slope_Angle_360' ? 'verdict' : 'value'
    const row: UsageEvaluationRow = { evaluation, label: usageEvaluationLabel(evaluation), key: usageFieldKey(evaluation, metric), metric }
    return evaluation.id === 'Slope_Angle' ? [row, { evaluation, label: '', key: usageFieldKey(evaluation, 'verdict'), metric: 'verdict' }] : [row]
  })
}

export function usageStatusText(data: UsageDashboard) {
  return data.status === 'READY' ? '확인' : '일부 항목 확인 필요'
}
