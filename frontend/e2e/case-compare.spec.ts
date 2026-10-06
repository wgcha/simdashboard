import { mkdirSync, readFileSync } from 'node:fs'
import { inflateRawSync } from 'node:zlib'
import { expect, test, type Download, type Locator, type Page, type Route } from '@playwright/test'
import { loginWorkspace, mockResultEnvironments, setWorkspaceFontSize } from './workspace-test-helpers'

// W5 Case 비교: 2–4 Cases of one request side by side from each Case's merged
// latest result. Mocked dashboard API, synthetic data only.
test.beforeEach(async ({ page }) => { await mockResultEnvironments(page) })

const SHOT_DIR = '/tmp/claude-0'
const OPERATION = 'b'.repeat(32)
const CASES = ['a', 'b', 'c'] as const
type CaseKey = (typeof CASES)[number]
const label = (key: CaseKey) => `Case ${key.toUpperCase()}`
// Selected-edge envelope per Scene; Case B has no Scene 3.
const VALUES: Record<CaseKey, Array<[string, number]>> = {
  a: [['1_Face_Drop_Scene01', 10], ['2_Face_Drop_Scene02', 20], ['3_Corner_Drop_Scene03', 30]],
  b: [['1_Face_Drop_Scene01', 12], ['2_Face_Drop_Scene02', 15]],
  c: [['1_Face_Drop_Scene01', 8], ['2_Face_Drop_Scene02', 25], ['3_Corner_Drop_Scene03', 31]],
}

type Options = { unitMismatch?: boolean }

function distributionCatalog() {
  const scope = (key: CaseKey) => ({ case_id: `case-${key}`, capture_id: `latest:case-${key}` })
  return {
    environment: 'DISTRIBUTION',
    cases: CASES.map((key) => ({ id: `case-${key}`, label: label(key), dashboard_case_id: `case-${key}` })),
    load_cases: CASES.map((key) => ({ id: `load-${key}`, label: 'Drop', ...scope(key) })),
    execution_runs: CASES.map((key) => ({ id: `run-${key}`, label: 'Run 1', load_case_id: `load-${key}`, ...scope(key) })),
    run_options: CASES.map((key) => ({ id: `option-${key}`, label: 'Individual', option_label: 'Individual', option_status: 'PRESENT', execution_run_id: `run-${key}`, mode: 'INDIVIDUAL', ...scope(key) })),
    modes: CASES.map((key) => ({ id: 'INDIVIDUAL', label: 'INDIVIDUAL', execution_run_id: `run-${key}`, ...scope(key) })),
    captures: CASES.map((key) => ({ id: `latest:case-${key}`, label: '최신 결과', dashboard_case_id: `case-${key}`, kind: 'LATEST', merged_capture_count: 1, case_id: `case-${key}` })),
    components: CASES.map((key) => ({ id: 'C23', label: 'C23', execution_run_id: `run-${key}`, mode: 'INDIVIDUAL', has_values: true, ...scope(key) })),
    scenes: [],
    bases: [{ id: 'DETAIL', label: '상세 추출값' }],
  }
}

function distribution(url: URL, options: Options) {
  const key = url.pathname.split('/').pop()!.replace('run-', '') as CaseKey
  const unit = options.unitMismatch && key === 'c' ? 'GPa' : 'MPa'
  const member = { id: `member-${key}`, label: label(key), simulation_case_id: `case-${key}`, load_case_id: `load-${key}`, execution_run_id: `run-${key}`, run_option_id: `option-${key}`, mode: 'INDIVIDUAL', capture_id: `latest:case-${key}`, component_id: 'C23', basis: 'DETAIL' }
  const scenes = VALUES[key].map(([name], index) => ({ id: `scene-${key}-${index + 1}`, label: name, scene_sequence_number: index + 1, scenario_number: index + 1, order_status: 'CONFIRMED', contact_code: 'Face1', repetition: '1st' }))
  return {
    contract_version: 1,
    context: { project_id: 'project-tv-001', request_id: 'request-drop-001', simulation_case_id: `case-${key}`, execution_run_id: `run-${key}`, capture_id: url.searchParams.get('capture_id'), context_key: `compare-${key}` },
    status: 'READY', members: [member], scenes,
    edge_peaks: scenes.map((scene, index) => ({ value: VALUES[key][index][1], unit, status: 'READY', completeness: 'FULL', basis: 'DETAIL', edge: 'TOP', scene_id: scene.id, member_id: member.id, is_selected_maximum: true })),
    series: scenes.map((scene, index) => ({ id: `series-${scene.id}`, value: VALUES[key][index][1], unit, status: 'READY', completeness: 'FULL', basis: 'DETAIL', scene_id: scene.id, scene_sequence_number: index + 1, member_id: member.id, edge: 'ENVELOPE', selected_edge_envelope: VALUES[key][index][1] })),
    contours: [], behaviors: [], location_peaks: [], quality_issues: [],
  }
}

const USAGE_KEYS = ['u1', 'u2', 'u3', 'u4', 'u5'] as const
function usageCatalog() {
  // Stored captures carry their time; u1 has the oldest result, so the default leaves it out.
  const times: Record<string, string> = { u1: '2026-09-01 09:00:00', u2: '2026-10-01 09:00:00', u3: '2026-10-02 09:00:00', u4: '2026-10-03 09:00:00', u5: '2026-10-04 09:00:00' }
  return {
    environment: 'USAGE',
    cases: USAGE_KEYS.map((key) => ({ id: `case-${key}`, label: `Usage ${key.toUpperCase()}`, dashboard_case_id: `case-${key}` })),
    load_cases: [], execution_runs: [], modes: [], components: [], bases: [], scenes: [],
    captures: USAGE_KEYS.flatMap((key) => [
      { id: `latest:case-${key}`, label: '최신 결과', case_id: `case-${key}`, dashboard_case_id: `case-${key}`, kind: 'LATEST', merged_capture_count: 1 },
      { id: `capture-${key}`, label: times[key], case_id: `case-${key}`, dashboard_case_id: `case-${key}` },
    ]),
  }
}

const usageValue = (value: number | null, unit: string, verdict: string | null = null) => ({ value, unit, status: 'READY', verdict, value_status: value == null ? 'NOT_APPLICABLE' : 'READY', verdict_status: verdict == null ? 'NOT_APPLICABLE' : 'READY' })
function usagePayload(caseId: string) {
  const n = Number(caseId.slice(-1))
  return {
    contract_version: 1, context: { project_id: 'project-tv-001', request_id: 'request-drop-001', simulation_case_id: caseId, capture_id: `latest:${caseId}`, context_key: `usage-${caseId}` }, status: 'READY', quality_issues: [],
    evaluations: [
      { id: 'Settle', name: 'Settle', status: 'READY', common: usageValue(1 + n / 10, 'deg'), front: null, rear: null, media: [] },
      { id: 'Wobble', name: 'Wobble', status: 'READY', common: null, front: usageValue(3 + n, 'mm'), rear: usageValue(4 + n, 'mm'), media: [] },
      { id: 'Horizontal_Force_Angle', name: 'Horizontal Force Angle', status: 'READY', common: null, front: usageValue(0.5, 'deg'), rear: usageValue(0.25, 'deg'), media: [] },
      { id: 'Slope_Angle', name: 'Slope Angle', status: 'READY', common: null, front: usageValue(10 + n, 'deg', 'OK'), rear: usageValue(8, 'deg', n % 2 ? 'NG' : 'OK'), media: [] },
      { id: 'Slope_Angle_360', name: 'Slope Angle 360', status: 'READY', common: null, front: usageValue(null, '', 'OK'), rear: usageValue(null, '', n === 3 ? 'NG' : 'OK'), media: [] },
    ],
  }
}

function preview(caseId: string, caseLabel: string, environment: 'DISTRIBUTION' | 'USAGE') {
  const dir = `R/Final/Report/${caseLabel}/${OPERATION}`
  return {
    schema_version: 2, operation_id: OPERATION, status: 'PREVIEW', project_id: 'project-tv-001', request_id: 'request-drop-001', environment,
    case_id: caseId, case_label: caseLabel, case_path: `R/Working/${caseLabel}`, capture_id: `latest:${caseId}`, basis: 'LATEST',
    scene_sources: [{ scene_path: `R/Working/${caseLabel}/Drop/Run 1/Individual/1_Face_Drop_Scene01`, source_capture_id: 'capture-1' }],
    capture_fingerprint: 'f', folder_schema_snapshot_id: 'schema', scene_paths: [],
    files: [{ source_relative_path: `R/Working/${caseLabel}/Drop/Run 1/Individual/1_Face_Drop_Scene01/result.csv`, case_relative_path: 'Drop/Run 1/Individual/1_Face_Drop_Scene01/result.csv', category: 'CAE', source_basis: 'SOURCE_CAPTURE', source_capture_id: 'capture-1', size: 60, sha256: '1'.repeat(64) }],
    excluded_scenes: [], counts: { CAE: 1, input_decks: 0, rad_decks: 0, inc_decks: 0, results: 1, scene_reports: 0 }, missing: { input_decks: false, rad_decks: false, inc_decks: false },
    excluded_capture_file_count: 0, previewed_at: '2026-10-06T00:00:00Z', plan_sha256: 'c'.repeat(64), can_confirm: true,
    output_paths: { CAE: `R/Final/CAE/${caseLabel}/${OPERATION}`, Reports: dir },
    report_files: { pptx: `${caseLabel}_report.pptx`, html: `${caseLabel}_report.html` },
    report_paths: { pptx: `${dir}/${caseLabel}_report.pptx`, html: `${dir}/${caseLabel}_report.html` },
    report_limits: { pptx: 67108864, html: 335544320 },
  }
}

async function fulfillJson(route: Route, body: unknown) {
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
}

async function installMocks(page: Page, options: Options = {}) {
  const distributionRequests: URL[] = []
  const usageRequests: URL[] = []
  const previews: Array<Record<string, string>> = []
  await page.route('**/api/folder-discovery/environments/sync', (route) => fulfillJson(route, { status: 'UNCHANGED', changed: false, snapshot_id: null, diff: { added: 0, removed: 0, changed: 0 }, code: null, message: null, check_mode: 'QUICK', checked_at: new Date().toISOString(), coalesced: false }))
  await page.route('**/api/dashboard/catalog**', (route) => fulfillJson(route, new URL(route.request().url()).searchParams.get('environment') === 'USAGE' ? usageCatalog() : distributionCatalog()))
  await page.route('**/api/dashboard/usage/cases/**', (route) => {
    const url = new URL(route.request().url())
    usageRequests.push(url)
    return fulfillJson(route, usagePayload(decodeURIComponent(url.pathname.split('/').pop()!)))
  })
  await page.route('**/api/dashboard/distribution/runs/**', (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/videos')) return fulfillJson(route, { contract_version: 1, context: {}, pagination: { page: 1, page_size: 20, total_items: 0, total_pages: 1, has_previous: false, has_next: false }, videos: [] })
    distributionRequests.push(url)
    return fulfillJson(route, distribution(url, options))
  })
  await page.route('**/api/dashboard/distribution/scenes/**', (route) => fulfillJson(route, { context: {}, scene: { id: 'scene-a-1', label: '1_Face_Drop_Scene01' }, edge_peaks: [], line_points: [], assets: [], quality_issues: [] }))
  await page.route('**/api/dashboard/finalizations/status**', (route) => fulfillJson(route, { latest: null, selected_case_latest: null, retryable_operations: [], unverified_records: 0 }))
  await page.route('**/api/dashboard/finalizations/preview', (route) => {
    const body = route.request().postDataJSON() as Record<string, string>
    previews.push(body)
    const caseLabel = body.environment === 'USAGE' ? `Usage ${body.case_id.slice(-2).toUpperCase()}` : `Case ${body.case_id.slice(-1).toUpperCase()}`
    return fulfillJson(route, preview(body.case_id, caseLabel, body.environment as 'DISTRIBUTION' | 'USAGE'))
  })
  return { distributionRequests, usageRequests, previews }
}

async function openDistribution(page: Page) {
  await loginWorkspace(page)
  await page.goto('/workspace/requests?project=project-tv-001&request=request-drop-001&view=case_results&result_environment=DISTRIBUTION&case=case-a')
  await expect(page.getByRole('region', { name: 'SPDM 해석 결과 대시보드' })).toBeVisible({ timeout: 15_000 })
  const path = page.getByRole('group', { name: 'Case 경로' })
  await expect(path).toContainText('Run 1')
  await expect(path).toContainText('Individual')
  await expect(page.getByTestId('distribution-dashboard')).toBeVisible()
}

async function openCaseCompare(page: Page) {
  await page.getByRole('tab', { name: 'Case 비교', exact: true }).click()
  const view = page.getByTestId('case-compare')
  await expect(view).toBeVisible()
  return view
}

function cell(table: Locator, scene: string, column: number) {
  return table.locator(`tr[data-scene="${scene}"] > td`).nth(column)
}

test('유통환경 Case 비교는 요약과 같은 값·경로로 Case를 나란히 놓고 증감·최저·없음을 표시한다', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const { distributionRequests } = await installMocks(page)
  await openDistribution(page)
  await expect.poll(() => distributionRequests.length).toBeGreaterThan(0)
  const summaryRequest = distributionRequests.find((url) => url.pathname.endsWith('/run-a'))!
  const view = await openCaseCompare(page)
  // Default: all Cases when there are at most four; the path bar's Case is the baseline.
  for (const key of CASES) await expect(view.getByRole('checkbox', { name: label(key), exact: true })).toBeChecked()
  await expect(view.getByRole('combobox', { name: '기준 Case' })).toHaveValue('case-a')
  const table = view.getByTestId('case-compare-distribution')
  await expect(table).toBeVisible()

  // Same request as the 요약 tab for Case A (merged latest result, same Component/Basis/edges/lines).
  const compareA = distributionRequests.filter((url) => url.pathname.endsWith('/run-a')).pop()!
  expect(Object.fromEntries(compareA.searchParams)).toEqual(Object.fromEntries(summaryRequest.searchParams))
  for (const key of CASES) {
    const request = distributionRequests.find((url) => url.pathname.endsWith(`/run-${key}`))!
    expect(request.searchParams.get('capture_id')).toBe(`latest:case-${key}`)
    expect(request.searchParams.get('run_option_id')).toBe(`option-${key}`)
    expect(request.searchParams.get('component_id')).toBe('C23')
  }

  // Values equal each Case's 요약 values; Δ vs Case A (value and %); lowest per row marked.
  await expect(cell(table, '1_Face_Drop_Scene01', 0)).toContainText('10 MPa')
  await expect(cell(table, '1_Face_Drop_Scene01', 1)).toContainText('12 MPa')
  await expect(cell(table, '1_Face_Drop_Scene01', 1)).toContainText('Δ +2 (+20.0%)')
  await expect(cell(table, '1_Face_Drop_Scene01', 2)).toContainText('Δ −2 (−20.0%)')
  await expect(cell(table, '1_Face_Drop_Scene01', 2)).toHaveAttribute('data-best', 'true')
  await expect(cell(table, '2_Face_Drop_Scene02', 1)).toContainText('Δ −5 (−25.0%)')
  await expect(cell(table, '2_Face_Drop_Scene02', 1)).toHaveAttribute('data-best', 'true')
  await expect(cell(table, '1_Face_Drop_Scene01', 0)).not.toContainText('Δ')
  await expect(table.locator('[data-best="true"]')).toHaveCount(4)
  // A Scene missing in a Case is shown as 없음 and highlighted.
  const missingRow = table.locator('tr[data-scene="3_Corner_Drop_Scene03"]')
  await expect(missingRow).toHaveClass(/has-missing/)
  await expect(missingRow.locator('th')).toContainText('Case B에 없음')
  await expect(cell(table, '3_Corner_Drop_Scene03', 1)).toHaveText('없음')
  await expect(cell(table, '3_Corner_Drop_Scene03', 1)).toHaveAttribute('data-missing', 'true')
  await expect(cell(table, '3_Corner_Drop_Scene03', 0)).toContainText('Case 최대')
  // Case-level summary row: the highest Scene of each Case.
  const summary = table.locator('tr[data-scene="__summary__"] > td')
  await expect(summary.nth(0)).toContainText('30 MPa')
  await expect(summary.nth(1)).toContainText('15 MPa')
  await expect(summary.nth(1)).toContainText('Δ −15 (−50.0%)')
  await expect(summary.nth(1)).toHaveAttribute('data-best', 'true')
  await expect(summary.nth(2)).toContainText('Δ +1 (+3.3%)')
  await expect(view).toContainText('단위: MPa')
  await expect(view.getByTestId('case-compare-unit-warning')).toHaveCount(0)

  // Another baseline recomputes Δ; deselecting a Case removes its column (2 minimum).
  await view.getByRole('combobox', { name: '기준 Case' }).selectOption('case-c')
  await expect(cell(table, '1_Face_Drop_Scene01', 0)).toContainText('Δ +2 (+25.0%)')
  await view.getByRole('checkbox', { name: 'Case B', exact: true }).uncheck()
  await expect(table.locator('thead th')).toHaveCount(3)
  await view.getByRole('checkbox', { name: 'Case A', exact: true }).uncheck()
  await expect(view).toContainText('비교할 Case를 2개 이상 고르세요.')
  await expect(view).not.toContainText(/NO_SELECTION|UNMATCHED|DETAIL\b/)
  expect(errors).toEqual([])
})

test('Case마다 단위가 다르면 증감 대신 경고를 표시한다', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  await installMocks(page, { unitMismatch: true })
  await openDistribution(page)
  const view = await openCaseCompare(page)
  const warning = view.getByTestId('case-compare-unit-warning')
  await expect(warning).toContainText('Case마다 단위가 달라 증감(Δ)을 계산하지 않았습니다')
  await expect(warning).toContainText('Case C GPa')
  const table = view.getByTestId('case-compare-distribution')
  await expect(cell(table, '1_Face_Drop_Scene01', 2)).toContainText('8 GPa')
  await expect(table).not.toContainText('Δ')
})

test('사용환경 Case 비교는 최신 4개를 기본으로 다섯 평가와 OK/NG를 색으로 보인다', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  const { usageRequests } = await installMocks(page)
  await loginWorkspace(page)
  await page.goto('/workspace/requests?project=project-tv-001&request=request-drop-001&view=case_results&result_environment=USAGE&case=case-u5')
  await expect(page.getByTestId('usage-dashboard')).toBeVisible({ timeout: 15_000 })
  const view = await openCaseCompare(page)
  // Five Cases: the four with the newest results are selected; a fifth cannot be added.
  await expect(view.getByRole('checkbox', { name: 'Usage U1', exact: true })).not.toBeChecked()
  await expect(view.getByRole('checkbox', { name: 'Usage U1', exact: true })).toBeDisabled()
  for (const key of ['u2', 'u3', 'u4', 'u5']) await expect(view.getByRole('checkbox', { name: `Usage ${key.toUpperCase()}`, exact: true })).toBeChecked()
  const table = view.getByTestId('case-compare-usage')
  await expect(table.locator('tbody tr')).toHaveCount(6)
  await expect(table.locator('thead th')).toHaveCount(5)
  for (const key of ['u2', 'u3', 'u4', 'u5']) expect(usageRequests.some((url) => url.pathname.endsWith(`/case-${key}`) && url.searchParams.get('capture_id') === `latest:case-${key}`)).toBe(true)
  // Columns follow catalog order (u2..u5); values match the 다섯 평가 종합 cells.
  const settle = table.locator('tr[data-evaluation="Settle:value"] > td')
  await expect(settle.nth(0)).toContainText('1.2 deg')
  await expect(settle.nth(3)).toContainText('1.5 deg')
  const slopeVerdict = table.locator('tr[data-evaluation="Slope_Angle:verdict"] > td')
  await expect(slopeVerdict.nth(1).locator('[data-tone="ng"]')).toContainText('NG')
  await expect(slopeVerdict.nth(0).locator('[data-tone="ok"]')).toHaveCount(2)
  const slope360 = table.locator('tr[data-evaluation="Slope_Angle_360:verdict"] > td')
  await expect(slope360.nth(1).locator('[data-tone="ng"]')).toHaveCount(1)
  await expect(table).toContainText('Slope_Angle')
  await expect(table).toContainText('Horizontal_Force_Angle')
})

test('비교 열의 이 Case를 Final 지정은 그 Case의 Final 창을 연다', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  const { previews } = await installMocks(page)
  await openDistribution(page)
  const view = await openCaseCompare(page)
  const table = view.getByTestId('case-compare-distribution')
  await table.getByRole('button', { name: 'Case B을(를) Final 지정' }).focus()
  await page.keyboard.press('Enter')
  const dialog = page.getByRole('dialog', { name: 'Final 지정 확인' })
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('Case B')
  expect(previews.at(-1)).toMatchObject({ case_id: 'case-b', capture_id: 'latest:case-b', environment: 'DISTRIBUTION' })
  await expect(view.getByTestId('case-compare-final')).toContainText('Final 지정 대상 · Case B')
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  // Another column opens the dialog for that Case.
  await table.getByRole('button', { name: 'Case C을(를) Final 지정' }).click()
  await expect(page.getByRole('dialog', { name: 'Final 지정 확인' })).toContainText('Case C')
  expect(previews.at(-1)).toMatchObject({ case_id: 'case-c', capture_id: 'latest:case-c' })
})

async function downloadFrom(page: Page, dialog: Locator, count = 2) {
  const downloads: Download[] = []
  const listener = (download: Download) => { downloads.push(download) }
  page.on('download', listener)
  await dialog.getByRole('button', { name: '다운로드', exact: true }).click()
  await expect.poll(() => downloads.length, { timeout: 20_000 }).toBe(count)
  await page.waitForTimeout(500)
  page.off('download', listener)
  return downloads
}

/** Every slide XML text of a PPTX (minimal ZIP central-directory reader). */
function pptxSlidesText(buffer: Buffer) {
  let end = buffer.length - 22
  while (end >= 0 && buffer.readUInt32LE(end) !== 0x06054b50) end -= 1
  const count = buffer.readUInt16LE(end + 10)
  let offset = buffer.readUInt32LE(end + 16)
  const texts: string[] = []
  for (let index = 0; index < count; index += 1) {
    const method = buffer.readUInt16LE(offset + 10)
    const size = buffer.readUInt32LE(offset + 20)
    const nameLength = buffer.readUInt16LE(offset + 28)
    const extra = buffer.readUInt16LE(offset + 30)
    const comment = buffer.readUInt16LE(offset + 32)
    const local = buffer.readUInt32LE(offset + 42)
    const name = buffer.toString('utf8', offset + 46, offset + 46 + nameLength)
    if (/^ppt\/slides\/slide\d+\.xml$/.test(name)) {
      const start = local + 30 + buffer.readUInt16LE(local + 26) + buffer.readUInt16LE(local + 28)
      const data = buffer.subarray(start, start + size)
      texts.push((method === 8 ? inflateRawSync(data) : data).toString('utf8'))
    }
    offset += 46 + nameLength + extra + comment
  }
  return texts
}

test('보고서의 Case 비교 포함은 후보 Case 비교 표를 넣고, 끄면 기존 보고서와 같다', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  await installMocks(page)
  await openDistribution(page)
  // Outside the Case 비교 tab the option is offered but disabled.
  await page.getByRole('button', { name: '보고서', exact: true }).click()
  let dialog = page.getByTestId('case-report-dialog')
  await expect(dialog.getByTestId('case-report-counts')).toContainText('Scene 3개')
  await expect(dialog.getByRole('checkbox', { name: /Case 비교 포함/ })).toBeDisabled()
  await dialog.getByRole('button', { name: '닫기', exact: true }).last().click()

  const view = await openCaseCompare(page)
  await expect(view.getByTestId('case-compare-distribution')).toBeVisible()
  await page.getByRole('button', { name: '보고서', exact: true }).click()
  dialog = page.getByTestId('case-report-dialog')
  await expect(dialog.getByTestId('case-report-counts')).toContainText('Scene 3개')
  const include = dialog.getByRole('checkbox', { name: /Case 비교 포함/ })
  await expect(include).toBeEnabled()
  await expect(include).not.toBeChecked()
  await dialog.getByRole('checkbox', { name: 'HTML', exact: true }).check()
  // Unticked: no comparison section.
  let downloads = await downloadFrom(page, dialog)
  const plainHtml = readFileSync(await downloads.find((item) => item.suggestedFilename().endsWith('.html'))!.path() as string, 'utf8')
  const plainSlides = pptxSlidesText(readFileSync(await downloads.find((item) => item.suggestedFilename().endsWith('.pptx'))!.path() as string))
  expect(plainHtml).not.toContain('후보 Case 비교')
  expect(plainSlides.join('')).not.toContain('후보 Case 비교')

  await include.check()
  downloads = await downloadFrom(page, dialog)
  const html = readFileSync(await downloads.find((item) => item.suggestedFilename().endsWith('.html'))!.path() as string, 'utf8')
  const slides = pptxSlidesText(readFileSync(await downloads.find((item) => item.suggestedFilename().endsWith('.pptx'))!.path() as string))
  expect(html).toContain('<h2 id="case-compare-title">후보 Case 비교</h2>')
  expect(html).toContain('Case A (기준)')
  expect(html).toContain('Δ +2 (+20.0%)')
  expect(html).toContain('<td class="num">없음</td>')
  expect(html).toContain('Case 최대 (Scene 전체)')
  expect(slides.length).toBe(plainSlides.length + 1)
  expect(slides.join('')).toContain('후보 Case 비교')
  expect(slides.join('')).toContain('Case 최대 (Scene 전체)')
  // Everything before the comparison is unchanged.
  const marker = '<section aria-labelledby="case-compare-title"'
  expect(html.slice(0, html.indexOf(marker)).replace(/생성 [^<]+/g, '')).toBe(plainHtml.slice(0, plainHtml.indexOf('</main>')).replace(/생성 [^<]+/g, ''))
})

test.describe('Case 비교 at 4K 150%', () => {
  test.use({ viewport: { width: 2560, height: 1440 }, deviceScaleFactor: 1.5 })
  for (const points of [14, 18]) {
    test(`Case 비교 화면 ${points}pt`, async ({ page }) => {
      mkdirSync(SHOT_DIR, { recursive: true })
      await installMocks(page)
      await openDistribution(page)
      await setWorkspaceFontSize(page, points)
      const view = await openCaseCompare(page)
      await expect(view.getByTestId('case-compare-distribution')).toBeVisible()
      expect(await page.evaluate(() => document.scrollingElement!.scrollWidth <= document.scrollingElement!.clientWidth)).toBe(true)
      await page.screenshot({ path: `${SHOT_DIR}/w5-case-compare-2560-${points}pt.png`, fullPage: false })
      if (points === 18) {
        await page.goto('/workspace/requests?project=project-tv-001&request=request-drop-001&view=case_results&result_environment=USAGE&case=case-u5')
        const usage = await openCaseCompare(page)
        await expect(usage.getByTestId('case-compare-usage')).toBeVisible()
        expect(await page.evaluate(() => document.scrollingElement!.scrollWidth <= document.scrollingElement!.clientWidth)).toBe(true)
        await page.screenshot({ path: `${SHOT_DIR}/w5-case-compare-usage-2560-18pt.png`, fullPage: false })
      }
    })
  }
})
