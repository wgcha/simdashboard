import { expect, test } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import type { PortfolioOverview } from '../src/types'
import { loginWorkspace } from './workspace-test-helpers'

test('P2 result pagination handles boundaries and resets after size, search, project and filter changes', async ({ page }) => {
  // Each boundary reloads the real authenticated shell; allow Windows cold starts.
  test.setTimeout(180_000)
  await loginWorkspace(page, 'e2e-admin', '/')
  const original = await (await page.request.get('/api/portfolio/overview')).json() as PortfolioOverview
  const template = original.records.find((record) => record.run_id)!
  let count = 26
  await page.route('**/api/portfolio/overview**', (route) => route.fulfill({ json: {
    ...original,
    records: Array.from({ length: count }, (_, index) => ({ ...template,
      request_id: `density-request-${index}`, load_case_id: `density-case-${index}`,
      request_title: `밀도 회귀 ${String(index + 1).padStart(2, '0')}`,
      verdict: index % 2 ? 'PASS' : 'FAIL', result_count: 1,
    })),
  } }))
  await page.reload()
  const dashboard = page.getByTestId('result-overview-dashboard')
  const rows = dashboard.locator('article.result-overview-result-row')
  const size = dashboard.getByLabel('결과 목록 페이지 크기', { exact: true })
  const nav = dashboard.getByRole('navigation', { name: '결과 목록 페이지' })
  const next = nav.getByRole('button', { name: '다음', exact: true })
  const previous = nav.getByRole('button', { name: '이전', exact: true })
  await expect(size).toHaveValue('25')
  await expect(rows).toHaveCount(25)
  await expect(previous).toBeDisabled()
  await next.click()
  await expect(rows).toHaveCount(1)
  await expect(nav).toContainText('2 / 2')
  await expect(next).toBeDisabled()
  await size.selectOption('10')
  await expect(rows).toHaveCount(10)
  await expect(nav).toContainText('1 / 3')
  await next.click()
  await size.selectOption('50')
  await expect(rows).toHaveCount(26)
  await size.selectOption('10')
  await next.click()
  await dashboard.getByLabel('의뢰 제목, 하중 경우 검색').fill('검색 변경')
  await expect(nav).toContainText('1 / 3')
  await next.click()
  await dashboard.getByLabel('결과 프로젝트').selectOption(original.filter_options.projects[0].id)
  await expect(nav).toContainText('1 / 3')
  await next.click()
  await dashboard.getByRole('button', { name: '기준 미충족', exact: true }).click()
  await expect(nav).toContainText('1 / 2')
  for (const verdict of await rows.locator('.result-overview-verdict').all()) await expect(verdict).toHaveText('FAIL')
  await dashboard.getByRole('button', { name: '전체 보기', exact: true }).click()

  for (const boundary of [0, 1, 5, 6, 25, 26]) {
    count = boundary
    await page.reload()
    await expect(size).toHaveValue('25')
    await expect(rows).toHaveCount(Math.min(boundary, 25))
    if (boundary === 0) await expect(dashboard).toContainText('조건에 맞는 결과가 없습니다.')
    if (boundary > 25) {
      await next.click()
      await expect(rows).toHaveCount(1)
      await expect(next).toBeDisabled()
    } else await expect(next).not.toBeVisible()
  }
  for (const boundary of [10, 11]) {
    count = boundary
    await page.reload()
    await size.selectOption('10')
    await expect(rows).toHaveCount(10)
    if (boundary === 11) {
      await next.click()
      await expect(rows).toHaveCount(1)
      await expect(next).toBeDisabled()
    } else await expect(next).not.toBeVisible()
  }
})

test('P2 result controls stay readable in both themes and at mobile 18pt', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.setViewportSize({ width: 1366, height: 768 })
  await loginWorkspace(page, 'e2e-admin', '/')
  const dashboard = page.getByTestId('result-overview-dashboard')
  await expect(dashboard.locator('article.result-overview-result-row').first()).toBeVisible()
  for (const width of [1366, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 768 })
    for (const theme of ['라이트', '다크']) {
      await page.getByRole('button', { name: theme, exact: true }).click()
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await expect(dashboard.getByLabel('결과 목록 페이지 크기', { exact: true })).toHaveValue('25')
      if (process.env.GUI_QA_OUTPUT_DIR) {
        mkdirSync(process.env.GUI_QA_OUTPUT_DIR, { recursive: true })
        await page.screenshot({ path: path.join(process.env.GUI_QA_OUTPUT_DIR, `p2-overview-${width}-${theme === '라이트' ? 'light' : 'dark'}.png`) })
      }
    }
  }
  const sidebar = page.getByRole('complementary', { name: '주 메뉴' })
  await sidebar.getByRole('button', { name: '메뉴 열기', exact: true }).click()
  for (let i = 0; i < 4; i++) await sidebar.getByRole('button', { name: '전체 글자 크기 늘리기' }).click()
  await sidebar.getByRole('button', { name: '메뉴 닫기', exact: true }).click()
  await expect(page.locator('.app-shell')).toHaveCSS('font-size', '24px')
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  const pageSize = dashboard.getByLabel('결과 목록 페이지 크기', { exact: true })
  await pageSize.scrollIntoViewIfNeeded()
  expect(await pageSize.evaluate((element) => {
    const style = getComputedStyle(element)
    const context = document.createElement('canvas').getContext('2d')!
    context.font = style.font
    const selectedText = (element as HTMLSelectElement).selectedOptions[0].textContent || ''
    return element.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) >= context.measureText(selectedText).width
  })).toBe(true)
  const firstRow = dashboard.locator('article.result-overview-result-row').first()
  const dateBox = await firstRow.locator('.result-overview-date').boundingBox()
  const actionsBox = await firstRow.locator('.result-overview-row-actions').boundingBox()
  expect(actionsBox!.y).toBeGreaterThanOrEqual(dateBox!.y + dateBox!.height)
  if (process.env.GUI_QA_OUTPUT_DIR) await page.screenshot({ path: path.join(process.env.GUI_QA_OUTPUT_DIR, 'p2-overview-mobile-18pt-results.png') })
  const search = dashboard.getByLabel('의뢰 제목, 하중 경우 검색')
  await search.focus()
  await expect(search).toBeFocused()
  expect(await search.evaluate((element) => element.getBoundingClientRect().height)).toBeGreaterThanOrEqual(38)
  await search.fill('존재하지않는회귀검색')
  await expect(dashboard).toContainText('조건에 맞는 결과가 없습니다.')
  expect(errors).toEqual([])
})
