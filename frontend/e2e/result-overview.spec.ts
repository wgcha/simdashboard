import { expect, test } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import type { PortfolioOverview } from '../src/types'
import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

test('첫 화면은 독립 결과 대시보드이며 실제 최신 Run 집계와 일치한다', async ({ page }) => {
  await page.setViewportSize({ width: 1505, height: 1045 })
  await loginWorkspace(page, 'e2e-admin', '/')
  await expect(page).toHaveURL(/\/workspace\/overview(?:\?|$)/)
  await page.getByRole('button', { name: '라이트', exact: true }).click()
  const dashboard = page.getByTestId('result-overview-dashboard')
  await expect(dashboard.getByRole('heading', { name: '결과 대시보드', exact: true })).toBeVisible()
  await expect(page.getByTestId('focused-request-overview')).toHaveCount(0)
  const data = await (await page.request.get('/api/portfolio/overview')).json() as PortfolioOverview
  await expect(dashboard.getByRole('button', { name: '기준 미충족', exact: true }).locator('b')).toHaveText(String(data.records.filter((record) => record.run_id && record.result_count > 0 && record.verdict === 'FAIL').length))
  await expect(dashboard.getByRole('button', { name: '결과 보유', exact: true }).locator('b')).toHaveText(String(data.records.filter((record) => record.run_id).length))
  await expect(dashboard.getByRole('button', { name: '결과 대기', exact: true }).locator('b')).toHaveText(String(data.records.filter((record) => record.load_case_id && !record.run_id).length))
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  if (process.env.GUI_QA_OUTPUT_DIR) {
    mkdirSync(process.env.GUI_QA_OUTPUT_DIR, { recursive: true })
    await page.screenshot({ path: path.join(process.env.GUI_QA_OUTPUT_DIR, 'results-dashboard-desktop.png') })
  }
  await dashboard.getByRole('button', { name: '기준 미충족', exact: true }).click()
  await expect(dashboard.getByRole('button', { name: '기준 미충족', exact: true })).toHaveAttribute('aria-pressed', 'true')
  for (const verdict of await dashboard.locator('.result-overview-verdict').all()) await expect(verdict).toHaveText('FAIL')
  await page.setViewportSize({ width: 390, height: 844 })
  await expect.poll(async () => (await page.locator('main.main-shell').boundingBox())?.x ?? 999).toBeLessThanOrEqual(1)
  await expect.poll(async () => (await dashboard.boundingBox())?.width ?? 0).toBeGreaterThan(350)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  if (process.env.GUI_QA_OUTPUT_DIR) await page.screenshot({ path: path.join(process.env.GUI_QA_OUTPUT_DIR, 'results-dashboard-mobile.png') })
})

test('대시보드 결과에서 의뢰와 하중 경우 및 정확한 Run으로 연결된다', async ({ page }) => {
  await loginWorkspace(page, 'e2e-admin', '/')
  const dashboard = page.getByTestId('result-overview-dashboard')
  await dashboard.getByLabel('의뢰 제목, 하중 경우 검색').fill('Run 비교: 회귀와 개선')
  const row = dashboard.locator('article.result-overview-result-row').filter({ hasText: 'Run 비교: 회귀와 개선' })
  await expect(row).toHaveCount(1)
  const data = await (await page.request.get('/api/portfolio/overview')).json() as PortfolioOverview
  const record = data.records.find((item) => item.request_id === 'request-showcase-compare')!
  let releaseRuns!: () => void
  let startedRuns!: () => void
  const runsStarted = new Promise<void>((resolve) => { startedRuns = resolve })
  const heldRuns = new Promise<void>((resolve) => { releaseRuns = resolve })
  await page.route('**/api/load-cases/loadcase-showcase-compare/runs', async (route) => {
    startedRuns()
    await heldRuns
    await route.continue()
  })
  await row.getByRole('button', { name: '결과 검토', exact: true }).click()
  await runsStarted
  const summary = page.getByRole('region', { name: '현재 Run 핵심 결과' })
  await expect(summary.getByText('Run #1', { exact: true })).toHaveCount(0)
  releaseRuns()
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue(record.request_id)
  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveValue(record.load_case_id)
  await expect(page.getByLabel('결과 버전 선택')).toHaveValue(record.run_id!)
  await expect(summary).toContainText('Run #3')
  await expect(summary).toContainText('표시 항목의 기준')
  await expect(summary.locator('.result-summary-value')).toContainText('중 첫 항목')
  await openWorkspaceRoute(page, '/workspace/overview')
  await expect(dashboard).toBeVisible()
})

test('대기 의뢰로 이동 중에는 이전 의뢰의 작업을 실행할 수 없다', async ({ page }) => {
  await loginWorkspace(page)
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption('project-feature-showcase')
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption('request-showcase-compare')
  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveValue('loadcase-showcase-compare')
  await openWorkspaceRoute(page, '/workspace/overview')
  let releaseContext!: () => void
  let startedContext!: () => void
  const contextStarted = new Promise<void>((resolve) => { startedContext = resolve })
  const heldContext = new Promise<void>((resolve) => { releaseContext = resolve })
  await page.route('**/api/requests/request-showcase-workflow/load-cases', async (route) => {
    startedContext()
    await heldContext
    await route.continue()
  })
  await page.getByRole('button', { name: '워크플로: 진행·차단·대기 실무 진행 상태 예제', exact: true }).click()
  await contextStarted
  await expect(page.getByTestId('focused-request-overview')).toHaveCount(0)
  releaseContext()
  await page.getByRole('button', { name: '작업 이어하기', exact: true }).click()
  await expect(page.getByRole('heading', { name: '워크플로: 진행·차단·대기', exact: true })).toBeVisible()
  await expect(page.getByText('이 의뢰에는 배정된 작업 계획이 없어 실행할 수 없습니다.', { exact: true })).toBeVisible()
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue('request-showcase-workflow')
  await expect.poll(() => new URL(page.url()).searchParams.get('request')).toBe('request-showcase-workflow')
})

test('대시보드에서 의뢰 개요와 기존 운영 차트를 각각 열 수 있다', async ({ page }) => {
  await loginWorkspace(page, 'e2e-admin', '/')
  const dashboard = page.getByTestId('result-overview-dashboard')
  await dashboard.getByLabel('의뢰 제목, 하중 경우 검색').fill('변수 카탈로그: 데이터 대기')
  const row = dashboard.locator('article.result-overview-result-row')
  await expect(row).toHaveCount(1)
  await row.getByRole('button', { name: '의뢰 보기', exact: true }).click()
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue('request-showcase-waiting')
  await expect(page.getByTestId('focused-request-overview')).toBeVisible()
  await openWorkspaceRoute(page, '/workspace/overview')
  await dashboard.getByRole('button', { name: '운영 현황', exact: true }).click()
  await expect(page.locator('.portfolio-grid')).toBeVisible()
  await page.getByRole('button', { name: '결과 대시보드', exact: true }).click()
  await expect(dashboard).toBeVisible()
})
