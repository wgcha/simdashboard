import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { expect, test, type Page, type Route } from '@playwright/test'
import type { DashboardDistribution } from '../src/shared/api/simulationDashboard'
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
    run_options: [{ id: 'option-individual', label: 'Individual', option_label: 'Individual', option_status: 'PRESENT', case_id: CASE_ID, execution_run_id: RUN_ID, mode: MODE, capture_id: CAPTURE_ID }],
    modes: [{ id: MODE, label: MODE, case_id: CASE_ID, execution_run_id: RUN_ID, capture_id: CAPTURE_ID }],
    captures: [{ id: CAPTURE_ID, label: 'capture A', case_id: CASE_ID }],
    components: [{ id: COMPONENT_ID, label: COMPONENT_ID, case_id: CASE_ID, execution_run_id: RUN_ID, mode: MODE, capture_id: CAPTURE_ID }],
    bases: [{ id: 'DETAIL', label: '상세 추출값' }],
  }
}

function usageDashboardPayload(requestId = 'request-showcase-waiting') {
  const directions = (index: number) => ({
    value: index * 10 + 1,
    unit: 'mm',
    unit_status: 'CONFIRMED',
    completeness: 'FULL',
    status: 'READY',
    basis: 'REPORTED_SUMMARY',
    scope: 'CAPTURE',
  })
  return {
    context: { project_id: 'project-feature-showcase', request_id: requestId, simulation_case_id: 'usage-case', capture_id: 'usage-capture', context_key: `usage-${requestId}` },
    status: 'READY',
    evaluations: Array.from({ length: 5 }, (_, index) => ({
      id: `evaluation-${index + 1}`,
      name: `Evaluation ${index + 1}`,
      status: 'READY',
      common: directions(index),
      front: directions(index + 1),
      rear: directions(index + 2),
      verdict: 'PASS',
      media: [],
      reference: null,
    })),
    quality_issues: [],
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
  await page.goto('/workspace/requests?project=project-tv-001&request=request-drop-001&view=case_results')
  await expect(page.getByRole('region', { name: 'SPDM 해석 결과 대시보드' })).toBeVisible()
}

async function installDashboardMocks(page: Page, options: { slowUsage?: boolean; distribution?: DashboardDistribution } = {}) {
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
  await page.route('**/api/dashboard/distribution/runs/**', (route) => {
    const payload = structuredClone(options.distribution ?? distributionPayload()) as DashboardDistribution
    if (new URL(route.request().url()).searchParams.get('edge_keys') === '') {
      payload.series = payload.series.map((point) => ({ ...point, value: null, selected_edge_envelope: null, status: 'NO_SELECTION', completeness: 'NO_SELECTION' }))
    }
    return fulfillJson(route, payload)
  })
  await page.route('**/api/dashboard/distribution/scenes/**', (route) => {
    const sceneId = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop() ?? '')
    return fulfillJson(route, sceneDetail(sceneId))
  })
  return { releaseUsage }
}

async function chooseDistribution(page: Page) {
  await page.getByRole('button', { name: '유통환경', exact: true }).click()
  const select = (label: string) => page.locator('.simulation-dashboard__controls label').filter({ hasText: label }).locator('select:visible')
  await expect(page.locator('.simulation-dashboard__controls')).toContainText('Case A')
  for (const [label, value] of [['해석 Case', CASE_ID], ['수집 버전', CAPTURE_ID], ['하중경우', 'load-drop'], ['Run Case', RUN_ID], ['Run Option', 'option-individual'], ['Component', COMPONENT_ID], ['Basis', 'DETAIL']] as const) {
    const field = select(label)
    if (await field.count()) await field.selectOption(value)
  }
  await expect(page.getByTestId('distribution-dashboard')).toBeVisible()
}

test('single Run Option is compact and its stable id is pinned in the URL', async ({ page }) => {
  await installDashboardMocks(page)
  await openResults(page)
  await chooseDistribution(page)
  await expect(page.locator('.simulation-dashboard__choice').filter({ hasText: 'Run Option' })).toContainText('Individual')
  await expect(page).toHaveURL(/case_option=option-individual/)
  await expect(page.locator('.simulation-dashboard__history')).not.toHaveAttribute('open', '')
  const evidenceDir = join(tmpdir(), 'simdashboard-option-qa'); mkdirSync(evidenceDir, { recursive: true })
  await page.screenshot({ path: join(evidenceDir, 'compact-option-desktop.png'), fullPage: false })
})

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
  await expect(controls).toContainText('하중경우')
  await expect(controls).toContainText('Case A')
  releaseUsage()
  await page.waitForTimeout(150)
  await expect(controls).toContainText('하중경우')
  await expect(page.getByText('다섯 평가 종합')).toHaveCount(0)
})

function comparisonRegressionPayload(): DashboardDistribution {
  const payload = distributionPayload() as DashboardDistribution
  const firstMember = payload.members[0]
  payload.members.push({ ...firstMember, id: 'member-b', label: 'Case B', simulation_case_id: 'case-b', capture_id: 'capture-b', execution_run_id: 'run-b' })
  payload.scenes.push(...payload.scenes.map((scene) => ({ ...scene, id: `${scene.id}-b`, label: `Case B · ${scene.label}` })))
  payload.series.push(...payload.series.map((point) => ({ ...point, id: `${point.id}-b`, scene_id: `${point.scene_id}-b`, member_id: 'member-b', value: point.value === null ? null : point.value + 100, selected_edge_envelope: point.selected_edge_envelope == null ? null : point.selected_edge_envelope + 100 })))
  payload.edge_peaks.push(...payload.edge_peaks.map((point) => ({ ...point, scene_id: `${point.scene_id}-b`, member_id: 'member-b', value: point.value === null ? null : point.value + 100 })))
  return payload
}

test('Case legend preserves an empty selection and stable colors when hiding and restoring cases', async ({ page }) => {
  await installDashboardMocks(page, { distribution: comparisonRegressionPayload() })
  await openResults(page)
  await chooseDistribution(page)
  await page.getByRole('button', { name: '엣지별 수준', exact: true }).click()
  const legend = page.getByRole('group', { name: 'Case 범례' })
  const caseA = legend.getByRole('checkbox', { name: 'Case A', exact: true })
  const caseB = legend.getByRole('checkbox', { name: 'Case B', exact: true })
  const colorB = await caseB.locator('..').locator('i').getAttribute('style')
  await caseA.uncheck()
  await expect(caseB).toBeChecked()
  await expect(caseB.locator('..').locator('i')).toHaveAttribute('style', colorB!)
  await caseB.uncheck()
  await expect(caseA).not.toBeChecked()
  await expect(caseB).not.toBeChecked()
  await expect(page.locator('.simulation-dashboard__edge-panels .recharts-bar-rectangle')).toHaveCount(0)
  await page.getByRole('button', { name: '전체 선택', exact: true }).click()
  await expect(caseA).toBeChecked()
  await expect(caseB).toBeChecked()
  await page.getByRole('button', { name: '전체 해제', exact: true }).click()
  await expect(caseA).not.toBeChecked()
  await expect(caseB).not.toBeChecked()
  await caseB.check()
  await expect(caseA).not.toBeChecked()
  await expect(caseB).toBeChecked()
  await page.getByTestId('simulation-chart-edge-TOP').locator('.recharts-bar-rectangle').last().click()
  await expect(page.getByText('Scene 상세 · scene-20-b', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '전체 선택', exact: true }).click()
  for (const graphMode of ['dot', 'line']) {
    await page.getByLabel('TOP 엣지 수준 그래프 종류', { exact: true }).selectOption(graphMode)
    const chart = page.getByTestId('simulation-chart-edge-TOP')
    await expect(chart.locator('.recharts-line-dot')).toHaveCount(40)
    await chart.locator('.recharts-line-dot').last().click()
    await expect(page.getByText('Scene 상세 · scene-20-b', { exact: true })).toBeVisible()
  }
})

test('clearing every edge shows no selection and selecting an edge restores the summary', async ({ page }) => {
  await installDashboardMocks(page)
  await openResults(page)
  await chooseDistribution(page)
  const picker = page.locator('.simulation-dashboard__toolbar').getByRole('checkbox')
  // Only edge controls, leaving the four line controls selected.
  for (const edge of ['TOP', 'BOTTOM', 'LEFT', 'RIGHT']) await picker.and(page.getByRole('checkbox', { name: edge, exact: true })).uncheck()
  await expect(page.locator('.simulation-dashboard__summary')).toContainText('선택 없음')
  await expect(page.locator('.simulation-dashboard__summary .recharts-bar-rectangle')).toHaveCount(0)
  await page.getByRole('checkbox', { name: 'TOP', exact: true }).check()
  await expect(page.locator('.simulation-dashboard__summary .recharts-bar-rectangle')).toHaveCount(20)
})

test('chart modes preserve missing and unordered scenes and expanded charts close with Escape', async ({ page }) => {
  const payload = distributionPayload() as DashboardDistribution
  payload.scenes[18] = { ...payload.scenes[18], scene_sequence_number: null, order_status: 'UNCONFIRMED' }
  await installDashboardMocks(page, { distribution: payload })
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await openResults(page)
  await chooseDistribution(page)
  // The login flow intentionally probes /auth/me before authentication (401).
  // Collect console health for the dashboard interaction after that handshake.
  page.on('console', (message) => { if (message.type() === 'error' || message.type() === 'warning') errors.push(message.text()) })
  const summary = page.getByTestId('simulation-chart-summary')
  const mode = page.getByLabel('Scene별 엣지 최대응력 그래프 종류', { exact: true })
  await mode.selectOption('dot')
  await expect(summary.locator('.recharts-bar')).toHaveCount(0)
  await expect(summary.locator('.recharts-line-dot')).toHaveCount(19)
  await summary.locator('.recharts-line-dot').nth(8).hover()
  await expect(summary.locator('.recharts-tooltip-wrapper')).toContainText('Case A')
  await summary.locator('.recharts-line-dot').last().click()
  await expect(page.getByText('Scene 상세 · scene-20', { exact: true })).toBeVisible()
  await mode.selectOption('line')
  await expect(page.locator('.simulation-dashboard__summary .simulation-dashboard__chart-notice')).toContainText('미확인')
  await expect(summary.locator('.recharts-line-dot')).toHaveCount(19)
  const paths = await summary.locator('.recharts-line-curve:not([stroke="transparent"])').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('d') ?? ''))
  // Missing Scene12 and unordered Scene19 must interrupt the connected line.
  expect(paths.join('').split('M').length - 1).toBeGreaterThanOrEqual(3)
  const numericTicks = (await summary.locator('.recharts-xAxis .recharts-cartesian-axis-tick-value').allTextContents()).filter((label) => /^\d+$/.test(label)).map(Number)
  expect(numericTicks).toEqual([...numericTicks].sort((left, right) => left - right))
  expect(new Set(numericTicks).size).toBe(numericTicks.length)
  for (let step = 0; step < 4; step++) await page.getByRole('button', { name: '전체 글자 크기 늘리기', exact: true }).click()
  await expect(page.locator('.app-shell')).toHaveCSS('--ui-font-size', '18pt')
  await page.getByRole('button', { name: 'Scene별 엣지 최대응력 그래프 확대', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('dialog')).toContainText('Scene별 엣지 최대응력')
  expect(await page.getByRole('dialog').evaluate((element) => getComputedStyle(element).getPropertyValue('--ui-font-size').trim())).toBe('18pt')
  expect(await page.getByRole('dialog').locator('.recharts-text').first().evaluate((element) => parseFloat(getComputedStyle(element).fontSize))).toBeCloseTo(21, 1)
  await expect.poll(async () => (await page.getByRole('dialog').locator('.recharts-wrapper > svg.recharts-surface').boundingBox())?.height ?? 0).toBeGreaterThan(250)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await expect(page).toHaveURL(/view=case_results/)
  expect(await page.title()).not.toBe('')
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  const evidence = join(tmpdir(), 'simdashboard-issue27-qa')
  mkdirSync(evidence, { recursive: true })
  await page.screenshot({ path: join(evidence, 'chart-desktop.png'), fullPage: true })
  await page.locator('.simulation-dashboard__summary').screenshot({ path: join(evidence, 'summary-desktop.png') })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(mode).toBeVisible()
  await page.screenshot({ path: join(evidence, 'chart-mobile.png'), fullPage: true })
  await page.locator('.simulation-dashboard__summary').screenshot({ path: join(evidence, 'summary-mobile.png') })
  expect(errors).toEqual([])
})

test('contour values retain scope and unknown timing with descriptions and scale guidance after transpose', async ({ page }) => {
  const payload = distributionPayload() as DashboardDistribution
  payload.contours[19].value = { value: 20, unit: null, scope: 'SELECTED_EDGE_LINES', basis: 'DETAIL', status: 'READY' }
  payload.quality_issues = ['UNPROCESSED_FILE:case/Drop/run/Scene/notes.txt:UNSUPPORTED_EXTENSION']
  await installDashboardMocks(page, { distribution: payload })
  await openResults(page)
  await chooseDistribution(page)
  await page.getByRole('button', { name: '컨투어', exact: true }).click()
  const matrix = page.locator('.simulation-dashboard__matrix')
  const cell = page.locator('[data-cell-id="cell-scene-20-member-a"]')
  await expect(matrix).toContainText('Scale bar')
  await expect(matrix).toContainText('미확인')
  await expect(matrix).toContainText('Face1')
  await expect(cell).toContainText('20')
  await expect(cell).toContainText('단위 미확인')
  await expect(cell).toContainText('시간')
  await expect(cell).toContainText('상세 추출')
  await expect(page.locator('.simulation-dashboard__issues')).toContainText('미지원 확장자')
  await expect(matrix.locator('[title="20_Face_Drop_Scene20"]')).toHaveCount(1)
  await page.getByRole('button', { name: '행/열 전치', exact: true }).click()
  await expect(cell).toHaveCount(1)
  await expect(cell).toContainText('20')
  await cell.click()
  await expect(page.getByText('Scene 상세 · scene-20', { exact: true })).toBeVisible()
})

test('fresh request reaches the Case usage review and clears prior capture context on request switch', async ({ page }) => {
  let usageRequests = 0
  await page.route('**/api/requests/*/load-cases**', (route) => fulfillJson(route, []))
  await page.route('**/api/dashboard/catalog**', async (route) => {
    const url = new URL(route.request().url())
    if (url.searchParams.get('environment') === 'USAGE' && url.searchParams.get('request_id') === 'request-showcase-waiting') return fulfillJson(route, usageCatalog())
    return fulfillJson(route, { ...usageCatalog(), cases: [], captures: [] })
  })
  await page.route('**/api/dashboard/usage/cases/**', (route) => {
    usageRequests += 1
    return fulfillJson(route, usageDashboardPayload())
  })
  await loginWorkspace(page, 'e2e-viewer', '/workspace/requests?project=project-feature-showcase&request=request-showcase-waiting')

  const project = page.getByLabel('프로젝트 선택', { exact: true })
  const request = page.getByLabel('의뢰 선택', { exact: true })
  await expect(project).toHaveValue('project-feature-showcase')
  await expect(request).toHaveValue('request-showcase-waiting')
  await page.getByRole('button', { name: 'Case 결과', exact: true }).click()
  const controls = page.locator('.simulation-dashboard__controls')
  const caseBadge = controls.locator('.simulation-dashboard__choice').filter({ hasText: '해석 Case' })
  const captureBadge = controls.locator('.simulation-dashboard__capture-pin')
  await expect(caseBadge).toContainText('Usage Case')
  await expect(captureBadge).toContainText('usage capture')
  await expect(page.getByTestId('usage-dashboard')).toBeVisible()
  await expect.poll(() => usageRequests).toBeGreaterThan(0)
  await expect(page.getByTestId('usage-dashboard').locator('tbody tr')).toHaveCount(5)
  await expect(page.getByTestId('usage-dashboard').locator('thead')).toContainText('전방')
  await expect(page.getByTestId('usage-dashboard')).toContainText('11 mm')
  const caseResultsButton = page.getByRole('button', { name: 'Case 결과', exact: true })
  await expect(caseResultsButton).toHaveAttribute('aria-current', 'step')
  await expect(page.locator('.request-journey [aria-current="step"]')).toHaveCount(1)
  await page.getByRole('button', { name: '의뢰 개요', exact: true }).click()
  await expect(caseResultsButton).toBeVisible()
  await caseResultsButton.click()
  await expect(caseBadge).toContainText('Usage Case')
  await expect(captureBadge).toContainText('usage capture')
  await expect(page.locator('.request-results-refresh')).toHaveCount(0)
  await expect(page.getByTestId('usage-dashboard').locator('tbody tr')).toHaveCount(5)
  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveCount(0)
  await expect(page.locator('.request-results-refresh')).toHaveCount(0)
  await page.reload()
  await expect(caseBadge).toContainText('Usage Case')
  await expect(captureBadge).toContainText('usage capture')
  await expect(page.getByTestId('usage-dashboard').locator('tbody tr')).toHaveCount(5)
  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveCount(0)
  const outputDirectory = process.env.GUI_QA_OUTPUT_DIR ?? join(tmpdir(), 'simdashboard-usage-qa')
  mkdirSync(outputDirectory, { recursive: true })
  await page.screenshot({ path: join(outputDirectory, 'usage-case-entry-desktop.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: join(outputDirectory, 'usage-case-entry-mobile.png'), fullPage: true })

  await project.selectOption('project-tv-001')
  await expect(request).not.toHaveValue('request-showcase-waiting')
  await expect(page.getByTestId('usage-dashboard')).toHaveCount(0)
})
