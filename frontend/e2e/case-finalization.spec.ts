import { inflateRawSync } from 'node:zlib'
import { expect, test, type Page, type Route } from '@playwright/test'
import { loginWorkspace, mockResultEnvironments } from './workspace-test-helpers'

// Mixed-environment request: keeps the 사용환경/유통환경 toggle these specs click (E4).
test.beforeEach(async ({ page }) => { await mockResultEnvironments(page) })

// Final 지정 (stage 6): preview of the latest-result basis, PPTX/HTML report
// choice, browser-built reports uploaded per format, then confirm. Mocked API,
// synthetic data only.
const CASE_ID = 'case-a'
const CAPTURE_ID = 'latest:case-a'
const RUN_ID = 'run-a'
const MODE = 'INDIVIDUAL'
const OPERATION = 'a'.repeat(32)
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAEElEQVR4nGPQqDgBRww4OQBBxhDhzXmo9QAAAABJRU5ErkJggg==', 'base64')
const SHOT_DIR = '/tmp/claude-0'

const catalog = {
  environment: 'DISTRIBUTION',
  cases: [{ id: CASE_ID, label: 'Case A' }],
  load_cases: [{ id: 'load-drop', label: 'Drop', case_id: CASE_ID, capture_id: CAPTURE_ID }],
  execution_runs: [{ id: RUN_ID, label: 'Run A', case_id: CASE_ID, load_case_id: 'load-drop', capture_id: CAPTURE_ID }],
  run_options: [{ id: 'option-individual', label: 'Individual', option_label: 'Individual', option_status: 'PRESENT', case_id: CASE_ID, execution_run_id: RUN_ID, mode: MODE, capture_id: CAPTURE_ID }],
  modes: [{ id: MODE, label: MODE, case_id: CASE_ID, execution_run_id: RUN_ID, capture_id: CAPTURE_ID }],
  captures: [{ id: CAPTURE_ID, label: '최신 결과', case_id: CASE_ID, kind: 'LATEST', merged_capture_count: 2 }],
  components: [{ id: 'C23', label: 'C23', case_id: CASE_ID, execution_run_id: RUN_ID, mode: MODE, capture_id: CAPTURE_ID }],
  bases: [{ id: 'DETAIL', label: '상세 추출값' }],
}

// Several Run Options: none is chosen on screen; Cumulative has no result yet.
const multiOptionCatalog = {
  ...catalog,
  run_options: [
    ...catalog.run_options,
    { id: 'option-assembly', label: 'Assembly', option_label: 'Assembly', option_status: 'PRESENT', case_id: CASE_ID, execution_run_id: RUN_ID, mode: 'ASSEMBLY', capture_id: CAPTURE_ID },
    { id: 'option-cumulative', label: 'Cumulative', option_label: 'Cumulative', option_status: 'PRESENT', case_id: CASE_ID, execution_run_id: RUN_ID, mode: 'CUMULATIVE', capture_id: null },
  ],
  components: [...catalog.components, { id: 'C23-asm', label: 'C23', case_id: CASE_ID, execution_run_id: RUN_ID, mode: 'ASSEMBLY', run_option_id: 'option-assembly', capture_id: CAPTURE_ID }],
}

const usageCatalog = { environment: 'USAGE', cases: [{ id: 'usage-case', label: 'Usage Case' }], load_cases: [], execution_runs: [], modes: [], components: [], bases: [], captures: [{ id: 'latest:usage-case', label: '최신 결과', case_id: 'usage-case', kind: 'LATEST', merged_capture_count: 1 }] }
const usageValue = (value: number | null, unit: string, verdict: string | null = null) => ({ value, unit, status: 'READY', verdict, value_status: value == null ? 'NOT_APPLICABLE' : 'READY', verdict_status: verdict == null ? 'NOT_APPLICABLE' : 'READY' })
const usagePayload = {
  contract_version: 1,
  context: { project_id: 'project-tv-001', request_id: 'request-drop-001', simulation_case_id: 'usage-case', capture_id: 'latest:usage-case', context_key: 'usage-final' },
  status: 'READY',
  evaluations: [
    { id: 'Settle', name: 'Settle', status: 'READY', common: usageValue(2.5, 'deg'), front: null, rear: null, media: [{ asset_id: 'img-1', kind: 'IMAGE', status: 'READY', title: 'Settle.png' }] },
    { id: 'Wobble', name: 'Wobble', status: 'READY', common: null, front: usageValue(6.25, 'mm'), rear: usageValue(7, 'mm'), media: [{ asset_id: 'vid-small', kind: 'VIDEO', status: 'READY', title: 'Wobble.mp4' }] },
    { id: 'Horizontal_Force_Angle', name: 'Horizontal Force Angle', status: 'READY', common: null, front: usageValue(1, 'deg'), rear: usageValue(1.5, 'deg'), media: [] },
    { id: 'Slope_Angle', name: 'Slope Angle', status: 'READY', common: null, front: usageValue(11, 'deg', 'OK'), rear: usageValue(8, 'deg', 'NG'), media: [] },
    { id: 'Slope_Angle_360', name: 'Slope Angle 360', status: 'READY', common: null, front: usageValue(null, '', 'OK'), rear: usageValue(null, '', 'OK'), media: [] },
  ],
  quality_issues: [],
}

function distribution(optionId = 'option-individual') {
  const base = optionId === 'option-assembly' ? 30 : 10
  const member = { id: 'member-a', label: 'Case A', simulation_case_id: CASE_ID, load_case_id: 'load-drop', execution_run_id: RUN_ID, run_option_id: optionId, mode: optionId === 'option-assembly' ? 'ASSEMBLY' : MODE, capture_id: CAPTURE_ID, component_id: 'C23', basis: 'DETAIL' }
  const scenes = ['2_Face', '3_Face'].map((label, index) => ({ id: `scene-${index + 1}`, label, scene_sequence_number: index + 1, scenario_number: index + 1, order_status: 'CONFIRMED', contact_code: 'Face1', repetition: '1st' }))
  return {
    contract_version: 1,
    context: { project_id: 'project-tv-001', request_id: 'request-drop-001', simulation_case_id: CASE_ID, load_case_id: 'load-drop', execution_run_id: RUN_ID, run_option_id: 'option-individual', mode: MODE, capture_id: CAPTURE_ID, component_id: 'C23', basis: 'DETAIL', context_key: 'final' },
    status: 'READY', members: [member], scenes,
    edge_peaks: scenes.map((scene, index) => ({ value: base + index, unit: 'MPa', unit_status: 'CONFIRMED', completeness: 'FULL', status: 'READY', basis: 'DETAIL', scope: 'SELECTED_EDGE_LINES', edge: 'TOP', scene_id: scene.id, member_id: member.id })),
    series: scenes.map((scene, index) => ({ id: `series-${scene.id}`, value: base + index, unit: 'MPa', status: 'READY', completeness: 'FULL', basis: 'DETAIL', scene_id: scene.id, scene_sequence_number: index + 1, member_id: member.id, edge: 'TOP', selected_edge_envelope: base + index })),
    contours: [{ cell_id: 'cell-1', scene_id: 'scene-1', member_id: member.id, status: 'READY', reason: null, asset: { asset_id: 'img-1', kind: 'IMAGE', status: 'READY', title: 'Contour.png', frame_role: 'FINAL_FRAME' } }],
    behaviors: [], quality_issues: [],
  }
}

const videos = {
  contract_version: 1,
  context: { ...distribution().context, option_label: 'Individual', load_case_name: 'Drop', run_label: 'Run A' },
  pagination: { page: 1, page_size: 20, total_items: 2, total_pages: 1, has_previous: false, has_next: false },
  videos: [
    { video_id: 'v-1', asset_id: 'vid-small', scene_id: 'scene-1', scene_label: '2_Face', title: 'BEHAVIOR_2_Face.mp4', status: 'READY', source_capture_id: 'capture-1' },
    { video_id: 'v-2', asset_id: 'vid-big', scene_id: 'scene-2', scene_label: '3_Face', title: 'BEHAVIOR_3_Face_large.mp4', status: 'READY', source_capture_id: 'capture-2' },
  ],
}

const files = [
  { source_relative_path: 'R/Working/Case A/Drop/Run A/Individual/2_Face/model.rad', case_relative_path: 'Drop/Run A/Individual/2_Face/model.rad', category: 'CAE', source_basis: 'CURRENT_CONFIRMED_SCENE', size: 40, sha256: '1'.repeat(64) },
  { source_relative_path: 'R/Working/Case A/Drop/Run A/Individual/2_Face/result.csv', case_relative_path: 'Drop/Run A/Individual/2_Face/result.csv', category: 'CAE', source_basis: 'SOURCE_CAPTURE', source_capture_id: 'capture-1', size: 60, sha256: '2'.repeat(64) },
  { source_relative_path: 'R/Working/Case A/Drop/Run A/Individual/3_Face/result.csv', case_relative_path: 'Drop/Run A/Individual/3_Face/result.csv', category: 'CAE', source_basis: 'SOURCE_CAPTURE', source_capture_id: 'capture-2', size: 60, sha256: '3'.repeat(64) },
]
const reportsDir = `R/Final/Report/Case A/${OPERATION}`
const usageReportsDir = `U/Final/Report/Usage Case/${OPERATION}`
const preview = {
  schema_version: 2, operation_id: OPERATION, status: 'PREVIEW', project_id: 'project-tv-001', request_id: 'request-drop-001', environment: 'DISTRIBUTION',
  case_id: CASE_ID, case_label: 'Case A', case_path: 'R/Working/Case A', capture_id: CAPTURE_ID, basis: 'LATEST',
  scene_sources: [{ scene_path: 'R/Working/Case A/Drop/Run A/Individual/2_Face', source_capture_id: 'capture-1' }, { scene_path: 'R/Working/Case A/Drop/Run A/Individual/3_Face', source_capture_id: 'capture-2' }],
  capture_fingerprint: 'f', folder_schema_snapshot_id: 'schema', scene_paths: [], files,
  excluded_scenes: [{ scene_path: 'R/Working/Case A/Drop/Run A/Individual/4_Face', source_capture_id: 'capture-3', reason: 'CAPTURE_SCHEMA_MISSING' }],
  counts: { CAE: 3, input_decks: 1, rad_decks: 1, inc_decks: 0, results: 2, scene_reports: 0 }, missing: { input_decks: false, rad_decks: false, inc_decks: true },
  excluded_capture_file_count: 0, previewed_at: '2026-10-03T00:00:00Z', plan_sha256: 'c'.repeat(64), can_confirm: true,
  output_paths: { CAE: `R/Final/CAE/Case A/${OPERATION}`, Reports: reportsDir },
  report_files: { pptx: 'Case_A_report.pptx', html: 'Case_A_report.html' },
  report_paths: { pptx: `${reportsDir}/Case_A_report.pptx`, html: `${reportsDir}/Case_A_report.html` },
  report_limits: { pptx: 67108864, html: 335544320 },
}

const usagePreview = {
  ...preview, environment: 'USAGE', case_id: 'usage-case', case_label: 'Usage Case', case_path: 'U/Working/Usage Case', capture_id: 'latest:usage-case',
  scene_sources: ['Settle', 'Wobble', 'Horizontal_Force_Angle', 'Slope_Angle', 'Slope_Angle_360'].map((name) => ({ scene_path: `U/Working/Usage Case/${name}`, source_capture_id: 'usage-capture-1' })),
  excluded_scenes: [],
  files: [{ source_relative_path: 'U/Working/Usage Case/Settle/result.json', case_relative_path: 'Settle/result.json', category: 'CAE', source_basis: 'SOURCE_CAPTURE', source_capture_id: 'usage-capture-1', size: 80, sha256: '4'.repeat(64) }],
  counts: { CAE: 1, input_decks: 0, rad_decks: 0, inc_decks: 0, results: 1, scene_reports: 0 }, missing: { input_decks: false, rad_decks: false, inc_decks: false },
  output_paths: { CAE: `U/Final/CAE/Usage Case/${OPERATION}`, Reports: usageReportsDir },
  report_files: { pptx: 'Usage_Case_report.pptx', html: 'Usage_Case_report.html' },
  report_paths: { pptx: `${usageReportsDir}/Usage_Case_report.pptx`, html: `${usageReportsDir}/Usage_Case_report.html` },
}

type Uploads = Array<{ format: string; query: URLSearchParams; contentType: string; head: string; size: number; text: string; body: Buffer }>

/** Every slide XML of a PPTX (minimal ZIP reader: central directory + raw deflate). */
function slideXml(buffer: Buffer) {
  let end = buffer.length - 22
  while (end >= 0 && buffer.readUInt32LE(end) !== 0x06054b50) end -= 1
  const count = buffer.readUInt16LE(end + 10)
  let offset = buffer.readUInt32LE(end + 16)
  const slides: string[] = []
  for (let index = 0; index < count; index += 1) {
    const method = buffer.readUInt16LE(offset + 10)
    const compressedSize = buffer.readUInt32LE(offset + 20)
    const nameLength = buffer.readUInt16LE(offset + 28)
    const extraLength = buffer.readUInt16LE(offset + 30)
    const commentLength = buffer.readUInt16LE(offset + 32)
    const localOffset = buffer.readUInt32LE(offset + 42)
    const entryName = buffer.toString('utf8', offset + 46, offset + 46 + nameLength)
    if (/^ppt\/slides\/slide\d+\.xml$/.test(entryName)) {
      const dataStart = localOffset + 30 + buffer.readUInt16LE(localOffset + 26) + buffer.readUInt16LE(localOffset + 28)
      const data = buffer.subarray(dataStart, dataStart + compressedSize)
      slides.push((method === 8 ? inflateRawSync(data) : data).toString('utf8'))
    }
    offset += 46 + nameLength + extraLength + commentLength
  }
  return slides.join('\n')
}

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

type JobState = 'RUNNING' | 'FAILED' | 'COMPLETE'

/** W2: confirm queues a background copy job; GET /{id}/job reports it (scripted per test). */
function jobView(plan: typeof preview, formats: Array<'pptx' | 'html'>, state: JobState | 'QUEUED') {
  const reports = formats.map((format) => ({ format, file_name: plan.report_files[format], size: 100, sha256: '9'.repeat(64), relative_path: plan.report_paths[format] }))
  const record = state === 'COMPLETE' ? { ...plan, status: 'COMPLETE', created_by: 'test', confirmed_at: '2026-10-03T00:01:00Z', reports } : null
  const total = plan.files.reduce((sum, file) => sum + file.size, 0)
  return {
    operation_id: plan.operation_id, state, phase: state === 'RUNNING' || state === 'FAILED' ? 'COPYING' : null,
    files_done: state === 'COMPLETE' ? plan.files.length : 1, files_total: plan.files.length,
    bytes_done: state === 'COMPLETE' ? total : plan.files[0].size, bytes_total: total,
    current_file: state === 'RUNNING' ? plan.files[1]?.case_relative_path ?? null : null,
    error: state === 'FAILED' ? { code: 'FINALIZATION_PUBLISH_BUSY', message: '다른 프로그램이 임시 폴더를 사용하고 있어 Final 폴더로 옮기지 못했습니다.' } : null,
    attempt: 1, queued_at: '2026-10-03T00:00:30Z', started_at: '2026-10-03T00:00:31Z', updated_at: '2026-10-03T00:00:32Z',
    case_id: plan.case_id, capture_id: plan.capture_id, reports, output_paths: plan.output_paths, active: state === 'RUNNING', record,
  }
}

async function installMocks(page: Page, options: { failFirstUpload?: boolean; multiOption?: boolean; jobStates?: JobState[]; status?: () => Record<string, unknown>; layouts?: unknown[] } = {}) {
  if (options.layouts) {
    const layouts = options.layouts
    await page.route('**/api/report-layouts', (route) => fulfillJson(route, layouts))
  }
  const uploads: Uploads = []
  const confirms: Array<Record<string, unknown>> = []
  const jobPolls: string[] = []
  const script = { states: options.jobStates ?? ['RUNNING', 'COMPLETE'] as JobState[] }
  let lastConfirm: { plan: typeof preview; formats: Array<'pptx' | 'html'> } = { plan: preview, formats: ['pptx'] }
  const usageCaptures: string[] = []
  let failures = options.failFirstUpload ? 1 : 0
  await page.route('**/api/folder-discovery/environments/sync', (route) => fulfillJson(route, { status: 'UNCHANGED', changed: false, snapshot_id: null, diff: { added: 0, removed: 0, changed: 0 }, code: null, message: null, check_mode: 'QUICK', checked_at: new Date().toISOString(), coalesced: false }))
  await page.route('**/api/dashboard/catalog**', (route) => fulfillJson(route, new URL(route.request().url()).searchParams.get('environment') === 'USAGE' ? usageCatalog : options.multiOption ? multiOptionCatalog : catalog))
  await page.route('**/api/dashboard/usage/cases/**', (route) => {
    usageCaptures.push(new URL(route.request().url()).searchParams.get('capture_id') ?? '')
    return fulfillJson(route, usagePayload)
  })
  await page.route('**/api/dashboard/distribution/scenes/**', (route) => fulfillJson(route, { context: distribution().context, scene: { id: 'scene-1', label: 'scene-1' }, edge_peaks: [], line_points: [], assets: [], quality_issues: [] }))
  await page.route('**/api/dashboard/assets/**', (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop() ?? '')
    if (id === 'img-1') return route.fulfill({ status: 200, contentType: 'image/png', body: PNG })
    if (id === 'vid-small') return route.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.from('synthetic-mp4-bytes') })
    if (id === 'vid-big') return route.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.alloc(21 * 1024 * 1024, 1) })
    return route.fulfill({ status: 404, body: '' })
  })
  await page.route('**/api/dashboard/distribution/runs/**', (route) => {
    const url = new URL(route.request().url())
    return fulfillJson(route, url.pathname.endsWith('/videos') ? videos : distribution(url.searchParams.get('run_option_id') ?? undefined))
  })
  await page.route('**/api/dashboard/finalizations/status**', (route) => fulfillJson(route, options.status ? options.status() : { latest: null, selected_case_latest: null, retryable_operations: [], unverified_records: 0 }))
  await page.route('**/api/dashboard/finalizations/preview', (route) => {
    const body = route.request().postDataJSON() as Record<string, string>
    if (body.environment === 'USAGE') {
      expect(body).toMatchObject({ case_id: 'usage-case', capture_id: 'latest:usage-case' })
      return fulfillJson(route, usagePreview)
    }
    expect(body).toMatchObject({ case_id: CASE_ID, capture_id: CAPTURE_ID, environment: 'DISTRIBUTION' })
    return fulfillJson(route, preview)
  })
  await page.route(`**/api/dashboard/finalizations/${OPERATION}/reports/*`, async (route) => {
    const request = route.request()
    expect(request.method()).toBe('PUT')
    const url = new URL(request.url())
    const body = request.postDataBuffer() ?? Buffer.alloc(0)
    const format = url.pathname.split('/').pop() ?? ''
    uploads.push({ format, query: url.searchParams, contentType: request.headers()['content-type'] ?? '', head: body.subarray(0, 15).toString('latin1'), size: body.length, text: format === 'html' ? body.toString('utf8') : '', body: Buffer.from(body) })
    if (failures > 0) {
      failures -= 1
      return fulfillJson(route, { detail: { code: 'FINALIZATION_REPORT_PPTX_INVALID', message: 'PPTX 보고서를 읽을 수 없습니다.' } }, 422)
    }
    const plan = url.searchParams.get('environment') === 'USAGE' ? usagePreview : preview
    return fulfillJson(route, { operation_id: OPERATION, format, file_name: plan.report_files[format as 'pptx' | 'html'], size: body.length, sha256: '9'.repeat(64), report_path: plan.report_paths[format as 'pptx' | 'html'], status: 'STAGED' })
  })
  await page.route('**/api/dashboard/finalizations/confirm', (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    confirms.push(body)
    lastConfirm = { plan: body.environment === 'USAGE' ? usagePreview : preview, formats: body.report_formats as Array<'pptx' | 'html'> }
    return fulfillJson(route, jobView(lastConfirm.plan, lastConfirm.formats, 'QUEUED'))
  })
  await page.route(`**/api/dashboard/finalizations/${OPERATION}/job**`, (route) => {
    const url = new URL(route.request().url())
    expect(url.searchParams.get('capture_id')).toBeNull()
    jobPolls.push(url.searchParams.get('case_id') ?? '')
    const state = script.states.length > 1 ? script.states.shift()! : script.states[0]
    return fulfillJson(route, jobView(lastConfirm.plan, lastConfirm.formats, state))
  })
  return { uploads, confirms, usageCaptures, jobPolls, script }
}

async function openFinal(page: Page) {
  await loginWorkspace(page)
  await page.goto('/workspace/requests?project=project-tv-001&request=request-drop-001&view=case_results')
  await expect(page.getByRole('region', { name: 'SPDM 해석 결과 대시보드' })).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: '유통환경', exact: true }).click()
  await expect(page.getByTestId('distribution-dashboard')).toBeVisible()
  await expect(page.getByRole('button', { name: '보고서', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Final 지정', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Final 지정 확인' })
  await expect(dialog).toBeVisible()
  return dialog
}

test('Final 지정은 고른 PPTX·HTML을 만들어 형식별로 올린 뒤 확정하고 저장 위치를 보인다', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const { uploads, confirms } = await installMocks(page)
  const dialog = await openFinal(page)
  await expect(dialog).toContainText('최신 결과 · Scene 2개 · 결과 버전 2개')
  // A Scene whose newest capture cannot be used is listed, never replaced by an older capture.
  await expect(dialog.getByTestId('case-final-excluded-scenes')).toContainText('제외되는 Scene 1개')
  await expect(dialog.getByTestId('case-final-excluded-scenes')).toContainText('4_Face · 최신 결과에 확정 위치 정보 없음')
  await expect(dialog).toContainText('Scene 폴더 파일 3개')
  await expect(dialog).toContainText('임시·잠금 파일(~$*, *.tmp)과 숨김·시스템 항목은 복사하지 않습니다.')
  await expect(dialog.getByTestId('case-final-report-range')).toContainText('Case 전체 · 모든 Run Case · Run Option')
  await expect(dialog).not.toContainText('PDF')
  const pptx = dialog.getByRole('checkbox', { name: /PPTX/ })
  const html = dialog.getByRole('checkbox', { name: /HTML/ })
  const withVideos = dialog.getByRole('checkbox', { name: '영상 포함' })
  const confirm = dialog.getByRole('button', { name: 'Final 지정 확정', exact: true })
  await expect(pptx).toBeChecked()
  await expect(html).not.toBeChecked()
  // PPTX embeds mp4 videos too: the option follows "at least one format".
  await expect(withVideos).toBeEnabled()
  // At least one format is required.
  await pptx.uncheck()
  await expect(confirm).toBeDisabled()
  await expect(withVideos).toBeDisabled()
  await expect(dialog).toContainText('하나 이상 고르세요')
  await pptx.check()
  await html.check()
  await withVideos.check()
  await page.screenshot({ path: `${SHOT_DIR}/case-final-dialog-1440.png` })
  await confirm.click()
  await expect(page.getByRole('dialog', { name: 'Final 지정 완료' })).toBeVisible({ timeout: 20_000 })
  const done = page.getByRole('dialog', { name: 'Final 지정 완료' })
  await expect(done).toContainText(`R/Final/CAE/Case A/${OPERATION}`)
  await expect(done).toContainText(`${reportsDir}/Case_A_report.pptx`)
  await expect(done).toContainText(`${reportsDir}/Case_A_report.html`)
  // The 21 MB video exceeds the per-video cap and is listed as skipped.
  await expect(done).toContainText('BEHAVIOR_3_Face_large.mp4')
  await page.screenshot({ path: `${SHOT_DIR}/case-final-done-1440.png` })
  expect(uploads.map((item) => item.format)).toEqual(['pptx', 'html'])
  expect(uploads[0].head.startsWith('PK')).toBe(true)
  expect(uploads[0].contentType).toContain('presentationml')
  expect(uploads[1].head.toLowerCase()).toBe('<!doctype html>')
  expect(uploads[1].contentType).toContain('text/html')
  for (const upload of uploads) expect(Object.fromEntries(upload.query)).toMatchObject({ project_id: 'project-tv-001', request_id: 'request-drop-001', environment: 'DISTRIBUTION', case_id: CASE_ID, capture_id: CAPTURE_ID })
  expect(confirms).toHaveLength(1)
  expect(confirms[0]).toMatchObject({ operation_id: OPERATION, capture_id: CAPTURE_ID, report_formats: ['pptx', 'html'] })
  await done.getByRole('button', { name: '닫기', exact: true }).last().click()
  await expect(done).not.toBeVisible()
  await expect(page.locator('.case-finalization')).toContainText('Final 지정 완료')
  expect(errors).toEqual([])
})

test('Final 지정 업로드 오류를 대화상자에 보이고 같은 요청으로 다시 시도한다', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const { uploads, confirms } = await installMocks(page, { failFirstUpload: true })
  const dialog = await openFinal(page)
  await dialog.getByRole('button', { name: 'Final 지정 확정', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('PPTX 보고서를 읽을 수 없습니다.', { timeout: 20_000 })
  expect(confirms).toHaveLength(0)
  await dialog.getByRole('button', { name: '재시도', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Final 지정 완료' })).toBeVisible({ timeout: 20_000 })
  expect(uploads.map((item) => item.format)).toEqual(['pptx', 'pptx'])
  // The retry re-sends the same bytes for the same operation.
  expect(uploads[1].size).toBe(uploads[0].size)
  expect(confirms).toHaveLength(1)
  expect(confirms[0]).toMatchObject({ operation_id: OPERATION, report_formats: ['pptx'] })
  expect(errors).toEqual([])
})

test('Final 복사는 진행률을 보이고 창을 닫아도 계속되며 실패하면 같은 Final ID로 재시도한다', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const { confirms, jobPolls, script } = await installMocks(page, { jobStates: ['RUNNING'] })
  const dialog = await openFinal(page)
  await dialog.getByRole('button', { name: 'Final 지정 확정', exact: true }).click()
  const progress = dialog.getByTestId('case-final-job')
  await expect(progress).toContainText('Final 복사 중 15%', { timeout: 20_000 })
  await expect(progress).toContainText('파일 1/3')
  await expect(progress).toContainText('현재: Drop/Run A/Individual/2_Face/result.csv')
  await expect(progress.getByRole('progressbar', { name: 'Final 복사 진행률' })).toHaveAttribute('aria-valuenow', '15')
  await expect(dialog).toContainText('창을 닫아도 복사는 계속됩니다.')
  await page.screenshot({ path: `${SHOT_DIR}/case-final-progress-1440.png` })
  // Closing the dialog keeps the job; the Case results header keeps showing it.
  await dialog.getByRole('button', { name: '닫기 (복사 계속)', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  const status = page.getByTestId('case-final-status')
  await expect(status).toContainText('Final 복사 중 15%')
  const pollsWhileClosed = jobPolls.length
  await expect.poll(() => jobPolls.length, { timeout: 10_000 }).toBeGreaterThan(pollsWhileClosed)
  // A failure shows its reason and a retry with the same Final ID and reports.
  script.states = ['FAILED']
  await expect(status).toContainText('Final 복사 실패', { timeout: 10_000 })
  await expect(status).toHaveAttribute('title', /다른 프로그램이 임시 폴더를 사용하고 있어/)
  script.states = ['COMPLETE']
  await page.locator('.case-finalization').getByRole('button', { name: '재시도', exact: true }).click()
  await expect(page.locator('.case-finalization')).toContainText('Final 지정 완료', { timeout: 10_000 })
  expect(confirms).toHaveLength(2)
  expect(confirms[1]).toMatchObject({ operation_id: OPERATION, capture_id: CAPTURE_ID, report_formats: ['pptx'] })
  expect(jobPolls.every((caseId) => caseId === CASE_ID)).toBe(true)
  expect(errors).toEqual([])
})

test('현재 Final이 다른 Case면 헤더에 보이고 확인해야 재지정하며 요약 파일 갱신을 요청할 수 있다', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const otherFinal = { operation_id: 'b'.repeat(32), case_id: 'case-b', case_label: 'Case B', case_path: 'R/Working/Case B', designated_by: 'kim', designated_at: '2026-10-05T01:00:00Z', schema_version: 3, output_paths: { CAE: `R/Final/CAE/Case B/${'b'.repeat(32)}`, Reports: `R/Final/Report/Case B/${'b'.repeat(32)}` }, verified: true }
  let summaryState: 'MISSING' | 'OK' = 'MISSING'
  const repairs: Array<Record<string, unknown>> = []
  const statusBody = () => ({
    latest: null, selected_case_latest: null, retryable_operations: [], unverified_records: 0, active_operations: [],
    current_final: otherFinal,
    final_history: [{ operation_id: otherFinal.operation_id, case_id: 'case-b', case_label: 'Case B', designated_by: 'kim', designated_at: otherFinal.designated_at, schema_version: 3, role: 'CURRENT' },
      { operation_id: 'c'.repeat(32), case_id: CASE_ID, case_label: 'Case A', designated_by: 'lee', designated_at: '2026-10-04T01:00:00Z', schema_version: 2, role: 'PREVIOUS' }],
    summary: { state: summaryState, path: 'R/Final/current.json', final_id: summaryState === 'OK' ? otherFinal.operation_id : null },
  })
  const { confirms } = await installMocks(page, { status: statusBody, jobStates: ['COMPLETE'] })
  await page.route('**/api/dashboard/finalizations/summary/repair', (route) => {
    repairs.push(route.request().postDataJSON() as Record<string, unknown>)
    summaryState = 'OK'
    return fulfillJson(route, statusBody())
  })
  const dialog = await openFinal(page)
  const header = page.locator('.case-finalization')
  await expect(page.getByTestId('case-final-current')).toContainText('현재 Final · Case B · kim')
  await expect(page.getByTestId('case-final-summary-repair')).toContainText('요약 파일 갱신 필요')
  // The override is a global-admin action: not offered without can_override_summary.
  await expect(page.getByTestId('case-final-summary-override')).toHaveCount(0)
  const replace = dialog.getByTestId('case-final-replace')
  await expect(replace).toContainText('현재 Final(Case B)을 이전 Final로 바꾸고 이 Case를 Final로 지정합니다')
  await replace.getByText('Final 이력 2건').click()
  await expect(replace.getByRole('list', { name: 'Final 이력' })).toContainText('현재')
  await expect(replace.getByRole('list', { name: 'Final 이력' })).toContainText('이전')
  const confirm = dialog.getByRole('button', { name: 'Final 지정 확정', exact: true })
  await expect(confirm).toBeDisabled()
  await replace.getByRole('checkbox').check()
  await expect(confirm).toBeEnabled()
  await page.screenshot({ path: `${SHOT_DIR}/case-final-replace-1440.png` })
  await confirm.click()
  await expect(page.getByRole('dialog', { name: 'Final 지정 완료' })).toBeVisible({ timeout: 20_000 })
  expect(confirms).toHaveLength(1)
  await page.getByRole('dialog', { name: 'Final 지정 완료' }).getByRole('button', { name: '닫기', exact: true }).last().click()
  await header.getByRole('button', { name: '요약 파일 갱신', exact: true }).click()
  await expect(page.getByTestId('case-final-summary-repair')).toHaveCount(0, { timeout: 10_000 })
  expect(repairs[0]).toMatchObject({ project_id: 'project-tv-001', request_id: 'request-drop-001', environment: 'DISTRIBUTION', case_id: CASE_ID })
  expect(errors).toEqual([])
})

test('요약 파일 충돌은 전역 관리자에게만 강제 갱신을 보이고 확인 후 override로 요청한다', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const current = { operation_id: 'b'.repeat(32), case_id: CASE_ID, case_label: 'Case A', case_path: 'R/Working/Case A', designated_by: 'kim', designated_at: '2026-10-05T01:00:00Z', schema_version: 3, output_paths: null, verified: true, verification: 'SHA256', missing: false }
  let state: 'CONFLICT' | 'OK' = 'CONFLICT'
  const repairs: Array<Record<string, unknown>> = []
  const statusBody = () => ({ latest: null, selected_case_latest: null, retryable_operations: [], unverified_records: 0, active_operations: [], current_final: current, final_history: [], summary: { state, path: 'R/Final/current.json', final_id: null }, can_override_summary: true })
  await installMocks(page, { status: statusBody })
  await page.route('**/api/dashboard/finalizations/summary/repair', (route) => {
    repairs.push(route.request().postDataJSON() as Record<string, unknown>)
    state = 'OK'
    return fulfillJson(route, statusBody())
  })
  await openFinal(page)
  await page.getByRole('dialog', { name: 'Final 지정 확인' }).getByRole('button', { name: '취소', exact: true }).click()
  await expect(page.getByTestId('case-final-summary-conflict')).toContainText('요약 파일 충돌')
  page.once('dialog', (dialog) => void dialog.accept())
  await page.getByTestId('case-final-summary-override').click()
  await expect(page.getByTestId('case-final-summary-conflict')).toHaveCount(0, { timeout: 10_000 })
  expect(repairs).toEqual([expect.objectContaining({ override: true, case_id: CASE_ID })])
})

test('Final 보고서는 고른 레이아웃과 보고서 정보를 쓰고 업로드 템플릿 미적용을 레이아웃 옆에 알린다', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const base = { coverVariant: 'balanced', sectionOrder: ['series', 'scalar', 'media'], variablePlacements: [], includeMedia: true, description: '' }
  const standard = { id: 'report-layout-standard', name: '표준', version: 1, ...base, accentColor: '1F6FB2' }
  const company = { id: 'company-template', name: '회사 템플릿', version: 2, ...base, accentColor: 'C0392B', slideMaster: { backgroundColor: 'F8FBFD', design: 'frame', accentColor: 'C0392B' }, templateSource: 'pptx_upload', templateAssetId: 'template-asset-1', templateBindings: {} }
  const layouts = [standard, company].map((definition) => ({ id: definition.id, name: definition.name, description: '', version: definition.version, definition, is_system: definition.id === 'report-layout-standard', is_active: true, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', updated_by: 'admin' }))
  const { uploads } = await installMocks(page, { layouts, jobStates: ['COMPLETE'] })
  const dialog = await openFinal(page)
  const layoutBox = dialog.getByTestId('case-final-layout')
  const select = layoutBox.getByRole('combobox', { name: 'PPTX 레이아웃' })
  await expect(select).toHaveValue('report-layout-standard')
  await expect(layoutBox.getByTestId('case-report-template-note')).toHaveCount(0)
  await select.selectOption('company-template')
  await expect(layoutBox.getByTestId('case-report-template-note')).toContainText('업로드 PPTX 템플릿은 Case 보고서에 적용되지 않습니다')
  const meta = dialog.getByTestId('case-report-meta')
  await expect(meta.getByRole('textbox', { name: '작성자' })).not.toHaveValue('')
  await meta.getByRole('textbox', { name: '작성자' }).fill('김해석')
  await meta.getByRole('textbox', { name: '개발단계' }).fill('DV 3차')
  await meta.getByRole('textbox', { name: '검토조건' }).fill('낙하 1.2 m 조건')
  await meta.getByRole('textbox', { name: '결론' }).fill('기준 만족 W7')
  await dialog.getByRole('checkbox', { name: /HTML/ }).check()
  await page.screenshot({ path: `${SHOT_DIR}/case-final-layout-meta-1440.png` })
  await dialog.getByRole('button', { name: 'Final 지정 확정', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Final 지정 완료' })).toBeVisible({ timeout: 20_000 })
  const pptx = uploads.find((item) => item.format === 'pptx')!
  const html = uploads.find((item) => item.format === 'html')!
  const slides = slideXml(pptx.body)
  // The chosen layout (its accent colour), rendered natively (no template), with the report information.
  expect(slides).toContain('C0392B')
  expect(slides).toContain('김해석')
  expect(slides).toContain('DV 3차')
  expect(html.text).toContain('<dt>개발단계</dt><dd>DV 3차</dd>')
  expect(html.text).toContain('<dt>검토조건</dt><dd>낙하 1.2 m 조건</dd>')
  expect(html.text).toContain('<dt>결론</dt><dd>기준 만족 W7</dd>')
  // The choice is remembered for the next Final/보고서 dialog of this request.
  const remembered = await page.evaluate(() => Object.entries(window.localStorage).filter(([key]) => key.startsWith('vdsim.caseReport.lastLayout.v1:')).map(([, value]) => value))
  expect(remembered).toEqual(['company-template'])
  expect(errors).toEqual([])
})

test('유통환경 Final 보고서는 화면 선택과 무관하게 Case의 모든 Run Option을 담고 결과 없는 항목은 결과 없음으로 표시한다', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const { uploads, confirms } = await installMocks(page, { multiOption: true })
  await loginWorkspace(page)
  await page.goto('/workspace/requests?project=project-tv-001&request=request-drop-001&view=case_results&result_environment=DISTRIBUTION')
  await expect(page.getByRole('region', { name: 'SPDM 해석 결과 대시보드' })).toBeVisible({ timeout: 15_000 })
  // Several Run Options and none chosen: the on-screen 보고서 stays off, Final does not.
  await expect(page.getByRole('group', { name: 'Case 경로' }).getByRole('combobox', { name: 'Run Option', exact: true })).toHaveValue('')
  await expect(page.getByRole('button', { name: '보고서', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Final 지정', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Final 지정 확인' })
  await expect(dialog.getByTestId('case-final-report-range')).toContainText('Case 전체')
  await dialog.getByRole('checkbox', { name: /HTML/ }).check()
  const confirm = dialog.getByRole('button', { name: 'Final 지정 확정', exact: true })
  await expect(confirm).toBeEnabled()
  await confirm.click()
  await expect(page.getByRole('dialog', { name: 'Final 지정 완료' })).toBeVisible({ timeout: 30_000 })
  expect(uploads.map((item) => item.format)).toEqual(['pptx', 'html'])
  const html = uploads[1].text
  expect(html).toContain('Case 전체 · Run Option 3개 (결과 있음 2개)')
  expect(html).toContain('<h2 id="s1-title">Drop › Run A › Individual</h2>')
  expect(html).toContain('<h2 id="s2-title">Drop › Run A › Assembly</h2>')
  expect(html).toContain('<h2 id="s3-title">Drop › Run A › Cumulative</h2>')
  expect(html).toContain('최대 11 MPa · 3_Face')
  expect(html).toContain('최대 31 MPa · 3_Face')
  expect(html).toContain('<p class="no-result">결과 없음</p>')
  expect(uploads[0].head.startsWith('PK')).toBe(true)
  expect(confirms[0]).toMatchObject({ environment: 'DISTRIBUTION', report_formats: ['pptx', 'html'] })
  expect(errors).toEqual([])
})

test.describe('사용환경 Final 지정', () => {
  test.use({ viewport: { width: 2560, height: 1440 }, deviceScaleFactor: 1.5 })
  test('사용환경 Case도 보고서를 만들어 올린 뒤 Final 지정을 확정한다', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    const { uploads, confirms, usageCaptures } = await installMocks(page)
    await loginWorkspace(page)
    await page.goto('/workspace/requests?project=project-tv-001&request=request-drop-001&view=case_results')
    await expect(page.getByTestId('usage-dashboard')).toBeVisible({ timeout: 15_000 })
    await expect(page.getByRole('button', { name: '보고서', exact: true })).toBeEnabled()
    await page.getByRole('button', { name: 'Final 지정', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: 'Final 지정 확인' })
    await expect(dialog).toBeVisible()
    await expect(dialog.getByTestId('case-final-report-range')).toContainText('사용환경 Case 전체 · 다섯 평가 종합')
    await expect(dialog).toContainText('Usage_Case_report.pptx')
    await dialog.getByRole('checkbox', { name: /HTML/ }).check()
    await dialog.getByRole('checkbox', { name: '영상 포함' }).check()
    const confirm = dialog.getByRole('button', { name: 'Final 지정 확정', exact: true })
    await expect(confirm).toBeEnabled()
    await page.screenshot({ path: `${SHOT_DIR}/followup-usage-final-dialog.png` })
    await confirm.click()
    const done = page.getByRole('dialog', { name: 'Final 지정 완료' })
    await expect(done).toBeVisible({ timeout: 20_000 })
    await expect(done).toContainText(`${usageReportsDir}/Usage_Case_report.html`)
    await page.screenshot({ path: `${SHOT_DIR}/followup-usage-final-done.png` })
    expect(uploads.map((item) => item.format)).toEqual(['pptx', 'html'])
    for (const upload of uploads) expect(Object.fromEntries(upload.query)).toMatchObject({ environment: 'USAGE', case_id: 'usage-case', capture_id: 'latest:usage-case' })
    expect(uploads[0].head.startsWith('PK')).toBe(true)
    const html = uploads[1].text
    for (const text of ['다섯 평가 종합', '2.5 deg · 확인', '6.25 mm · 확인', '11 deg · 확인', 'NG · 확인', 'Slope_Angle_360', 'Settle.png', 'Wobble.mp4']) expect(html).toContain(text)
    expect(html).toMatch(/<video controls preload="metadata" src="data:video\/mp4;base64,/)
    expect(confirms[0]).toMatchObject({ environment: 'USAGE', operation_id: OPERATION, report_formats: ['pptx', 'html'] })
    // Screen, Final report and Final basis all read the same latest rule (newest capture for usage).
    expect(usageCaptures.length).toBeGreaterThan(0)
    expect(new Set(usageCaptures)).toEqual(new Set(['latest:usage-case']))
    expect(errors).toEqual([])
  })
})
