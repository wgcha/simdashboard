import type { WidgetType } from '../../types'
import type { RequestResultWidget } from './types'

export type RequestResultWidgetCatalogItem = {
  type: WidgetType
  label: string
  description: string
  group: '요약' | 'KPI' | '판정' | '표' | '시계열' | '이미지' | '영상' | '전용 평가'
  data_contracts: readonly string[]
  variable_key?: string
}

export const REQUEST_RESULT_WIDGET_CATALOG: readonly RequestResultWidgetCatalogItem[] = [
  { type: 'summary', label: '하중 조건 요약', description: '해석 Run과 주요 결과를 한눈에 표시합니다.', group: '요약', data_contracts: ['RESULT_RUN'] },
  { type: 'kpi', label: 'KPI 카드', description: '대표 수치 하나를 강조합니다.', group: 'KPI', data_contracts: ['SCALAR_RESULT'], variable_key: 'max_stress' },
  { type: 'verdict', label: '패스/실패 카드', description: 'PASS·FAIL 또는 기준 판정을 표시합니다.', group: '판정', data_contracts: ['SCALAR_RESULT'], variable_key: 'overall_verdict' },
  { type: 'gauge', label: '임계값 게이지', description: '값과 관리 기준을 게이지로 비교합니다.', group: 'KPI', data_contracts: ['SCALAR_RESULT'] },
  { type: 'edge_bar', label: '막대그래프', description: '항목별 결과를 막대로 비교합니다.', group: '표', data_contracts: ['SCALAR_RESULT'] },
  { type: 'time_series', label: '시계열 그래프', description: '시간 또는 스텝별 결과 변화를 표시합니다.', group: '시계열', data_contracts: ['TIME_SERIES'], variable_key: 'time_history' },
  { type: 'scatter', label: '산점도', description: '두 결과 변수의 관계를 표시합니다.', group: '시계열', data_contracts: ['SCALAR_RESULT'] },
  { type: 'result_table', label: '데이터 테이블', description: '여러 결과 변수를 행과 열로 보여줍니다.', group: '표', data_contracts: ['SCALAR_RESULT'] },
  { type: 'contour', label: '컨투어 이미지', description: '해석 결과 분포 이미지를 표시합니다.', group: '이미지', data_contracts: ['MEDIA_ASSET'], variable_key: 'contour' },
  { type: 'video', label: '영상 플레이어', description: '변형·응력 애니메이션 결과를 표시합니다.', group: '영상', data_contracts: ['MEDIA_ASSET'], variable_key: 'animation' },
  { type: 'video_grid', label: '낙하 영상 비교', description: '여러 낙하 영상을 비교합니다.', group: '영상', data_contracts: ['MEDIA_ASSET'] },
  { type: 'note', label: '수행자 의견', description: '수행자가 남긴 결과 메모를 표시합니다.', group: '요약', data_contracts: ['RESULT_RUN'] },
  { type: 'open_cell_map', label: 'Open Cell 맵', description: 'Open Cell 위치별 결과를 표시합니다.', group: '전용 평가', data_contracts: ['LOAD_CASE', 'SCALAR_RESULT'] },
  { type: 'open_cell_summary', label: 'Open Cell 판정 요약', description: 'Open Cell 결과 판정을 요약합니다.', group: '전용 평가', data_contracts: ['SCALAR_RESULT'] },
  { type: 'chassis_summary', label: 'Chassis 판정 요약', description: 'Chassis 결과 판정을 요약합니다.', group: '전용 평가', data_contracts: ['SCALAR_RESULT'] },
  { type: 'chassis_diagram', label: 'Chassis 위치도', description: 'Chassis 측정 위치를 표시합니다.', group: '전용 평가', data_contracts: ['SCALAR_RESULT'] },
  { type: 'chassis_bar', label: 'Chassis 비교 그래프', description: 'Chassis 항목별 결과를 비교합니다.', group: '전용 평가', data_contracts: ['SCALAR_RESULT'] },
  { type: 'chassis_table', label: 'Chassis 상세 표', description: 'Chassis 결과 상세값을 표시합니다.', group: '전용 평가', data_contracts: ['SCALAR_RESULT'] },
]

export const REQUEST_RESULT_WIDGETS_BY_TYPE = new Map(REQUEST_RESULT_WIDGET_CATALOG.map((item) => [item.type, item]))

export function widgetIdForType(type: WidgetType, ordinal = 0) {
  return ordinal ? `request-result-${type}-${ordinal + 1}` : `request-result-${type}`
}

export function catalogItemForWidget(widget: Pick<RequestResultWidget, 'type' | 'id'>): RequestResultWidgetCatalogItem {
  return REQUEST_RESULT_WIDGETS_BY_TYPE.get(widget.type) ?? {
    type: widget.type,
    label: widget.id,
    description: '기존 작업 유형에 저장된 결과 위젯입니다.',
    group: '요약',
    data_contracts: [],
  }
}
