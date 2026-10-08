import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { expect, test, type Page } from '@playwright/test'
import { expectCaseResultsLayout, loginWorkspace, mockResultEnvironments, setWorkspaceFontSize } from './workspace-test-helpers'

const requestId = 'request-showcase-waiting'
const projectId = 'project-feature-showcase'

const ids = { case_id: 'case-1', load_case_id: 'load-drop', execution_run_id: 'run-85' }
const catalog = {
  request_id: requestId,
  environment: 'DISTRIBUTION',
  scenes: [
    { scene_id: 'small-scene', label: 'Compact deck', relative_path: 'model/compact', hierarchy: {}, kind: 'SCENE', has_deck: true, ...ids, run_option_id: 'opt-individual' },
    { scene_id: 'large-scene', label: 'Large deck', relative_path: 'model/large', hierarchy: {}, kind: 'SCENE', has_deck: true, ...ids, run_option_id: 'opt-individual' },
    { scene_id: 'result-folder', label: 'Run 02 · results', relative_path: 'model/run-02/results', hierarchy: {}, kind: 'RESULTS', has_deck: true, ...ids, run_option_id: 'opt-cumulative' },
  ],
  hierarchy: {
    cases: [{ id: 'case-1', label: 'Package_Model_SetCase2', relative_path: 'model' }],
    load_cases: [{ id: 'load-drop', label: 'Drop', case_id: 'case-1', relative_path: 'model/Drop', capture_id: null, match_status: 'UNCAPTURED' }],
    execution_runs: [{ id: 'run-85', label: '85qn80h_ref_organized', case_id: 'case-1', load_case_id: 'load-drop', relative_path: 'model/Drop/85', capture_id: null, match_status: 'UNCAPTURED' }],
    run_options: [
      { id: 'opt-individual', label: 'INDIVIDUAL', case_id: 'case-1', execution_run_id: 'run-85', run_option_id: 'opt-individual', option_status: 'PRESENT', option_label: 'INDIVIDUAL', mode: 'INDIVIDUAL', relative_path: 'model/Drop/85/INDIVIDUAL', capture_id: null, match_status: 'UNCAPTURED' },
      { id: 'opt-cumulative', label: 'CUMULATIVE', case_id: 'case-1', execution_run_id: 'run-85', run_option_id: 'opt-cumulative', option_status: 'PRESENT', option_label: 'CUMULATIVE', mode: 'CUMULATIVE', relative_path: 'model/Drop/85/CUMULATIVE', capture_id: null, match_status: 'UNCAPTURED' },
    ],
  },
}

async function chooseScene(page: Page, optionId: string, sceneId?: string) {
  const path = page.getByRole('group', { name: '소재 덱 경로' })
  await path.getByLabel('Run Option', { exact: true }).selectOption(optionId)
  if (sceneId) await path.getByLabel('Scene', { exact: true }).selectOption(sceneId)
}

const longPartName = `Long rail assembly ${'with segmented reinforcement '.repeat(8)}`

function part(id: string, title: string, propertyId: string | null, materialId: string | null, thickness: number | null) {
  return { id, title, property_id: propertyId, material_id: materialId, thickness, raw_fields: {}, source: { file: 'starter_0000.rad', line: 21 } }
}

const curvePoints = Array.from({ length: 1601 }, (_, index) => ({ x: index / 100, y: index === 803 ? 5000 : Math.sin(index / 31) }))

function deckFor(parts: ReturnType<typeof part>[]) {
  return {
    parts,
    properties: [
      { id: 'PROP-A', subtype: 'SHELL', title: 'Thin shell property', fields: { material_id: 'MAT-A', thickness: 1.2, NIP: 5 }, raw_fields: {}, thickness: 1.2, thickness_display: null, source: { file: 'starter_0000.rad', line: 30 } },
      { id: 'PROP-B', subtype: 'SHELL', title: 'Thick shell property', fields: { material_id: 'MAT-A', thickness: 2.4, NIP: 7 }, raw_fields: {}, thickness: 2.4, thickness_display: null, source: { file: 'starter_0000.rad', line: 38 } },
    ],
    materials: [
      {
        id: 'MAT-A', subtype: 'MAT_024', title: 'Shared aluminum material', fields: { E: 70500, RHO_I: 2.7 }, raw_fields: { E: '7.05E+04' },
        density: { raw: '2.7E-9', value: 2.7e-9, unit: 'tonne/mm³', converted_value: 2700, converted_unit: 'kg/m³' },
        representative_e: 70500, law_id: 24,
        failures: [{ id: 'FAIL-A', subtype: 'FAIL_001', title: 'Tensile failure', fields: { EPSP: 0.15 }, raw_fields: {}, source: { file: 'starter_0000.rad', line: 50 } }],
        source: { file: 'starter_0000.rad', line: 40 },
      },
    ],
    functions: [{ id: 'FUN-01', title: 'Rate curve with a narrow peak', points: curvePoints, point_count: curvePoints.length, uses: [{ owner_type: 'material', owner_id: 'MAT-A', role: 'strain rate', x_unit: '1/s', y_unit: 'scale' }], source: { file: 'starter_0000.rad', line: 80 } }],
    warnings: [],
    unit_system: { input: { mass: 'tonne', length: 'mm', time: 's' }, work: { mass: 'tonne', length: 'mm', time: 's' } },
  }
}

const smallDeck = deckFor([
  part('P-001', 'Left frame rail', 'PROP-A', 'MAT-A', 1.2),
  part('P-002', longPartName, 'PROP-B', 'MAT-A', 2.4),
  part('P-003', 'Unresolved bracket', 'PROP-MISSING', 'MAT-MISSING', null),
  part('P-004', 'Unreferenced cover', null, null, null),
])

const largeDeck = deckFor(Array.from({ length: 128 }, (_, index) => {
  const number = String(index + 1).padStart(3, '0')
  return part(`P${number}`, `Panel ${number}`, index % 2 ? 'PROP-B' : 'PROP-A', 'MAT-A', index % 2 ? 2.4 : 1.2)
}))

async function mockMaterialsApi(page: Page) {
  // The request result environments endpoint (case-results-environment.md) decides which Case results tabs exist.
  await mockResultEnvironments(page)
  // Auto-sync is answered locally so the shared e2e backend does not scan seeded folders between tests.
  await page.route('**/api/folder-discovery/environments/sync', (route) => route.fulfill({ json: { status: 'UNCHANGED', changed: false, snapshot_id: null, diff: { added: 0, removed: 0, changed: 0 }, code: null, message: null, check_mode: 'QUICK', checked_at: new Date().toISOString(), coalesced: false } }))
  await page.route('**/api/materials/catalog**', (route) => route.fulfill({ json: catalog }))
  await page.route('**/api/materials/deck**', (route) => {
    const sceneId = new URL(route.request().url()).searchParams.get('scene_id')
    const scene = catalog.scenes.find((item) => item.scene_id === sceneId) ?? catalog.scenes[0]
    const deck = scene.scene_id === 'large-scene' ? largeDeck : smallDeck
    return route.fulfill({ json: { scene, files: [{ relative_path: `${scene.relative_path}/starter_0000.rad`, size_bytes: 4096 }], candidate_warnings: [], deck } })
  })
}

async function openMaterials(page: Page) {
  await loginWorkspace(page)
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption(projectId)
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption(requestId)
  await expect.poll(() => new URL(page.url()).searchParams.get('request')).toBe(requestId)
  // 소재·물성 is a tab inside Case 결과 (no separate journey button).
  await page.getByRole('navigation', { name: '의뢰 작업 여정' }).getByRole('button', { name: 'Case 결과', exact: true }).click()
  // These scenarios read a distribution (Radioss) deck: 소재·물성 follows the Case results environment.
  await page.getByRole('group', { name: '결과 환경' }).getByRole('button', { name: '유통환경' }).click()
  await expect.poll(() => new URL(page.url()).searchParams.get('result_environment')).toBe('DISTRIBUTION')
  await page.getByRole('tab', { name: '소재·물성', exact: true }).click()
  await expect(page).toHaveURL(/\/workspace\/requests\?.*view=case_results.*resultTab=materials/)
  await expect(page.getByRole('tab', { name: '소재·물성', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByLabel('프로젝트 선택', { exact: true })).toHaveValue(projectId)
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue(requestId)
  await expect(page.getByRole('complementary', { name: '주 메뉴' }).getByRole('link', { name: '모델 소재·물성', exact: true })).toHaveCount(0)
}

test('소재 탭은 폴더를 자동 확인하고 실패해도 기존 Scene을 유지한다', async ({ page }) => {
  // Fake timers let the test advance the 60 s poll without waiting for it.
  await page.clock.install()
  await mockMaterialsApi(page)
  const syncBodies: Array<Record<string, unknown>> = []
  const syncQueue: Array<Record<string, unknown>> = []
  const syncResult = (status: string, extra: Record<string, unknown> = {}) => ({ status, changed: status === 'REFRESHED', snapshot_id: 'snapshot-2', diff: { added: 0, removed: 0, changed: 0 }, code: null, message: null, check_mode: 'QUICK', checked_at: new Date().toISOString(), coalesced: false, ...extra })
  await page.route('**/api/folder-discovery/environments/sync', (route) => {
    syncBodies.push(route.request().postDataJSON())
    return route.fulfill({ json: syncQueue.shift() ?? syncResult('UNCHANGED') })
  })
  let catalogRequests = 0
  page.on('request', (request) => { if (request.url().includes('/api/materials/catalog')) catalogRequests += 1 })
  await openMaterials(page)
  await chooseScene(page, 'opt-individual', 'small-scene')
  const syncBar = page.getByRole('group', { name: '폴더 자동 확인' })
  await expect.poll(() => syncBodies.length).toBeGreaterThan(0)
  expect(syncBodies.at(-1)).toEqual({ project_id: projectId, request_id: requestId, environment: 'DISTRIBUTION', force: false })
  await expect(syncBar).toContainText('방금 확인')

  // The 60 s poll reports a change: catalogs are re-read and a short notice appears.
  const catalogsBefore = catalogRequests
  const sentBefore = syncBodies.length
  syncQueue.push(syncResult('REFRESHED', { diff: { added: 1, removed: 0, changed: 0 } }))
  await page.clock.fastForward(60_000)
  await expect.poll(() => syncBodies.length).toBeGreaterThan(sentBefore)
  await expect(syncBar.getByRole('status')).toContainText('새 결과 반영 · Scene +1')
  await expect.poll(() => catalogRequests).toBeGreaterThan(catalogsBefore)
  await expect(page.getByRole('group', { name: '소재 덱 경로' }).getByLabel('Scene', { exact: true })).toHaveValue('small-scene')

  // Files still being copied are not an error; the manual check is forced.
  syncQueue.push(syncResult('FAILED', { code: 'FOLDER_SCHEMA_FILE_BUSY', message: '폴더에 복사 중인 파일이 있습니다.' }))
  const forced = page.waitForRequest((request) => request.url().endsWith('/api/folder-discovery/environments/sync') && request.postDataJSON()?.force === true)
  await syncBar.getByRole('button', { name: '지금 확인' }).click()
  expect((await forced).postDataJSON()).toEqual({ project_id: projectId, request_id: requestId, environment: 'DISTRIBUTION', force: true })
  await expect(syncBar).toContainText('파일 복사 중 · 잠시 후 다시 확인')
  await expect(syncBar).not.toContainText('폴더 확인 필요')
  await expect(page.getByRole('group', { name: '소재 덱 경로' }).getByLabel('Scene', { exact: true })).toHaveValue('small-scene')
  await expect(page.getByRole('button', { name: '저장소 Refresh' })).toHaveCount(0)
})

test('소재 탭은 내 작업 문맥과 뒤로 가기를 유지하고 기존 주소를 새 탭으로 보낸다', async ({ page }) => {
  await mockMaterialsApi(page)
  await openMaterials(page)

  const journey = page.getByRole('navigation', { name: '의뢰 작업 여정' })
  await journey.getByRole('button', { name: 'Case 결과', exact: true }).click()
  await expect(page.getByRole('region', { name: 'SPDM 해석 결과 대시보드' })).toBeVisible()
  await expect.poll(() => new URL(page.url()).searchParams.get('resultTab')).toBeNull()
  await page.goBack()
  await expect(page.getByRole('tab', { name: '소재·물성', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect.poll(() => new URL(page.url()).searchParams.get('resultTab')).toBe('materials')
  await page.goForward()
  await expect(page.getByRole('region', { name: 'SPDM 해석 결과 대시보드' })).toBeVisible()

  await page.goto(`/workspace/materials?project=${projectId}&request=${requestId}`)
  // A full reload re-bootstraps the workspace before the redirected tab renders.
  await expect(page.getByRole('tab', { name: '소재·물성', exact: true })).toHaveAttribute('aria-selected', 'true', { timeout: 15_000 })
  await expect(page).toHaveURL(/\/workspace\/requests\?.*view=case_results.*resultTab=materials/)
})

test('내 작업에서 프로젝트와 의뢰를 바꾸면 소재 조회도 선택 문맥을 따라간다', async ({ page }) => {
  await mockMaterialsApi(page)
  await openMaterials(page)

  const requestCatalog = page.waitForRequest((request) => request.url().includes('/api/materials/catalog') && request.url().includes('request_id=request-showcase-compare'))
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption('request-showcase-compare')
  await requestCatalog
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue('request-showcase-compare')
  await expect.poll(() => new URL(page.url()).searchParams.get('request')).toBe('request-showcase-compare')
  await expect.poll(() => new URL(page.url()).searchParams.get('resultTab')).toBe('materials')

  const projectCatalog = page.waitForRequest((request) => request.url().includes('/api/materials/catalog') && request.url().includes('request_id='))
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption('project-tv-001')
  const catalogRequest = await projectCatalog
  const catalogRequestId = new URL(catalogRequest.url()).searchParams.get('request_id')
  expect(catalogRequestId).toBeTruthy()
  await expect(page.getByLabel('프로젝트 선택', { exact: true })).toHaveValue('project-tv-001')
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue(catalogRequestId!)
  await expect.poll(() => new URL(page.url()).searchParams.get('project')).toBe('project-tv-001')
  await expect.poll(() => new URL(page.url()).searchParams.get('request')).toBe(catalogRequestId)
  await expect.poll(() => new URL(page.url()).searchParams.get('resultTab')).toBe('materials')
  await expect(page.getByRole('tab', { name: '소재·물성', exact: true })).toHaveAttribute('aria-selected', 'true')
})

test('소재 표는 긴 이름과 누락 참조를 보여 주고 같은 Material에서도 Part 문맥을 바꾼다', async ({ page }) => {
  await mockMaterialsApi(page)
  await openMaterials(page)

  await expect(page.getByLabel('소재 덱 환경 선택')).toHaveCount(0)
  const path = page.getByRole('group', { name: '소재 덱 경로' })
  // Single candidates collapse to fixed text; nothing is auto-picked across branches.
  await expect(path.locator('[data-hierarchy-level="Case"]')).toContainText('Package_Model_SetCase2')
  await expect(path.locator('[data-hierarchy-level="하중경우"]')).toContainText('Drop')
  await expect(page.locator('.materials-part-table')).toHaveCount(0)
  await chooseScene(page, 'opt-cumulative')
  await expect(path.locator('[data-hierarchy-level="Scene"]')).toContainText('Run 02 · results')
  await expect(page.getByText('4 / 4 Parts')).toBeVisible()
  await chooseScene(page, 'opt-individual', 'small-scene')
  await expect.poll(() => new URL(page.url()).searchParams.get('case_option')).toBe('opt-individual')

  const rows = page.locator('.materials-part-table tbody tr')
  await expect(rows).toHaveCount(4)
  await expect(page.getByText('4 / 4 Parts')).toBeVisible()
  const longRow = rows.filter({ hasText: 'P-002' })
  await expect(longRow.locator('td').first()).toHaveAttribute('title', new RegExp(longPartName.slice(0, 40)))

  await rows.filter({ hasText: 'P-001' }).click()
  await expect(page.locator('.materials-detail-panel').getByRole('heading', { level: 2 })).toHaveText('Left frame rail')
  await rows.filter({ hasText: 'P-002' }).click()
  const details = page.locator('.materials-detail-panel')
  await expect(details.getByRole('heading', { level: 2 })).toHaveText(longPartName)
  await expect(details).toContainText('Property · SHELL / PROP-B')
  await expect(details).toContainText('같은 Material을 쓰는 Part')
  await expect(details).toContainText('P-001')
  await expect(details).toContainText('P-002')
  await expect(details).toContainText('FAIL-A')
  await expect(details).toContainText('FUN-01')
  await expect(details).toContainText('극값 포함')
  await expect(details).toContainText('추출 대표값 · 단위 정보 없음')
  if (process.env.MATERIALS_SCREENSHOT_PATH) {
    await page.screenshot({ path: process.env.MATERIALS_SCREENSHOT_PATH, fullPage: false })
  }

  await rows.filter({ hasText: 'P-003' }).click()
  await expect(details).toContainText('Material MAT-MISSING을 찾을 수 없습니다.')
  await expect(details).toContainText('누락 · PROP-MISSING')
  await expect.poll(() => new URL(page.url()).searchParams.get('part')).toBe('P-003')

  const search = page.getByLabel('Part 검색')
  await search.fill('P-002')
  await expect(rows).toHaveCount(1)
  await expect.poll(() => new URL(page.url()).searchParams.get('filter')).toBe('P-002')
  await expect.poll(() => new URL(page.url()).searchParams.get('part')).toBe('P-002')
})

test('대량 덱의 Part 딥링크는 해당 페이지를 열고 뒤로가기는 이전 선택을 복원한다', async ({ page }) => {
  await mockMaterialsApi(page)
  await openMaterials(page)

  const deepLink = new URL(page.url())
  deepLink.searchParams.set('scene', 'large-scene')
  deepLink.searchParams.set('part', 'P076')
  deepLink.searchParams.delete('filter')
  await page.goto(`${deepLink.pathname}${deepLink.search}`)

  const rows = page.locator('.materials-part-table tbody tr')
  // A full reload re-bootstraps the workspace (auth, projects, request context) before the deck loads.
  await expect(page.getByText('128 / 128 Parts')).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.materials-pagination')).toContainText('51–100행')
  const deepLinkedRow = rows.filter({ hasText: 'P076' })
  await expect(deepLinkedRow).toHaveCount(1)
  await expect(deepLinkedRow).toHaveAttribute('aria-selected', 'true')
  // An old scene-only link restores its parent path.
  await expect.poll(() => new URL(page.url()).searchParams.get('case_option')).toBe('opt-individual')

  await rows.filter({ hasText: 'P075' }).click()
  await expect.poll(() => new URL(page.url()).searchParams.get('part')).toBe('P075')
  await expect(page.locator('.materials-detail-panel').getByRole('heading', { level: 2 })).toHaveText('Panel 075')
  await page.goBack()
  await expect.poll(() => new URL(page.url()).searchParams.get('part')).toBe('P076')
  await expect(rows.filter({ hasText: 'P076' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.materials-pagination')).toContainText('51–100행')
})

test('소재·물성 탭은 1440·1920 화면과 11·18pt에서 가로 스크롤 없이 경로를 표시한다', async ({ page }) => {
  await mockMaterialsApi(page)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await openMaterials(page)
  await chooseScene(page, 'opt-individual', 'small-scene')
  await expect(page.getByText('4 / 4 Parts')).toBeVisible()
  for (const [width, height] of [[1440, 1000], [1920, 1080]] as const) {
    await page.setViewportSize({ width, height })
    for (const points of [11, 18]) {
      await setWorkspaceFontSize(page, points)
      await expectCaseResultsLayout(page)
      await page.screenshot({ path: join(tmpdir(), `materials-${width}-${points}pt.png`), fullPage: false })
    }
  }
})

const usageCatalog = {
  request_id: requestId,
  environment: 'USAGE',
  scenes: [
    { scene_id: 'usage-settle', label: 'Settle', relative_path: 'usage/Case_A/Settle', hierarchy: {}, kind: 'SCENE', has_deck: true, case_id: 'usage-case', load_case_id: '', execution_run_id: '', run_option_id: '' },
    { scene_id: 'usage-wobble', label: 'Wobble', relative_path: 'usage/Case_A/Wobble', hierarchy: {}, kind: 'SCENE', has_deck: false, case_id: 'usage-case', load_case_id: '', execution_run_id: '', run_option_id: '' },
  ],
  hierarchy: { cases: [{ id: 'usage-case', label: 'Assy_RES_Model_SetCase1', relative_path: 'usage/Case_A' }], load_cases: [], execution_runs: [], run_options: [] },
  conflicts: [],
}

const optistructDeck = {
  solver: 'OPTISTRUCT',
  parts: [
    { id: '2', title: 'open_cell', property_id: '16', material_id: '9', thickness: 1.0, raw_fields: { 'HyperMesh 컴포넌트': 'open_cell' }, source: null },
    { id: '13', title: 'cover_rear', property_id: '53', material_id: '50250', thickness: 1.9, raw_fields: {}, source: null },
  ],
  properties: [
    { id: '16', subtype: 'PSHELL', title: 'Glass_1.0T', fields: { MID1: 9, T: 1.0 }, raw_fields: { MID1: '9', T: '1.0' }, thickness: 1.0, thickness_display: '1', source: { file: 'usage/Case_A/Settle/model.fem', line: 109 } },
    { id: '53', subtype: 'PSHELL', title: 'PCABSED20_1.9t', fields: { MID1: 50250, T: 1.9 }, raw_fields: {}, thickness: 1.9, thickness_display: '1.9', source: { file: 'usage/Case_A/Settle/model.fem', line: 115 } },
  ],
  materials: [
    { id: '9', subtype: 'MAT1', title: 'Glass', fields: { E: 64500, NU: 0.3, RHO: 3.3e-9 }, raw_fields: { RHO: '3.3-9' }, density: { raw: '3.3-9', value: 3.3e-9, unit: 't/mm^3 (추정)', converted_value: 3.3, converted_unit: 'g/cm^3' }, representative_e: 64500, law_id: null, failures: [], source: { file: 'usage/Case_A/Settle/model.fem', line: 153 } },
    { id: '50250', subtype: 'MAT1', title: 'PC+ABS+ED20', fields: { E: 4534, NU: 0.36, 'MATS1.TID': 61065 }, raw_fields: {}, density: { raw: '1.1-9', value: 1.1e-9, unit: 't/mm^3 (추정)', converted_value: 1.1, converted_unit: 'g/cm^3' }, representative_e: 4534, law_id: null, failures: [], source: { file: 'usage/Case_A/Settle/model.fem', line: 167 } },
  ],
  functions: [{ id: '61065', card: 'TABLEMD', title: 'M36_PC+ABS+ED20', points: [{ x: 0, y: 27.4 }, { x: 0.01, y: 50.2 }, { x: 0.2, y: 54 }], point_count: 3, uses: [{ owner_type: 'material', owner_id: '50250', role: 'MATS1 응력-변형률', x_unit: '2열(소성 변형률로 추정)', y_unit: '1열(응력으로 추정)' }], source: { file: 'usage/Case_A/Settle/model.fem', line: 290 } }],
  warnings: [{ code: 'OPTISTRUCT_UNITS_INFERRED', message: 'OptiStruct 입력에는 단위 카드가 없어 밀도 크기로 mm·t·s 단위계를 추정했습니다.', file: null, line: null }],
  unit_system: { input: { mass: 't', length: 'mm', time: 's' }, work: { mass: 't', length: 'mm', time: 's' } },
}

function usageAnalysis(status: string, extra: Record<string, unknown> = {}) {
  return { solver: 'OPTISTRUCT', status, relative_path: 'usage/Case_A/Settle/model.fem', size_bytes: 800 * 1024 * 1024, max_bytes: 2 * 1024 ** 3, bytes_done: 0, bytes_total: 800 * 1024 * 1024, progress: 0, queue_position: 0, parse_seconds: null, parsed_at: null, error_code: null, error_message: null, cached: false, ...extra }
}

test('사용환경 Case 결과의 소재·물성은 사용환경을 유지하고 OptiStruct 분석 진행을 보여 준다', async ({ page }) => {
  await mockResultEnvironments(page, ['USAGE', 'DISTRIBUTION'])
  await page.route('**/api/folder-discovery/environments/sync', (route) => route.fulfill({ json: { status: 'UNCHANGED', changed: false, snapshot_id: null, diff: { added: 0, removed: 0, changed: 0 }, code: null, message: null, check_mode: 'QUICK', checked_at: new Date().toISOString(), coalesced: false } }))
  const catalogEnvironments: string[] = []
  await page.route('**/api/materials/catalog**', (route) => {
    const environment = new URL(route.request().url()).searchParams.get('environment') ?? ''
    catalogEnvironments.push(environment)
    return route.fulfill({ json: environment === 'USAGE' ? usageCatalog : catalog })
  })
  const deckQueries: URLSearchParams[] = []
  const usageSteps = [usageAnalysis('QUEUED', { queue_position: 1 }), usageAnalysis('RUNNING', { bytes_done: 300 * 1024 * 1024, progress: 0.375 })]
  await page.route('**/api/materials/deck**', (route) => {
    const query = new URL(route.request().url()).searchParams
    deckQueries.push(query)
    if (query.get('environment') !== 'USAGE') {
      const scene = catalog.scenes.find((item) => item.scene_id === query.get('scene_id')) ?? catalog.scenes[0]
      return route.fulfill({ json: { scene, files: [], candidate_warnings: [], deck: smallDeck } })
    }
    const scene = usageCatalog.scenes[0]
    const next = usageSteps.shift()
    if (next) return route.fulfill({ json: { scene, files: [{ relative_path: scene.relative_path + '/model.fem', size_bytes: 800 * 1024 * 1024 }], candidate_warnings: [], analysis: next, deck: null } })
    return route.fulfill({ json: { scene, files: [{ relative_path: scene.relative_path + '/model.fem', size_bytes: 800 * 1024 * 1024 }], candidate_warnings: [], analysis: usageAnalysis('READY', { bytes_done: 800 * 1024 * 1024, progress: 1, parse_seconds: 8.4, cached: false }), deck: optistructDeck } })
  })

  await loginWorkspace(page)
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption(projectId)
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption(requestId)
  await page.getByRole('navigation', { name: '의뢰 작업 여정' }).getByRole('button', { name: 'Case 결과', exact: true }).click()
  const toggle = page.getByRole('group', { name: '결과 환경' })
  await toggle.getByRole('button', { name: '사용환경' }).click()
  await expect(toggle.getByRole('button', { name: '사용환경' })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('tab', { name: '소재·물성', exact: true }).click()
  await expect(page.getByRole('tab', { name: '소재·물성', exact: true })).toHaveAttribute('aria-selected', 'true')
  // Opening 소재·물성 keeps 사용환경 (before the fix it switched to 유통환경).
  await expect(toggle.getByRole('button', { name: '사용환경' })).toHaveAttribute('aria-pressed', 'true')
  await expect.poll(() => new URL(page.url()).searchParams.get('result_environment')).not.toBe('DISTRIBUTION')
  await expect.poll(() => catalogEnvironments.at(-1)).toBe('USAGE')
  expect(catalogEnvironments).not.toContain('DISTRIBUTION')

  const path = page.getByRole('group', { name: '소재 덱 경로' })
  await path.getByLabel('Scene', { exact: true }).selectOption('usage-settle')
  await expect(page.getByRole('status').filter({ hasText: '분석' }).first()).toBeVisible()
  await expect(page.getByText('분석 중 · 38%')).toBeVisible()
  await expect(page.getByText('2 / 2 Parts')).toBeVisible({ timeout: 10_000 })
  expect(deckQueries.every((query) => query.get('environment') === 'USAGE')).toBe(true)
  await expect(page.locator('.materials-analysis-source')).toContainText('OptiStruct · model.fem · 800 MB')
  await page.locator('.materials-part-table tbody tr').filter({ hasText: 'cover_rear' }).click()
  const details = page.locator('.materials-detail-panel')
  await expect(details).toContainText('COMP 13')
  await expect(details).toContainText('Material · MAT1 / 50250')
  await expect(details).toContainText('TABLEMD 61065')
  await expect(details).toContainText('원본 RHO')
  if (process.env.MATERIALS_SCREENSHOT_PATH) await page.screenshot({ path: process.env.MATERIALS_SCREENSHOT_PATH.replace(/\.png$/, '-usage.png'), fullPage: false })

  // Leaving 소재·물성 returns to the same environment.
  await page.getByRole('tab', { name: '요약', exact: true }).click()
  await expect.poll(() => new URL(page.url()).searchParams.get('resultTab')).toBeNull()
  await expect(toggle.getByRole('button', { name: '사용환경' })).toHaveAttribute('aria-pressed', 'true')
  await expect.poll(() => new URL(page.url()).searchParams.get('result_environment')).not.toBe('DISTRIBUTION')

  // Switching the environment inside 소재·물성 keeps the tab and reads the distribution catalog.
  await page.getByRole('tab', { name: '소재·물성', exact: true }).click()
  await toggle.getByRole('button', { name: '유통환경' }).click()
  await expect(page.getByRole('tab', { name: '소재·물성', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect.poll(() => catalogEnvironments.at(-1)).toBe('DISTRIBUTION')
})

test('사용환경 입력 파일 분석 실패는 원인과 다시 분석을 보여 준다', async ({ page }) => {
  await mockResultEnvironments(page, ['USAGE'])
  await page.route('**/api/folder-discovery/environments/sync', (route) => route.fulfill({ json: { status: 'UNCHANGED', changed: false, snapshot_id: null, diff: { added: 0, removed: 0, changed: 0 }, code: null, message: null, check_mode: 'QUICK', checked_at: new Date().toISOString(), coalesced: false } }))
  await page.route('**/api/materials/catalog**', (route) => route.fulfill({ json: usageCatalog }))
  const retries: string[] = []
  await page.route('**/api/materials/deck**', (route) => {
    const query = new URL(route.request().url()).searchParams
    retries.push(query.get('retry') ?? '')
    const scene = usageCatalog.scenes[0]
    const analysis = query.get('retry') === 'true'
      ? usageAnalysis('READY', { progress: 1, parse_seconds: 1.2 })
      : usageAnalysis('FAILED', { error_code: 'MATERIALS_INCLUDE_CYCLE', error_message: 'INCLUDE 파일 사이에 순환 참조가 있습니다.', cached: true })
    return route.fulfill({ json: { scene, files: [], candidate_warnings: [], analysis, deck: analysis.status === 'READY' ? optistructDeck : null } })
  })
  await loginWorkspace(page)
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption(projectId)
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption(requestId)
  await page.getByRole('navigation', { name: '의뢰 작업 여정' }).getByRole('button', { name: 'Case 결과', exact: true }).click()
  await page.getByRole('tab', { name: '소재·물성', exact: true }).click()
  await page.getByRole('group', { name: '소재 덱 경로' }).getByLabel('Scene', { exact: true }).selectOption('usage-settle')
  const failure = page.locator('.materials-analysis--failed')
  await expect(failure).toContainText('입력 파일을 분석하지 못했습니다.')
  await expect(failure).toContainText('순환 참조')
  await failure.getByRole('button', { name: '다시 분석' }).click()
  await expect(page.getByText('2 / 2 Parts')).toBeVisible()
  expect(retries).toContain('true')
})
