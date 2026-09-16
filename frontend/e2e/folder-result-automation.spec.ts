import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { loginWorkspace } from './workspace-test-helpers'

async function fixture(page: Page, info: TestInfo, configured: boolean) {
  const suffix = `${Date.now()}-${info.workerIndex}`
  const root = info.outputPath('source')
  const projectPath = `P${suffix}_자동화 프로젝트`
  const requestPath = `${projectPath}/R002_동일 의뢰`
  const loadPaths = [`${requestPath}/A_001_동일 하중`, `${requestPath}/B_001_동일 하중`]
  for (const [index, loadPath] of loadPaths.entries()) {
    mkdirSync(join(root, loadPath, 'results'), { recursive: true })
    writeFileSync(join(root, loadPath, 'results/result.csv'), `force\n${42 + index}\n`)
    if (configured && index === 0) writeFileSync(join(root, loadPath, 'results/unrelated.csv'), 'unknown\n1\n')
  }
  await loginWorkspace(page, 'e2e-admin', '/workspace/catalog/schemas')
  const create = async (endpoint: string, data: unknown) => {
    const response = await page.request.post(`/api/semantic-mapping/${endpoint}`, { data })
    expect(response.ok(), await response.text()).toBeTruthy()
    return response.json()
  }
  const item = await create('items', { definition: { key: `auto_force_${suffix.replaceAll('-', '_')}`, label: '자동 하중', kind: 'scalar', unit: 'N' } })
  const template = await create('templates', { name: `자동 결과 ${suffix}`, definition: { widgets: [{ id: 'auto-force', type: 'kpi', title: '자동 하중 카드', item_ids: [item.id] }] } })
  const recipe = await create('recipes', {
    name: `폴더 자동 CSV ${suffix}`,
    definition: { format: 'csv', display_template_id: template.id, display_template_version: 1, mappings: [{ source: 'force', source_unit: 'N', result_item_id: item.id }] },
    sample_filename: 'result.csv', sample_content_base64: Buffer.from('force\n42\n').toString('base64'),
  })
  await create('activate-bundle', { recipe_id: recipe.id, recipe_version: 1, template_id: template.id, template_version: 1 })
  const storage = await page.request.put('/api/storage/config', { data: { root } })
  expect(storage.ok(), await storage.text()).toBeTruthy()
  const rules = [
    { depth: 1, role: 'PROJECT', delimiter: '_', code_token: 1, name_from_token: 2 },
    { depth: 2, role: 'REQUEST', delimiter: '_', code_token: 1, name_from_token: 2 },
    { depth: 3, role: 'LOAD_CASE', delimiter: '_', code_token: 2, name_from_token: 3, analysis_type: 'DROP' },
    { depth: 4, role: 'RESULTS', delimiter: '', keyword: 'results', ...(configured ? { result_config: { recipe_ids: [recipe.id] } } : {}) },
  ]
  const saved = await page.request.put('/api/folder-discovery/rules', { data: { relative_path: '', rules, expected_revision: 0 } })
  expect(saved.ok(), await saved.text()).toBeTruthy()
  await page.goto('/workspace/catalog/schemas')
  await page.getByRole('button', { name: '폴더 조사·업무 생성', exact: true }).click()
  return { suffix, root, loadPaths, recipe, template, rules, screen: page.getByTestId('folder-discovery-workspace') }
}

test('confirmed folders process results without reentering paths and distinguish duplicate task labels', async ({ page }, info) => {
  test.setTimeout(180_000)
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const data = await fixture(page, info, true)
  await data.screen.getByRole('button', { name: '전체 트리 조사', exact: true }).click()
  await data.screen.getByRole('button', { name: '업무 생성 미리보기', exact: true }).click()
  await expect(data.screen.locator('.folder-discovery-preview')).toContainText('동일 하중')
  const applying = page.waitForResponse((response) => response.url().endsWith('/folder-discovery/apply'))
  await data.screen.getByRole('button', { name: '검토한 업무 생성 적용', exact: true }).click()
  const applied = await applying
  expect(applied.ok(), await applied.text()).toBeTruthy()
  const outcome = await applied.json()
  expect(outcome.bindings.created_ids).toHaveLength(2)
  const catalog = await (await page.request.get('/api/semantic-mapping/catalog')).json()
  const binding = catalog.bindings.find((row: { relative_path: string }) => row.relative_path === `${data.loadPaths[0]}/results`)
  expect(binding).toBeTruthy()
  const casesResponse = await page.request.get(`/api/requests/${binding.request_id}/load-cases`)
  const cases = await casesResponse.json()
  expect(cases).toHaveLength(2)
  expect(cases.every((row: { selection_metadata: { code: string } }) => row.selection_metadata.code === '001')).toBe(true)
  const resultPath = `${data.loadPaths[0]}/results`
  const row = data.screen.getByTestId('folder-connection-row').filter({ hasText: resultPath })
  const refreshing = page.waitForResponse((response) => response.url().endsWith(`/load-cases/${binding.load_case_id}/results/refresh`) && response.request().method() === 'POST')
  await row.getByRole('button', { name: `${resultPath} 결과 조회`, exact: true }).click()
  const refresh = await refreshing
  expect(refresh.ok(), await refresh.text()).toBeTruthy()
  const processed = await refresh.json()
  expect(processed.display_run_id).toBeTruthy()
  expect(processed.results.some((row: { status: string }) => row.status === 'IMPORTED')).toBe(true)
  await expect(data.screen.getByTestId('result-widget-display').locator('.semantic-result-kpi strong')).toHaveText('42.00')
  const files = data.screen.getByTestId('folder-result-file-status')
  const unresolvedFile = files.getByRole('row').filter({ hasText: 'unrelated.csv' })
  await expect(unresolvedFile).toContainText('UNMAPPED')
  await expect(unresolvedFile).not.toContainText('run-')
  await expect(unresolvedFile.getByRole('link', { name: '결과 검토' })).toHaveCount(0)
  const repeated = await page.request.post(`/api/semantic-mapping/load-cases/${binding.load_case_id}/results/refresh`)
  expect(repeated.ok(), await repeated.text()).toBeTruthy()
  expect((await repeated.json()).display_run_id).toBe(processed.display_run_id)
  const reviewLink = files.getByRole('row').filter({ hasText: '/results/result.csv' }).getByRole('link', { name: '결과 검토', exact: true })
  await expect(reviewLink).toHaveAttribute('href', new RegExp(`loadCase=${binding.load_case_id}&run=${processed.display_run_id}`))
  await reviewLink.click()
  const panel = page.getByRole('region', { name: '레시피로 연결한 결과' })
  await expect(panel.locator('.semantic-result-kpi strong')).toHaveText('42.00')
  const picker = page.getByLabel('하중 경우 선택', { exact: true })
  await expect(picker).toHaveValue(binding.load_case_id)
  const labels = await picker.locator('option').allTextContents()
  expect(labels.filter((label) => label.includes('001') && label.includes('동일 하중'))).toHaveLength(2)
  expect(new Set(labels).size).toBe(labels.length)
  await expect(page.getByLabel('의뢰 선택', { exact: true }).locator('option:checked')).toContainText('R002')
  // The regular results workspace must import a changed file and select its new exact Run.
  await page.getByRole('navigation', { name: '의뢰 작업 여정' }).getByRole('button', { name: '의뢰 개요', exact: true }).click()
  await expect(panel).toBeHidden()
  writeFileSync(join(data.root, data.loadPaths[0], 'results/result.csv'), 'force\n84\n')
  const workspaceRefresh = page.waitForResponse((response) => response.url().endsWith(`/load-cases/${binding.load_case_id}/results/refresh`) && response.request().method() === 'POST')
  await page.getByRole('button', { name: '현재 하중 경우 결과 파일 확인', exact: true }).click()
  const workspaceResponse = await workspaceRefresh
  expect(workspaceResponse.ok(), await workspaceResponse.text()).toBeTruthy()
  const changed = await workspaceResponse.json()
  expect(changed.display_run_id).not.toBe(processed.display_run_id)
  await expect(panel.locator('.semantic-result-kpi strong')).toHaveText('84.00')
  await expect(page).toHaveURL(new RegExp(`run=${changed.display_run_id}`))
  await panel.scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('automatic-folder-result.png') })
  await page.reload()
  await expect(panel.locator('.semantic-result-kpi strong')).toHaveText('84.00')
  await page.setViewportSize({ width: 390, height: 844 })
  await panel.scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('automatic-folder-result-mobile.png') })
  // Refresh chooses the saved Run's template, so a prior display override must clear.
  await page.setViewportSize({ width: 1280, height: 720 })
  await page.goto('/workspace/catalog/schemas')
  const semantic = page.locator('.semantic-page')
  await semantic.getByRole('button', { name: '3. 결과 조회', exact: true }).click()
  await semantic.getByLabel('프로젝트', { exact: true }).selectOption(binding.project_id)
  await semantic.getByLabel('의뢰', { exact: true }).selectOption(binding.request_id)
  await semantic.getByLabel('하중 경우', { exact: true }).selectOption(binding.load_case_id)
  await semantic.getByLabel('템플릿', { exact: true }).selectOption(data.template.id)
  await semantic.getByRole('button', { name: '파일 확인·결과 조회', exact: true }).click()
  await expect(semantic.getByLabel('Run ID (선택)', { exact: true })).toHaveValue(changed.display_run_id)
  await expect(semantic.getByLabel('템플릿', { exact: true })).toHaveValue('')
  await expect(semantic.getByTestId('result-widget-display').locator('.semantic-result-kpi strong')).toHaveText('84.00')
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  expect(errors).toEqual([])
})

test('existing result folders accept one reviewed bulk configuration without recreating tasks', async ({ page }, info) => {
  test.setTimeout(150_000)
  const data = await fixture(page, info, false)
  await data.screen.getByRole('button', { name: '전체 트리 조사', exact: true }).click()
  await data.screen.getByRole('button', { name: '업무 생성 미리보기', exact: true }).click()
  await expect(data.screen.locator('.folder-discovery-preview')).toContainText('읽기 설정 필요')
  await data.screen.getByRole('button', { name: '검토한 업무 생성 적용', exact: true }).click()
  await expect(data.screen.getByRole('status').filter({ hasText: '프로젝트 1' })).toBeVisible()
  const bulk = data.screen.getByTestId('folder-result-bulk-config')
  const firstPath = `${data.loadPaths[0]}/results`
  for (const path of data.loadPaths) await bulk.getByLabel(`${path}/results 일괄 설정 대상`, { exact: true }).check()
  await bulk.getByLabel(`일괄 결과 레시피 ${data.recipe.name ?? `폴더 자동 CSV ${data.suffix}`}`, { exact: true }).check()
  await bulk.getByRole('button', { name: '설정 영향 미리보기', exact: true }).click()
  const apply = bulk.getByRole('button', { name: '일괄 적용', exact: true })
  await expect(apply).toBeEnabled()
  // A changed target must invalidate the preview instead of applying unreviewed settings.
  await bulk.getByLabel(`${firstPath} 일괄 설정 대상`, { exact: true }).uncheck()
  await expect(apply).toBeDisabled()
  await bulk.getByLabel(`${firstPath} 일괄 설정 대상`, { exact: true }).check()
  await bulk.getByRole('button', { name: '설정 영향 미리보기', exact: true }).click()
  await expect(apply).toBeEnabled()
  const applying = page.waitForResponse((response) => response.url().endsWith('/connections/result-config/apply'))
  await apply.click()
  const response = await applying
  expect(response.ok(), await response.text()).toBeTruthy()
  const catalog = await (await page.request.get('/api/semantic-mapping/catalog')).json()
  const bindings = catalog.bindings.filter((row: { relative_path: string }) => data.loadPaths.some((path) => row.relative_path === `${path}/results`))
  expect(bindings).toHaveLength(2)
  const binding = bindings.find((row: { relative_path: string }) => row.relative_path === firstPath)
  const row = data.screen.getByTestId('folder-connection-row').filter({ hasText: firstPath })
  await expect(row.getByRole('button', { name: `${firstPath} 결과 조회`, exact: true })).toBeEnabled()
  await row.getByRole('button', { name: `${firstPath} 결과 조회`, exact: true }).click()
  await expect(data.screen.getByTestId('result-widget-display').locator('.semantic-result-kpi strong')).toHaveText('42.00')
  const cases = await (await page.request.get(`/api/requests/${binding.request_id}/load-cases`)).json()
  expect(cases).toHaveLength(2)
  await page.screenshot({ path: info.outputPath('existing-folder-bulk-results.png') })
})

test('a viewer can read saved results but cannot trigger file imports', async ({ page }) => {
  await loginWorkspace(page, 'e2e-viewer', '/workspace/requests?project=project-tv-001&request=request-drop-001&loadCase=loadcase-drop-bottom-001&view=custom')
  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveValue('loadcase-drop-bottom-001')
  await expect(page.getByRole('button', { name: '현재 하중 경우 결과 파일 확인', exact: true })).toBeDisabled()
  const stored = await page.request.get('/api/semantic-mapping/results?load_case_id=loadcase-drop-bottom-001')
  expect(stored.ok(), await stored.text()).toBeTruthy()
  const importing = await page.request.post('/api/semantic-mapping/load-cases/loadcase-drop-bottom-001/results/refresh')
  expect(importing.status()).toBe(403)
})
