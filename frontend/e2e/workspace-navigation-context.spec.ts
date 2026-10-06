import { expect, test, type Page } from '@playwright/test'
import { loginWorkspace, mockResultEnvironments, openWorkspaceRoute } from './workspace-test-helpers'

// docs/contracts/workspace-navigation.md — Verifier 1–3.
const PROJECT = 'project-feature-showcase'
const REQUEST = 'request-showcase-waiting'

function usageCatalog() {
  return {
    environment: 'USAGE',
    cases: [{ id: 'usage-case', label: 'Usage Case' }],
    load_cases: [], execution_runs: [], modes: [],
    captures: [{ id: 'usage-capture', label: 'usage capture', case_id: 'usage-case' }],
    components: [], bases: [],
  }
}

test.beforeEach(async ({ page }) => {
  await mockResultEnvironments(page)
  await page.route('**/api/dashboard/finalizations/status**', (route) => route.fulfill({ json: { latest: null, selected_case_latest: null, retryable_operations: [], unverified_records: 0 } }))
  // Folder-registered request: no legacy load cases, therefore no overview.
  await page.route('**/api/requests/*/load-cases**', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/dashboard/catalog**', (route) => route.fulfill({ json: usageCatalog() }))
  await page.route('**/api/dashboard/usage/cases/**', (route) => route.abort())
})

async function openCaseResults(page: Page) {
  await loginWorkspace(page, 'e2e-admin', `/workspace/requests?project=${PROJECT}&request=${REQUEST}&view=case_results&result_environment=USAGE&capture=usage-capture&case=usage-case`)
  await expect(page.getByRole('region', { name: 'SPDM 해석 결과 대시보드' })).toBeVisible({ timeout: 15_000 })
  await expect(page).toHaveURL(/view=case_results/)
}

const REQUEST_SCREEN_KEYS = ['view', 'capture', 'result_environment', 'case', 'scene', 'part', 'loadCase', 'run', 'page', 'resultTab']

async function expectRequestScreenQueryCleared(page: Page) {
  // The state→URL sync runs after the page settles; it must not re-add request-screen keys.
  await page.waitForLoadState('networkidle')
  await expect.poll(() => {
    const query = new URL(page.url()).searchParams
    return { leaked: REQUEST_SCREEN_KEYS.filter((key) => query.has(key)), project: query.get('project'), request: query.get('request') }
  }).toEqual({ leaked: [], project: PROJECT, request: REQUEST })
}

test('Case results → variable catalog shows the catalog empty state and drops request-screen query', async ({ page }) => {
  await openCaseResults(page)
  await openWorkspaceRoute(page, '/workspace/catalog/variables')
  await expect(page).toHaveURL(/\/workspace\/catalog\/variables/)
  await expect(page.getByTestId('variable-catalog-empty')).toBeVisible()
  await expect(page.getByText('선택한 의뢰에는 변수 카탈로그 데이터가 없습니다.')).toBeVisible()
  await expect(page.getByRole('heading', { name: '접근 가능한 프로젝트가 없습니다.' })).toHaveCount(0)
  await expect(page.getByRole('navigation', { name: '의뢰 작업 여정' })).toHaveCount(0)
  await expectRequestScreenQueryCleared(page)
})

for (const [pathname, marker] of [
  ['/workspace/catalog/templates', '/workspace/catalog/templates'],
  ['/workspace/help', '/workspace/help'],
  ['/workspace/examples', '/workspace/examples'],
  ['/workspace/admin/audit', '/workspace/admin/audit'],
] as const) {
  test(`Case results → ${pathname} renders its own page without the bootstrap screen`, async ({ page }) => {
    await openCaseResults(page)
    await openWorkspaceRoute(page, pathname)
    await expect(page).toHaveURL(new RegExp(marker.replace(/\//g, '\\/')))
    // The bootstrap fallback embeds the data workspace journey; the page itself does not.
    await expect(page.getByRole('navigation', { name: '의뢰 작업 여정' })).toHaveCount(0)
    await expectRequestScreenQueryCleared(page)
  })
}
