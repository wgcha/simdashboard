import { expect, test, type Page } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { loginWorkspace } from './workspace-test-helpers'

async function fixture(page: Page) {
  const token = `${Date.now()}_${Math.random().toString(16).slice(2, 8)}`
  const directory = `review_${token}`
  const root = path.resolve('../output/qa/browser-storage')
  mkdirSync(path.join(root, directory), { recursive: true })
  writeFileSync(path.join(root, directory, 'result.csv'), 'force\n42\n')
  const configured = await page.request.put('/api/storage/config', { data: { root } })
  expect(configured.status(), await configured.text()).toBe(200)
  const create = async (endpoint: string, data: unknown) => {
    const response = await page.request.post(`/api/semantic-mapping/${endpoint}`, { data })
    expect(response.status(), await response.text()).toBeLessThan(300)
    return response.json()
  }
  const item = await create('items', { definition: { key: `review_force_${token}`, label: '검토 하중', kind: 'scalar', unit: 'N' } })
  const definition = { format: 'csv', mappings: [{ source: 'force', source_unit: 'N', result_item_id: item.id }] }
  const sample = { sample_filename: 'sample.csv', sample_content_base64: Buffer.from('force\n42\n').toString('base64') }
  const recipe = await create('recipes', { name: `검토 레시피 ${token}`, definition, ...sample })
  const alternate = await create('recipes', { name: `대체 레시피 ${token}`, definition, ...sample })
  const template = await create('templates', { name: `검토 템플릿 ${token}`, definition: { widgets: [{ id: 'force', type: 'kpi', title: '검토 하중 카드', item_ids: [item.id] }] } })
  await create('activate-bundle', { recipe_id: recipe.id, recipe_version: 1, template_id: template.id, template_version: 1 })
  await create('activate-bundle', { recipe_id: alternate.id, recipe_version: 1, template_id: template.id, template_version: 1, expected_template_active_version: 1 })
  const binding = await create('bindings', { relative_path: directory, project_id: 'project-tv-001', request_id: 'request-drop-001', load_case_id: 'loadcase-drop-bottom-001', role: 'RESULTS', recipe_ids: [recipe.id, alternate.id], template_id: template.id })
  const refresh = await create(`bindings/${binding.id}/refresh`, {})
  expect(refresh.results[0].status).toBe('AMBIGUOUS')
  return { recipe, alternate, template, binding, directory }
}

test('real ambiguous file requires revalidation and explicit import; bound impact renders and waits for confirmation', async ({ page }) => {
  test.setTimeout(180_000)
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await loginWorkspace(page, 'e2e-admin', '/workspace/catalog/schemas')
  const data = await fixture(page)
  await page.goto('/workspace/catalog/schemas')
  await expect(page.getByRole('heading', { name: '결과 의미 연결' })).toBeVisible()
  expect(await page.title()).toBeTruthy()
  await page.getByRole('button', { name: '2. 폴더 연결', exact: true }).click()
  const row = page.locator('.binding-list article').filter({ hasText: data.directory })
  await row.getByRole('button', { name: '검토함', exact: true }).click()
  const queue = page.locator('.semantic-review-queue')
  await queue.getByRole('button', { name: /result.csv/ }).click()
  const choice = queue.getByLabel('검증 레시피')
  await choice.selectOption(`${data.recipe.id}:1`)
  const confirm = queue.getByRole('button', { name: '명시적으로 등록', exact: true })
  await expect(confirm).toBeDisabled()
  await queue.getByRole('button', { name: '서버 재검증', exact: true }).click()
  await expect(queue.locator('.semantic-result-kpi strong')).toHaveText('42.00')
  await expect(confirm).toBeEnabled()
  await choice.selectOption(`${data.alternate.id}:1`)
  await expect(confirm).toBeDisabled()
  await choice.selectOption(`${data.recipe.id}:1`)
  await expect(confirm).toBeEnabled()
  await page.screenshot({ path: '../output/qa/review-ready-desktop.png', fullPage: true })
  const confirmed = page.waitForResponse(response => response.url().endsWith('/confirm') && response.request().method() === 'POST')
  await confirm.click()
  const result = await (await confirmed).json()
  expect(result.status).toBe('IMPORTED')
  expect(result.run_id).toBeTruthy()
  await queue.getByLabel('검토 상태').selectOption('IMPORTED')
  await queue.getByRole('button', { name: /result.csv/ }).click()
  await queue.locator('summary').click()
  await expect(queue.locator('.semantic-review-history')).toContainText('IMPORTED')
  await queue.getByRole('button', { name: '다시 검토', exact: true }).click()
  await expect(confirm).toBeDisabled()
  await queue.getByLabel('검증 레시피').selectOption(`${data.recipe.id}:1`)
  await queue.getByRole('button', { name: '서버 재검증', exact: true }).click()
  await expect(confirm).toBeEnabled()
  const duplicate = page.waitForResponse(response => response.url().endsWith('/confirm') && response.request().method() === 'POST')
  await confirm.click()
  const reused = await (await duplicate).json()
  expect(reused.status).toBe('SKIPPED')
  expect(reused.run_id).toBe(result.run_id)
  await page.reload()
  await page.getByLabel('저장된 레시피').selectOption(data.recipe.id)
  await page.getByLabel('저장된 템플릿').selectOption(data.template.id)
  await page.locator('.file-drop input').setInputFiles({ name: 'sample.csv', mimeType: 'text/csv', buffer: Buffer.from('force\n42\n') })
  await page.getByRole('button', { name: '미리보기 실행', exact: true }).click()
  await expect(page.locator('.semantic-result-kpi strong')).toHaveText('42.00')
  // Opening a sparse API-created definition fills the editor's explicit defaults.
  // Save the displayed configuration before asking to activate its exact version.
  const saved = page.waitForResponse(response => response.url().endsWith('/recipes') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '레시피 새 버전 저장', exact: true }).click()
  expect((await saved).status()).toBeLessThan(300)
  let activations = 0
  page.on('request', request => { if (request.url().endsWith('/activate-bundle')) activations += 1 })
  await page.getByRole('button', { name: /함께 활성화/ }).click()
  const dialog = page.getByRole('dialog', { name: '활성화 영향 미리보기' })
  await expect(dialog.getByText(data.directory, { exact: true })).toBeVisible()
  await expect(dialog.getByRole('button', { name: '검토 후 활성화', exact: true })).toBeEnabled()
  expect(activations).toBe(0)
  await page.screenshot({ path: '../output/qa/impact-desktop.png' })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: '../output/qa/impact-mobile.png' })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: '검토 후 활성화', exact: true }).click()
  await expect(dialog).toBeHidden()
  expect(activations).toBe(1)
  expect(errors).toEqual([])
  await page.context().clearCookies()
  await loginWorkspace(page, 'e2e-viewer')
  expect((await page.request.get(`/api/semantic-mapping/bindings/${data.binding.id}/review-items`)).status()).toBe(403)
})

test('late review listing cannot populate a different binding', async ({ page }) => {
  await loginWorkspace(page, 'e2e-admin', '/workspace/catalog/schemas')
  const data = await fixture(page)
  await page.goto('/workspace/catalog/schemas')
  await page.getByRole('button', { name: '2. 폴더 연결', exact: true }).click()
  let release: (() => void) | undefined
  let entered: (() => void) | undefined
  const started = new Promise<void>(resolve => { entered = resolve })
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route(`**/bindings/${data.binding.id}/review-items?**`, async route => {
    const response = await route.fetch()
    entered?.()
    await gate
    await route.fulfill({ response })
  })
  const row = page.locator('.binding-list article').filter({ hasText: data.directory })
  await row.getByRole('button', { name: '검토함', exact: true }).click()
  await started
  await row.getByRole('button', { name: '검토함 닫기', exact: true }).click()
  release?.()
  await expect(page.locator('.semantic-review-queue')).toHaveCount(0)
  await page.unrouteAll({ behavior: 'wait' })
  await row.getByRole('button', { name: '검토함', exact: true }).click()
  await expect(page.locator('.semantic-review-queue').getByRole('button', { name: /result.csv/ })).toBeVisible()
})
