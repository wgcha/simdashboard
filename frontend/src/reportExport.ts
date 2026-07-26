import pptxgen from 'pptxgenjs'
import type { Overview } from './types'

export type ReportExportOptions = {
  author: string
  developmentStage: string
  reportDate: string
  reliabilityName: string
  reviewPurpose: string
  reviewConditions: string
  reviewResult: string
  reviewConclusion: string
}

export type ReportScope = 'open_cell' | 'chassis'

const COLORS = {
  navy: '101A34',
  blue: '1898D5',
  lightBlue: 'EAF7FD',
  cyan: '16B8D4',
  orange: 'FF9948',
  ink: '172033',
  muted: '667085',
  line: 'B8D9E8',
  white: 'FFFFFF',
  pass: '138A5B',
  fail: 'D7475C',
  amber: 'B7791F',
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function formatNumber(value: unknown, digits = 2) {
  return isFiniteNumber(value) ? value.toFixed(digits) : ''
}

function formatDate(date: Date) {
  return `${date.getFullYear()}.${String(date.getMonth() + 1).padStart(2, '0')}.${String(date.getDate()).padStart(2, '0')}.`
}

function simulationName(analysisType: string) {
  const names: Record<string, string> = {
    DROP: '낙하',
    SIDE_CLAMP: 'Side Clamp',
    STACKING: '적재',
  }
  return names[analysisType] ?? analysisType.replaceAll('_', ' ')
}

function parameterSummary(parameters: Overview['load_case']['parameters']) {
  const labels: Record<string, string> = {
    drop_height_mm: '낙하 높이',
    impact_direction: '충격 방향',
    clamp_pressure_kpa: '클램프 압력',
    pressure_mpa: '클램프 압력',
    hold_time_s: '유지 시간',
  }
  return Object.entries(parameters)
    .map(([key, value]) => `${labels[key] ?? key}: ${Array.isArray(value) ? value.join(', ') : value}`)
    .join('\n')
}

function resultSummary(overview: Overview) {
  if (!overview.scalar_results.length) return ''
  return overview.scalar_results
    .slice(0, 8)
    .map((item) => {
      const value = formatNumber(item.value_double)
      const unit = value ? item.unit : ''
      const verdict = value ? ` (${item.verdict})` : ''
      return `${item.display_name}: ${value}${unit ? ` ${unit}` : ''}${verdict}`
    })
    .join('\n')
}

export function createDefaultReportOptions(overview: Overview): ReportExportOptions {
  const note = overview.notes[0]
  return {
    author: note?.author ?? '',
    developmentStage: 'DV 1차',
    reportDate: formatDate(new Date()),
    reliabilityName: simulationName(overview.load_case.analysis_type),
    reviewPurpose: overview.load_case.request_title || `${overview.load_case.product_name} 신뢰성 검토`,
    reviewConditions: parameterSummary(overview.load_case.parameters) || overview.load_case.name,
    reviewResult: resultSummary(overview),
    reviewConclusion: overview.overall_verdict === 'PASS'
      ? '검토 사양을 만족하여 PASS로 판정함.'
      : overview.overall_verdict === 'FAIL'
        ? '기준 초과 항목이 확인되어 FAIL로 판정함. 상세 근거와 개선 검토가 필요함.'
        : '판정에 필요한 결과 데이터가 부족함.',
  }
}

function isChassisVariable(variableKey: string) {
  return variableKey.toLowerCase().startsWith('chassis_rear_')
}

export function filterOverviewForReport(overview: Overview, scope: ReportScope): Overview {
  const includeVariable = (variableKey: string) => scope === 'chassis'
    ? isChassisVariable(variableKey)
    : !isChassisVariable(variableKey)
  const scopeTitle = scope === 'chassis' ? 'Chassis rear 휨 평가' : '오픈셀 파손 평가'
  const includeMedia = (media: Overview['media'][number]) => {
    const metadataKey = typeof media.metadata?.variable_key === 'string' ? media.metadata.variable_key : ''
    if (metadataKey) return includeVariable(metadataKey)
    const resultGroup = typeof media.metadata?.result_group === 'string' ? media.metadata.result_group.toUpperCase() : ''
    if (resultGroup) return scope === 'chassis' ? resultGroup === 'CHASSIS_REAR' : resultGroup === 'OPEN_CELL'
    return true
  }
  return {
    ...overview,
    load_case: {
      ...overview.load_case,
      request_title: `${overview.load_case.project_name} ${scopeTitle}`,
    },
    overall_verdict: scope === 'chassis' ? overview.analysis_verdicts.chassis_rear : overview.analysis_verdicts.open_cell,
    scalar_results: overview.scalar_results.filter((item) => includeVariable(item.variable_key)),
    time_series: overview.time_series.filter((item) => includeVariable(item.variable_key)),
    curves: overview.curves.filter((item) => includeVariable(item.variable_key)),
    result_locations: overview.result_locations.filter((item) => includeVariable(item.variable_key)),
    media: overview.media.filter(includeMedia),
  }
}

export function validateReportOptions(options: ReportExportOptions) {
  const required: Array<[string, string]> = [
    ['작성자', options.author],
    ['시뮬레이션 종류', options.reliabilityName],
    ['검토 목적', options.reviewPurpose],
    ['검토 사양 조건', options.reviewConditions],
    ['검토 결과', options.reviewResult],
    ['검토 결론', options.reviewConclusion],
  ]
  const missing = required.find(([, value]) => !value.trim())
  if (missing) throw new Error(`${missing[0]}을(를) 입력해 주세요.`)
  if (!/^(DV|PV|PR)\s+[1-9]\d*차$/i.test(options.developmentStage.trim())) {
    throw new Error('개발단계는 DV 1차, PV 2차, PR 1차 형식으로 입력해 주세요.')
  }
  if (!/^\d{4}\.\d{2}\.\d{2}\.$/.test(options.reportDate.trim())) {
    throw new Error('작성날짜는 YYYY.MM.DD. 형식으로 입력해 주세요.')
  }
}

function addTextBox(
  slide: pptxgen.Slide,
  title: string,
  body: string,
  x: number,
  y: number,
  w: number,
  h: number,
  accent = COLORS.blue,
) {
  slide.addShape('roundRect', {
    x, y, w, h,
    rectRadius: 0.06,
    fill: { color: COLORS.white },
    line: { color: accent, width: 1.2 },
  })
  slide.addText(title, {
    x: x + 0.18, y: y + 0.1, w: w - 0.36, h: 0.28,
    fontFace: 'Noto Sans KR', fontSize: 11, bold: true, color: COLORS.ink,
    margin: 0,
  })
  slide.addShape('line', { x: x + 0.14, y: y + 0.42, w: w - 0.28, h: 0, line: { color: 'D6EAF3', width: 0.8 } })
  slide.addText(body || '', {
    x: x + 0.18, y: y + 0.52, w: w - 0.36, h: h - 0.66,
    fontFace: 'Noto Sans KR', fontSize: 10, color: COLORS.ink,
    breakLine: false, valign: 'top', margin: 0.03, fit: 'shrink',
    bullet: body.includes('\n') ? { type: 'bullet' } : undefined,
  })
}

function addFrame(slide: pptxgen.Slide, page: number, title?: string) {
  slide.background = { color: 'F8FBFD' }
  slide.addShape('rect', { x: 0.18, y: 0.16, w: 12.97, h: 7.16, fill: { color: 'F8FBFD', transparency: 100 }, line: { color: COLORS.blue, width: 2 } })
  slide.addShape('line', { x: 0.18, y: 7.08, w: 12.97, h: 0, line: { color: COLORS.blue, width: 1.2 } })
  if (title) {
    slide.addText(title, { x: 0.48, y: 0.34, w: 10.8, h: 0.42, fontFace: 'Noto Sans KR', fontSize: 20, bold: true, color: COLORS.ink, margin: 0 })
  }
  slide.addText(String(page).padStart(2, '0'), { x: 12.25, y: 7.1, w: 0.55, h: 0.18, fontFace: 'Arial', fontSize: 7, color: COLORS.muted, align: 'right', margin: 0 })
}

function addCover(slide: pptxgen.Slide, overview: Overview, options: ReportExportOptions) {
  addFrame(slide, 1)
  const verdictColor = overview.overall_verdict === 'PASS' ? COLORS.pass : overview.overall_verdict === 'FAIL' ? COLORS.fail : COLORS.amber
  slide.addText(`${overview.load_case.project_name} 해석 결과 보고서`, {
    x: 0.55, y: 0.42, w: 8.6, h: 0.5,
    fontFace: 'Noto Sans KR', fontSize: 25, bold: true, color: COLORS.ink, margin: 0,
  })
  slide.addText(`${options.author} | ${options.developmentStage}`, {
    x: 0.57, y: 0.98, w: 5.2, h: 0.25,
    fontFace: 'Noto Sans KR', fontSize: 10, color: COLORS.muted, margin: 0,
  })
  slide.addText(options.reportDate, {
    x: 9.65, y: 0.98, w: 2.92, h: 0.25,
    fontFace: 'Noto Sans KR', fontSize: 10, color: COLORS.muted, align: 'right', margin: 0,
  })
  slide.addShape('roundRect', { x: 10.84, y: 0.4, w: 1.72, h: 0.42, rectRadius: 0.18, fill: { color: verdictColor }, line: { color: verdictColor } })
  slide.addText(overview.overall_verdict, { x: 10.84, y: 0.49, w: 1.72, h: 0.2, fontFace: 'Arial', fontSize: 11, bold: true, color: COLORS.white, align: 'center', margin: 0 })

  addTextBox(slide, '시뮬레이션 종류', options.reliabilityName, 0.52, 1.4, 4.02, 0.94)
  addTextBox(slide, '검토 목적', options.reviewPurpose, 4.72, 1.4, 7.93, 0.94)
  addTextBox(slide, '검토 사양 조건', options.reviewConditions, 0.52, 2.54, 4.02, 3.64)
  addTextBox(slide, '검토 결과', options.reviewResult, 4.72, 2.54, 7.93, 2.42, COLORS.cyan)
  addTextBox(slide, '검토 결론', options.reviewConclusion, 4.72, 5.14, 7.93, 1.04, verdictColor)
}

function downsample<T>(items: T[], maxPoints = 25) {
  if (items.length <= maxPoints) return items
  const step = (items.length - 1) / (maxPoints - 1)
  return Array.from({ length: maxPoints }, (_, index) => items[Math.round(index * step)])
}

function addSeriesSlides(pptx: pptxgen, overview: Overview, startPage: number) {
  const groups = new Map<string, Overview['time_series']>()
  for (const point of overview.time_series) {
    const current = groups.get(point.variable_key) ?? []
    current.push(point)
    groups.set(point.variable_key, current)
  }
  let page = startPage
  for (const [, rawPoints] of groups) {
    const points = downsample(
      [...rawPoints]
        .filter((item) => isFiniteNumber(item.time_value) && isFiniteNumber(item.value))
        .sort((a, b) => a.time_value - b.time_value),
    )
    if (!points.length) continue
    const slide = pptx.addSlide()
    const first = points[0]
    const peak = points.reduce((best, item) => item.value > best.value ? item : best, points[0])
    const exactScalar = overview.scalar_results.find((item) => item.variable_key.replace(/_max_|_peak_/, '_').includes(first.variable_key.replace(/_time$/, '')))
    const scalar = exactScalar ?? overview.scalar_results
      .filter((item) => item.unit === first.value_unit && isFiniteNumber(item.value_double))
      .sort((a, b) => b.value_double - a.value_double)[0]
    addFrame(slide, page++, `${first.display_name} — 시간 이력`)
    slide.addText(`Peak ${peak.value.toFixed(2)} ${first.value_unit}  |  t = ${peak.time_value.toFixed(2)} ${first.time_unit}`, {
      x: 0.5, y: 0.9, w: 8.3, h: 0.35, fontFace: 'Noto Sans KR', fontSize: 13, bold: true, color: COLORS.ink, margin: 0,
    })
    slide.addChart(pptx.ChartType.line, [{
      name: first.display_name,
      labels: points.map((item) => item.time_value.toFixed(3)),
      values: points.map((item) => item.value),
    }], {
      x: 0.58, y: 1.42, w: 9.2, h: 4.96,
      catAxisLabelFontFace: 'Arial', catAxisLabelFontSize: 8,
      valAxisLabelFontFace: 'Arial', valAxisLabelFontSize: 8,
      showLegend: false, showTitle: false,
      showValue: false, lineSize: 2.2,
      chartColors: [COLORS.blue],
      catAxisTitle: `${first.time_unit}`,
      valAxisTitle: `${first.value_unit}`,
      showValAxisTitle: true,
      showCatAxisTitle: true,
    })
    addTextBox(slide, '근거 요약', [
      `최대값: ${peak.value.toFixed(2)} ${first.value_unit}`,
      `최대값 발생시간: ${peak.time_value.toFixed(2)} ${first.time_unit}`,
      scalar && isFiniteNumber(scalar.threshold_double) ? `판정 기준: ${scalar.threshold_double.toFixed(2)} ${scalar.unit}` : '판정 기준:',
      scalar && isFiniteNumber(scalar.value_double) ? `판정: ${scalar.verdict}` : '판정:',
    ].join('\n'), 10.02, 1.42, 2.62, 2.35, scalar?.verdict === 'FAIL' ? COLORS.fail : COLORS.blue)
    addTextBox(slide, '해석 위치/메모', overview.notes[0]?.body ?? '대시보드 등록 결과를 기반으로 자동 생성됨.', 10.02, 3.98, 2.62, 2.4)
  }
  return page
}

function addScalarSlide(pptx: pptxgen, overview: Overview, page: number) {
  if (!overview.scalar_results.length) return page
  const slide = pptx.addSlide()
  addFrame(slide, page++, '위치별 결과 — 기준 대비율')
  const items = overview.scalar_results.slice(0, 10)
  const chartItems = items.filter((item) =>
    isFiniteNumber(item.value_double) &&
    isFiniteNumber(item.threshold_double) &&
    item.threshold_double !== 0,
  )
  if (chartItems.length) {
    slide.addChart(pptx.ChartType.bar, [{
      name: '기준 대비율',
      labels: chartItems.map((item) => item.display_name),
      values: chartItems.map((item) => item.value_double / item.threshold_double * 100),
    }], {
      x: 0.55, y: 1.15, w: 8.2, h: 5.35,
      showLegend: false, showTitle: false, showValue: true,
      catAxisLabelFontFace: 'Noto Sans KR', catAxisLabelFontSize: 9,
      valAxisLabelFontFace: 'Arial', valAxisLabelFontSize: 8,
      valAxisTitle: '기준 대비율 (%)',
      showValAxisTitle: true,
      chartColors: [COLORS.cyan],
    })
  } else {
    slide.addShape('rect', { x: 0.55, y: 1.15, w: 8.2, h: 5.35, fill: { color: 'F8FBFD', transparency: 100 }, line: { color: 'D6E4EC', width: 0.8 } })
  }
  const tableRows = items.map((item) => [
    { text: item.display_name, options: { bold: true, color: COLORS.ink } },
    { text: isFiniteNumber(item.value_double) ? `${item.value_double.toFixed(2)} ${item.unit}` : '', options: { color: COLORS.ink } },
    { text: isFiniteNumber(item.threshold_double) ? `${item.threshold_double.toFixed(2)} ${item.unit}` : '', options: { color: COLORS.muted } },
    { text: isFiniteNumber(item.value_double) ? item.verdict : '', options: { bold: true, color: item.verdict === 'PASS' ? COLORS.pass : COLORS.fail } },
  ])
  slide.addTable([
    [
      { text: '항목', options: { bold: true, color: COLORS.white, fill: { color: COLORS.navy } } },
      { text: '결과', options: { bold: true, color: COLORS.white, fill: { color: COLORS.navy } } },
      { text: '기준', options: { bold: true, color: COLORS.white, fill: { color: COLORS.navy } } },
      { text: '판정', options: { bold: true, color: COLORS.white, fill: { color: COLORS.navy } } },
    ],
    ...tableRows,
  ], {
    x: 8.98, y: 1.15, w: 3.66, h: 5.35,
    border: { type: 'solid', color: 'D6E4EC', pt: 0.6 },
    fill: { color: COLORS.white },
    color: COLORS.ink,
    fontFace: 'Noto Sans KR',
    fontSize: 7.5,
    margin: 0.05,
    rowH: 0.42,
    colW: [1.46, 0.82, 0.82, 0.56],
  })
  return page
}

async function blobToDataUri(blob: Blob) {
  return await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(blob)
  })
}

async function addMediaSlides(pptx: pptxgen, overview: Overview, startPage: number) {
  let page = startPage
  for (const media of overview.media) {
    const isImage = media.asset_type === 'IMAGE' || media.mime_type.startsWith('image/')
    const slide = pptx.addSlide()
    addFrame(slide, page++, media.title || '해석 스크린샷')
    if (isImage && media.asset_url) {
      try {
        const response = await fetch(media.asset_url)
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        slide.addImage({ data: await blobToDataUri(await response.blob()), x: 0.62, y: 1.05, w: 9.35, h: 5.75 })
      } catch {
        addTextBox(slide, '이미지', '이미지를 불러오지 못했습니다. 원본 자산 경로를 확인해 주세요.', 0.62, 1.05, 9.35, 5.75, COLORS.fail)
      }
    } else {
      addTextBox(slide, '애니메이션/영상 자료', 'PPTX 호환성을 위해 영상은 자동 삽입하지 않습니다. 대표 프레임을 이미지로 등록하거나 원본 자산을 별도로 첨부해 주세요.', 0.62, 1.05, 9.35, 5.75, COLORS.amber)
    }
    addTextBox(slide, '자료 정보', [
      `유형: ${media.asset_type ?? media.mime_type}`,
      `파일: ${media.file_path}`,
      '용도: 해석 결과 근거 자료',
    ].join('\n'), 10.22, 1.05, 2.4, 2.2)
  }
  return page
}

function safeFilename(value: string) {
  return value.replace(/[\\/:*?"<>|]/g, '_').replace(/\s+/g, '_').slice(0, 80)
}

export async function exportAnalysisReport(overview: Overview, options: ReportExportOptions) {
  validateReportOptions(options)
  const pptx = new pptxgen()
  pptx.layout = 'LAYOUT_WIDE'
  pptx.author = options.author
  pptx.subject = `${overview.load_case.project_name} 해석 결과`
  pptx.title = `${overview.load_case.project_name} 해석 결과 보고서`
  pptx.company = 'Analysis Canvas'
  pptx.theme = {
    headFontFace: 'Noto Sans KR',
    bodyFontFace: 'Noto Sans KR',
  }

  addCover(pptx.addSlide(), overview, options)
  let page = addSeriesSlides(pptx, overview, 2)
  page = addScalarSlide(pptx, overview, page)
  await addMediaSlides(pptx, overview, page)

  const filename = `${safeFilename(overview.load_case.project_name)}_${safeFilename(options.reliabilityName)}_${safeFilename(options.reviewPurpose)}_해석결과보고서.pptx`
  await pptx.writeFile({ fileName: filename, compression: true })
  return filename
}
