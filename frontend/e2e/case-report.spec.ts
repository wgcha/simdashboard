import { mkdirSync, readFileSync } from 'node:fs'
import { inflateRawSync } from 'node:zlib'
import { expect, test, type Download, type Page, type Route } from '@playwright/test'
import { loginWorkspace } from './workspace-test-helpers'

// Case results "보고서": PPTX and/or HTML built in the browser from the selection
// fixed when the dialog opened (mocked dashboard API, synthetic data only).
const CASE_ID = 'case-a'
const CAPTURE_ID = 'latest:case-a'
const RUN_ID = 'run-a'
const MODE = 'INDIVIDUAL'
const SCRIPT_SCENE = '<script>alert(1)</script>_Scene03'
const PEAK_SCENE = '2_Face_Drop_Scene02'
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAEElEQVR4nGPQqDgBRww4OQBBxhDhzXmo9QAAAABJRU5ErkJggg==', 'base64')
const SHOT_DIR = '/tmp/claude-0'

type Options = { twoOptions?: boolean; bigVideo?: boolean }

function catalog(options: Options) {
  const runOptions = [{ id: 'option-individual', label: 'Individual', option_label: 'Individual', option_status: 'PRESENT', case_id: CASE_ID, execution_run_id: RUN_ID, mode: MODE, capture_id: CAPTURE_ID }]
  if (options.twoOptions) runOptions.push({ id: 'option-assembly', label: 'Assembly', option_label: 'Assembly', option_status: 'PRESENT', case_id: CASE_ID, execution_run_id: RUN_ID, mode: 'ASSEMBLY', capture_id: CAPTURE_ID })
  return {
    environment: 'DISTRIBUTION',
    cases: [{ id: CASE_ID, label: 'Case A' }],
    load_cases: [{ id: 'load-drop', label: 'Drop', case_id: CASE_ID, capture_id: CAPTURE_ID }],
    execution_runs: [{ id: RUN_ID, label: 'Run A', case_id: CASE_ID, load_case_id: 'load-drop', capture_id: CAPTURE_ID }],
    run_options: runOptions,
    modes: [{ id: MODE, label: MODE, case_id: CASE_ID, execution_run_id: RUN_ID, capture_id: CAPTURE_ID }],
    captures: [{ id: CAPTURE_ID, label: '최신 결과', case_id: CASE_ID, kind: 'LATEST', merged_capture_count: 2 }],
    components: [
      { id: 'C23', label: 'C23', case_id: CASE_ID, execution_run_id: RUN_ID, mode: MODE, capture_id: CAPTURE_ID },
      { id: 'C23', label: 'C23', case_id: CASE_ID, execution_run_id: RUN_ID, mode: 'ASSEMBLY', capture_id: CAPTURE_ID },
    ],
    bases: [{ id: 'DETAIL', label: '상세 추출값' }],
  }
}

function distribution(url: URL) {
  const optionId = url.searchParams.get('run_option_id') ?? 'option-individual'
  const member = { id: 'member-a', label: 'Case A', simulation_case_id: CASE_ID, load_case_id: 'load-drop', execution_run_id: RUN_ID, run_option_id: optionId, mode: url.searchParams.get('mode') ?? MODE, capture_id: CAPTURE_ID, component_id: 'C23', basis: 'DETAIL' }
  const labels = ['1_Face_Drop_Scene01', PEAK_SCENE, SCRIPT_SCENE]
  const scenes = labels.map((label, index) => ({ id: `scene-${index + 1}`, label, scene_sequence_number: index + 1, scenario_number: index + 1, order_status: 'CONFIRMED', contact_code: 'Face1', repetition: '1st' }))
  const envelope = [10.5, 42.25, 7]
  const edges = ['TOP', 'BOTTOM', 'LEFT', 'RIGHT'] as const
  return {
    contract_version: 1,
    context: { project_id: 'project-tv-001', request_id: 'request-drop-001', simulation_case_id: CASE_ID, load_case_id: 'load-drop', execution_run_id: RUN_ID, run_option_id: optionId, mode: member.mode, capture_id: CAPTURE_ID, component_id: 'C23', basis: 'DETAIL', context_key: `report-${optionId}` },
    status: 'READY',
    members: [member],
    scenes,
    edge_peaks: scenes.flatMap((scene, index) => edges.map((edge, edgeIndex) => ({ value: envelope[index] - edgeIndex, unit: 'MPa', unit_status: 'CONFIRMED', completeness: 'FULL', status: 'READY', basis: 'DETAIL', scope: 'SELECTED_EDGE_LINES', edge, scene_id: scene.id, member_id: member.id }))),
    series: scenes.map((scene, index) => ({ id: `series-${scene.id}`, value: envelope[index], unit: 'MPa', status: 'READY', completeness: 'FULL', basis: 'DETAIL', scene_id: scene.id, scene_sequence_number: index + 1, member_id: member.id, edge: 'TOP', selected_edge_envelope: envelope[index] })),
    contours: scenes.map((scene, index) => ({ cell_id: `cell-${scene.id}`, scene_id: scene.id, member_id: member.id, status: index === 0 ? 'READY' : 'MISSING', reason: null, asset: index === 0 ? { asset_id: 'img-1', kind: 'IMAGE', status: 'READY', title: 'Contour_Scene01.png', frame_role: 'FINAL_FRAME' } : null })),
    behaviors: [],
    quality_issues: [],
  }
}

function videoPage(url: URL, options: Options) {
  const videos = [
    { video_id: 'v-1', asset_id: 'vid-small', scene_id: 'scene-1', scene_label: '1_Face_Drop_Scene01', title: 'BEHAVIOR_scene01.mp4', status: 'READY', source_capture_id: 'capture-1' },
    ...(options.bigVideo ? [{ video_id: 'v-2', asset_id: 'vid-big', scene_id: 'scene-2', scene_label: PEAK_SCENE, title: 'BEHAVIOR_scene02_large.mp4', status: 'READY', source_capture_id: 'capture-1' }] : []),
  ]
  return {
    contract_version: 1,
    context: { ...distribution(url).context, option_label: 'Individual', load_case_name: 'Drop', run_label: 'Run A' },
    pagination: { page: 1, page_size: 100, total_items: videos.length, total_pages: 1, has_previous: false, has_next: false },
    videos,
  }
}

async function fulfillJson(route: Route, body: unknown) {
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
}

async function installMocks(page: Page, options: Options = {}) {
  const distributionRequests: URL[] = []
  await page.route('**/api/dashboard/finalizations/status**', (route) => route.fulfill({ json: { latest: null, selected_case_latest: null, retryable_operations: [], unverified_records: 0 } }))
  await page.route('**/api/folder-discovery/environments/sync', (route) => route.fulfill({ json: { status: 'UNCHANGED', changed: false, snapshot_id: null, diff: { added: 0, removed: 0, changed: 0 }, code: null, message: null, check_mode: 'QUICK', checked_at: new Date().toISOString(), coalesced: false } }))
  await page.route('**/api/dashboard/catalog**', (route) => fulfillJson(route, catalog(options)))
  await page.route('**/api/dashboard/distribution/scenes/**', (route) => fulfillJson(route, { context: distribution(new URL(route.request().url())).context, scene: { id: 'scene-1', label: 'scene-1' }, edge_peaks: [], line_points: [], assets: [], quality_issues: [] }))
  await page.route('**/api/dashboard/assets/**', (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop() ?? '')
    if (id === 'img-1') return route.fulfill({ status: 200, contentType: 'image/png', body: PNG })
    if (id === 'vid-small') return route.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.from('synthetic-mp4-bytes') })
    if (id === 'vid-big') return route.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.alloc(21 * 1024 * 1024, 1) })
    return route.fulfill({ status: 404, body: '' })
  })
  await page.route('**/api/dashboard/distribution/runs/**', (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/videos')) return fulfillJson(route, videoPage(url, options))
    distributionRequests.push(url)
    return fulfillJson(route, distribution(url))
  })
  return distributionRequests
}

async function openCaseResults(page: Page, optionId = 'option-individual') {
  await loginWorkspace(page)
  await page.goto('/workspace/requests?project=project-tv-001&request=request-drop-001&view=case_results')
  await expect(page.getByRole('region', { name: 'SPDM 해석 결과 대시보드' })).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: '유통환경', exact: true }).click()
  const path = page.getByRole('group', { name: 'Case 경로' })
  await expect(path).toContainText('Case A')
  // A single candidate collapses to fixed text; several stay a select once the catalog settles.
  const option = path.getByRole('combobox', { name: 'Run Option', exact: true })
  await expect(path.locator('.shared-hierarchy-choice--fixed[data-hierarchy-level="Run Option"]').or(option.and(page.locator('select:enabled')))).toBeVisible()
  if (await option.count()) {
    // Automatic path repairs can briefly clear an early choice; retry until it sticks.
    await expect(async () => { await option.selectOption(optionId, { timeout: 2_000 }); await expect(option).toHaveValue(optionId, { timeout: 1_000 }) }).toPass({ timeout: 10_000 })
  }
  await expect(page.getByTestId('distribution-dashboard')).toBeVisible()
}

async function openReport(page: Page) {
  await page.getByRole('button', { name: '보고서', exact: true }).click()
  const dialog = page.getByTestId('case-report-dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByTestId('case-report-counts')).toContainText('Scene 3개')
  return dialog
}

async function setFormats(dialog: ReturnType<Page['getByTestId']>, formats: { pptx: boolean; html: boolean }) {
  await dialog.getByRole('checkbox', { name: 'PPTX', exact: true }).setChecked(formats.pptx)
  await dialog.getByRole('checkbox', { name: 'HTML', exact: true }).setChecked(formats.html)
}

async function downloadAll(page: Page, dialog: ReturnType<Page['getByTestId']>, count: number) {
  const downloads: Download[] = []
  const listener = (download: Download) => { downloads.push(download) }
  page.on('download', listener)
  await dialog.getByRole('button', { name: '다운로드', exact: true }).click()
  await expect.poll(() => downloads.length, { timeout: 20_000 }).toBe(count)
  await expect(dialog.getByRole('status').filter({ hasText: '다운로드' })).toBeVisible()
  await page.waitForTimeout(500)
  page.off('download', listener)
  expect(downloads).toHaveLength(count)
  return downloads
}

async function downloadText(download: Download) {
  return readFileSync(await download.path() as string, 'utf8')
}

/** Minimal ZIP reader (central directory + raw deflate) for checking PPTX parts. */
function zipEntry(buffer: Buffer, name: string) {
  let end = buffer.length - 22
  while (end >= 0 && buffer.readUInt32LE(end) !== 0x06054b50) end -= 1
  expect(end).toBeGreaterThanOrEqual(0)
  const count = buffer.readUInt16LE(end + 10)
  let offset = buffer.readUInt32LE(end + 16)
  for (let index = 0; index < count; index += 1) {
    const method = buffer.readUInt16LE(offset + 10)
    const compressedSize = buffer.readUInt32LE(offset + 20)
    const nameLength = buffer.readUInt16LE(offset + 28)
    const extraLength = buffer.readUInt16LE(offset + 30)
    const commentLength = buffer.readUInt16LE(offset + 32)
    const localOffset = buffer.readUInt32LE(offset + 42)
    const entryName = buffer.toString('utf8', offset + 46, offset + 46 + nameLength)
    if (entryName === name) {
      const dataStart = localOffset + 30 + buffer.readUInt16LE(localOffset + 26) + buffer.readUInt16LE(localOffset + 28)
      const data = buffer.subarray(dataStart, dataStart + compressedSize)
      return (method === 8 ? inflateRawSync(data) : data).toString('utf8')
    }
    offset += 46 + nameLength + extraLength + commentLength
  }
  return null
}

test('보고서 버튼은 Case 범위로 열리고 형식이 없으면 다운로드를 막는다', async ({ page }) => {
  await installMocks(page)
  await openCaseResults(page)
  const head = page.locator('.case-results-head__actions')
  await expect(head.getByRole('button', { name: '보고서', exact: true })).toBeEnabled()
  await expect(head.getByRole('button', { name: 'Final 지정', exact: true })).toBeVisible()
  const dialog = await openReport(page)
  await expect(dialog).toHaveAttribute('data-case-id', CASE_ID)
  await expect(dialog).toHaveAttribute('data-capture-id', CAPTURE_ID)
  await expect(dialog.locator('.case-report__scope')).toContainText('Case A')
  await expect(dialog.locator('.case-report__scope')).toContainText('Run A')
  await expect(dialog.locator('.case-report__scope')).toContainText('Individual')
  await expect(dialog.locator('.case-report__scope')).toContainText('C23 · 상세 추출값')
  await expect(dialog).not.toContainText('PDF')
  const videos = dialog.getByRole('checkbox', { name: '영상 포함', exact: true })
  await expect(videos).toBeDisabled()
  await setFormats(dialog, { pptx: false, html: false })
  await expect(dialog.getByRole('button', { name: '다운로드', exact: true })).toBeDisabled()
  await expect(dialog).toContainText('형식을 하나 이상 선택하세요.')
  await setFormats(dialog, { pptx: false, html: true })
  await expect(videos).toBeEnabled()
  await expect(videos).not.toBeChecked()
  await expect(dialog.getByRole('button', { name: '다운로드', exact: true })).toBeEnabled()
})

test('PPTX만 고르면 파일 하나를 받고 첫 슬라이드에 Case와 Scene이 있다', async ({ page }) => {
  await installMocks(page)
  await openCaseResults(page)
  const dialog = await openReport(page)
  await setFormats(dialog, { pptx: true, html: false })
  const [download] = await downloadAll(page, dialog, 1)
  expect(download.suggestedFilename()).toMatch(/^Case_A_\d{8}-\d{4}\.pptx$/)
  const zip = readFileSync(await download.path() as string)
  expect(zip.subarray(0, 2).toString('latin1')).toBe('PK')
  const slide1 = zipEntry(zip, 'ppt/slides/slide1.xml')
  expect(slide1).not.toBeNull()
  expect(slide1).toContain('Case A')
  expect(slide1).toContain(PEAK_SCENE)
  expect(zipEntry(zip, 'ppt/slides/slide2.xml')).toContain('42.25 MPa')
})

test('HTML만 고르면 자체 포함 HTML 하나를 받고 영상은 기본으로 넣지 않으며 Scene 이름을 이스케이프한다', async ({ page, browser }) => {
  await installMocks(page)
  await openCaseResults(page)
  const dialog = await openReport(page)
  await setFormats(dialog, { pptx: false, html: true })
  const [download] = await downloadAll(page, dialog, 1)
  expect(download.suggestedFilename()).toMatch(/^Case_A_\d{8}-\d{4}\.html$/)
  const html = await downloadText(download)
  expect(html).toContain('Case A')
  expect(html).toContain(PEAK_SCENE)
  expect(html).toContain('42.25 MPa')
  expect(html).toMatch(/<img src="data:image\/png;base64,/)
  expect(html).not.toContain('<video')
  expect(html).toContain('BEHAVIOR_scene01.mp4')
  expect(html).not.toContain(SCRIPT_SCENE)
  expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;_Scene03')
  expect(html).not.toMatch(/<script/i)
  expect(html).not.toMatch(/\b(?:src|href)="(?!data:)/)
  expect(html).not.toMatch(/https?:\/\/|url\(/)
  expect(html).not.toContain('PDF')

  // Render the downloaded file on its own (no network, no scripts needed).
  const context = await browser.newContext({ viewport: { width: 2560, height: 1440 }, deviceScaleFactor: 1.5, javaScriptEnabled: false, offline: true })
  const view = await context.newPage()
  const external: string[] = []
  view.on('request', (request) => { if (!request.url().startsWith('data:') && !request.url().startsWith('blob:') && request.url() !== 'about:blank') external.push(request.url()) })
  await view.setContent(html, { waitUntil: 'load' })
  await expect(view.getByRole('heading', { name: 'Case A 해석 결과 보고서' })).toBeVisible()
  await expect(view.locator('img')).toHaveJSProperty('naturalWidth', 4)
  expect(external).toEqual([])
  mkdirSync(SHOT_DIR, { recursive: true })
  await view.screenshot({ path: `${SHOT_DIR}/stage5-html-report.png`, fullPage: true })
  await context.close()
})

test('영상 포함은 data URI 영상을 넣고 상한을 넘는 영상은 건너뛴 목록에 보여준다', async ({ page }) => {
  await installMocks(page, { bigVideo: true })
  await openCaseResults(page)
  const dialog = await openReport(page)
  await setFormats(dialog, { pptx: false, html: true })
  await dialog.getByRole('checkbox', { name: '영상 포함', exact: true }).check()
  const [download] = await downloadAll(page, dialog, 1)
  const html = await downloadText(download)
  expect(html).toMatch(/<video controls preload="metadata" src="data:video\/mp4;base64,/)
  expect((html.match(/<video /g) ?? []).length).toBe(1)
  expect(html).toContain('BEHAVIOR_scene02_large.mp4')
  const skipped = dialog.getByTestId('case-report-skipped')
  await expect(skipped).toContainText('1개')
  await expect(skipped).toContainText('BEHAVIOR_scene02_large.mp4')
  await expect(skipped).not.toContainText('BEHAVIOR_scene01.mp4')
})

test('두 형식을 모두 고르면 PPTX와 HTML 두 파일을 받는다', async ({ page }) => {
  await installMocks(page)
  await openCaseResults(page)
  const dialog = await openReport(page)
  await setFormats(dialog, { pptx: true, html: true })
  const downloads = await downloadAll(page, dialog, 2)
  const names = downloads.map((item) => item.suggestedFilename()).sort()
  expect(names[0]).toMatch(/^Case_A_\d{8}-\d{4}\.html$/)
  expect(names[1]).toMatch(/^Case_A_\d{8}-\d{4}\.pptx$/)
  expect(names[0].replace(/\.html$/, '')).toBe(names[1].replace(/\.pptx$/, ''))
})

test('보고서 창을 연 뒤 선택을 바꿔도 보고서 범위는 그대로다', async ({ page }) => {
  await installMocks(page, { twoOptions: true })
  await openCaseResults(page, 'option-individual')
  const dialog = await openReport(page)
  await expect(dialog.locator('.case-report__scope')).toContainText('Individual')
  // Change the page selection while the dialog is open (browser history navigation).
  await page.evaluate(() => {
    const url = new URL(window.location.href)
    url.searchParams.set('case_option', 'option-assembly')
    window.history.pushState(window.history.state, '', url)
    window.dispatchEvent(new PopStateEvent('popstate', { state: window.history.state }))
  })
  await expect(page).toHaveURL(/case_option=option-assembly/)
  await expect(page.getByRole('group', { name: 'Case 경로' }).getByRole('combobox', { name: 'Run Option', exact: true })).toHaveValue('option-assembly')
  await expect(dialog.locator('.case-report__scope')).toContainText('Individual')
  await expect(dialog.locator('.case-report__scope')).not.toContainText('Assembly')
  await setFormats(dialog, { pptx: false, html: true })
  const [download] = await downloadAll(page, dialog, 1)
  const html = await downloadText(download)
  expect(html).toContain('<dd>Individual</dd>')
  expect(html).not.toContain('Assembly')
})

test.describe('4K 기준 화면', () => {
  test.use({ viewport: { width: 2560, height: 1440 }, deviceScaleFactor: 1.5 })
  test('헤더의 보고서 버튼과 열린 보고서 창', async ({ page }) => {
    await installMocks(page)
    await openCaseResults(page)
    mkdirSync(SHOT_DIR, { recursive: true })
    const head = page.locator('.case-results-head')
    await expect(head.getByRole('button', { name: '보고서', exact: true })).toBeVisible()
    await head.screenshot({ path: `${SHOT_DIR}/stage5-header.png` })
    const dialog = await openReport(page)
    await dialog.getByRole('checkbox', { name: 'HTML', exact: true }).check()
    await page.screenshot({ path: `${SHOT_DIR}/stage5-dialog.png` })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  })
})
