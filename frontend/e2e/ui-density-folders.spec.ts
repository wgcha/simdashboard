import { expect, test, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'

import { loginWorkspace, setWorkspaceFontSize } from './workspace-test-helpers'

const profile = { id: 'usage-default', environment: 'USAGE', name: '사용환경 기본', revision: 1, rules: { rules: [{ role_kind: 'SIMULATION_CASE', parent_role: 'REQUEST', pattern: 'Assy*', match_mode: 'glob' }] }, active: true }
const nodes = [
  { id: 'n-project', name: 'Project_A', relative_path: 'Project_A', parent_path: '', depth: 0, file_count: 0, allowed_roles: ['PROJECT'], role_kind: 'PROJECT', status: 'CONFIRMED', message: '프로젝트' },
  { id: 'n-request', name: 'WR_1042_SimType1', relative_path: 'Project_A/WR_1042_SimType1', parent_path: 'Project_A', depth: 1, file_count: 0, allowed_roles: ['REQUEST'], role_kind: 'REQUEST', status: 'CONFIRMED', message: '의뢰' },
  { id: 'n-case', name: 'Assy_RES_Main', relative_path: 'Project_A/WR_1042_SimType1/Assy_RES_Main', parent_path: 'Project_A/WR_1042_SimType1', depth: 2, file_count: 1, allowed_roles: ['SIMULATION_CASE'], role_kind: 'SIMULATION_CASE', status: 'CONFIRMED', message: 'Case' },
]

const depthSchema = {
  schema_set_id: 'dss-e2e', created_at: '2026-10-02T00:00:00Z', created_by: 'e2e-admin',
  upper: { levels: [{ level: 1, role: 'PROJECT' }, { level: 2, role: 'REQUEST' }] },
  environments: {
    USAGE: { profile_id: 'dss-e2e-usage', environment_keyword: '사용', lower: { levels: [{ level: 1, role: 'WORKING', fixed_name: 'Working' }, { level: 2, role: 'SIMULATION_CASE' }, { level: 3, role: 'SCENE' }], below_last: 'CONTENT' }, usage_sources: null },
    DISTRIBUTION: { profile_id: 'dss-e2e-distribution', environment_keyword: '유통', lower: { levels: [{ level: 1, role: 'WORKING', fixed_name: 'Working' }, { level: 2, role: 'SIMULATION_CASE' }, { level: 3, role: 'LOAD_CASE' }, { level: 4, role: 'EXECUTION_RUN' }, { level: 5, role: 'RUN_OPTION' }, { level: 6, role: 'SCENE' }], below_last: 'CONTENT' }, usage_sources: null },
  },
}

async function setFont(page: Page, _current: number, target: number) {
  await setWorkspaceFontSize(page, target)
}

async function mockFolderSurfaces(page: Page) {
  await page.route('**/api/folder-discovery/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname
    if (pathname.endsWith('/environments/depth-schema')) return route.fulfill({ json: depthSchema })
    if (pathname.endsWith('/environments/depth-schema/samples')) return route.fulfill({ json: { levels: [{ level: 1, folder_count: 1, samples: [{ name: 'Project_A', count: 1 }], truncated: false }], requests_sampled: 1 } })
    if (pathname.endsWith('/environments')) return route.fulfill({ json: { items: [profile] } })
    if (pathname.endsWith('/environments/history')) return route.fulfill({ json: { items: [], offset: 0, limit: 50, total: 0 } })
    if (pathname.endsWith('/browse')) return route.fulfill({ json: { configured: true, relative_path: '', root_path: 'D:/synthetic', entries: [{ name: 'Project_A', relative_path: 'Project_A', is_directory: true }] } })
    if (pathname.endsWith('/catalog')) return route.fulfill({ json: { roles: [{ key: 'PROJECT', label: '프로젝트', kind: 'PROJECT', active: true }, { key: 'REQUEST', label: '의뢰', kind: 'REQUEST', active: true }, { key: 'LOAD_CASE', label: '하중 경우', kind: 'LOAD_CASE', active: true }], analysis_types: [{ key: 'DROP', label: 'DROP', active: true }] } })
    if (pathname.endsWith('/rules')) return route.fulfill({ json: { rules: [], revision: 0 } })
    if (pathname.endsWith('/saved-rules') || pathname.endsWith('/history') || pathname.endsWith('/connections')) return route.fulfill({ json: { items: [], offset: 0, limit: 100, total: 0 } })
    if (pathname.endsWith('/environments/scan')) return route.fulfill({ json: { id: 'scan-1', environment: 'USAGE', profile_id: profile.id, relative_path: 'Project_A', status: 'COMPLETE', nodes, issues: [] } })
    return route.fulfill({ json: {} })
  })
  await page.route('**/api/projects', (route) => route.fulfill({ json: [{ id: 'project-tv-001', name: 'Demo Project' }] }))
  await page.route('**/api/projects/project-tv-001/requests', (route) => route.fulfill({ json: [{ id: 'request-drop-001', project_id: 'project-tv-001', title: 'WR-1042 의뢰', status: 'READY' }] }))
  await page.route('**/api/requests/request-drop-001/load-cases', (route) => route.fulfill({ json: [] }))
}

for (const { name, width, height } of [{ name: 'desktop', width: 1366, height: 768 }, { name: 'wide', width: 1920, height: 1080 }]) {
test(`P4 ${name} folder roots scale by role, keep controls usable, and contain their picker`, async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await mockFolderSurfaces(page)
  await page.setViewportSize({ width, height })
  await loginWorkspace(page, 'e2e-admin', '/workspace/catalog/schemas')

  const environment = page.locator('.folder-environment-workspace')
  await expect(environment).toHaveAttribute('data-ui-density', 'v1')
  let current = 14
  for (const pt of [14, 18]) {
    await setFont(page, current, pt)
    current = pt
    for (const [theme, label] of [['light', '라이트'], ['dark', '다크']] as const) {
      await page.getByRole('button', { name: label, exact: true }).click()
      await expect(page.locator('.app-shell')).toHaveAttribute('data-theme', theme)
      const measured = await environment.evaluate((element) => {
        const font = (selector: string) => parseFloat(getComputedStyle(element.querySelector(selector)!).fontSize)
        return { title: font('h1'), body: font('select'), control: font('.saved-work-tabs button') }
      })
      const body = pt * 4 / 3
      expect(measured.title).toBeCloseTo(body * 1.5, 1)
      expect(measured.body).toBeCloseTo(body, 1)
      expect(measured.control).toBeCloseTo(body, 1)
      await expect(environment.getByRole('button', { name: '폴더 찾아보기', exact: true })).toBeVisible()
      const minimum = await environment.getByRole('button', { name: '조사 시작', exact: true }).evaluate((element) => parseFloat(getComputedStyle(element).minHeight))
      expect(minimum).toBeGreaterThanOrEqual(Math.max(32, body * 12 / 7) - .1)
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      if (process.env.GUI_QA_OUTPUT_DIR && pt === 18) {
        mkdirSync(process.env.GUI_QA_OUTPUT_DIR, { recursive: true })
        await page.screenshot({ path: path.join(process.env.GUI_QA_OUTPUT_DIR, `p4-folders-${name}-${theme}-${pt}.png`) })
      }
    }
  }

  await environment.getByRole('button', { name: '저장된 규칙', exact: true }).click()
  await expect(environment.getByRole('heading', { name: '저장된 규칙' })).toBeVisible()
  // DEPTH_V1 editor: one table per segment and exactly two actions (확인 · 저장).
  await expect(environment.getByRole('table', { name: '상위 구조 깊이 표' })).toBeVisible()
  await expect(environment.getByRole('button', { name: '확인', exact: true })).toBeVisible()
  await expect(environment.getByRole('button', { name: '저장', exact: true })).toBeVisible()
  await expect(environment.getByLabel('편집할 규칙')).toHaveCount(0)

  await page.getByRole('button', { name: '폴더 연결', exact: true }).click()
  await environment.locator('input[placeholder="비우면 저장소 전체"]').fill('Project_A')
  await environment.getByRole('button', { name: '조사 시작', exact: true }).click()
  await expect(environment.getByRole('heading', { name: '구조 확인' })).toBeVisible()
  await expect.poll(async () => Number.parseFloat(await environment.getByRole('heading', { name: '구조 확인' }).evaluate((element) => getComputedStyle(element).fontSize))).toBeCloseTo(30, 1)
  // The selected folder's role is read-only text (no select) and stays at least caption-sized.
  const roleText = environment.locator('.folder-node-detail').getByLabel('선택 폴더 역할')
  await expect(roleText).toBeVisible()
  await expect(environment.locator('.folder-node-detail select[aria-label="선택 폴더 역할"]')).toHaveCount(0)
  const roleFont = await roleText.evaluate((element) => parseFloat(getComputedStyle(element).fontSize))
  const captionFont = await environment.locator('.folder-node-detail code').first().evaluate((element) => parseFloat(getComputedStyle(element).fontSize))
  expect(roleFont).toBeGreaterThanOrEqual(captionFont - .1)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  if (process.env.GUI_QA_OUTPUT_DIR) await page.screenshot({ path: path.join(process.env.GUI_QA_OUTPUT_DIR, `p4-folders-${name}-structure.png`) })

  await page.getByRole('tab', { name: '의미 매핑', exact: true }).click()
  await page.getByRole('button', { name: '폴더 조사·업무 생성', exact: true }).click()
  const discovery = page.getByTestId('folder-discovery-workspace')
  await expect(discovery).toHaveAttribute('data-ui-density', 'v1')
  await discovery.getByRole('button', { name: '최상위 폴더 선택', exact: true }).click()
  const picker = page.getByRole('dialog', { name: '최상위 폴더 선택' })
  await expect(picker).toBeVisible()
  await expect.poll(async () => Number.parseFloat(await picker.getByRole('heading', { name: '조사할 최상위 폴더 선택' }).evaluate((element) => getComputedStyle(element).fontSize))).toBeCloseTo(30, 1)
  expect(await picker.evaluate((element) => Boolean(element.closest('[data-ui-density="v1"]')))).toBe(true)
  if (process.env.GUI_QA_OUTPUT_DIR) await page.screenshot({ path: path.join(process.env.GUI_QA_OUTPUT_DIR, `p4-folders-${name}-picker.png`) })
  await picker.press('Escape')
  await expect(picker).toBeHidden()
  expect(errors).toEqual([])
})
}
