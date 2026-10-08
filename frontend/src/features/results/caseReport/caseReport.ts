/**
 * Case 결과 보고서 (PPTX · HTML).
 *
 * One recipe (`CaseReportData`) is built once from the scope fixed when the
 * report was opened and rendered per format. A recipe has one or more sections:
 * the on-screen 유통환경 selection and the 사용환경 Case are one section each;
 * the Final designation report of a distribution Case has one section per
 * Run Case · Run Option. Everything here is independent of React state so the
 * Final designation flow builds and uploads the same files.
 */
import { simulationDashboardApi, type DashboardAssetBlob, type DashboardCatalog, type DashboardChoice, type DashboardDistribution, type DashboardRunVideo, type DashboardValue, type UsageDashboard } from '../../../shared/api/simulationDashboard'
import { chartSvg, distributionChartGroups, SCENE_DETAIL_POSITIONS, usageChartGroups, type CaseReportChartGroup, type CaseReportSceneDetail } from './caseReportCharts'
import { captureFirstFrame } from './caseReportMedia'
import { api } from '../../../api'
import type { ContentSnapshot, ReportExportOptions } from '../../../reportExport'
import type { Overview, ReportContentItem, ReportElementDefinition, ReportLayoutDefinition, ReportSlideDefinition, ReportSource } from '../../../types'
import type { CaseCompareReport } from '../caseCompare'
import { reportMetaRows, type CaseReportMeta } from './reportMeta'
import { distributionMember, noEdgeSelected, sceneEnvelopes } from '../distributionValues'
import { USAGE_DIRECTION_LABELS, USAGE_DIRECTIONS, usageCell, usageEvaluationLabel, usageEvaluationRows, usageStatusText } from '../usageEvaluations'

export type CaseReportSource = Extract<ReportSource, { kind: 'case_results' }>
export type CaseUsageReportSource = Extract<ReportSource, { kind: 'case_usage' }>
export type CaseFinalReportSource = Extract<ReportSource, { kind: 'case_final' }>
export type CaseReportDataSource = CaseReportSource | CaseUsageReportSource | CaseFinalReportSource
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
export type CaseUsageReportLabels = { project: string; request: string; caseLabel: string; reference: string }
export type CaseFinalReportLabels = { project: string; request: string; caseLabel: string; component: string }

/** `comparison`: the Case 비교 table frozen when the dialog opened (W5); offered as "Case 비교 포함". */
export type CaseDistributionReportScope = { source: CaseReportSource; labels: CaseReportLabels; comparison?: CaseCompareReport | null }
export type CaseUsageReportScope = { source: CaseUsageReportSource; labels: CaseUsageReportLabels; comparison?: CaseCompareReport | null }
export type CaseFinalReportScope = { source: CaseFinalReportSource; labels: CaseFinalReportLabels }
/** Scope of the on-screen 보고서 button (current selection). */
export type CaseReportScope = CaseDistributionReportScope | CaseUsageReportScope
/** Scope of the Final designation report (whole Case). */
export type CaseReportFinalScope = CaseUsageReportScope | CaseFinalReportScope

/** `animated`: the asset is a video (animated contour); without videos its first frame is the image. */
export type CaseReportImage = { id: string; assetId: string; scene: string; kind: '컨투어' | '거동' | '결과'; title: string; animated?: boolean }
/** `kind`: '컨투어' for an animated contour (otherwise a Scene/evaluation video). */
export type CaseReportVideo = { id: string; assetId: string; scene: string; fileName: string; kind?: '컨투어' }
export type CaseReportTable = { title: string; headers: string[]; rows: string[][]; numericFrom: number }
export type CaseReportSection = {
  /** '' for a single-section report (keeps stage-5 content ids). */
  id: string
  /** Section heading; '' for a single-section report. */
  heading: string
  /** Section scope (Component · 기준) shown under the heading. */
  scopeRows: Array<{ label: string; value: string }>
  /** Non-empty when the section has no result (e.g. `결과 없음`); tables and media are then omitted. */
  empty: string
  summary: CaseReportTable & { note: string }
  sceneTable: CaseReportTable | null
  /** The charts the screen shows for this result (요약·엣지별 수준·Scene 상세, or 사용환경 평가별 값). */
  chartGroups: CaseReportChartGroup[]
  images: CaseReportImage[]
  videos: CaseReportVideo[]
}

export type CaseReportData = {
  version: 2
  environment: 'DISTRIBUTION' | 'USAGE'
  source: CaseReportDataSource
  title: string
  caseLabel: string
  generatedAt: string
  generatedLabel: string
  scopeRows: Array<{ label: string; value: string }>
  sections: CaseReportSection[]
  /** W5 "후보 Case 비교" (only when the user ticked "Case 비교 포함"). */
  comparison?: CaseCompareReport
}

/** The recipe with or without the frozen Case 비교 table; without it the outputs are unchanged. */
export function withCaseComparison(data: CaseReportData, comparison: CaseCompareReport | null | undefined): CaseReportData {
  if (!comparison) {
    if (!data.comparison) return data
    const { comparison: _omitted, ...rest } = data
    void _omitted
    return rest
  }
  return { ...data, comparison }
}

export type CaseReportMediaResult = DashboardAssetBlob
export type CaseReportMediaLoader = (assetId: string, options: { maxBytes?: number; signal?: AbortSignal }) => Promise<CaseReportMediaResult>

/** Default loader: the typed dashboard API client. */
export const caseReportMediaLoader: CaseReportMediaLoader = (assetId, options) => simulationDashboardApi.assetBlob(assetId, options)

/** Caps are raw (pre-base64) sizes; data URIs make the HTML about 1.33× larger. */
export const CASE_REPORT_VIDEO_MAX_BYTES = 20 * 1024 * 1024
export const CASE_REPORT_VIDEO_TOTAL_MAX_BYTES = 200 * 1024 * 1024
export const CASE_REPORT_IMAGE_MAX_BYTES = 30 * 1024 * 1024
export const CASE_REPORT_IMAGE_TOTAL_MAX_BYTES = 300 * 1024 * 1024
export const CASE_REPORT_NO_RESULT = '결과 없음'
/** Same wording as the 요약 tab when no edge is selected. */
export const CASE_REPORT_NO_EDGE = '선택 없음'
const NO_EDGE_NOTE = '선택 없음: 표시 옵션에서 엣지를 하나 이상 선택하세요.'
const PPTX_TABLE_ROWS = 12
const EDGES = ['TOP', 'BOTTOM', 'LEFT', 'RIGHT'] as const

const pad = (value: number) => String(value).padStart(2, '0')
const abortError = () => new DOMException('Aborted', 'AbortError')

function valueText(value?: Pick<DashboardValue, 'value' | 'unit'> | null) {
  return value?.value == null ? '값 없음' : `${value.value}${value.unit ? ` ${value.unit}` : ' · 단위 미확인'}`
}

function sceneNumber(scene: { scene_sequence_number: number | null }) {
  return scene.scene_sequence_number == null ? '순번 미확인' : String(scene.scene_sequence_number)
}

function generatedLabelOf(date: Date) {
  return `${date.getFullYear()}.${pad(date.getMonth() + 1)}.${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

const filled = (rows: Array<{ label: string; value: string }>) => rows.map((row) => ({ ...row, value: row.value || '없음' }))

/** One section from a distribution result (same values as the 요약·Scene 비교 tabs). */
export function buildDistributionSection(input: { id: string; heading: string; scopeRows: Array<{ label: string; value: string }>; distribution: DashboardDistribution; videos: DashboardRunVideo[]; edgeKeys: string; sceneDetails?: CaseReportSceneDetail[] }): CaseReportSection {
  const { distribution, videos } = input
  const prefix = input.id ? `${input.id}:` : ''
  const member = distributionMember(distribution)
  const memberId = member?.id
  const noEdgeSelection = noEdgeSelected(distribution, input.edgeKeys)
  const peaksByScene = new Map<string, DashboardDistribution['edge_peaks']>()
  for (const peak of distribution.edge_peaks) {
    if (peak.member_id !== memberId) continue
    peaksByScene.set(peak.scene_id, [...(peaksByScene.get(peak.scene_id) ?? []), peak])
  }
  const sceneLabel = new Map(distribution.scenes.map((scene) => [scene.id, scene.label]))

  let peak: { scene: string; value: number; unit: string | null } | null = null
  const summaryRows = sceneEnvelopes(distribution, input.edgeKeys).map(({ scene, value: envelope, unit }) => {
    if (envelope != null && (!peak || envelope > peak.value)) peak = { scene: scene.label, value: envelope, unit }
    const edgePeaks = (peaksByScene.get(scene.id) ?? []).filter((item) => item.value != null)
    const maxEdge = edgePeaks.reduce<(typeof edgePeaks)[number] | null>((best, item) => !best || (item.value ?? 0) > (best.value ?? 0) ? item : best, null)
    const envelopeText = noEdgeSelection ? CASE_REPORT_NO_EDGE : valueText(envelope == null ? null : { value: envelope, unit })
    return [sceneNumber(scene), scene.label, envelopeText, maxEdge ? `${maxEdge.edge} · ${valueText(maxEdge)}` : '값 없음']
  })
  const finalPeak = peak as { scene: string; value: number; unit: string | null } | null
  const sceneRows = distribution.scenes.map((scene) => {
    const peaks = peaksByScene.get(scene.id) ?? []
    const description = [scene.description, scene.contact_code, scene.repetition].filter(Boolean).join(' · ') || '설명 미확인'
    return [sceneNumber(scene), scene.label, description, ...EDGES.map((edge) => valueText(peaks.find((item) => item.edge === edge)))]
  })
  // Same cells as the 컨투어·거동 matrices: the Case's member, a matched asset whose status is READY
  // (an asset without a status counts as READY, like the screen, which shows any matched asset).
  const ready = <T extends { member_id: string; asset?: DashboardDistribution['contours'][number]['asset'] }>(cell: T) => cell.member_id === memberId && Boolean(cell.asset?.asset_id) && (cell.asset!.status ?? 'READY') === 'READY'
  const contours = distribution.contours.filter(ready)
  const animated = contours.filter((cell) => cell.asset!.kind === 'VIDEO')
  const fileOf = (title: string | null | undefined, fallback: string) => title || fallback
  const images: CaseReportImage[] = [
    ...contours.map((cell) => ({ id: `${prefix}contour:${cell.cell_id}`, assetId: cell.asset!.asset_id, scene: sceneLabel.get(cell.scene_id) ?? cell.scene_id, kind: '컨투어' as const, title: cell.asset!.kind === 'VIDEO' ? `${fileOf(cell.asset!.title, '컨투어 영상')} · 첫 프레임` : fileOf(cell.asset!.title, '컨투어'), ...(cell.asset!.kind === 'VIDEO' ? { animated: true } : {}) })),
    ...distribution.behaviors.filter((cell) => ready(cell) && cell.asset!.kind !== 'VIDEO')
      .map((cell) => ({ id: `${prefix}behavior:${cell.cell_id}`, assetId: cell.asset!.asset_id, scene: sceneLabel.get(cell.scene_id) ?? cell.scene_id, kind: '거동' as const, title: `${cell.subject_role === 'UNKNOWN' ? '거동' : cell.subject_role} 거동` })),
  ]
  // Animated contours are videos too; the Run video list may hold the same asset, listed once as 컨투어.
  const contourAssets = new Set(animated.map((cell) => cell.asset!.asset_id))
  const reportVideos: CaseReportVideo[] = [
    ...videos.filter((video) => !contourAssets.has(video.asset_id)).map((video) => ({ id: `${prefix}${video.video_id}`, assetId: video.asset_id, scene: video.scene_label, fileName: video.title })),
    ...animated.map((cell) => ({ id: `${prefix}contour-video:${cell.cell_id}`, assetId: cell.asset!.asset_id, scene: sceneLabel.get(cell.scene_id) ?? cell.scene_id, fileName: fileOf(cell.asset!.title, '컨투어 영상'), kind: '컨투어' as const })),
  ]
  return {
    id: input.id,
    heading: input.heading,
    scopeRows: filled(input.scopeRows),
    empty: '',
    summary: {
      title: '요약 · 선택 엣지 최대응력',
      headers: ['순번', 'Scene', '선택 엣지 최대응력', '최대 엣지'],
      rows: summaryRows,
      numericFrom: 2,
      note: noEdgeSelection ? NO_EDGE_NOTE : finalPeak ? `최대 ${valueText({ value: finalPeak.value, unit: finalPeak.unit })} · ${finalPeak.scene}` : '값 없음',
    },
    sceneTable: { title: 'Scene 비교 · 엣지별 최대응력', headers: ['순번', 'Scene', '자세·충돌', ...EDGES], rows: sceneRows, numericFrom: 3 },
    chartGroups: distributionChartGroups({ prefix, distribution, edgeKeys: input.edgeKeys, sceneDetails: input.sceneDetails }),
    images,
    videos: reportVideos,
  }
}

/** Section without results: shown as `결과 없음` instead of blocking the report. */
export function emptySection(id: string, heading: string, scopeRows: Array<{ label: string; value: string }>, text = CASE_REPORT_NO_RESULT): CaseReportSection {
  return { id, heading, scopeRows: filled(scopeRows), empty: text, summary: { title: '요약', headers: [], rows: [], numericFrom: Number.POSITIVE_INFINITY, note: text }, sceneTable: null, chartGroups: [], images: [], videos: [] }
}

/** The 사용환경 "다섯 평가 종합" table and the evaluations' media, as the screen shows them. */
export function buildUsageSection(usage: UsageDashboard, withReference: boolean): CaseReportSection {
  const cellText = (value: DashboardValue | null | undefined, metric: 'value' | 'verdict') => {
    const view = usageCell(value, metric)
    return view.label ? `${view.text} · ${view.label}` : view.text
  }
  const rows = usageEvaluationRows(usage).map((row) => {
    const reference = row.evaluation.reference
    const referenceText = !reference ? '미선택' : reference.reason || USAGE_DIRECTIONS.flatMap((direction) => reference[direction] ? [`${USAGE_DIRECTION_LABELS[direction]}: ${cellText(reference[direction], row.metric)}`] : []).join('\n') || '해당 없음'
    return [row.label || `${usageEvaluationLabel(row.evaluation)} 판정`, row.key ?? '', ...USAGE_DIRECTIONS.map((direction) => cellText(row.evaluation[direction], row.metric)), ...(withReference ? [referenceText] : [])]
  })
  const images: CaseReportImage[] = []
  const videos: CaseReportVideo[] = []
  for (const evaluation of usage.evaluations) {
    const scene = usageEvaluationLabel(evaluation)
    for (const asset of evaluation.media ?? []) {
      if (asset.kind === 'VIDEO') videos.push({ id: `usage:${evaluation.id}:${asset.asset_id}`, assetId: asset.asset_id, scene, fileName: asset.title || '영상' })
      else if (asset.status === 'READY') images.push({ id: `usage:${evaluation.id}:${asset.asset_id}`, assetId: asset.asset_id, scene, kind: '결과', title: asset.title || '결과 이미지' })
    }
  }
  return {
    id: '',
    heading: '',
    scopeRows: [],
    empty: '',
    summary: { title: '다섯 평가 종합', headers: ['평가', '원문 키', '공통', '전방', '후방', ...(withReference ? ['Reference'] : [])], rows, numericFrom: 2, note: `평가 상태: ${usageStatusText(usage)}` },
    sceneTable: null,
    chartGroups: usageChartGroups(usage, withReference),
    images,
    videos,
  }
}

/** Builds the format-independent recipe for the on-screen 유통환경 selection. Pure. */
export function buildCaseReportData(input: { scope: CaseDistributionReportScope; distribution: DashboardDistribution; videos: DashboardRunVideo[]; sceneDetails?: CaseReportSceneDetail[]; generatedAt: Date }): CaseReportData {
  const { scope, distribution, videos, generatedAt } = input
  const { labels, source } = scope
  const generatedLabel = generatedLabelOf(generatedAt)
  return {
    version: 2,
    environment: 'DISTRIBUTION',
    source,
    title: `${labels.caseLabel} 해석 결과 보고서`,
    caseLabel: labels.caseLabel,
    generatedAt: generatedAt.toISOString(),
    generatedLabel,
    scopeRows: filled([
      { label: '프로젝트', value: labels.project },
      { label: '의뢰', value: labels.request },
      { label: 'Case', value: labels.caseLabel },
      { label: '하중경우', value: labels.loadCase },
      { label: 'Run Case', value: labels.run },
      { label: 'Run Option', value: labels.option },
      { label: 'Component · 기준', value: [labels.component, labels.basis].filter(Boolean).join(' · ') },
      { label: '생성 일시', value: generatedLabel },
    ]),
    sections: [buildDistributionSection({ id: '', heading: '', scopeRows: [], distribution, videos, edgeKeys: source.edgeKeys, sceneDetails: input.sceneDetails })],
  }
}

/** Recipe of a 사용환경 Case. Pure. */
export function buildCaseUsageReportData(input: { scope: CaseUsageReportScope; usage: UsageDashboard; generatedAt: Date }): CaseReportData {
  const { scope, usage, generatedAt } = input
  const { labels, source } = scope
  const generatedLabel = generatedLabelOf(generatedAt)
  const withReference = Boolean(source.referenceCaseId && source.referenceCaptureId)
  return {
    version: 2,
    environment: 'USAGE',
    source,
    title: `${labels.caseLabel} 사용환경 해석 결과 보고서`,
    caseLabel: labels.caseLabel,
    generatedAt: generatedAt.toISOString(),
    generatedLabel,
    scopeRows: filled([
      { label: '프로젝트', value: labels.project },
      { label: '의뢰', value: labels.request },
      { label: 'Case', value: labels.caseLabel },
      { label: '환경', value: '사용환경' },
      ...(withReference ? [{ label: 'Reference', value: labels.reference }] : []),
      { label: '생성 일시', value: generatedLabel },
    ]),
    sections: [buildUsageSection(usage, withReference)],
  }
}

/** Recipe of a whole distribution Case: one section per Run Case · Run Option. Pure. */
export function buildCaseFinalReportData(input: { scope: CaseFinalReportScope; sections: CaseReportSection[]; generatedAt: Date }): CaseReportData {
  const { scope, sections, generatedAt } = input
  const { labels, source } = scope
  const generatedLabel = generatedLabelOf(generatedAt)
  const withResult = sections.filter((section) => !section.empty).length
  return {
    version: 2,
    environment: 'DISTRIBUTION',
    source,
    title: `${labels.caseLabel} 해석 결과 보고서`,
    caseLabel: labels.caseLabel,
    generatedAt: generatedAt.toISOString(),
    generatedLabel,
    scopeRows: filled([
      { label: '프로젝트', value: labels.project },
      { label: '의뢰', value: labels.request },
      { label: 'Case', value: labels.caseLabel },
      { label: '범위', value: `Case 전체 · Run Option ${sections.length}개 (결과 있음 ${withResult}개)` },
      { label: '생성 일시', value: generatedLabel },
    ]),
    sections,
  }
}

const SCENE_DETAIL_CONCURRENCY = 4

/**
 * Scene 상세 (TOP/BOT/LH/RH line values) of every Scene of the Case's member, as
 * the screen reads it on a Scene click. A failed read becomes an empty chart
 * with the reason (abort still aborts the build).
 */
export async function loadCaseReportSceneDetails(distribution: DashboardDistribution, lineIndices: string, signal?: AbortSignal): Promise<CaseReportSceneDetail[]> {
  const member = distributionMember(distribution)
  if (!member) return []
  const jobs = distribution.scenes.flatMap((scene) => SCENE_DETAIL_POSITIONS.map((position) => ({ sceneId: scene.id, position })))
  const results: CaseReportSceneDetail[] = new Array(jobs.length)
  let next = 0
  const worker = async () => {
    while (next < jobs.length) {
      const index = next++
      const job = jobs[index]
      if (signal?.aborted) throw abortError()
      try {
        const detail = await simulationDashboardApi.sceneDetail(job.sceneId, { execution_run_id: member.execution_run_id, capture_id: member.capture_id, run_option_id: member.run_option_id, mode: member.mode, component_id: member.component_id, basis: member.basis, line_indices: lineIndices, position: job.position }, signal)
        results[index] = { ...job, detail }
      } catch (reason) {
        if (signal?.aborted) throw reason
        results[index] = { ...job, detail: null, error: reason instanceof Error && reason.message ? reason.message : '읽기 오류' }
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(SCENE_DETAIL_CONCURRENCY, jobs.length) }, worker))
  return results
}

/** Loads everything the recipe needs for the fixed distribution scope through the typed API client. */
export async function loadCaseReportSources(source: Pick<CaseReportSource, 'runId' | 'captureId' | 'optionId' | 'mode' | 'componentId' | 'basis' | 'edgeKeys' | 'lineIndices'>, signal?: AbortSignal): Promise<{ distribution: DashboardDistribution; videos: DashboardRunVideo[]; sceneDetails: CaseReportSceneDetail[] }> {
  const distributionPromise = simulationDashboardApi.distribution(source.runId, { capture_id: source.captureId, run_option_id: source.optionId || undefined, mode: source.mode, component_id: source.componentId, basis: source.basis as 'DETAIL' | 'REPORTED_SUMMARY', edge_keys: source.edgeKeys, line_indices: source.lineIndices }, signal)
  const videos: DashboardRunVideo[] = []
  for (let page = 1; page <= 250; page += 1) {
    const result = await simulationDashboardApi.videos(source.runId, { capture_id: source.captureId, run_option_id: source.optionId || undefined, mode: source.optionId ? undefined : source.mode || undefined, page, page_size: 20 }, signal)
    videos.push(...(result.videos ?? []))
    if (!result.pagination.has_next) break
  }
  const distribution = await distributionPromise
  return { distribution, videos, sceneDetails: await loadCaseReportSceneDetails(distribution, source.lineIndices, signal) }
}

/** Project and request names for the cover (falls back to the given labels). */
export async function resolveReportNames(projectId: string, requestId: string) {
  const [project, request] = await Promise.all([
    api.projects().then((items) => items.find((item) => item.id === projectId)?.name).catch(() => undefined),
    api.requests(projectId).then((items) => items.find((item) => item.id === requestId)?.title).catch(() => undefined),
  ])
  return { project, request }
}

/** Loads and builds the recipe of the on-screen scope (distribution selection or usage Case). */
export async function loadCaseReport(scope: CaseReportScope, options: { signal?: AbortSignal; generatedAt?: Date } = {}): Promise<CaseReportData> {
  const generatedAt = options.generatedAt ?? new Date()
  const { source } = scope
  const namesPromise = resolveReportNames(source.projectId, source.requestId)
  if (source.kind === 'case_usage') {
    const usage = await simulationDashboardApi.usage(source.caseId, source.captureId, source.referenceCaseId || undefined, source.referenceCaptureId || undefined, options.signal)
    const names = await namesPromise
    const labels = { ...scope.labels as CaseUsageReportLabels, project: names.project || scope.labels.project, request: names.request || scope.labels.request }
    return buildCaseUsageReportData({ scope: { source, labels }, usage, generatedAt })
  }
  const [sources, names] = await Promise.all([loadCaseReportSources(source, options.signal), namesPromise])
  const labels = { ...scope.labels as CaseReportLabels, project: names.project || scope.labels.project, request: names.request || scope.labels.request }
  return buildCaseReportData({ scope: { source, labels }, distribution: sources.distribution, videos: sources.videos, sceneDetails: sources.sceneDetails, generatedAt })
}

const uniqueChoices = <T extends DashboardChoice>(items: T[]) => Array.from(new Map(items.map((item) => [item.id, item])).values())
const basisLabel = (id: string) => id === 'REPORTED_SUMMARY' ? '원본 요약' : id === 'DETAIL' ? '상세 추출값' : id

/**
 * Every Run Case · Run Option of the Case in the merged latest result, with the
 * same candidate rules as the Case results path. Options without a result
 * become `결과 없음` sections; read errors fail the build (no silent gaps).
 */
export async function loadCaseFinalSections(source: CaseFinalReportSource, preferredComponent: string, signal?: AbortSignal, catalogInput?: DashboardCatalog): Promise<CaseReportSection[]> {
  const catalog = catalogInput ?? await simulationDashboardApi.catalog(source.projectId, source.requestId, 'DISTRIBUTION', signal)
  const capture = source.captureId
  const current = (item: DashboardChoice) => item.capture_id == null || (capture !== '' && item.capture_id === capture)
  const sections: CaseReportSection[] = []
  const loads = uniqueChoices(catalog.load_cases.filter((item) => item.case_id === source.catalogCaseId && current(item)))
  for (const load of loads) {
    const runs = uniqueChoices(catalog.execution_runs.filter((item) => item.case_id === source.catalogCaseId && item.load_case_id === load.id && current(item)))
    for (const run of runs) {
      const explicit = catalog.run_options ?? []
      const options = uniqueChoices(explicit.length
        ? explicit.filter((item) => item.execution_run_id === run.id && current(item))
        : catalog.modes.filter((item) => item.execution_run_id === run.id && current(item)))
      if (!options.length) {
        sections.push(emptySection(`s${sections.length + 1}`, [load.label, run.label].join(' › '), [], `Run Option 없음 · ${CASE_REPORT_NO_RESULT}`))
        continue
      }
      for (const option of options) {
        if (signal?.aborted) throw abortError()
        const id = `s${sections.length + 1}`
        const heading = [load.label, run.label, option.option_label || option.label].join(' › ')
        const mode = option.mode ?? option.id
        const components = uniqueChoices(catalog.components.filter((item) => (item.execution_run_id === run.id || item.run_id === run.id) && item.mode === mode && item.capture_id === capture && (!item.run_option_id || item.run_option_id === option.id)))
        const component = components.find((item) => preferredComponent && item.label === preferredComponent) ?? components.find((item) => item.has_values) ?? components[0]
        const bases = uniqueChoices(catalog.bases)
        const basis = bases.find((item) => item.id === source.basis) ?? bases.find((item) => item.has_values) ?? bases[0]
        const hasData = Boolean(capture && run.capture_id === capture && option.capture_id === capture && component && basis)
        const scopeRows = [{ label: 'Component · 기준', value: hasData ? [component!.label, basisLabel(basis!.id)].join(' · ') : '' }]
        if (!hasData) { sections.push(emptySection(id, heading, scopeRows)); continue }
        const loaded = await loadCaseReportSources({ runId: run.id, captureId: capture, optionId: option.id, mode, componentId: component!.id, basis: basis!.id, edgeKeys: source.edgeKeys, lineIndices: source.lineIndices }, signal)
        sections.push(buildDistributionSection({ id, heading, scopeRows, distribution: loaded.distribution, videos: loaded.videos, edgeKeys: source.edgeKeys, sceneDetails: loaded.sceneDetails }))
      }
    }
  }
  return sections
}

/** Loads and builds the Final designation recipe: the usage Case, or every Run Option of a distribution Case. */
export async function loadCaseFinalReport(scope: CaseReportFinalScope, options: { signal?: AbortSignal; generatedAt?: Date } = {}): Promise<CaseReportData> {
  if (scope.source.kind === 'case_usage') return await loadCaseReport(scope as CaseUsageReportScope, options)
  const source = scope.source
  const labels = scope.labels as CaseFinalReportLabels
  const [sections, names] = await Promise.all([loadCaseFinalSections(source, labels.component, options.signal), resolveReportNames(source.projectId, source.requestId)])
  return buildCaseFinalReportData({ scope: { source, labels: { ...labels, project: names.project || labels.project, request: names.request || labels.request } }, sections, generatedAt: options.generatedAt ?? new Date() })
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

export const allReportImages = (data: CaseReportData) => data.sections.flatMap((section) => section.images)
export const allReportVideos = (data: CaseReportData) => data.sections.flatMap((section) => section.videos)

function coverText(data: CaseReportData) {
  const lines = [...data.scopeRows.map((row) => `${row.label}: ${row.value}`), ...(data.comparison ? [`후보 Case 비교: ${data.comparison.headers.slice(data.comparison.environment === 'USAGE' ? 2 : 1).join(', ')}`] : [])]
  if (data.sections.length === 1 && !data.sections[0].heading) return [...lines, `요약: ${data.sections[0].summary.note}`].join('\n')
  const parts = data.sections.slice(0, 10).map((section) => `· ${section.heading}: ${section.empty || section.summary.note}`)
  const more = data.sections.length > 10 ? [`· 외 ${data.sections.length - 10}개`] : []
  return [...lines, '구성:', ...parts, ...more].join('\n')
}

export type CaseReportContentOptions = {
  /** Videos (and animated contours) embedded: one slide per video; animated contours are not shown as still images. */
  includeVideos?: boolean
  /** Videos read for this build (PPTX embeds mp4 only). */
  videos?: CaseReportLoadedVideos
}

/** Why a video is not embedded in the PPTX ('' when it is). */
export function pptxVideoReason(video: CaseReportVideo, options: CaseReportContentOptions) {
  if (!options.includeVideos) return '포함 안 함'
  const loaded = options.videos?.videos.get(video.id)
  if (loaded) return loaded.mime === 'video/mp4' ? '' : 'PowerPoint 호환 형식(mp4)이 아님'
  return options.videos?.skipped.find((item) => item.id === video.id)?.reason ?? '읽지 않음'
}

/**
 * PPTX content items (one per slide); tables are paginated. The content set
 * (ids and presentations) depends only on the recipe and `includeVideos`, never
 * on what could be read, so a prepared layout stays valid for the build.
 */
export function buildCaseReportContents(data: CaseReportData, images: Map<string, ContentSnapshot['image']> = new Map(), options: CaseReportContentOptions = {}): ReportContentItem[] {
  const key = `${data.source.caseId}:${data.source.captureId}`
  const pageTitle = (title: string, index: number, total: number) => total > 1 ? `${title} (${index + 1}/${total})` : title
  const items: ReportContentItem[] = [{ contentId: `case:${key}:scope`, kind: 'case_scope', sourceKey: key, title: '보고서 범위', defaultPresentation: 'text', data: { text: coverText(data) } satisfies ContentSnapshot }]
  for (const section of data.sections) {
    const base = `case:${key}${section.id ? `:${section.id}` : ''}`
    const named = (title: string) => section.heading ? `${section.heading} · ${title}` : title
    if (section.empty) {
      const scope = section.scopeRows.filter((row) => row.value !== '없음').map((row) => `${row.label}: ${row.value}`)
      items.push({ contentId: `${base}:empty`, kind: 'case_summary', sourceKey: key, title: section.heading || data.caseLabel, defaultPresentation: 'text', data: { text: [section.empty, ...scope].join('\n') } satisfies ContentSnapshot })
      continue
    }
    const summaryPages = chunk(section.summary.rows, PPTX_TABLE_ROWS)
    items.push(...(summaryPages.length ? summaryPages : [[]]).map((rows, index, pages): ReportContentItem => ({
      contentId: `${base}:summary:${index + 1}`, kind: 'case_summary', sourceKey: key, title: pageTitle(named(section.summary.title), index, pages.length), defaultPresentation: rows.length ? 'table' : 'text',
      data: { text: rows.length ? section.summary.note : '표시할 결과가 없습니다.', tableHeaders: section.summary.headers, tableRows: rows } satisfies ContentSnapshot,
    })))
    const [summaryChart, ...otherCharts] = section.chartGroups
    const chartItem = (group: CaseReportChartGroup): ReportContentItem => ({
      contentId: `case:${key}:${group.id}`, kind: 'case_chart', sourceKey: key, title: named(group.title), defaultPresentation: 'chart',
      data: { charts: group.charts, text: group.charts.map((chart) => chart.title).join('\n') } satisfies ContentSnapshot,
    })
    if (summaryChart) items.push(chartItem(summaryChart))
    if (section.sceneTable) {
      const table = section.sceneTable
      items.push(...chunk(table.rows, PPTX_TABLE_ROWS).map((rows, index, pages): ReportContentItem => ({
        contentId: `${base}:scenes:${index + 1}`, kind: 'case_scene_table', sourceKey: key, title: pageTitle(named(table.title), index, pages.length), defaultPresentation: 'table',
        data: { tableHeaders: table.headers, tableRows: rows } satisfies ContentSnapshot,
      })))
    }
    items.push(...otherCharts.map(chartItem))
    items.push(...section.images.filter((image) => !(image.animated && options.includeVideos)).map((image): ReportContentItem => {
      const loaded = images.get(image.id)
      return {
        contentId: `${base}:image:${image.id}`, kind: 'case_image', sourceKey: image.assetId, title: named(`${image.scene} · ${image.kind}`), defaultPresentation: 'image',
        data: { image: loaded, text: loaded ? image.title : `${image.title}\n${image.animated ? '첫 프레임을 만들지 못했습니다.' : '이미지를 넣지 못했습니다.'}` } satisfies ContentSnapshot,
      }
    }))
    if (options.includeVideos) {
      items.push(...section.videos.map((video): ReportContentItem => {
        const loaded = options.videos?.videos.get(video.id)
        const reason = pptxVideoReason(video, options)
        return {
          contentId: `${base}:video:${video.id}`, kind: 'case_video', sourceKey: video.assetId, title: named(`${video.scene} · ${video.kind === '컨투어' ? '컨투어 영상' : '영상'}`), defaultPresentation: 'image',
          data: { video: loaded && !reason ? { dataUri: loaded.dataUri, poster: loaded.poster, width: loaded.width, height: loaded.height } : undefined, text: reason ? `${video.fileName}\n영상을 넣지 못했습니다: ${reason}` : video.fileName } satisfies ContentSnapshot,
        }
      }))
    }
    const videoRows = section.videos.map((video) => [video.scene, video.kind === '컨투어' ? `${video.fileName} (컨투어)` : video.fileName, ...(options.includeVideos ? [pptxVideoReason(video, options) || '포함'] : [])])
    items.push(...chunk(videoRows, PPTX_TABLE_ROWS).map((rows, index, pages): ReportContentItem => ({
      contentId: `${base}:videos:${index + 1}`, kind: 'case_videos', sourceKey: key, title: pageTitle(named('영상 목록'), index, pages.length), defaultPresentation: 'table',
      data: { text: rows.map((row) => row.join(' · ')).join('\n'), tableHeaders: ['Scene', '영상 파일', ...(options.includeVideos ? ['PPTX'] : [])], tableRows: rows } satisfies ContentSnapshot,
    })))
  }
  if (data.comparison) {
    const comparison = data.comparison
    const scope = comparison.scopeRows.map((row) => `${row.label}: ${row.value || '없음'}`).join(' · ')
    items.push(...chunk(comparison.rows, PPTX_TABLE_ROWS).map((rows, index, pages): ReportContentItem => ({
      contentId: `${CASE_COMPARE_CONTENT_PREFIX}${key}:${index + 1}`, kind: 'case_scene_table', sourceKey: key, title: pageTitle(comparison.title, index, pages.length), defaultPresentation: 'table',
      data: { text: [scope, comparison.note].filter(Boolean).join('\n'), tableHeaders: comparison.headers, tableRows: rows } satisfies ContentSnapshot,
    })))
  }
  return items
}

/** Content ids of the Case 비교 slides start with this prefix. */
export const CASE_COMPARE_CONTENT_PREFIX = 'case-compare:'
const COMPARE_SLIDE_PREFIX = 'slide-case-compare-'

/**
 * Adds (or removes) the Case 비교 slides at the end of a prepared layout so
 * ticking "Case 비교 포함" keeps the other slides and any edits to them.
 */
export function syncCaseComparisonSlides(layout: ReportLayoutDefinition, contents: ReportContentItem[]): ReportLayoutDefinition {
  const slides = (layout.slides ?? []).filter((slide) => !slide.id.startsWith(COMPARE_SLIDE_PREFIX))
  const compare = contents.filter((content) => content.contentId.startsWith(CASE_COMPARE_CONTENT_PREFIX))
  // The other slides keep their signature state: only a layout in sync before stays in sync.
  const signature = caseReportContentSignature(contents)
  const inSync = [signature, caseReportContentSignature(contents.filter((content) => !content.contentId.startsWith(CASE_COMPARE_CONTENT_PREFIX)))].includes(layout.contentSignature ?? '')
  return { ...layout, contentSignature: inSync ? signature : layout.contentSignature, slides: [...slides, ...compare.map((content, index) => contentSlide(content, `${COMPARE_SLIDE_PREFIX}${index + 1}`, `compare-${index}`))] }
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
  return [cover, ...rest.map((content, index): ReportSlideDefinition => content.contentId.startsWith(CASE_COMPARE_CONTENT_PREFIX)
    ? contentSlide(content, `${COMPARE_SLIDE_PREFIX}${rest.filter((item, position) => position < index && item.contentId.startsWith(CASE_COMPARE_CONTENT_PREFIX)).length + 1}`, `compare-${index}`)
    : contentSlide(content, `slide-case-${index + 1}-${content.kind}`, String(index)))]
}

function contentSlide(content: ReportContentItem, id: string, suffix: string): ReportSlideDefinition {
  const type: ReportElementDefinition['type'] = content.defaultPresentation === 'card' ? 'scalar-card' : content.defaultPresentation
  return {
    id, name: content.title, kind: 'custom', repeat: 'none',
    elements: [
      element(`case-title-${suffix}`, 'title', content.title, [1, 1, 30, 2], { source: 'content', contentId: content.contentId, key: 'title' }),
      element(`case-body-${suffix}`, type, content.title, [1, 4, 30, 12], { source: 'content', contentId: content.contentId, key: 'body' }),
    ],
  }
}

/**
 * Applies the Case slides to a company layout; slides edited for this same scope
 * are kept. The company PPTX template binding (templateSource/templateAssetId/
 * templateBindings) is kept in the definition so a saved copy never loses it;
 * it is only overridden at render time (`caseReportRenderLayout`).
 */
export function prepareCaseReportLayout(layout: ReportLayoutDefinition, source: CaseReportDataSource, contents: ReportContentItem[], forceDefault = false): ReportLayoutDefinition {
  const sameSource = JSON.stringify(layout.sourceScope) === JSON.stringify(source)
  const contentSignature = caseReportContentSignature(contents)
  const base = { ...layout, sourceScope: source, contentSignature }
  // Slides are reused only for the same scope AND the same content set (charts, images,
  // videos); a layout saved before the content changed is rebuilt, not left stale.
  if (!forceDefault && layout.contentMode && sameSource && layout.contentSignature === contentSignature && layout.slides?.length) return base
  return { ...base, contentMode: 'one-per-slide', slides: createCaseReportSlides(contents) }
}

/** Short deterministic signature of the content ids and presentations (FNV-1a). */
export function caseReportContentSignature(contents: ReportContentItem[]) {
  let hash = 0x811c9dc5
  for (const character of contents.map((content) => `${content.contentId}|${content.defaultPresentation}`).join('\n')) {
    hash ^= character.charCodeAt(0)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  return `v1:${contents.length}:${hash.toString(16).padStart(8, '0')}`
}

const META_FIELDS: Array<[keyof CaseReportMeta, string, string]> = [
  ['author', 'author', '작성자'], ['developmentStage', 'development_stage', '개발단계'],
  ['reviewConditions', 'review_conditions', '검토조건'], ['reviewConclusion', 'review_conclusion', '결론'],
]

/** W7: non-empty report information as boxes along the bottom of the cover slide (render time only). */
export function withCaseReportMeta(layout: ReportLayoutDefinition, meta: CaseReportMeta | undefined): ReportLayoutDefinition {
  const present = META_FIELDS.filter(([field]) => meta?.[field]?.trim())
  if (!present.length || !layout.slides?.length) return layout
  const coverIndex = Math.max(0, layout.slides.findIndex((slide) => slide.kind === 'cover'))
  const width = Math.floor(30 / present.length)
  const elements = present.map(([, key, label], index) => element(`case-cover-meta-${key}`, 'text', label, [1 + index * width, 16, width - (index === present.length - 1 ? 0 : 1), 2], { source: 'field', key }))
  return { ...layout, slides: layout.slides.map((slide, index) => index === coverIndex ? { ...slide, elements: [...slide.elements.filter((item) => !item.id.startsWith('case-cover-meta-')), ...elements] } : slide) }
}

/** Uploaded PPTX templates do not support this report format; render with the visual layout. */
export function caseReportRenderLayout(layout: ReportLayoutDefinition): ReportLayoutDefinition {
  return { ...layout, templateSource: 'native', templateAssetId: undefined, templateBindings: {} }
}

/** Minimal Overview the shared renderer and layout editor need; it carries no results of its own. */
export function caseReportOverview(data: CaseReportData, labels: { project: string; request: string; loadCase: string }): Overview {
  const source = data.source
  return {
    load_case: { id: 'loadCaseId' in source ? source.loadCaseId : '', name: labels.loadCase, analysis_type: 'CASE_RESULTS', project_name: labels.project, product_name: '', request_title: labels.request, request_id: source.requestId, project_id: source.projectId, created_at: data.generatedAt, parameters: {} },
    run: 'runId' in source ? source.runId : source.caseId, overall_verdict: 'NO_DATA', analysis_verdicts: { open_cell: 'NO_DATA', chassis_rear: 'NO_DATA' }, threshold: null,
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

/** Images read once per build and shared by both formats. `usedBytes`: raw bytes read (budgets of the Final upload). */
export type CaseReportLoadedImages = { images: Map<string, NonNullable<ContentSnapshot['image']>>; skipped: string[]; usedBytes?: number }

const imageName = (image: CaseReportImage) => `${image.scene} · ${image.kind} · ${image.title}`

/**
 * Reads every report image once (per-image 30 MB, total 300 MB raw); others are listed as skipped.
 * An animated contour is read as a video (≤ 20 MB) and its first frame becomes the image; with
 * `includeVideos` it is embedded as a video instead and not read here.
 */
export async function loadCaseReportImages(data: CaseReportData, options: { loadMedia?: CaseReportMediaLoader; signal?: AbortSignal; maxImageBytes?: number; maxTotalImageBytes?: number; includeVideos?: boolean } = {}): Promise<CaseReportLoadedImages> {
  const loadMedia = options.loadMedia ?? caseReportMediaLoader
  const perImage = options.maxImageBytes ?? CASE_REPORT_IMAGE_MAX_BYTES
  const totalCap = options.maxTotalImageBytes ?? CASE_REPORT_IMAGE_TOTAL_MAX_BYTES
  const images = new Map<string, NonNullable<ContentSnapshot['image']>>()
  const skipped: string[] = []
  let used = 0
  for (const image of allReportImages(data)) {
    if (image.animated && options.includeVideos) continue
    if (options.signal?.aborted) throw abortError()
    const remaining = totalCap - used
    try {
      const cap = Math.min(image.animated ? CASE_REPORT_VIDEO_MAX_BYTES : perImage, remaining)
      const result = remaining > 0 ? await loadMedia(image.assetId, { maxBytes: cap, signal: options.signal }) : { status: 'TOO_LARGE' as const, size: 0 }
      if (result.status !== 'OK') { skipped.push(imageName(image)); continue }
      if (image.animated) {
        const frame = await captureFirstFrame(new Blob([result.blob], { type: mediaType(result.blob, image.title, 'video') }), { signal: options.signal })
        if (!frame) { skipped.push(`${imageName(image)} (첫 프레임을 만들지 못함)`); continue }
        used += frame.dataUri.length * 0.75
        images.set(image.id, frame)
        continue
      }
      used += result.blob.size
      images.set(image.id, { dataUri: await safeDataUri(result.blob, image.title, 'image'), ...(await imageSize(result.blob)) })
    } catch (reason) {
      if (options.signal?.aborted) throw reason
      skipped.push(imageName(image))
    }
  }
  return { images, skipped, usedBytes: Math.round(used) }
}

export type CaseReportLoadedVideo = { dataUri: string; mime: string; poster?: string; width?: number; height?: number }
/** Videos read once per build and shared by both formats; `skipped` carries the reason (size cap, read error). */
export type CaseReportLoadedVideos = { videos: Map<string, CaseReportLoadedVideo>; skipped: Array<{ id: string; name: string; reason: string }>; usedBytes: number }

export const caseReportVideoName = (video: CaseReportVideo) => `${video.scene} · ${video.kind === '컨투어' ? '컨투어 · ' : ''}${video.fileName}`

/**
 * Reads every report video once (per video 20 MB, total 200 MB raw, or less via
 * `maxTotalVideoBytes`). `posters`: the first frame (PNG) for the PPTX cover.
 */
export async function loadCaseReportVideos(data: CaseReportData, options: { loadMedia?: CaseReportMediaLoader; signal?: AbortSignal; maxVideoBytes?: number; maxTotalVideoBytes?: number; posters?: boolean } = {}): Promise<CaseReportLoadedVideos> {
  const loadMedia = options.loadMedia ?? caseReportMediaLoader
  const perVideo = options.maxVideoBytes ?? CASE_REPORT_VIDEO_MAX_BYTES
  const totalCap = options.maxTotalVideoBytes ?? CASE_REPORT_VIDEO_TOTAL_MAX_BYTES
  const videos = new Map<string, CaseReportLoadedVideo>()
  const skipped: CaseReportLoadedVideos['skipped'] = []
  const mb = (bytes: number) => `${Math.round(bytes / (1024 * 1024))}MB`
  let used = 0
  for (const video of allReportVideos(data)) {
    if (options.signal?.aborted) throw abortError()
    const remaining = totalCap - used
    try {
      const result = remaining > 0 ? await loadMedia(video.assetId, { maxBytes: Math.min(perVideo, remaining), signal: options.signal }) : { status: 'TOO_LARGE' as const, size: 0 }
      if (result.status !== 'OK') {
        skipped.push({ id: video.id, name: caseReportVideoName(video), reason: result.size > perVideo ? `영상당 ${mb(perVideo)} 초과` : `전체 영상 ${mb(totalCap)} 초과` })
        continue
      }
      used += result.blob.size
      const mime = mediaType(result.blob, video.fileName, 'video')
      const typed = new Blob([result.blob], { type: mime })
      const poster = options.posters ? await captureFirstFrame(typed, { signal: options.signal }) : null
      videos.set(video.id, { dataUri: await blobToDataUri(typed), mime, ...(poster ? { poster: poster.dataUri, width: poster.width, height: poster.height } : {}) })
    } catch (reason) {
      if (options.signal?.aborted) throw reason
      skipped.push({ id: video.id, name: caseReportVideoName(video), reason: '읽기 오류' })
    }
  }
  return { videos, skipped, usedBytes: used }
}

/** Videos not embedded in the given format, as `<Scene · 파일> — <사유>` (the dialog lists them). */
export function skippedVideoNames(data: CaseReportData, videos: CaseReportLoadedVideos, format: CaseReportFormat) {
  return allReportVideos(data).flatMap((video) => {
    const reason = format === 'pptx' ? pptxVideoReason(video, { includeVideos: true, videos }) : videos.videos.has(video.id) ? '' : videos.skipped.find((item) => item.id === video.id)?.reason ?? '읽기 오류'
    return reason ? [`${caseReportVideoName(video)} — ${reason}`] : []
  })
}

export type CaseReportPptxOptions = {
  /** Company layout (already prepared or not); the standard layout when omitted. */
  layout?: ReportLayoutDefinition
  labels?: { project: string; request: string; loadCase: string }
  loadMedia?: CaseReportMediaLoader
  /** Images already read for this build (shared with the HTML format). */
  images?: CaseReportLoadedImages
  signal?: AbortSignal
  /** W7: author and optional 개발단계·검토조건·결론 for the cover/summary. */
  meta?: CaseReportMeta
  /** Embed mp4 videos (and animated contours) with `addMedia`; others are listed with the reason. */
  includeVideos?: boolean
  /** Videos already read for this build (shared with the HTML format). */
  videos?: CaseReportLoadedVideos
}

/** PPTX through the existing pptxgenjs layout renderer (charts are native, editable PowerPoint charts). */
export async function buildCaseReportPptx(data: CaseReportData, options: CaseReportPptxOptions = {}): Promise<Blob> {
  const [reportModule, loaded] = await Promise.all([import('../../../reportExport'), options.images ?? loadCaseReportImages(data, { loadMedia: options.loadMedia, signal: options.signal, includeVideos: options.includeVideos })])
  if (options.signal?.aborted) throw abortError()
  const videos = options.includeVideos ? options.videos ?? await loadCaseReportVideos(data, { loadMedia: options.loadMedia, signal: options.signal, posters: true }) : undefined
  if (options.signal?.aborted) throw abortError()
  const contents = buildCaseReportContents(data, loaded.images, { includeVideos: options.includeVideos, videos })
  const layout = withCaseReportMeta(caseReportRenderLayout(prepareCaseReportLayout(options.layout ?? reportModule.DEFAULT_REPORT_LAYOUT, data.source, contents)), options.meta)
  const scopeValue = (label: string) => data.scopeRows.find((row) => row.label === label)?.value ?? ''
  const labels = options.labels ?? { project: scopeValue('프로젝트'), request: scopeValue('의뢰'), loadCase: scopeValue('하중경우') }
  const reviewResult = data.sections.length === 1 ? data.sections[0].empty || data.sections[0].summary.note : scopeValue('범위')
  const meta = options.meta
  const reportOptions: ReportExportOptions = { author: meta?.author.trim() ?? '', developmentStage: meta?.developmentStage.trim() ?? '', reportDate: data.generatedLabel, reliabilityName: data.environment === 'USAGE' ? '사용환경 Case 결과' : 'Case 결과', reviewPurpose: labels.request, reviewConditions: meta?.reviewConditions.trim() ?? '', reviewResult, reviewConclusion: meta?.reviewConclusion.trim() ?? '', reportTitle: data.title }
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
.part{display:grid;gap:16px;padding:20px 24px;border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;background:#fff}
.part>section{padding:0;border:0;border-radius:0}
h1{margin:0 0 4px;font-size:24px}h2{margin:0 0 12px;font-size:18px}h3{margin:0 0 10px;font-size:16px}
.part>h2{margin:0}
.meta{margin:0;color:var(--muted)}
dl{display:grid;grid-template-columns:max-content 1fr;gap:6px 18px;margin:16px 0 0}dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere}
.peak{margin:0 0 12px;font-weight:600}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:7px 10px;border:1px solid var(--line);text-align:left;vertical-align:top;overflow-wrap:anywhere;white-space:pre-line}
th{background:var(--head)}
td.num{white-space:pre-line}
.media{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:16px}
figure{margin:0;padding:10px;border:1px solid var(--line);border-radius:8px;background:#fbfcfe}
figure img,figure video{display:block;width:100%;height:auto;max-height:520px;object-fit:contain;background:#0b1620;border-radius:4px}
figcaption{margin-top:6px;font-size:13px;color:var(--muted);overflow-wrap:anywhere}
figcaption strong{color:var(--ink)}
.file{padding:18px 12px;border:1px dashed var(--line);border-radius:4px;color:var(--muted);overflow-wrap:anywhere}
.empty{margin:0;color:var(--muted)}
.no-result{margin:0;padding:14px 12px;border:1px dashed var(--line);border-radius:6px;color:var(--muted);font-weight:600}
.chart-group{margin:0 0 16px}.chart-title{margin:0 0 8px;font-weight:600}
.charts{display:grid;grid-template-columns:repeat(auto-fill,minmax(480px,1fr));gap:12px}
figure.chart{background:#fff}figure.chart svg{display:block;width:100%;height:auto}
`

function htmlTable(headers: string[], rows: string[][], numericFrom = Number.POSITIVE_INFINITY) {
  if (!rows.length) return '<p class="empty">표시할 결과가 없습니다.</p>'
  return `<div class="table-wrap"><table><thead><tr>${headers.map((header) => `<th scope="col">${escapeHtml(header)}</th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell, index) => `<td${index >= numericFrom ? ' class="num"' : ''}>${escapeHtml(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`
}

export type CaseReportHtmlOptions = {
  includeVideos: boolean
  loadMedia?: CaseReportMediaLoader
  /** Images already read for this build (shared with the PPTX format). */
  images?: CaseReportLoadedImages
  /** Videos already read for this build (shared with the PPTX format). */
  videos?: CaseReportLoadedVideos
  signal?: AbortSignal
  maxVideoBytes?: number
  maxTotalVideoBytes?: number
  /** W7: non-empty fields are added to the scope block. */
  meta?: CaseReportMeta
}

/**
 * One self-contained HTML file: inline CSS, media as data URIs, no scripts and
 * no external references (enforced by a Content-Security-Policy meta tag).
 */
export async function buildCaseReportHtml(data: CaseReportData, options: CaseReportHtmlOptions): Promise<{ blob: Blob; skippedVideos: string[]; skippedImages: string[] }> {
  const loadMedia = options.loadMedia ?? caseReportMediaLoader
  const perVideo = options.maxVideoBytes ?? CASE_REPORT_VIDEO_MAX_BYTES
  const totalCap = options.maxTotalVideoBytes ?? CASE_REPORT_VIDEO_TOTAL_MAX_BYTES
  const loaded = options.images ?? await loadCaseReportImages(data, { loadMedia, signal: options.signal, includeVideos: options.includeVideos })
  const videos = options.includeVideos ? options.videos ?? await loadCaseReportVideos(data, { loadMedia, signal: options.signal, maxVideoBytes: perVideo, maxTotalVideoBytes: totalCap }) : undefined
  const parts: BlobPart[] = []
  const multi = data.sections.length > 1 || Boolean(data.sections[0]?.heading)
  parts.push(`<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; media-src data:; style-src 'unsafe-inline'; form-action 'none'; base-uri 'none'">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n<meta name="generator" content="VD Simulation Workbench">\n<title>${escapeHtml(data.title)}</title>\n<style>${HTML_STYLE}</style>\n</head>\n<body>\n<main>\n`)
  parts.push(`<header><h1>${escapeHtml(data.title)}</h1><p class="meta">생성 ${escapeHtml(data.generatedLabel)}</p><dl>${[...data.scopeRows, ...reportMetaRows(options.meta)].map((row) => `<dt>${escapeHtml(row.label)}</dt><dd>${escapeHtml(row.value)}</dd>`).join('')}</dl></header>\n`)
  for (const [index, section] of data.sections.entries()) {
    const h = multi ? 'h3' : 'h2'
    const anchor = `s${index + 1}`
    if (multi) parts.push(`<div class="part" role="region" aria-labelledby="${anchor}-title"><h2 id="${anchor}-title">${escapeHtml(section.heading || data.caseLabel)}</h2>${section.scopeRows.length ? `<dl>${section.scopeRows.map((row) => `<dt>${escapeHtml(row.label)}</dt><dd>${escapeHtml(row.value)}</dd>`).join('')}</dl>` : ''}\n`)
    if (section.empty) {
      parts.push(`<p class="no-result">${escapeHtml(section.empty)}</p>\n`)
    } else {
      parts.push(`<section aria-labelledby="${anchor}-summary"><${h} id="${anchor}-summary">${escapeHtml(section.summary.title)}</${h}><p class="peak">${escapeHtml(section.summary.note)}</p>${htmlTable(section.summary.headers, section.summary.rows, section.summary.numericFrom)}</section>\n`)
      if (section.sceneTable) parts.push(`<section aria-labelledby="${anchor}-scenes"><${h} id="${anchor}-scenes">${escapeHtml(section.sceneTable.title)}</${h}>${htmlTable(section.sceneTable.headers, section.sceneTable.rows, section.sceneTable.numericFrom)}</section>\n`)
      if (section.chartGroups.length) {
        parts.push(`<section aria-labelledby="${anchor}-charts" data-section="charts"><${h} id="${anchor}-charts">그래프</${h}>`)
        for (const group of section.chartGroups) {
          parts.push(`<div class="chart-group"><p class="chart-title">${escapeHtml(group.title)}</p><div class="charts">`)
          for (const chart of group.charts) parts.push(`<figure class="chart">${chartSvg(chart)}${chart.notes?.length ? `<figcaption>${chart.notes.map(escapeHtml).join('<br>')}</figcaption>` : ''}</figure>`)
          parts.push('</div></div>')
        }
        parts.push('</section>\n')
      }
      const sectionImages = section.images.filter((image) => !(image.animated && options.includeVideos))
      parts.push(`<section aria-labelledby="${anchor}-images"><${h} id="${anchor}-images">결과 이미지</${h}>`)
      if (!sectionImages.length) parts.push('<p class="empty">결과 이미지가 없습니다.</p>')
      else {
        parts.push('<div class="media">')
        for (const image of sectionImages) {
          const dataUri = loaded.images.get(image.id)?.dataUri
          const caption = `<figcaption><strong>${escapeHtml(image.scene)}</strong> · ${escapeHtml(image.kind)} · ${escapeHtml(image.title)}</figcaption>`
          parts.push(dataUri ? `<figure><img src="${dataUri}" alt="${escapeHtml(`${image.scene} ${image.kind}`)}">${caption}</figure>` : `<figure><div class="file">${image.animated ? '애니메이션 컨투어의 첫 프레임을 만들지 못했습니다.' : '용량 제한 또는 읽기 오류로 이미지를 넣지 못했습니다.'}</div>${caption}</figure>`)
        }
        parts.push('</div>')
      }
      parts.push(`</section>\n<section aria-labelledby="${anchor}-videos"><${h} id="${anchor}-videos">영상</${h}>`)
      if (!section.videos.length) parts.push('<p class="empty">영상이 없습니다.</p>')
      else {
        parts.push('<div class="media">')
        for (const video of section.videos) {
          const caption = `<figcaption><strong>${escapeHtml(video.scene)}</strong> · ${video.kind === '컨투어' ? '컨투어 영상 · ' : ''}${escapeHtml(video.fileName)}</figcaption>`
          const loadedVideo = videos?.videos.get(video.id)
          const embedded = loadedVideo ? `<video controls preload="metadata" src="${loadedVideo.dataUri}" aria-label="${escapeHtml(`${video.scene} ${video.fileName}`)}"></video>` : ''
          const reason = videos?.skipped.find((item) => item.id === video.id)?.reason
          const note = options.includeVideos ? `${reason ? `${reason}로 ` : '용량 제한 또는 읽기 오류로 '}영상을 포함하지 않았습니다.` : '영상 파일은 보고서에 포함하지 않았습니다.'
          parts.push(`<figure>${embedded || `<div class="file">${escapeHtml(video.fileName)}<br>${note}</div>`}${caption}</figure>`)
        }
        parts.push('</div>')
      }
      parts.push('</section>\n')
    }
    if (multi) parts.push('</div>\n')
  }
  if (data.comparison) {
    const comparison = data.comparison
    parts.push(`<section aria-labelledby="case-compare-title" data-section="case-compare"><h2 id="case-compare-title">${escapeHtml(comparison.title)}</h2><dl>${comparison.scopeRows.map((row) => `<dt>${escapeHtml(row.label)}</dt><dd>${escapeHtml(row.value || '없음')}</dd>`).join('')}</dl><p class="peak">${escapeHtml(comparison.note)}</p>${htmlTable(comparison.headers, comparison.rows, comparison.environment === 'USAGE' ? 2 : 1)}</section>\n`)
  }
  parts.push('</main>\n</body>\n</html>\n')
  if (options.signal?.aborted) throw abortError()
  return { blob: new Blob(parts, { type: 'text/html;charset=utf-8' }), skippedVideos: videos ? skippedVideoNames(data, videos, 'html') : [], skippedImages: loaded.skipped }
}
