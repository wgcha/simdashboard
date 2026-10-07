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
