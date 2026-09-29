import { expect, test, type Page } from '@playwright/test'
import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

const requestId = 'request-showcase-waiting'
const projectId = 'project-feature-showcase'

const catalog = {
  scenes: [
    { scene_id: 'small-scene', label: 'Compact deck', relative_path: 'model/compact', hierarchy: {}, has_deck: true },
    { scene_id: 'large-scene', label: 'Large deck', relative_path: 'model/large', hierarchy: {}, has_deck: true },
  ],
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
  await openWorkspaceRoute(page, '/workspace/materials')
  await expect(page.getByRole('heading', { name: '모델 소재·물성', exact: true })).toBeVisible()
  await expect(page.getByLabel('소재 덱 의뢰 선택')).toHaveValue(requestId)
  await expect(page.locator('.request-workspace-header')).toHaveCount(0)
}

test('소재 표는 긴 이름과 누락 참조를 보여 주고 같은 Material에서도 Part 문맥을 바꾼다', async ({ page }) => {
  await mockMaterialsApi(page)
  await openMaterials(page)

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
  await expect(page.getByText('128 / 128 Parts')).toBeVisible()
  await expect(page.locator('.materials-pagination')).toContainText('51–100행')
  const deepLinkedRow = rows.filter({ hasText: 'P076' })
  await expect(deepLinkedRow).toHaveCount(1)
  await expect(deepLinkedRow).toHaveAttribute('aria-selected', 'true')

  await rows.filter({ hasText: 'P075' }).click()
  await expect.poll(() => new URL(page.url()).searchParams.get('part')).toBe('P075')
  await expect(page.locator('.materials-detail-panel').getByRole('heading', { level: 2 })).toHaveText('Panel 075')
  await page.goBack()
  await expect.poll(() => new URL(page.url()).searchParams.get('part')).toBe('P076')
  await expect(rows.filter({ hasText: 'P076' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.materials-pagination')).toContainText('51–100행')
})
