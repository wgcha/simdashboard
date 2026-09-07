import { expect, test, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import type { Overview } from '../src/types'
import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

async function selectComparisonRequest(page: Page) {
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption('project-feature-showcase')
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption('request-showcase-compare')
  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveValue('loadcase-showcase-compare')
  await expect.poll(() => new URL(page.url()).searchParams.get('request')).toBe('request-showcase-compare')
}

async function openComparisonResults(page: Page) {
  await openWorkspaceRoute(page, '/workspace/overview')
  const dashboard = page.getByTestId('result-overview-dashboard')
  await dashboard.getByLabel('의뢰 제목, 하중 경우 검색').fill('Run 비교: 회귀와 개선')
  await dashboard.getByRole('button', { name: '결과 검토', exact: true }).click()
  await expect(page.getByRole('region', { name: '현재 Run 핵심 결과' })).toBeVisible()
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue('request-showcase-compare')
}

async function capture(page: Page, filename: string) {
  const directory = process.env.GUI_QA_OUTPUT_DIR
  if (!directory) return
  mkdirSync(directory, { recursive: true })
  await page.screenshot({ path: path.join(directory, filename), fullPage: false })
}

test('선택 의뢰에 집중하고 접힌 업무 도구와 설정도 접근할 수 있다', async ({ page }) => {
  await page.setViewportSize({ width: 1505, height: 1045 })
  await loginWorkspace(page)
  await page.getByRole('button', { name: '라이트', exact: true }).click()
  await expect(page.getByText('현재 할 일', { exact: true })).toBeVisible()
  await expect(page.getByTestId('focused-request-overview')).toHaveCount(1)
  await expect(page.getByRole('link', { name: '내 작업', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '결과 대시보드', exact: true })).toBeVisible()
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  await capture(page, 'request-overview-desktop.png')

  await openWorkspaceRoute(page, '/workspace/admin/work-types')
  await expect(page).toHaveURL(/\/workspace\/admin\/work-types(?:\?|$)/)
  await expect(page.getByRole('heading', { name: /작업 유형 관리/ }).first()).toBeVisible()
})

test('현재 프로젝트가 접수로 이어지고 의뢰와 하중 경우가 결과 등록에도 유지된다', async ({ page }) => {
  await loginWorkspace(page)
  await selectComparisonRequest(page)
  await openWorkspaceRoute(page, '/workspace/requests/new')
  await expect(page.getByLabel('의뢰 프로젝트', { exact: true })).toHaveValue('project-feature-showcase')
  await openWorkspaceRoute(page, '/workspace/requests')
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue('request-showcase-compare')
  await page.getByRole('button', { name: '결과 등록', exact: true }).click()
  await expect(page.getByLabel('등록 프로젝트 선택')).toHaveValue('project-feature-showcase')
  await expect(page.getByLabel('등록 의뢰 선택')).toHaveValue('request-showcase-compare')
  await expect(page.getByLabel('등록 하중 경우 선택')).toHaveValue('loadcase-showcase-compare')
  await page.goBack()
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue('request-showcase-compare')
})

test('작업 계획 없는 의뢰의 실행 진입은 다른 의뢰로 바뀌지 않는다', async ({ page }) => {
  await loginWorkspace(page)
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption('project-feature-showcase')
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption('request-showcase-workflow')
  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveValue('loadcase-showcase-workflow')
  await page.getByRole('button', { name: '작업 이어하기', exact: true }).click()
  const workbench = page.getByTestId('simulation-workbench')
  await expect(workbench).toContainText('작업 계획이 없어 실행할 수 없습니다')
  const selector = page.getByLabel('배정 작업 대상 의뢰')
  await expect(selector).toHaveValue('request-showcase-workflow')
  await expect.poll(() => new URL(page.url()).searchParams.get('request')).toBe('request-showcase-workflow')
  await expect(page.getByTestId('execute-selected-task')).toHaveCount(0)
  const assignedRequest = await selector.locator('option').evaluateAll((options) =>
    options.map((option) => (option as HTMLOptionElement).value).find((value) => value && value !== 'request-showcase-workflow'))
  expect(assignedRequest).toBeTruthy()
  await selector.selectOption(assignedRequest!)
  await expect.poll(() => new URL(page.url()).searchParams.get('request')).toBe(assignedRequest!)
  await expect(page.getByTestId('execute-selected-task')).toBeVisible()
  await page.getByRole('button', { name: '의뢰 개요', exact: true }).click()
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue(assignedRequest!)
})

test('결과 문맥과 과거 Run을 새로고침 후에도 복원한다', async ({ page }) => {
  await loginWorkspace(page)
  await openComparisonResults(page)
  const selector = page.getByLabel('결과 버전 선택')
  await expect.poll(() => selector.locator('option').count()).toBeGreaterThanOrEqual(2)
  const selected = await selector.inputValue()
  const previous = await selector.locator('option').evaluateAll((options, current) =>
    options.map((option) => (option as HTMLOptionElement).value).find((value) => value !== current)!, selected)
  const historicalLabel = await selector.locator(`option[value="${previous}"]`).innerText()
  const runNumber = historicalLabel.match(/^v(\d+)/)?.[1]
  await selector.selectOption(previous)
  await expect(selector).toBeEnabled()
  await expect.poll(() => new URL(page.url()).search).toContain(encodeURIComponent(previous))
  const historicalResponse = page.waitForResponse((response) => {
    const url = new URL(response.url())
    return url.pathname.endsWith('/loadcase-showcase-compare/overview') && url.searchParams.get('run_id') === previous && response.ok()
  })
  await page.reload()
  const expectedOverview = await (await historicalResponse).json() as Overview
  expect(expectedOverview.run).toBe(previous)
  await expect(page.getByLabel('프로젝트 선택', { exact: true })).toHaveValue('project-feature-showcase')
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue('request-showcase-compare')
  await expect(page.getByLabel('결과 버전 선택')).toHaveValue(previous)
  const summary = page.getByRole('region', { name: '현재 Run 핵심 결과' })
  await expect(summary).toContainText(`Run #${runNumber}`)
  const summaryText = await summary.innerText()
  const shownResult = expectedOverview.scalar_results.find((result) => summaryText.includes(result.display_name))
  expect(shownResult, 'The visible result must belong to the selected historical Run').toBeTruthy()
  await expect(summary.locator('.result-summary-value')).toContainText(shownResult!.value_double.toFixed(2))
  await expect(summary.locator('.result-summary-value')).toContainText(shownResult!.unit)
  await expect(page.locator('.analysis-page-jump')).toHaveCount(0)
  await capture(page, 'request-results-desktop.png')
})

test('다른 의뢰로 이동한 뒤 브라우저 뒤로 가기는 이전 의뢰를 복원한다', async ({ page }) => {
  await loginWorkspace(page)
  await selectComparisonRequest(page)
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption('request-showcase-waiting')
  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveValue('loadcase-showcase-waiting')
  await expect.poll(() => new URL(page.url()).searchParams.get('request')).toBe('request-showcase-waiting')
  await page.goBack()
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue('request-showcase-compare')
  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveValue('loadcase-showcase-compare')
})

test('결과 없는 의뢰에 이전 의뢰의 판정이나 Run을 보여주지 않는다', async ({ page }) => {
  await loginWorkspace(page)
  await openComparisonResults(page)
  await expect(page.getByLabel('결과 버전 선택')).toBeEnabled()
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption('request-showcase-waiting')
  await expect(page.getByLabel('결과 버전 선택')).toBeDisabled()
  await expect(page.getByLabel('결과 버전 선택').locator('option')).toHaveText('결과 없음')
  await expect(page.locator('.result-summary-value').filter({ hasText: 'MPa' })).toHaveCount(0)
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
})

test('모바일과 큰 글자에서도 의뢰 선택과 다음 행동이 화면 안에 보인다', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await loginWorkspace(page)
  await page.getByRole('button', { name: '라이트', exact: true }).click()
  await page.getByRole('button', { name: '메뉴 열기', exact: true }).click()
  await expect(page.getByRole('link', { name: '내 작업', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '메뉴 닫기', exact: true }).click()
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
  await capture(page, 'request-overview-mobile.png')
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.getByRole('button', { name: '전체 글자 크기 늘리기' }).click()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
  await expect(page.getByText('현재 할 일', { exact: true })).toBeVisible()
})

test('일반 사용자는 설정을 펼쳐도 관리자 기능과 결과 등록 권한을 얻지 않는다', async ({ page }) => {
  await loginWorkspace(page, 'e2e-viewer')
  await expect(page.getByRole('link', { name: '새 의뢰', includeHidden: true })).toHaveCount(0)
  await expect(page.locator('.sidebar a[href="/workspace/admin/work-types"]')).toHaveCount(0)
  await expect(page.locator('.sidebar a[href="/workspace/data"]')).toHaveCount(0)
  const registration = page.getByRole('button', { name: '결과 등록', exact: true })
  if (await registration.count()) await expect(registration).toBeDisabled()
  await page.goto('/workspace/data')
  await expect(page).toHaveURL(/\/workspace\/overview(?:\?|$)/)
  await expect(page.getByLabel('등록 의뢰 선택')).toHaveCount(0)
  await page.goto('/workspace/admin/work-types')
  await expect(page).toHaveURL(/\/workspace\/overview(?:\?|$)/)
  await expect(page.getByRole('heading', { name: /작업 유형 관리/ })).toHaveCount(0)
})

test('편집 중 여정 이동을 취소하면 URL과 선택 의뢰를 유지한다', async ({ page }) => {
  await loginWorkspace(page)
  await openComparisonResults(page)
  await page.getByRole('button', { name: '대시보드 편집', exact: true }).click()
  await expect(page.getByTestId('analysis-dashboard')).toBeVisible()
  const originalUrl = page.url()
  page.once('dialog', (dialog) => dialog.dismiss())
  await page.getByRole('button', { name: '결과 등록', exact: true }).click()
  await expect(page).toHaveURL(originalUrl)
  await expect(page.getByTestId('analysis-dashboard')).toBeVisible()
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue('request-showcase-compare')
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '결과 등록', exact: true }).click()
  await expect(page.getByLabel('등록 의뢰 선택')).toHaveValue('request-showcase-compare')
})

test('편집 중 의뢰 또는 Run 변경 취소는 편집 내용과 문맥을 유지한다', async ({ page }) => {
  await loginWorkspace(page)
  await openComparisonResults(page)
  await page.getByRole('button', { name: '대시보드 편집', exact: true }).click()
  await expect(page.getByTestId('analysis-dashboard')).toBeVisible()
  const originalUrl = page.url()
  const runSelector = page.getByLabel('결과 버전 선택')
  await expect(runSelector).toBeEnabled()
  await expect.poll(() => runSelector.locator('option').count()).toBeGreaterThanOrEqual(2)
  const originalRun = await runSelector.inputValue()
  const otherRun = await runSelector.locator('option').evaluateAll((options, current) => options.map((option) => (option as HTMLOptionElement).value).find((value) => value !== current)!, originalRun)
  page.once('dialog', (dialog) => dialog.dismiss())
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption('request-showcase-waiting')
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue('request-showcase-compare')
  await expect(page).toHaveURL(originalUrl)
  page.once('dialog', (dialog) => dialog.dismiss())
  await runSelector.selectOption(otherRun)
  await expect(runSelector).toHaveValue(originalRun)
  await expect(page).toHaveURL(originalUrl)
  await expect(page.getByTestId('analysis-dashboard')).toBeVisible()
})

for (const invalidKey of ['request', 'run', 'page'] as const) {
  test(`잘못된 ${invalidKey} 링크에서 다른 결과를 대신 표시하지 않는다`, async ({ page }) => {
    await loginWorkspace(page)
    await openComparisonResults(page)
    await expect(page.getByRole('region', { name: '현재 Run 핵심 결과' })).toBeVisible()
    const invalidUrl = new URL(page.url())
    invalidUrl.searchParams.set(invalidKey, 'e2e-nonexistent-context')
    await page.goto(invalidUrl.toString())
    await expect(page.getByRole('region', { name: '현재 Run 핵심 결과' })).toHaveCount(0)
    await expect(page.getByRole('navigation', { name: '의뢰 작업 여정' })).toBeVisible()
    await expect(page.locator('.toast')).toContainText(/링크|문맥|열 수|유효/)
  })
}

test('링크의 Run 검증이 지연되어도 기본 의뢰의 결과를 먼저 보여주지 않는다', async ({ page }) => {
  await loginWorkspace(page)
  await openComparisonResults(page)
  await expect(page.getByRole('region', { name: '현재 Run 핵심 결과' })).toBeVisible()
  const invalidUrl = new URL(page.url())
  invalidUrl.searchParams.set('run', 'e2e-invalid-slow-run')
  let release!: () => void
  const gate = new Promise<void>((resolve) => { release = resolve })
  await page.route('**/api/load-cases/loadcase-showcase-compare/runs', async (route) => { await gate; await route.continue() })
  try {
    await page.goto(invalidUrl.toString())
    await expect(page.getByTestId('workspace-context-restoring')).toBeVisible()
    await expect(page.getByRole('region', { name: '현재 Run 핵심 결과' })).toHaveCount(0)
  } finally { release() }
  await expect(page.getByTestId('workspace-context-restoring')).toHaveCount(0)
  await expect(page.getByRole('region', { name: '현재 Run 핵심 결과' })).toHaveCount(0)
})

test('의뢰 변경 API 실패가 다른 의뢰의 결과를 섞어 표시하지 않는다', async ({ page }) => {
  await loginWorkspace(page)
  await openComparisonResults(page)
  await expect(page.getByRole('region', { name: '현재 Run 핵심 결과' })).toBeVisible()
  await page.route('**/api/requests/request-showcase-waiting/load-cases', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'E2E 선택 조회 실패' }) }))
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption('request-showcase-waiting')
  await expect(page.locator('.full-state.error')).toBeVisible()
  await expect(page.getByRole('region', { name: '현재 Run 핵심 결과' })).toHaveCount(0)
  expect(new URL(page.url()).searchParams.get('request')).not.toBe('request-showcase-waiting')
})

test('결과 구성이 없는 의뢰는 새로고침해도 임의의 분석 화면으로 바뀌지 않는다', async ({ page }) => {
  await loginWorkspace(page)
  await selectComparisonRequest(page)
  await page.getByRole('button', { name: '결과 검토', exact: true }).click()
  await expect(page.getByTestId('result-layout-unconfigured')).toBeVisible()
  await expect.poll(() => new URL(page.url()).searchParams.get('view')).toBe('custom')
  await expect.poll(() => new URL(page.url()).searchParams.get('page')).toBeNull()
  await expect.poll(() => new URL(page.url()).searchParams.get('run')).toBeNull()
  await page.reload()
  await expect(page.getByTestId('result-layout-unconfigured')).toBeVisible()
  await expect(page.getByRole('region', { name: '현재 Run 핵심 결과' })).toHaveCount(0)
  await expect(page.getByLabel('결과 버전 선택')).toHaveCount(0)
})

test('결과 등록에서 검토로 이동할 때 조회가 실패하면 로딩 대신 오류를 보여준다', async ({ page }) => {
  await loginWorkspace(page)
  await selectComparisonRequest(page)
  await page.getByRole('button', { name: '결과 등록', exact: true }).click()
  await expect(page.getByLabel('등록 하중 경우 선택')).toHaveValue('loadcase-showcase-compare')
  await page.route('**/api/load-cases/loadcase-showcase-compare/overview*', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'E2E 결과 조회 실패' }) }))
  await page.getByRole('navigation', { name: '의뢰 작업 여정' }).getByRole('button', { name: '결과 검토', exact: true }).click()
  await expect(page.locator('.full-state.error')).toBeVisible()
  await expect(page.getByTestId('workspace-context-restoring')).toHaveCount(0)
})
