import assert from 'node:assert/strict'
import { comparisonXDomain, comparisonYAxisDomain, prepareComparisonSeries } from '../src/features/results/comparisonSeriesAxes.ts'

const points = [
  { time_value: 8, time_unit: 'ms', baseline_value: 90, target_value: 80 },
  { time_value: -2, time_unit: 'ms', baseline_value: 10, target_value: null },
  { time_value: 1, time_unit: 'ms', baseline_value: null, target_value: 50 },
  { time_value: -1, time_unit: 'ms', baseline_value: 20, target_value: 30 },
  { time_value: Number.NaN, time_unit: 'ms', baseline_value: 999, target_value: 999 },
]
const sorted = prepareComparisonSeries(points)
assert.deepEqual(sorted.map((point) => point.time_value), [-2, -1, 1, 8])
assert.deepEqual(comparisonXDomain(sorted), [-2, 8])
assert.deepEqual(comparisonYAxisDomain(sorted, true), [0, 90])
assert.deepEqual(comparisonYAxisDomain([{ time_value: 0, time_unit: 'ms', baseline_value: 0, target_value: 0 }], true), [-1, 1])
assert.deepEqual(comparisonYAxisDomain([{ time_value: 0, time_unit: 'ms', baseline_value: -5, target_value: -5 }], false), [-6, -4])
const large = Array.from({ length: 100_000 }, (_, index) => ({ time_value: index, time_unit: 'ms', baseline_value: index, target_value: null }))
assert.deepEqual(comparisonYAxisDomain(large, true), [0, 99_999])
console.log('comparison series axes self-test passed')
