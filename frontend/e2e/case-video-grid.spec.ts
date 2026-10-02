import { expect, test, type Page, type Route } from '@playwright/test'
import { loginWorkspace } from './workspace-test-helpers'

// Case results "영상" tab: every video of every Scene of the selected Run option (mocked API).
const CASE_ID = 'case-a'
const CAPTURE_ID = 'latest:case-a'
const RUN_ID = 'run-a'
const OPTION_ID = 'option-individual'
const MODE = 'INDIVIDUAL'

function catalog() {
  return {
    environment: 'DISTRIBUTION',
    cases: [{ id: CASE_ID, label: 'Case A' }],
    load_cases: [{ id: 'load-drop', label: 'Drop', case_id: CASE_ID, capture_id: CAPTURE_ID }],
    execution_runs: [{ id: RUN_ID, label: 'Run A', case_id: CASE_ID, load_case_id: 'load-drop', capture_id: CAPTURE_ID }],
    run_options: [{ id: OPTION_ID, label: 'Individual', option_label: 'Individual', option_status: 'PRESENT', case_id: CASE_ID, execution_run_id: RUN_ID, mode: MODE, capture_id: CAPTURE_ID }],
    modes: [{ id: MODE, label: MODE, case_id: CASE_ID, execution_run_id: RUN_ID, capture_id: CAPTURE_ID }],
    captures: [{ id: CAPTURE_ID, label: '최신 결과', case_id: CASE_ID, kind: 'LATEST', merged_capture_count: 2 }],
    components: [{ id: 'C23', label: 'C23', case_id: CASE_ID, execution_run_id: RUN_ID, mode: MODE, capture_id: CAPTURE_ID }],
    bases: [{ id: 'DETAIL', label: '상세 추출값' }],
  }
}

function distribution() {
  return {
    contract_version: 1,
    context: { project_id: 'project-tv-001', request_id: 'request-drop-001', simulation_case_id: CASE_ID, load_case_id: 'load-drop', execution_run_id: RUN_ID, run_option_id: OPTION_ID, mode: MODE, capture_id: CAPTURE_ID, component_id: 'C23', basis: 'DETAIL', context_key: 'video-fixture' },
    status: 'READY', members: [], scenes: [], edge_peaks: [], series: [], contours: [], behaviors: [], quality_issues: [],
  }
}

function videoPage(url: URL, total: number) {
  const page = Number(url.searchParams.get('page') ?? '1')
  const pageSize = Number(url.searchParams.get('page_size') ?? '20')
  const all = Array.from({ length: total }, (_, index) => ({
    video_id: `scene-${index + 1}:asset-${index + 1}`,
    asset_id: `asset-${index + 1}`,
    scene_id: `scene-${index + 1}`,
    scene_label: `${index + 1}_Face_Drop_Scene${String(index + 1).padStart(2, '0')}`,
    title: `BEHAVIOR_scene${String(index + 1).padStart(2, '0')}.mp4`,
    component_id: null,
    frame_role: 'UNKNOWN',
    status: 'READY',
    source_capture_id: index < 3 ? 'capture-1' : 'capture-2',
  }))
  const totalPages = total ? Math.ceil(total / pageSize) : 0
  return {
    contract_version: 1,
    context: { ...distribution().context, option_label: 'Individual', load_case_name: 'Drop', run_label: 'Run A' },
    pagination: { page, page_size: pageSize, total_items: total, total_pages: totalPages, has_previous: page > 1 && totalPages > 0, has_next: page < totalPages },
    videos: all.slice((page - 1) * pageSize, page * pageSize),
  }
}

async function fulfillJson(route: Route, body: unknown) {
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
}

async function installMocks(page: Page, totalVideos: number) {
  const requests: URL[] = []
  await page.route('**/api/dashboard/finalizations/status**', (route) => route.fulfill({ json: { latest: null, selected_case_latest: null, retryable_operations: [], unverified_records: 0 } }))
  await page.route('**/api/folder-discovery/environments/sync', (route) => route.fulfill({ json: { status: 'UNCHANGED', changed: false, snapshot_id: null, diff: { added: 0, removed: 0, changed: 0 }, code: null, message: null, check_mode: 'QUICK', checked_at: new Date().toISOString(), coalesced: false } }))
  await page.route('**/api/dashboard/catalog**', (route) => fulfillJson(route, catalog()))
  await page.route('**/api/dashboard/distribution/scenes/**', (route) => fulfillJson(route, { context: distribution().context, scene: { id: 'scene-1', label: 'scene-1' }, edge_peaks: [], line_points: [], assets: [], quality_issues: [] }))
  await page.route('**/api/dashboard/assets/**', (route) => route.fulfill({ status: 404, body: '' }))
  // Registered last so it wins over the broader runs/** handler for the videos route.
  await page.route('**/api/dashboard/distribution/runs/**', (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/videos')) {
      requests.push(url)
      return fulfillJson(route, videoPage(url, totalVideos))
    }
    return fulfillJson(route, distribution())
  })
  return requests
}

async function openVideoTab(page: Page) {
  await loginWorkspace(page)
  await page.goto(`/workspace/requests?project=project-tv-001&request=request-drop-001&view=case_results`)
  await expect(page.getByRole('region', { name: 'SPDM 해석 결과 대시보드' })).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: '유통환경', exact: true }).click()
  const path = page.getByRole('group', { name: 'Case 경로' })
  // Wait for the catalog before choosing levels (selects are disabled until it loads).
  await expect(path).toContainText('Case A')
  // Single candidates collapse to fixed text once auto-selection settles.
  await expect(path).toContainText('Individual')
  for (const [label, value] of [['Case', CASE_ID], ['하중경우', 'load-drop'], ['Run Case', RUN_ID], ['Run Option', OPTION_ID]] as const) {
    const field = path.getByRole('combobox', { name: label, exact: true })
    if (await field.count()) await field.selectOption(value)
  }
  await page.getByRole('tab', { name: '영상', exact: true }).click()
}

test('영상 탭은 Run Option의 모든 Scene 영상을 2×2로 보여주고 열 수를 바꾼다', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  const requests = await installMocks(page, 6)
  await openVideoTab(page)

  const grid = page.getByTestId('case-video-grid')
  await expect(grid).toBeVisible()
  expect(requests.at(-1)?.searchParams.get('capture_id')).toBe(CAPTURE_ID)
  expect(requests.at(-1)?.searchParams.get('run_option_id')).toBe(OPTION_ID)
  await expect(grid).toContainText('Individual')
  await expect(grid).toContainText('전체 6개 영상')
  const cards = grid.locator('.drop-video-card')
  await expect(cards).toHaveCount(4)
  await expect(cards.first()).toContainText('1_Face_Drop_Scene01')
  await expect(cards.first()).toContainText('BEHAVIOR_scene01.mp4')
  await expect(grid.locator('video').first()).toHaveAttribute('src', /\/api\/dashboard\/assets\/asset-1$/)
  await expect(grid).not.toContainText('SYNTHETIC')
  await expect(grid).not.toContainText('capture-1')

  const columnCount = () => grid.locator('.drop-video-grid').evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(' ').length)
  await expect.poll(columnCount).toBe(2)
  await grid.getByRole('group', { name: '영상 열 수' }).getByRole('button', { name: '3열', exact: true }).click()
  await expect(grid.getByRole('group', { name: '영상 열 수' }).getByRole('button', { name: '3열', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(cards).toHaveCount(6)
  await expect.poll(columnCount).toBe(3)
  await expect(grid.locator('.drop-video-media-heading')).toContainText('가로 3 × 세로 2')

  await grid.getByRole('button', { name: '2열', exact: true }).click()
  await expect(cards).toHaveCount(4)
  await grid.getByRole('button', { name: /다음 영상/ }).click()
  await expect(cards).toHaveCount(2)
  await expect(cards.first()).toContainText('5_Face_Drop_Scene05')

  await grid.getByRole('button', { name: /반복 꺼짐/ }).click()
  await expect.poll(() => grid.locator('video').evaluateAll((items) => items.every((item) => (item as HTMLVideoElement).loop))).toBe(true)
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
})

test('영상이 없는 Run Option은 안내 문구를 보여준다', async ({ page }) => {
  await installMocks(page, 0)
  await openVideoTab(page)
  await expect(page.getByTestId('case-video-grid-empty')).toContainText('이 Run Option에는 영상이 없습니다.')
})
