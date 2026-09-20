import { expect, test, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

async function setFont(page: Page, current: number, target: number, mobile: boolean) {
  const sidebar = page.getByRole('complementary', { name: '주 메뉴' })
  if (mobile) await sidebar.getByRole('button', { name: '메뉴 열기', exact: true }).click()
  const button = sidebar.getByRole('button', { name: target < current ? '전체 글자 크기 줄이기' : '전체 글자 크기 늘리기', exact: true })
  for (let i = 0; i < Math.abs(target - current); i++) await button.click()
  if (target === 11 || target === 18) await expect(button).toBeDisabled()
  if (mobile) await sidebar.getByRole('button', { name: '메뉴 닫기', exact: true }).click()
}

for (const width of [1366, 1920]) {
  test(`P3 ${width}px pilot roles scale with 11/14/18pt and migrated Help keeps the root contract`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 1920 ? 1080 : 768 })
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await loginWorkspace(page, 'e2e-admin', '/')
    const pilot = page.getByTestId('result-overview-dashboard')
    const htmlFont = await page.locator('html').evaluate((element) => getComputedStyle(element).fontSize)
    await expect(pilot).toHaveAttribute('data-ui-density', 'v1')
    let current = 14
    for (const pt of [11, 14, 18]) {
      await setFont(page, current, pt, false)
      current = pt
      for (const theme of ['라이트', '다크']) {
        await page.getByRole('button', { name: theme, exact: true }).click()
        const measured = await pilot.evaluate((element) => {
          const font = (selector: string) => parseFloat(getComputedStyle(element.querySelector(selector)!).fontSize)
          return { title: font('h1'), section: font('h2'), body: font('.result-overview-record-name strong'), caption: font('.result-overview-record-name small'), input: font('input') }
        })
        const body = pt * 4 / 3
        expect(measured.title).toBeCloseTo(body * 1.5, 1)
        expect(measured.section).toBeCloseTo(body * 1.25, 1)
        expect(measured.body).toBeCloseTo(body, 1)
        expect(measured.caption).toBeCloseTo(body * .875, 1)
        expect(measured.input).toBeCloseTo(body, 1)
        await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
        await expect(page.locator('html')).toHaveCSS('font-size', htmlFont)
        await expect(page.locator('html')).not.toHaveAttribute('data-ui-density')
        await expect(page.locator('body')).not.toHaveAttribute('data-ui-density')
        if (process.env.GUI_QA_OUTPUT_DIR && (pt === 14 || pt === 18)) {
          mkdirSync(process.env.GUI_QA_OUTPUT_DIR, { recursive: true })
          await page.screenshot({ path: path.join(process.env.GUI_QA_OUTPUT_DIR, `p3-overview-${width}-${pt}-${theme === '라이트' ? 'light' : 'dark'}.png`) })
        }
      }
    }
    await page.reload()
    await expect(page.locator('.app-shell')).toHaveCSS('--ui-font-size', '18pt')
    await expect(pilot.locator('h1')).toHaveCSS('font-size', '36px')
    await openWorkspaceRoute(page, '/workspace/help')
    await expect(page.locator('.help-center')).toBeVisible()
    await expect(page.locator('.help-center p').first()).toHaveCSS('font-size', '24px')
    await expect(page.locator('.help-center')).toHaveAttribute('data-ui-density', 'v1')
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    expect(errors).toEqual([])
  })
}
