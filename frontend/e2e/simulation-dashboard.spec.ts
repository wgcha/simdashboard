import { expect, test, type Page, type Route } from '@playwright/test'
import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

const CASE_ID = 'case-a'
const CAPTURE_ID = 'capture-a'
const MEMBER_ID = 'member-a'
const RUN_ID = 'run-a'
const MODE = 'INDIVIDUAL'
const COMPONENT_ID = 'C23'
const SCENE20_ASSET = 'data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%22120%22 height=%2280%22%3E%3Crect width=%22120%22 height=%2280%22 fill=%22%232e84d6%22/%3E%3Ctext x=%2260%22 y=%2244%22 fill=%22white%22 text-anchor=%22middle%22 font-size=%2218%22%3EScene20%3C/text%3E%3C/svg%3E'

function usageCatalog() {
  return {
    environment: 'USAGE',
    cases: [{ id: 'usage-case', label: 'Usage Case' }],
    load_cases: [],
    execution_runs: [],
    modes: [],
    captures: [{ id: 'usage-capture', label: 'usage capture', case_id: 'usage-case' }],
    components: [],
    bases: [],
  }
}

function distributionCatalog() {
  return {
    environment: 'DISTRIBUTION',
    cases: [{ id: CASE_ID, label: 'Case A' }],
    load_cases: [{ id: 'load-drop', label: 'Drop', case_id: CASE_ID, capture_id: CAPTURE_ID }],
    execution_runs: [{ id: RUN_ID, label: 'Run A', case_id: CASE_ID, load_case_id: 'load-drop', capture_id: CAPTURE_ID }],
    modes: [{ id: MODE, label: MODE, case_id: CASE_ID, execution_run_id: RUN_ID, capture_id: CAPTURE_ID }],
    captures: [{ id: CAPTURE_ID, label: 'capture A', case_id: CASE_ID }],
    components: [{ id: COMPONENT_ID, label: COMPONENT_ID, case_id: CASE_ID, execution_run_id: RUN_ID, mode: MODE, capture_id: CAPTURE_ID }],
    bases: [{ id: 'DETAIL', label: '상세 추출값' }],
  }
}

function distributionPayload() {
  const member = {
    id: MEMBER_ID,
    label: 'Case A',
    simulation_case_id: CASE_ID,
    load_case_id: 'load-drop',
    execution_run_id: RUN_ID,
    mode: MODE,
    capture_id: CAPTURE_ID,
    component_id: COMPONENT_ID,
    basis: 'DETAIL',
  }
  const scenes = Array.from({ length: 20 }, (_, index) => {
    const sequence = index + 1
    return {
      id: `scene-${sequence}`,
      label: `${sequence}_Face_Drop_Scene${String(sequence).padStart(2, '0')}`,
      scene_sequence_number: sequence,
      scenario_number: sequence,
      order_status: 'CONFIRMED',
      contact_code: 'Face1',
      repetition: '1st',
    }
  })
  const edges = ['TOP', 'BOTTOM', 'LEFT', 'RIGHT'] as const
  const edge_peaks = scenes.flatMap((scene) => edges.map((edge, edgeIndex) => ({
    value: scene.scene_sequence_number + edgeIndex / 10,
    unit: null,
    unit_status: 'UNCONFIRMED',
    completeness: 'FULL',
    status: 'READY',
    basis: 'DETAIL',
    scope: 'SELECTED_EDGE_LINES',
    edge,
    scene_id: scene.id,
    member_id: MEMBER_ID,
    line_index: 1,
    source_refs: [{ asset_id: `csv-${scene.scene_sequence_number}`, row: 2, column: 'Layer_1' }],
  })))
  const series = scenes.filter((scene) => scene.scene_sequence_number !== 12).map((scene) => ({
    id: `series-${scene.id}`,
    value: scene.scene_sequence_number,
    unit: null,
    unit_status: 'UNCONFIRMED',
    completeness: 'FULL',
    status: 'READY',
    basis: 'DETAIL',
    scene_id: scene.id,
    scene_sequence_number: scene.scene_sequence_number,
    member_id: MEMBER_ID,
    edge: 'TOP',
    selected_edge_envelope: scene.scene_sequence_number,
  }))
  const contours = scenes.map((scene) => ({
    cell_id: `cell-${scene.id}-${MEMBER_ID}`,
    scene_id: scene.id,
    member_id: MEMBER_ID,
    status: scene.scene_sequence_number === 20 ? 'READY' : 'MISSING',
    reason: scene.scene_sequence_number === 10 ? 'Scene10 image missing' : scene.scene_sequence_number === 20 ? null : '합성 fixture에는 컨투어 원본 없음',
    asset: scene.scene_sequence_number === 20 ? { asset_id: 'asset-scene20', kind: 'IMAGE', status: 'READY', url: SCENE20_ASSET, title: 'synthetic Scene20', frame_role: 'FINAL_FRAME', provenance: 'synthetic fixture' } : null,
  }))
  return {
    contract_version: 1,
    context: {
      project_id: 'project-tv-001',
      request_id: 'request-drop-001',
      simulation_case_id: CASE_ID,
      load_case_id: 'load-drop',
      execution_run_id: RUN_ID,
      mode: MODE,
      capture_id: CAPTURE_ID,
      component_id: COMPONENT_ID,
      basis: 'DETAIL',
      context_key: 'fixture-context',
    },
    status: 'READY',
    members: [member],
    scenes,
    edge_peaks,
    series,
    contours,
    behaviors: [],
    quality_issues: [],
  }
}

function sceneDetail(sceneId: string) {
  return {
    contract_version: 1,
    context: { project_id: 'project-tv-001', request_id: 'request-drop-001', simulation_case_id: CASE_ID, load_case_id: 'load-drop', execution_run_id: RUN_ID, mode: MODE, capture_id: CAPTURE_ID, component_id: COMPONENT_ID, basis: 'DETAIL', scene_id: sceneId, context_key: `fixture-${sceneId}` },
    scene: { id: sceneId, label: sceneId, scene_sequence_number: Number(sceneId.replace('scene-', '')), scenario_number: Number(sceneId.replace('scene-', '')), order_status: 'CONFIRMED' },
    edge_peaks: [],
    line_points: [],
    corner_points: [],
    assets: [],
    quality_issues: [],
  }
}

async function fulfillJson(route: Route, body: unknown) {
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
}

async function openResults(page: Page) {
  await loginWorkspace(page)
  await openWorkspaceRoute(page, '/workspace/overview')
  const overview = page.getByTestId('result-overview-dashboard')
  await overview.getByLabel('의뢰 제목, 하중 경우 검색').fill('')
  await overview.getByRole('button', { name: '결과 검토', exact: true }).first().click()
  await expect(page.getByRole('region', { name: 'SPDM 해석 결과 대시보드' })).toBeVisible()
}

async function installDashboardMocks(page: Page, options: { slowUsage?: boolean } = {}) {
  let releaseUsage!: () => void
  const usageGate = new Promise<void>((resolve) => { releaseUsage = resolve })
  await page.route('**/api/dashboard/catalog**', async (route) => {
    const url = new URL(route.request().url())
    if (url.searchParams.get('environment') === 'USAGE') {
      if (options.slowUsage) await usageGate
      try { await fulfillJson(route, usageCatalog()) } catch { /* request was aborted by tab switch */ }
      return
    }
    await fulfillJson(route, distributionCatalog())
  })
  await page.route('**/api/dashboard/distribution/runs/**', (route) => fulfillJson(route, distributionPayload()))
  await page.route('**/api/dashboard/distribution/scenes/**', (route) => {
    const sceneId = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop() ?? '')
    return fulfillJson(route, sceneDetail(sceneId))
  })
  return { releaseUsage }
}

async function chooseDistribution(page: Page) {
  await page.getByRole('button', { name: '유통환경', exact: true }).click()
  const select = (label: string) => page.locator('.simulation-dashboard__controls label').filter({ hasText: label }).locator('select')
  await expect(select('Simulation Case')).toBeVisible()
  await select('Simulation Case').selectOption(CASE_ID)
  await select('Capture').selectOption(CAPTURE_ID)
  await select('Load case').selectOption('load-drop')
  await select('run #').selectOption(RUN_ID)
  await select('Mode').selectOption(MODE)
  await select('Component').selectOption(COMPONENT_ID)
  await select('Basis').selectOption('DETAIL')
  await expect(page.getByTestId('distribution-dashboard')).toBeVisible()
}

test('synthetic distribution renders all 20 scenes, preserves contour cell IDs through transpose, and links graph selection to detail', async ({ page }) => {
  await installDashboardMocks(page)
  await openResults(page)
  await chooseDistribution(page)

  await page.getByRole('button', { name: '엣지별 수준', exact: true }).click()
  await expect(page.locator('.simulation-dashboard__scene-table > div')).toHaveCount(20)
  await expect(page.locator('.simulation-dashboard__scene-table')).toContainText('20_Face_Drop_Scene20')

  await page.getByRole('button', { name: '컨투어', exact: true }).click()
  const cell = page.locator('[data-cell-id="cell-scene-20-member-a"]')
  await expect(cell).toHaveCount(1)
  await page.getByRole('button', { name: '행/열 전치', exact: true }).click()
  await expect(page.locator('[data-cell-id="cell-scene-20-member-a"]')).toHaveCount(1)

  await page.getByRole('button', { name: '결과 요약', exact: true }).click()
  const bars = page.locator('.simulation-dashboard__summary .recharts-bar-rectangle')
  // Recharts keeps a geometry node for the null Scene12 datum; the fixture still
  // verifies that the final selectable bar carries Scene20's member context.
  await expect(bars).toHaveCount(20)
  await bars.nth(19).click()
  await expect(page.getByText('Scene 상세 · scene-20')).toBeVisible()

  await page.getByRole('button', { name: '컨투어', exact: true }).click()
  await page.locator('[data-cell-id="cell-scene-20-member-a"]').getByRole('button', { name: '자산 확대' }).click()
  await expect(page.getByRole('dialog')).toContainText('닫기')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('slow usage catalog cannot overwrite the selected distribution catalog', async ({ page }) => {
  const { releaseUsage } = await installDashboardMocks(page, { slowUsage: true })
  await openResults(page)
  await page.getByRole('button', { name: '유통환경', exact: true }).click()
  const controls = page.locator('.simulation-dashboard__controls')
  await expect(controls.locator('label').filter({ hasText: 'Load case' }).locator('select')).toBeVisible()
  await expect(controls.locator('label').filter({ hasText: 'Simulation Case' }).locator('option', { hasText: 'Case A' })).toHaveCount(1)
  releaseUsage()
  await page.waitForTimeout(150)
  await expect(controls.locator('label').filter({ hasText: 'Load case' }).locator('select')).toBeVisible()
  await expect(page.getByText('다섯 평가 종합')).toHaveCount(0)
})
