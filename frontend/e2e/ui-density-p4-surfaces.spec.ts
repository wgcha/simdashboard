import { expect, test, type Page, type Locator } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

async function fontSize(page: Page, from: number, to: number) {
  const control = page.getByRole('button', { name: to > from ? '전체 글자 크기 늘리기' : '전체 글자 크기 줄이기', exact: true })
  for (let n = 0; n < Math.abs(to - from); n++) await control.click()
}

async function checkSurface(page: Page, root: Locator, heading: string, pt: number) {
  await expect(root).toBeVisible()
  await expect(root).toHaveAttribute('data-ui-density', 'v1')
  await expect.poll(() => root.locator(heading).first().evaluate((e) => parseFloat(getComputedStyle(e).fontSize))).toBeCloseTo(pt * 2, 1)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
}

async function capture(page: Page, name: string) {
  if (!process.env.GUI_QA_OUTPUT_DIR) return
  mkdirSync(process.env.GUI_QA_OUTPUT_DIR, { recursive: true })
  await page.screenshot({ path: path.join(process.env.GUI_QA_OUTPUT_DIR, `p4-${name}.png`) })
}

for (const width of [1366, 1920]) {
  test(`P4 ${width}px help, personal settings and modeling preserve roles and interactions`, async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    // Never connect to the user's installed desktop helper during UI QA.
    await page.route('http://127.0.0.1:8766/**', (route) => route.abort('connectionrefused'))
    await page.setViewportSize({ width, height: width === 1366 ? 768 : 1080 })
    await loginWorkspace(page, 'e2e-admin', '/workspace/help')
    let current = 14
    for (const pt of [14, 18]) {
      await fontSize(page, current, pt)
      current = pt
      for (const theme of ['라이트', '다크']) {
        await page.getByRole('button', { name: theme, exact: true }).click()
        await openWorkspaceRoute(page, '/workspace/help')
        await checkSurface(page, page.locator('.help-center'), 'h1', pt)
        await expect.poll(() => page.locator('.help-flow > div > span').first().evaluate((e) => parseFloat(getComputedStyle(e).fontSize))).toBeCloseTo(pt * 4 / 3 * .875, 1)
        await expect.poll(() => page.locator('.help-center button').first().evaluate((e) => parseFloat(getComputedStyle(e).fontSize))).toBeCloseTo(pt * 4 / 3, 1)
        await openWorkspaceRoute(page, '/workspace/settings/local-pc')
        await checkSurface(page, page.getByTestId('local-pc-settings'), 'h1', pt)
        await openWorkspaceRoute(page, '/workspace/catalog/templates')
        await checkSurface(page, page.locator('.modeling-templates-page'), 'h1', pt)
        await page.getByRole('button', { name: /새 템플릿/ }).click()
        const modal = page.locator('.template-modal')
        await expect(modal).toBeVisible()
        await modal.getByLabel('템플릿 이름', { exact: true }).fill('P4 UI fixture')
        await expect(modal.getByLabel('템플릿 이름', { exact: true })).toHaveCSS('font-size', pt === 18 ? '24px' : '18.6667px')
        if (pt === 18 && theme === '다크') await capture(page, `modeling-dialog-${width}`)
        await modal.getByRole('button', { name: '취소', exact: true }).click()
        await expect(modal).toHaveCount(0)
      }
    }
    expect(errors).toEqual([])
  })

  test(`P4 ${width}px request intake and registration keep context with large text`, async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.route('http://127.0.0.1:8766/**', (route) => route.abort('connectionrefused'))
    await page.setViewportSize({ width, height: width === 1366 ? 768 : 1080 })
    await loginWorkspace(page)
    await page.getByLabel('프로젝트 선택', { exact: true }).selectOption('project-feature-showcase')
    await page.getByLabel('의뢰 선택', { exact: true }).selectOption('request-showcase-compare')
    await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveValue('loadcase-showcase-compare')
    await fontSize(page, 14, 18)
    for (const theme of ['라이트', '다크']) {
      await page.getByRole('button', { name: theme, exact: true }).click()
      await expect(page.locator('.request-workspace-header')).toHaveAttribute('data-ui-density', 'v1')
      await expect(page.getByLabel('프로젝트 선택', { exact: true })).toHaveCSS('font-size', '24px')
      await openWorkspaceRoute(page, '/workspace/requests/new')
      await checkSurface(page, page.locator('.request-intake-page'), 'h1', 18)
      await expect(page.getByLabel('의뢰 프로젝트', { exact: true })).toHaveValue('project-feature-showcase')
      await expect(page.getByLabel('의뢰 프로젝트', { exact: true })).toHaveCSS('font-size', '24px')
      await expect(page.locator('.intake-label-filter b').first()).toHaveCSS('font-size', '21px')
      await expect(page.locator('.expected-results-preview header span').first()).toHaveCSS('font-size', '21px')
      if (theme === '다크') await capture(page, `intake-${width}`)
      await openWorkspaceRoute(page, '/workspace/requests')
      await page.getByRole('navigation', { name: '의뢰 작업 여정' }).getByRole('button', { name: '결과 등록', exact: true }).click()
      const data = page.locator('.data-workspace')
      await expect(data).toHaveAttribute('data-ui-density', 'v1')
      await expect(data.locator('.data-form-card h2').first()).toHaveCSS('font-size', '30px')
      await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue('request-showcase-compare')
      await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveValue('loadcase-showcase-compare')
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
      if (theme === '다크') await capture(page, `registration-${width}`)
      await openWorkspaceRoute(page, '/workspace/requests')
    }
    expect(errors).toEqual([])
  })
}

test('P4 comparison scales filters, evidence and chart controls at 18pt', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  await loginWorkspace(page, 'e2e-admin', '/workspace/overview')
  const dashboard = page.getByTestId('result-overview-dashboard')
  await dashboard.getByLabel('의뢰 제목, 하중 경우 검색').fill('Run 비교: 회귀와 개선')
  await dashboard.getByRole('button', { name: '결과 검토', exact: true }).click()
  await page.locator('.analysis-tab').filter({ hasText: 'Run 비교' }).click()
  await fontSize(page, 14, 18)
  const comparison = page.locator('.comparison-workspace')
  for (const theme of ['라이트', '다크']) {
    await page.getByRole('button', { name: theme, exact: true }).click()
    await checkSurface(page, comparison, '.comparison-hero h2', 18)
    await expect(comparison.getByLabel('기준 Run', { exact: true })).toHaveCSS('font-size', '24px')
    await expect(comparison.locator('.comparison-evidence-header p').first()).toHaveCSS('font-size', '21px')
    await page.getByTestId('comparison-filter-regression').click()
    await expect(page.getByTestId('comparison-evidence-row').first()).toBeVisible()
    await page.getByTestId('comparison-evidence-row').first().click()
    await expect(page.getByTestId('comparison-evidence-detail')).toBeVisible()
  }
  await capture(page, 'comparison-1366')
})

test('P4 desktop request navigation restores context after back and reload', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  await page.route('http://127.0.0.1:8766/**', (route) => route.abort('connectionrefused'))
  await loginWorkspace(page)
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption('project-feature-showcase')
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption('request-showcase-compare')
  await fontSize(page, 14, 18)
  const journey = page.getByRole('navigation', { name: '의뢰 작업 여정' })
  await journey.getByRole('button', { name: '작업 실행', exact: true }).click()
  await expect(page.getByTestId('simulation-workbench')).toBeVisible()
  await journey.getByRole('button', { name: '결과 등록', exact: true }).click()
  await expect(page.locator('.data-workspace')).toBeVisible()
  await page.goBack()
  await expect(page.getByTestId('simulation-workbench')).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue('request-showcase-compare')
  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveValue('loadcase-showcase-compare')
  await expect(page.locator('.app-shell')).toHaveCSS('--ui-font-size', '18pt')
  await journey.getByRole('button', { name: 'Case 결과', exact: true }).click()
  await expect(journey.getByRole('button', { name: 'Case 결과', exact: true })).toHaveAttribute('aria-current', 'step')
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue('request-showcase-compare')
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
})
