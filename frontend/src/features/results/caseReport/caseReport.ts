/**
 * Case 결과 보고서 (PPTX · HTML).
 *
 * One recipe (`CaseReportData`) is built once from the selection fixed when the
 * report was opened and rendered per format. Everything here is independent of
 * React state so the Final designation flow can build and upload the same files.
 */
import { simulationDashboardApi, type DashboardAssetBlob, type DashboardDistribution, type DashboardMember, type DashboardRunVideo, type DashboardValue } from '../../../shared/api/simulationDashboard'
import type { ContentSnapshot, ReportExportOptions } from '../../../reportExport'
import type { Overview, ReportContentItem, ReportElementDefinition, ReportLayoutDefinition, ReportSlideDefinition, ReportSource } from '../../../types'

export type CaseReportSource = Extract<ReportSource, { kind: 'case_results' }>
export type CaseReportFormat = 'pptx' | 'html'

/** Labels shown to users for the fixed selection (never internal ids). */
export type CaseReportLabels = {
  project: string
  request: string
  caseLabel: string
  loadCase: string
  run: string
  option: string
  component: string
  basis: string
}

export type CaseReportScope = { source: CaseReportSource; labels: CaseReportLabels }

export type CaseReportImage = { id: string; assetId: string; scene: string; kind: '컨투어' | '거동'; title: string }
export type CaseReportVideo = { id: string; assetId: string; scene: string; fileName: string }

export type CaseReportData = {
  version: 1
  source: CaseReportSource
  title: string
  caseLabel: string
  generatedAt: string
  generatedLabel: string
  scopeRows: Array<{ label: string; value: string }>
  summary: { valueName: string; headers: string[]; rows: string[][]; peak: string }
  sceneTable: { headers: string[]; rows: string[][] }
  images: CaseReportImage[]
  videos: CaseReportVideo[]
}

export type CaseReportMediaResult = DashboardAssetBlob
export type CaseReportMediaLoader = (assetId: string, options: { maxBytes?: number; signal?: AbortSignal }) => Promise<CaseReportMediaResult>

/** Default loader: the typed dashboard API client. */
export const caseReportMediaLoader: CaseReportMediaLoader = (assetId, options) => simulationDashboardApi.assetBlob(assetId, options)

export const CASE_REPORT_VIDEO_MAX_BYTES = 20 * 1024 * 1024
export const CASE_REPORT_VIDEO_TOTAL_MAX_BYTES = 200 * 1024 * 1024
const IMAGE_MAX_BYTES = 30 * 1024 * 1024
const PPTX_TABLE_ROWS = 12
const EDGES = ['TOP', 'BOTTOM', 'LEFT', 'RIGHT'] as const

const pad = (value: number) => String(value).padStart(2, '0')

function valueText(value?: Pick<DashboardValue, 'value' | 'unit'> | null) {
  return value?.value == null ? '값 없음' : `${value.value}${value.unit ? ` ${value.unit}` : ' · 단위 미확인'}`
}

function sceneNumber(scene: { scene_sequence_number: number | null }) {
  return scene.scene_sequence_number == null ? '순번 미확인' : String(scene.scene_sequence_number)
}

function reportMember(distribution: DashboardDistribution, caseId: string): DashboardMember | undefined {
  return distribution.members.find((member) => member.simulation_case_id === caseId) ?? distribution.members[0]
}

/** Builds the format-independent report recipe. Pure: same input, same output. */
export function buildCaseReportData(input: { scope: CaseReportScope; distribution: DashboardDistribution; videos: DashboardRunVideo[]; generatedAt: Date }): CaseReportData {
  const { scope, distribution, videos, generatedAt } = input
  const { labels, source } = scope
  const member = reportMember(distribution, distribution.context.simulation_case_id ?? '')
  const memberId = member?.id
  const seriesByScene = new Map(distribution.series.filter((point) => point.member_id === memberId).map((point) => [point.scene_id, point]))
  const peaksByScene = new Map<string, DashboardDistribution['edge_peaks']>()
  for (const peak of distribution.edge_peaks) {
    if (peak.member_id !== memberId) continue
    peaksByScene.set(peak.scene_id, [...(peaksByScene.get(peak.scene_id) ?? []), peak])
  }
  const sceneLabel = new Map(distribution.scenes.map((scene) => [scene.id, scene.label]))

  let peak: { scene: string; value: number; unit: string | null } | null = null
  const summaryRows = distribution.scenes.map((scene) => {
    const point = seriesByScene.get(scene.id)
    const envelope = point?.selected_edge_envelope ?? point?.value ?? null
    if (envelope != null && (!peak || envelope > peak.value)) peak = { scene: scene.label, value: envelope, unit: point?.unit ?? null }
    const edgePeaks = (peaksByScene.get(scene.id) ?? []).filter((item) => item.value != null)
    const maxEdge = edgePeaks.reduce<(typeof edgePeaks)[number] | null>((best, item) => !best || (item.value ?? 0) > (best.value ?? 0) ? item : best, null)
    return [sceneNumber(scene), scene.label, valueText(envelope == null ? null : { value: envelope, unit: point?.unit ?? null }), maxEdge ? `${maxEdge.edge} · ${valueText(maxEdge)}` : '값 없음']
  })
  const finalPeak = peak as { scene: string; value: number; unit: string | null } | null
  const sceneRows = distribution.scenes.map((scene) => {
    const peaks = peaksByScene.get(scene.id) ?? []
    const description = [scene.description, scene.contact_code, scene.repetition].filter(Boolean).join(' · ') || '설명 미확인'
    return [sceneNumber(scene), scene.label, description, ...EDGES.map((edge) => valueText(peaks.find((item) => item.edge === edge)))]
  })
  const images: CaseReportImage[] = [
    ...distribution.contours.filter((cell) => cell.member_id === memberId && cell.asset && cell.asset.kind !== 'VIDEO' && cell.asset.status === 'READY')
      .map((cell) => ({ id: `contour:${cell.cell_id}`, assetId: cell.asset!.asset_id, scene: sceneLabel.get(cell.scene_id) ?? cell.scene_id, kind: '컨투어' as const, title: cell.asset!.title || '컨투어' })),
    ...distribution.behaviors.filter((cell) => cell.member_id === memberId && cell.asset && cell.asset.kind !== 'VIDEO' && cell.asset.status === 'READY')
      .map((cell) => ({ id: `behavior:${cell.cell_id}`, assetId: cell.asset!.asset_id, scene: sceneLabel.get(cell.scene_id) ?? cell.scene_id, kind: '거동' as const, title: `${cell.subject_role === 'UNKNOWN' ? '거동' : cell.subject_role} 거동` })),
  ]
  const generatedLabel = `${generatedAt.getFullYear()}.${pad(generatedAt.getMonth() + 1)}.${pad(generatedAt.getDate())} ${pad(generatedAt.getHours())}:${pad(generatedAt.getMinutes())}`
  return {
    version: 1,
    source,
    title: `${labels.caseLabel} 해석 결과 보고서`,
    caseLabel: labels.caseLabel,
    generatedAt: generatedAt.toISOString(),
    generatedLabel,
    scopeRows: [
      { label: '프로젝트', value: labels.project },
      { label: '의뢰', value: labels.request },
      { label: 'Case', value: labels.caseLabel },
      { label: '하중경우', value: labels.loadCase },
      { label: 'Run Case', value: labels.run },
      { label: 'Run Option', value: labels.option },
      { label: 'Component · 기준', value: [labels.component, labels.basis].filter(Boolean).join(' · ') },
      { label: '생성 일시', value: generatedLabel },
    ].map((row) => ({ ...row, value: row.value || '없음' })),
    summary: {
      valueName: '선택 엣지 최대응력',
      headers: ['순번', 'Scene', '선택 엣지 최대응력', '최대 엣지'],
      rows: summaryRows,
      peak: finalPeak ? `최대 ${valueText({ value: finalPeak.value, unit: finalPeak.unit })} · ${finalPeak.scene}` : '값 없음',
    },
    sceneTable: { headers: ['순번', 'Scene', '자세·충돌', ...EDGES], rows: sceneRows },
    images,
    videos: videos.map((video) => ({ id: video.video_id, assetId: video.asset_id, scene: video.scene_label, fileName: video.title })),
  }
}

/** Loads everything the recipe needs for the fixed scope through the typed API client. */
export async function loadCaseReportSources(source: CaseReportSource, signal?: AbortSignal): Promise<{ distribution: DashboardDistribution; videos: DashboardRunVideo[] }> {
  const distributionPromise = simulationDashboardApi.distribution(source.runId, { capture_id: source.captureId, run_option_id: source.optionId || undefined, mode: source.mode, component_id: source.componentId, basis: source.basis as 'DETAIL' | 'REPORTED_SUMMARY', edge_keys: source.edgeKeys, line_indices: source.lineIndices }, signal)
  const videos: DashboardRunVideo[] = []
  for (let page = 1; page <= 50; page += 1) {
    const result = await simulationDashboardApi.videos(source.runId, { capture_id: source.captureId, run_option_id: source.optionId || undefined, mode: source.optionId ? undefined : source.mode || undefined, page, page_size: 100 }, signal)
    videos.push(...(result.videos ?? []))
    if (!result.pagination.has_next) break
  }
  return { distribution: await distributionPromise, videos }
}

const WINDOWS_RESERVED = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i

/** Deterministic Windows-safe file name: `<Case>_<YYYYMMDD-HHmm>.<ext>` (local time). */
export function caseReportFileName(caseLabel: string, generatedAt: Date | string, format: CaseReportFormat) {
  const date = typeof generatedAt === 'string' ? new Date(generatedAt) : generatedAt
  // eslint-disable-next-line no-control-regex
  let base = caseLabel.normalize('NFC').replace(/[\u0000-\u001f\u007f<>:"/\\|?*]/g, '_').replace(/\s+/g, '_').replace(/_+/g, '_').replace(/^[._]+|[._ ]+$/g, '').slice(0, 80).replace(/[._ ]+$/g, '')
  if (!base) base = 'Case'
  if (WINDOWS_RESERVED.test(base.split('.')[0])) base = `_${base}`
  const stamp = `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}`
  return `${base}_${stamp}.${format}`
}

function chunk<T>(items: T[], size: number) {
  const pages: T[][] = []
  for (let index = 0; index < items.length; index += size) pages.push(items.slice(index, index + size))
  return pages
}

/** PPTX content items (one per slide); tables are paginated. */
export function buildCaseReportContents(data: CaseReportData, images: Map<string, ContentSnapshot['image']> = new Map()): ReportContentItem[] {
  const key = `${data.source.caseId}:${data.source.captureId}`
  const pageTitle = (title: string, index: number, total: number) => total > 1 ? `${title} (${index + 1}/${total})` : title
  const summaryPages = chunk(data.summary.rows, PPTX_TABLE_ROWS)
  const scenePages = chunk(data.sceneTable.rows, PPTX_TABLE_ROWS)
  const videoPages = chunk(data.videos.map((video) => [video.scene, video.fileName]), PPTX_TABLE_ROWS)
  return [
    { contentId: `case:${key}:scope`, kind: 'case_scope', sourceKey: key, title: '보고서 범위', defaultPresentation: 'text', data: { text: [...data.scopeRows.map((row) => `${row.label}: ${row.value}`), `요약: ${data.summary.peak}`].join('\n') } satisfies ContentSnapshot },
    ...(summaryPages.length ? summaryPages : [[]]).map((rows, index, pages): ReportContentItem => ({
      contentId: `case:${key}:summary:${index + 1}`, kind: 'case_summary', sourceKey: key, title: pageTitle(`요약 · ${data.summary.valueName}`, index, pages.length), defaultPresentation: rows.length ? 'table' : 'text',
      data: { text: rows.length ? data.summary.peak : '표시할 Scene 결과가 없습니다.', tableHeaders: data.summary.headers, tableRows: rows } satisfies ContentSnapshot,
    })),
    ...scenePages.map((rows, index, pages): ReportContentItem => ({
      contentId: `case:${key}:scenes:${index + 1}`, kind: 'case_scene_table', sourceKey: key, title: pageTitle('Scene 비교 · 엣지별 최대응력', index, pages.length), defaultPresentation: 'table',
      data: { tableHeaders: data.sceneTable.headers, tableRows: rows } satisfies ContentSnapshot,
    })),
    ...data.images.map((image): ReportContentItem => {
      const loaded = images.get(image.id)
      return {
        contentId: `case:${key}:image:${image.id}`, kind: 'case_image', sourceKey: image.assetId, title: `${image.scene} · ${image.kind}`, defaultPresentation: loaded ? 'image' : 'text',
        data: { image: loaded, text: loaded ? image.title : `${image.title}\n이미지를 불러오지 못했습니다.` } satisfies ContentSnapshot,
      }
    }),
    ...videoPages.map((rows, index, pages): ReportContentItem => ({
      contentId: `case:${key}:videos:${index + 1}`, kind: 'case_videos', sourceKey: key, title: pageTitle('영상 목록', index, pages.length), defaultPresentation: 'table',
      data: { text: rows.map((row) => row.join(' · ')).join('\n'), tableHeaders: ['Scene', '영상 파일'], tableRows: rows } satisfies ContentSnapshot,
    })),
  ]
}

function element(id: string, type: ReportElementDefinition['type'], label: string, rect: [number, number, number, number], binding: ReportElementDefinition['binding']): ReportElementDefinition {
  const [x, y, w, h] = rect
  return { id, type, label, x, y, w, h, z: 1, binding, rules: { visibleWhenData: true } }
}

/** Cover (title, date, scope) followed by one slide per content item. */
export function createCaseReportSlides(contents: ReportContentItem[]): ReportSlideDefinition[] {
  const [scope, ...rest] = contents
  const cover: ReportSlideDefinition = { id: 'slide-case-cover', name: 'Case 결과 보고서', kind: 'cover', repeat: 'none', elements: [
    element('case-cover-title', 'title', '보고서 제목', [1, 1, 22, 2], { source: 'field', key: 'report_title' }),
    element('case-cover-date', 'text', '작성날짜', [24, 1, 7, 1], { source: 'field', key: 'report_date' }),
    ...(scope ? [element('case-cover-scope', 'text', scope.title, [1, 4, 30, 12], { source: 'content', contentId: scope.contentId, key: 'body' })] : []),
  ] }
  return [cover, ...rest.map((content, index): ReportSlideDefinition => {
    const type: ReportElementDefinition['type'] = content.defaultPresentation === 'card' ? 'scalar-card' : content.defaultPresentation
    return {
      id: `slide-case-${index + 1}-${content.kind}`, name: content.title, kind: 'custom', repeat: 'none',
      elements: [
        element(`case-title-${index}`, 'title', content.title, [1, 1, 30, 2], { source: 'content', contentId: content.contentId, key: 'title' }),
        element(`case-body-${index}`, type, content.title, [1, 4, 30, 12], { source: 'content', contentId: content.contentId, key: 'body' }),
      ],
    }
  })]
}

/** Applies the Case slides to a company layout; slides edited for this same scope are kept. */
export function prepareCaseReportLayout(layout: ReportLayoutDefinition, source: CaseReportSource, contents: ReportContentItem[], forceDefault = false): ReportLayoutDefinition {
  const sameSource = JSON.stringify(layout.sourceScope) === JSON.stringify(source)
  const base = { ...layout, sourceScope: source, templateSource: 'native' as const, templateAssetId: undefined, templateBindings: {} }
  if (!forceDefault && layout.contentMode && sameSource && layout.slides?.length) return base
  return { ...base, contentMode: 'one-per-slide', slides: createCaseReportSlides(contents) }
}

/** Minimal Overview the shared renderer and layout editor need; it carries no results of its own. */
export function caseReportOverview(data: CaseReportData, labels: { project: string; request: string; loadCase: string }): Overview {
  return {
    load_case: { id: data.source.loadCaseId, name: labels.loadCase, analysis_type: 'CASE_RESULTS', project_name: labels.project, product_name: '', request_title: labels.request, request_id: data.source.requestId, project_id: data.source.projectId, created_at: data.generatedAt, parameters: {} },
    run: data.source.runId, overall_verdict: 'NO_DATA', analysis_verdicts: { open_cell: 'NO_DATA', chassis_rear: 'NO_DATA' }, threshold: null,
    product_information: [], scalar_results: [], time_series: [], curves: [], result_locations: [], notes: [], media: [], template_execution: null,
  }
}

async function blobToDataUri(blob: Blob) {
  return await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(blob)
  })
}

const IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/bmp', 'image/svg+xml'])
const VIDEO_TYPES = new Set(['video/mp4', 'video/webm', 'video/ogg'])

function mediaType(blob: Blob, name: string, kind: 'image' | 'video') {
  const declared = blob.type.split(';')[0].trim().toLowerCase()
  if ((kind === 'image' ? IMAGE_TYPES : VIDEO_TYPES).has(declared)) return declared
  const extension = name.toLowerCase().split('.').pop() ?? ''
  const byExtension: Record<string, string> = kind === 'image'
    ? { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp', bmp: 'image/bmp' }
    : { mp4: 'video/mp4', m4v: 'video/mp4', webm: 'video/webm', ogv: 'video/ogg', ogg: 'video/ogg' }
  return byExtension[extension] ?? (kind === 'image' ? 'image/png' : 'video/mp4')
}

/** Data URI with a whitelisted media type (never the server-provided raw type). */
async function safeDataUri(blob: Blob, name: string, kind: 'image' | 'video') {
  return await blobToDataUri(new Blob([blob], { type: mediaType(blob, name, kind) }))
}

async function imageSize(blob: Blob): Promise<{ width?: number; height?: number }> {
  try {
    const bitmap = await createImageBitmap(blob)
    const size = { width: bitmap.width, height: bitmap.height }
    bitmap.close()
    return size
  } catch { return {} }
}

async function loadImages(data: CaseReportData, loadMedia: CaseReportMediaLoader, signal?: AbortSignal) {
  const loaded = new Map<string, NonNullable<ContentSnapshot['image']>>()
  for (const image of data.images) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    try {
      const result = await loadMedia(image.assetId, { maxBytes: IMAGE_MAX_BYTES, signal })
      if (result.status !== 'OK') continue
      loaded.set(image.id, { dataUri: await safeDataUri(result.blob, image.title, 'image'), ...(await imageSize(result.blob)) })
    } catch (reason) {
      if (signal?.aborted) throw reason
    }
  }
  return loaded
}

export type CaseReportPptxOptions = {
  /** Company layout (already prepared or not); the standard layout when omitted. */
  layout?: ReportLayoutDefinition
  labels?: Pick<CaseReportLabels, 'project' | 'request' | 'loadCase'>
  loadMedia?: CaseReportMediaLoader
  signal?: AbortSignal
}

/** PPTX through the existing pptxgenjs layout renderer (`case_results` source). */
export async function buildCaseReportPptx(data: CaseReportData, options: CaseReportPptxOptions = {}): Promise<Blob> {
  const [reportModule, images] = await Promise.all([import('../../../reportExport'), loadImages(data, options.loadMedia ?? caseReportMediaLoader, options.signal)])
  const contents = buildCaseReportContents(data, images)
  const layout = prepareCaseReportLayout(options.layout ?? reportModule.DEFAULT_REPORT_LAYOUT, data.source, contents)
  const scopeValue = (label: string) => data.scopeRows.find((row) => row.label === label)?.value ?? ''
  const labels = options.labels ?? { project: scopeValue('프로젝트'), request: scopeValue('의뢰'), loadCase: scopeValue('하중경우') }
  const reportOptions: ReportExportOptions = { author: '', developmentStage: '', reportDate: data.generatedLabel, reliabilityName: 'Case 결과', reviewPurpose: labels.request, reviewConditions: '', reviewResult: data.summary.peak, reviewConclusion: '', reportTitle: data.title }
  return await reportModule.renderReportPptxBlob(caseReportOverview(data, labels), reportOptions, layout, contents)
}

const HTML_ESCAPES: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }

/** Escapes text for HTML element content and quoted attribute values. */
export function escapeHtml(value: unknown) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => HTML_ESCAPES[character])
}

const HTML_STYLE = `
:root{color-scheme:light;--ink:#142033;--muted:#536276;--line:#d7e0ea;--head:#eef3f8;--accent:#1f6fb2}
*{box-sizing:border-box}
body{margin:0;background:#f6f8fb;color:var(--ink);font:15px/1.55 "Malgun Gothic","Apple SD Gothic Neo","Noto Sans KR",sans-serif}
main{width:min(1280px,100% - 48px);margin:32px auto;display:grid;gap:24px}
header,section{padding:20px 24px;border:1px solid var(--line);border-radius:10px;background:#fff}
h1{margin:0 0 4px;font-size:24px}h2{margin:0 0 12px;font-size:18px}
.meta{margin:0;color:var(--muted)}
dl{display:grid;grid-template-columns:max-content 1fr;gap:6px 18px;margin:16px 0 0}dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere}
.peak{margin:0 0 12px;font-weight:600}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:7px 10px;border:1px solid var(--line);text-align:left;vertical-align:top;overflow-wrap:anywhere}
th{background:var(--head)}
td.num{white-space:nowrap}
.media{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:16px}
figure{margin:0;padding:10px;border:1px solid var(--line);border-radius:8px;background:#fbfcfe}
figure img,figure video{display:block;width:100%;height:auto;max-height:520px;object-fit:contain;background:#0b1620;border-radius:4px}
figcaption{margin-top:6px;font-size:13px;color:var(--muted);overflow-wrap:anywhere}
figcaption strong{color:var(--ink)}
.file{padding:18px 12px;border:1px dashed var(--line);border-radius:4px;color:var(--muted);overflow-wrap:anywhere}
.empty{margin:0;color:var(--muted)}
`

function htmlTable(headers: string[], rows: string[][], numericFrom = Number.POSITIVE_INFINITY) {
  if (!rows.length) return '<p class="empty">표시할 결과가 없습니다.</p>'
  return `<div class="table-wrap"><table><thead><tr>${headers.map((header) => `<th scope="col">${escapeHtml(header)}</th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell, index) => `<td${index >= numericFrom ? ' class="num"' : ''}>${escapeHtml(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`
}

export type CaseReportHtmlOptions = {
  includeVideos: boolean
  loadMedia?: CaseReportMediaLoader
  signal?: AbortSignal
  maxVideoBytes?: number
  maxTotalVideoBytes?: number
}

/**
 * One self-contained HTML file: inline CSS, media as data URIs, no scripts and
 * no external references (enforced by a Content-Security-Policy meta tag).
 */
export async function buildCaseReportHtml(data: CaseReportData, options: CaseReportHtmlOptions): Promise<{ blob: Blob; skippedVideos: string[] }> {
  const loadMedia = options.loadMedia ?? caseReportMediaLoader
  const perVideo = options.maxVideoBytes ?? CASE_REPORT_VIDEO_MAX_BYTES
  const totalCap = options.maxTotalVideoBytes ?? CASE_REPORT_VIDEO_TOTAL_MAX_BYTES
  const images = await loadImages(data, loadMedia, options.signal)
  const parts: BlobPart[] = []
  const skippedVideos: string[] = []
  parts.push(`<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; media-src data:; style-src 'unsafe-inline'; form-action 'none'; base-uri 'none'">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n<meta name="generator" content="VD Simulation Workbench">\n<title>${escapeHtml(data.title)}</title>\n<style>${HTML_STYLE}</style>\n</head>\n<body>\n<main>\n`)
  parts.push(`<header><h1>${escapeHtml(data.title)}</h1><p class="meta">생성 ${escapeHtml(data.generatedLabel)}</p><dl>${data.scopeRows.map((row) => `<dt>${escapeHtml(row.label)}</dt><dd>${escapeHtml(row.value)}</dd>`).join('')}</dl></header>\n`)
  parts.push(`<section aria-labelledby="summary-title"><h2 id="summary-title">요약 · ${escapeHtml(data.summary.valueName)}</h2><p class="peak">${escapeHtml(data.summary.peak)}</p>${htmlTable(data.summary.headers, data.summary.rows, 2)}</section>\n`)
  parts.push(`<section aria-labelledby="scene-title"><h2 id="scene-title">Scene 비교 · 엣지별 최대응력</h2>${htmlTable(data.sceneTable.headers, data.sceneTable.rows, 3)}</section>\n`)
  parts.push('<section aria-labelledby="image-title"><h2 id="image-title">결과 이미지</h2>')
  if (!data.images.length) parts.push('<p class="empty">결과 이미지가 없습니다.</p>')
  else {
    parts.push('<div class="media">')
    for (const image of data.images) {
      const loaded = images.get(image.id)
      const caption = `<figcaption><strong>${escapeHtml(image.scene)}</strong> · ${escapeHtml(image.kind)} · ${escapeHtml(image.title)}</figcaption>`
      parts.push(loaded ? `<figure><img src="${loaded.dataUri}" alt="${escapeHtml(`${image.scene} ${image.kind}`)}">${caption}</figure>` : `<figure><div class="file">이미지를 불러오지 못했습니다.</div>${caption}</figure>`)
    }
    parts.push('</div>')
  }
  parts.push('</section>\n<section aria-labelledby="video-title"><h2 id="video-title">영상</h2>')
  if (!data.videos.length) parts.push('<p class="empty">영상이 없습니다.</p>')
  else {
    parts.push('<div class="media">')
    let used = 0
    for (const video of data.videos) {
      const caption = `<figcaption><strong>${escapeHtml(video.scene)}</strong> · ${escapeHtml(video.fileName)}</figcaption>`
      let embedded = ''
      if (options.includeVideos) {
        const remaining = totalCap - used
        try {
          const result = remaining > 0 ? await loadMedia(video.assetId, { maxBytes: Math.min(perVideo, remaining), signal: options.signal }) : { status: 'TOO_LARGE' as const, size: 0 }
          if (result.status === 'OK') {
            used += result.blob.size
            embedded = `<video controls preload="metadata" src="${await safeDataUri(result.blob, video.fileName, 'video')}" aria-label="${escapeHtml(`${video.scene} ${video.fileName}`)}"></video>`
          } else skippedVideos.push(`${video.scene} · ${video.fileName}`)
        } catch (reason) {
          if (options.signal?.aborted) throw reason
          skippedVideos.push(`${video.scene} · ${video.fileName}`)
        }
      }
      const note = options.includeVideos ? '용량 제한 또는 읽기 오류로 영상을 포함하지 않았습니다.' : '영상 파일은 보고서에 포함하지 않았습니다.'
      parts.push(`<figure>${embedded || `<div class="file">${escapeHtml(video.fileName)}<br>${note}</div>`}${caption}</figure>`)
    }
    parts.push('</div>')
  }
  parts.push('</section>\n</main>\n</body>\n</html>\n')
  return { blob: new Blob(parts, { type: 'text/html;charset=utf-8' }), skippedVideos }
}
