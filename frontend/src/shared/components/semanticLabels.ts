export const semanticWidgetTypeLabels: Record<string, string> = {
  kpi: 'KPI 카드', gauge: '임계값 게이지', table: '결과 표', bar: '막대 그래프',
  line: '선 그래프', scatter: '산점도', image: '이미지', video: '영상',
  result_table: '결과 표', time_series: '시계열 그래프', edge_bar: '막대 그래프',
}

export const semanticFormatLabels: Record<string, string> = {
  csv: 'CSV', json: 'JSON', text: 'TEXT', txt: 'TEXT',
}

export const semanticStatusLabels: Record<string, string> = {
  IMPORTED: '등록 완료', SKIPPED: '중복으로 건너뜀', UNMAPPED: '매핑되지 않음',
  AMBIGUOUS: '매핑 후보가 여러 개', INVALID: '형식 오류', PENDING: '처리 대기',
  FAILED: '처리 실패', READY: '준비 완료', OPEN: '검토 대기',
}

export function semanticStatusLabel(status: string | null | undefined) {
  if (!status) return '상태 없음'
  return semanticStatusLabels[status] ?? status
}

export function semanticFormatLabel(format: string | null | undefined) {
  if (!format) return '형식 미상'
  return semanticFormatLabels[format.toLowerCase()] ?? format.toUpperCase()
}
