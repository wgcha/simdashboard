import { expect, test } from '@playwright/test'
import { loginWorkspace } from './workspace-test-helpers'

test('folder completion links retain binding ownership and exclude unresolved files', async ({ page }, testInfo) => {
  const owner = { project_id: 'project-tv-001', request_id: 'request-drop-001', load_case_id: 'loadcase-drop-bottom-001' }
  // Controlled refresh responses exercise UI ownership independently of filesystem timing.
  await page.route('**/api/semantic-mapping/catalog', async (route) => {
    const response = await route.fetch()
    const catalog = await response.json()
    await route.fulfill({ response, json: { ...catalog, recipes: [{ id: 'active-format-recipe', name: '활성 형식 검증', latest_version: 2, active_version: 1, active_format: 'csv', definition: { format: 'json', mappings: [] } }], bindings: ['first', 'second'].map((id) => ({
      id, relative_path: `handoff/${id}`, role: 'RESULTS', recipe_ids: [], template_id: null, ...owner,
    })) } })
  })
  await page.route('**/api/semantic-mapping/bindings/first/refresh', (route) => route.fulfill({ json: {
    partial: true, results: [
      { relative_path: 'good.csv', status: 'IMPORTED', run_id: 'first-run' },
      { relative_path: 'duplicate.csv', status: 'SKIPPED', run_id: 'existing-run' },
      { relative_path: 'no-widgets.csv', status: 'SKIPPED', run_id: 'empty-run', review_available: false, clear_reason: 'NO_DISPLAY_TEMPLATE' },
      { relative_path: 'unmatched.csv', status: 'UNMAPPED', review_available: false, candidate_errors: [{ recipe_id: 'csv-recipe', code: 'SEMANTIC_SOURCE_MISSING', message: 'Missing source field: stress' }] },
      { relative_path: 'bad.csv', status: 'ERROR', run_id: 'invalid-run' },
      { relative_path: 'waiting.csv', status: 'AMBIGUOUS' },
    ],
  } }))
  let releaseRefresh!: () => void
  const gate = new Promise<void>((resolve) => { releaseRefresh = resolve })
  await page.route('**/api/semantic-mapping/bindings/second/refresh', async (route) => {
    await gate
    await route.fulfill({ json: { partial: false, results: [{ relative_path: 'next.csv', status: 'IMPORTED', run_id: 'second-run' }] } })
  })
  await loginWorkspace(page, 'e2e-admin', '/workspace/catalog/schemas')
  await page.getByRole('button', { name: '2. 폴더 연결', exact: true }).click()
  const screen = page.locator('.semantic-page')
  await expect(screen.getByRole('checkbox', { name: '활성 형식 검증 · CSV · 활성 v1', exact: true })).toBeVisible()
  const first = screen.locator('.binding-list article').filter({ hasText: 'handoff/first' })
  const second = screen.locator('.binding-list article').filter({ hasText: 'handoff/second' })
  await first.getByRole('button', { name: '새로고침', exact: true }).click()
  const links = screen.getByRole('link', { name: '결과 검토', exact: true })
  await expect(links).toHaveCount(2)
  await expect(screen.getByRole('row').filter({ hasText: 'no-widgets.csv' })).toContainText('템플릿')
  await expect(screen.getByRole('row').filter({ hasText: 'unmatched.csv' })).toContainText('Missing source field: stress')
  const diagnostic = screen.getByRole('row').filter({ hasText: 'unmatched.csv' }).locator('.folder-refresh-detail')
  await expect(diagnostic).toHaveCSS('white-space', 'normal')
  await expect(diagnostic).toHaveCSS('text-overflow', 'clip')
  await diagnostic.scrollIntoViewIfNeeded()
  await page.screenshot({ path: testInfo.outputPath('folder-diagnostics.png') })
  const href = await links.first().getAttribute('href')
  const destination = new URL(href!, page.url())
  expect(destination.searchParams.get('project')).toBe(owner.project_id)
  expect(destination.searchParams.get('request')).toBe(owner.request_id)
  expect(destination.searchParams.get('loadCase')).toBe(owner.load_case_id)
  expect(destination.searchParams.get('run')).toBe('first-run')
  // Editing a different target does not retarget already imported results.
  await screen.getByLabel('프로젝트', { exact: true }).selectOption('project-tv-001')
  await expect(links.first()).toHaveAttribute('href', href!)
  await second.getByRole('button', { name: '새로고침', exact: true }).click()
  try { await expect(links).toHaveCount(0) } finally { releaseRefresh() }
  await expect(links).toHaveCount(1)
  await expect(links.first()).toHaveAttribute('href', /run=second-run/)
})
