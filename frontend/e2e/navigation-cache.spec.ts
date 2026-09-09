import { expect, test, type Page, type TestInfo } from '@playwright/test'

import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

type Gate = { promise: Promise<void>; release: () => void }

function gate(): Gate {
  let release!: () => void
  const promise = new Promise<void>((resolve) => { release = resolve })
  return { promise, release }
}

async function visibleResultTitle(page: Page, exclude = '') {
  const titles = page.getByTestId('result-overview-dashboard').locator('article.result-overview-result-row .result-overview-record-name strong')
  await expect(titles.first()).toBeVisible()
  return (await titles.allTextContents()).map((value) => value.trim()).find((value) => value && value !== exclude) ?? ''
}

test('따뜻한 결과 대시보드 재방문은 느린 재검증 중에도 내용과 앱 shell을 유지한다', async ({ page }, testInfo: TestInfo) => {
  await page.setViewportSize({ width: 2560, height: 1440 })
  const pageErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  let hold = false
  let startedResolve!: () => void
  const started = new Promise<void>((resolve) => { startedResolve = resolve })
  const refresh = gate()
  await page.route('**/api/portfolio/overview*', async (route) => {
    if (!hold) return route.continue()
    startedResolve()
    await refresh.promise
    await route.continue()
  })

  await loginWorkspace(page, 'e2e-admin', '/')
  const title = await visibleResultTitle(page)
  await openWorkspaceRoute(page, '/workspace/help')
  hold = true
  try {
    await openWorkspaceRoute(page, '/workspace/overview')
    await started
    await expect(page.locator('.app-shell')).toBeVisible()
    await expect(page.getByTestId('result-overview-dashboard')).toBeVisible()
    await expect(page.getByText(title, { exact: true }).first()).toBeVisible()
    await expect(page.getByText('운영 데이터를 집계하고 있습니다.', { exact: true })).toHaveCount(0)
    await page.screenshot({ path: testInfo.outputPath('menu-navigation-2560.png'), fullPage: true })
  } finally {
    refresh.release()
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByTestId('result-overview-dashboard')).toBeVisible()
  await expect(page.getByRole('heading', { name: '결과 대시보드', exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('menu-navigation-390.png'), fullPage: true })
  expect(pageErrors).toEqual([])
})

test('새 검색 key를 불러오는 동안 이전 결과 행을 새 문맥처럼 재사용하지 않는다', async ({ page }) => {
  const searchText = 'Run 비교: 회귀와 개선'
  const searchGate = gate()
  let startedResolve!: () => void
  const started = new Promise<void>((resolve) => { startedResolve = resolve })
  await page.route('**/api/portfolio/overview*', async (route) => {
    const query = new URL(route.request().url()).searchParams.get('search')
    if (query !== searchText) return route.continue()
    startedResolve()
    await searchGate.promise
    await route.continue()
  })

  await loginWorkspace(page, 'e2e-admin', '/')
  const searchInput = page.getByLabel('의뢰 제목, 하중 경우 검색')
  await searchInput.fill('')
  await expect(page.getByTestId('result-overview-dashboard').locator('article.result-overview-result-row').first()).toBeVisible()
  const oldTitle = await visibleResultTitle(page, searchText)
  expect(oldTitle).toBeTruthy()
  await searchInput.fill(searchText)
  await started
  try {
    await expect(page.locator('main.main-shell')).toBeVisible()
    await expect(page.getByText(oldTitle, { exact: true })).toHaveCount(0)
    await expect(page.getByLabel('의뢰 제목, 하중 경우 검색')).toBeFocused()
    await expect(page.getByText('결과 대시보드를 갱신하고 있습니다.', { exact: true })).toBeVisible()
  } finally {
    searchGate.release()
  }
  const dashboard = page.getByTestId('result-overview-dashboard')
  await expect(dashboard.locator('article.result-overview-result-row')).toHaveCount(1)
  await expect(dashboard.getByText(searchText, { exact: true })).toBeVisible()
  await searchInput.fill('')
  await expect(dashboard.locator('article.result-overview-result-row').first()).toBeVisible()
})

test('성공한 변경은 현재 화면을 재검증하면서 cached 내용을 유지한다', async ({ page }) => {
  let hold = false
  let startedResolve!: () => void
  const started = new Promise<void>((resolve) => { startedResolve = resolve })
  const refresh = gate()
  await page.route('**/api/portfolio/overview*', async (route) => {
    if (!hold) return route.continue()
    startedResolve()
    await refresh.promise
    await route.continue()
  })
  await page.route('**/api/storage/config', async (route) => {
    if (route.request().method() !== 'PUT') return route.continue()
    await route.fulfill({ json: { configured: true, locked: false, root: 'E:\\cache-invalidation-e2e' } })
  })

  await loginWorkspace(page, 'e2e-admin', '/')
  const title = await visibleResultTitle(page)
  const settings = page.locator('details.storage-global-settings')
  await settings.getByLabel('고정 결과 원본 폴더 설정').click()
  const rootInput = settings.getByLabel('고정 결과 원본 폴더', { exact: true })
  await expect(rootInput).toBeVisible()
  await rootInput.fill('E:\\cache-invalidation-e2e')
  hold = true
  await settings.getByRole('button', { name: '저장', exact: true }).click()
  await started
  try {
    await expect(page.getByTestId('result-overview-dashboard')).toBeVisible()
    await expect(page.getByText(title, { exact: true }).first()).toBeVisible()
  } finally {
    refresh.release()
  }
})

test('403과 로그아웃은 이전 사용자의 cached 결과를 제거한다', async ({ page }) => {
  let forbidNext = false
  await page.route('**/api/portfolio/overview*', async (route) => {
    if (!forbidNext) return route.continue()
    forbidNext = false
    await route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'forbidden cache probe' }) })
  })

  await loginWorkspace(page, 'e2e-admin', '/')
  const title = await visibleResultTitle(page)
  await openWorkspaceRoute(page, '/workspace/help')
  forbidNext = true
  await openWorkspaceRoute(page, '/workspace/overview')
  await expect(page.getByTestId('result-overview-dashboard')).toHaveCount(0)
  await expect(page.getByText(title, { exact: true })).toHaveCount(0)
  await expect(page.locator('.portfolio-state.error')).toContainText('forbidden cache probe')

  await page.getByRole('button', { name: '로그아웃', exact: true }).click()
  await expect(page.getByRole('button', { name: '로그인', exact: true })).toBeVisible()
  const loginGate = gate()
  let loginRefreshResolve!: () => void
  const loginRefreshStarted = new Promise<void>((resolve) => { loginRefreshResolve = resolve })
  await page.route('**/api/portfolio/overview*', async (route) => {
    loginRefreshResolve()
    await loginGate.promise
    await route.continue()
  })
  await page.getByLabel('사용자 이름').fill('e2e-admin')
  await page.getByLabel('비밀번호').fill('e2e-validation-password')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await loginRefreshStarted
  try {
    await expect(page.getByText(title, { exact: true })).toHaveCount(0)
  } finally {
    loginGate.release()
  }
  await expect(page.getByTestId('result-overview-dashboard')).toBeVisible()
})
