import { expect, test } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

for (const viewport of [
  { width: 1366, height: 768 },
  { width: 2560, height: 1440 },
  { width: 390, height: 844 },
]) {
  test(`P1 ${viewport.width}px keeps workspace gutters aligned and font preferences intact`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await loginWorkspace(page)
    const journey = page.getByRole('navigation', { name: '의뢰 작업 여정' })
    await journey.getByRole('button', { name: '작업 실행', exact: true }).click()
    await expect(page.locator('.work-item-detail')).toBeVisible()
    const selectedRequest = await page.getByLabel('의뢰 선택', { exact: true }).inputValue()
    const expectedPadding = viewport.width <= 620 ? 14 : Math.min(40, Math.max(24, viewport.width * .02))

    for (const theme of ['라이트', '다크']) {
      await page.getByRole('button', { name: theme, exact: true }).click()
      await expect(page.locator('.app-shell')).toHaveCSS('--ui-font-size', '14pt')
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      const boxes = await page.evaluate(() => {
        const sample = (selector: string) => {
          const element = document.querySelector<HTMLElement>(selector)!
          const box = element.getBoundingClientRect()
          const style = getComputedStyle(element)
          return { x: box.x, width: box.width, padding: Number.parseFloat(style.paddingLeft), maxWidth: style.maxWidth }
        }
        return {
          header: sample('.request-workspace-header'),
          topbar: sample('.topbar'),
          journey: sample('.request-journey'),
          workbench: sample('.workbench-page.assigned-only'),
          detail: sample('.work-item-detail'),
          list: sample('.assigned-work-list'),
        }
      })
      for (const item of [boxes.header, boxes.topbar, boxes.workbench]) {
        expect(item.padding).toBeCloseTo(expectedPadding, 1)
      }
      expect(boxes.journey.x).toBeCloseTo(boxes.header.x + expectedPadding, 1)
      expect(boxes.workbench.x + expectedPadding).toBeCloseTo(boxes.journey.x, 1)
      expect(boxes.detail.maxWidth).toBe('none')
      expect(boxes.list.maxWidth).toBe('none')
      await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue(selectedRequest)
      if (process.env.GUI_QA_OUTPUT_DIR) {
        mkdirSync(process.env.GUI_QA_OUTPUT_DIR, { recursive: true })
        await page.screenshot({ path: path.join(process.env.GUI_QA_OUTPUT_DIR, `p1-execution-${viewport.width}-${theme === '라이트' ? 'light' : 'dark'}.png`) })
      }
    }

    const sidebar = page.getByRole('complementary', { name: '주 메뉴' })
    if (viewport.width <= 620) await sidebar.getByRole('button', { name: '메뉴 열기', exact: true }).click()
    for (let step = 0; step < 4; step += 1) {
      await sidebar.getByRole('button', { name: '전체 글자 크기 늘리기', exact: true }).click()
    }
    await expect(sidebar.getByRole('button', { name: '전체 글자 크기 늘리기', exact: true })).toBeDisabled()
    if (viewport.width <= 620) await sidebar.getByRole('button', { name: '메뉴 닫기', exact: true }).click()
    await expect(page.locator('.app-shell')).toHaveCSS('--ui-font-size', '18pt')
    await expect(page.locator('.app-shell')).toHaveCSS('font-size', '24px')
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.reload()
    await expect(page.locator('.app-shell')).toHaveCSS('--ui-font-size', '18pt')
    await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue(selectedRequest)
    await openWorkspaceRoute(page, '/workspace/help')
    await expect(page.locator('.help-center')).toBeVisible()
    expect(await page.locator('.help-center').evaluate((element) => getComputedStyle(element).maxWidth)).toBe('1450px')
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}
