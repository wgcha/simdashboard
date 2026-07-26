import { exportAnalysisReport, filterOverviewForReport, type ReportExportOptions } from '../src/reportExport'
import type { Overview } from '../src/types'

const timeSeries = Array.from({ length: 41 }, (_, index) => {
  const time = index * 0.25
  return [
    { variable_key: 'top_edge_stress_time', display_name: '상단 Edge 응력', time_value: time, value: 68 * Math.exp(-((time - 4.5) ** 2) / 4), time_unit: 'ms', value_unit: 'MPa' },
    { variable_key: 'chassis_rear_deformation_time', display_name: 'Chassis rear 휨량', time_value: time, value: 5.8 * (1 - Math.exp(-time / 2.4)), time_unit: 'ms', value_unit: 'mm' },
  ]
}).flat()

const overview = {
  load_case: {
    id: 'smoke-load-case',
    name: 'Bottom Drop 800 mm',
    analysis_type: 'DROP',
    project_name: 'ORION 65 OLED',
    product_name: 'ORION-65-OLED-C',
    request_title: '오픈셀 응력 및 Chassis rear 휨 평가',
    request_id: 'smoke-request',
    project_id: 'smoke-project',
    created_at: '2026-07-23T00:00:00Z',
    parameters: { drop_height_mm: 800, impact_direction: 'BOTTOM' },
  },
  run: 'smoke-run',
  overall_verdict: 'FAIL',
  analysis_verdicts: { open_cell: 'PASS', chassis_rear: 'FAIL' },
  threshold: 75,
  product_information: [],
  scalar_results: [
    { id: '1', variable_key: 'top_edge_max_stress', display_name: '상단 Edge 최대 응력', value_double: 68, unit: 'MPa', threshold_double: 75, verdict: 'PASS' },
    { id: '2', variable_key: 'bottom_edge_max_stress', display_name: '하단 Edge 최대 응력', value_double: 71, unit: 'MPa', threshold_double: 75, verdict: 'PASS' },
    { id: '2b', variable_key: 'left_edge_max_stress', display_name: '좌측 Edge 최대 응력', value_double: null as unknown as number, unit: 'MPa', threshold_double: 75, verdict: 'FAIL' },
    { id: '3', variable_key: 'chassis_rear_top_deformation', display_name: 'Chassis rear 상단 휨량', value_double: 5.8, unit: 'mm', threshold_double: 5, verdict: 'FAIL' },
    { id: '4', variable_key: 'chassis_rear_bottom_deformation', display_name: 'Chassis rear 하단 휨량', value_double: 4.3, unit: 'mm', threshold_double: 5, verdict: 'PASS' },
    { id: '5', variable_key: 'chassis_rear_center_deformation', display_name: 'Chassis rear 중앙 휨량', value_double: null as unknown as number, unit: 'mm', threshold_double: 5, verdict: 'FAIL' },
  ],
  time_series: timeSeries,
  curves: [],
  result_locations: [],
  notes: [{ id: 'note-1', author: '홍길동', body: '최대 응력 발생 위치와 Chassis rear 잔류 휨을 중심으로 검토함.', created_at: '2026-07-23T00:00:00Z' }],
  media: [],
  template_execution: null,
} satisfies Overview

const options: ReportExportOptions = {
  author: '홍길동',
  developmentStage: 'DV 1차',
  reportDate: '2026.07.23.',
  reliabilityName: '낙하',
  reviewPurpose: '오픈셀 응력 평가 및 Chassis rear 휨 평가',
  reviewConditions: '낙하 높이: 800 mm\n충격 방향: BOTTOM\n해석 모델: ORION-65-OLED-C',
  reviewResult: 'Open Cell 최대 응력은 기준 이내임.\nChassis rear 상단 휨량 5.80 mm로 기준 5.00 mm를 초과함.',
  reviewConclusion: 'Open Cell은 PASS이나 Chassis rear 상단 휨량이 기준을 초과하여 전체 FAIL로 판정함.',
}

const openCellOverview = filterOverviewForReport(overview, 'open_cell')
await exportAnalysisReport(openCellOverview, {
  ...options,
  reviewPurpose: '오픈셀 파손 평가',
  reviewResult: 'Open Cell 최대 응력은 기준 이내임.',
  reviewConclusion: '오픈셀 응력이 검토 기준 이내이므로 PASS로 판정함.',
})

const chassisOverview = filterOverviewForReport(overview, 'chassis')
await exportAnalysisReport(chassisOverview, {
  ...options,
  reviewPurpose: 'Chassis rear 휨 평가',
  reviewResult: 'Chassis rear 상단 휨량 5.80 mm로 기준 5.00 mm를 초과함.',
  reviewConclusion: 'Chassis rear 상단 휨량이 기준을 초과하여 FAIL로 판정함.',
})
