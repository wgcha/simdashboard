import { expect, test, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

async function capture(page: Page, filename: string) {
  const directory = process.env.GUI_QA_OUTPUT_DIR
  if (!directory) return
  mkdirSync(directory, { recursive: true })
  await page.screenshot({ path: path.join(directory, filename), fullPage: false })
}

test('결과 위젯은 내용을 유지한 채 확대하고 Escape로 원래 위치와 포커스로 돌아온다', async ({ page }) => {
  const pageErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.setViewportSize({ width: 920, height: 700 })
  await page.goto('/')
  await page.getByLabel('사용자 이름').fill('e2e-admin')
  await page.getByLabel('비밀번호').fill('e2e-validation-password')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await openWorkspaceRoute(page, '/workspace/requests')
  await page.locator('.request-journey').getByRole('button', { name: /결과 검토|상세 분석/ }).click()

  const widget = page.locator('dialog.widget-card').first()
  await expect(widget).toBeVisible()
  await expect(widget).toHaveRole('article')
  await widget.locator('.widget-body').evaluate((element) => { (window as Window & { widgetFocusContent?: Element }).widgetFocusContent = element })
  const title = await widget.getByRole('heading').textContent()
  expect(title).toBeTruthy()
  const expand = widget.getByRole('button', { name: `${title} 확대 보기` })
  await expand.focus()
  await expand.click()

  const focusedWidget = page.locator('dialog.widget-card[aria-modal="true"]')
  await expect(focusedWidget).toBeVisible()
  await expect(focusedWidget).toHaveRole('dialog')
  await expect.poll(() => focusedWidget.locator('.widget-body').evaluate((element) => element === (window as Window & { widgetFocusContent?: Element }).widgetFocusContent)).toBe(true)
  await expect(focusedWidget).toHaveCSS('width', /8\d\dpx/)
  await expect(focusedWidget.getByRole('button', { name: `${title} 원래 크기로` })).toBeFocused()
  await capture(page, 'widget-focus-desktop.png')
  await page.keyboard.press('Escape')
  await expect(focusedWidget).toHaveCount(0)
  await expect(expand).toBeFocused()
  await expect(widget).toHaveRole('article')

  await page.setViewportSize({ width: 390, height: 844 })
  await expand.click()
  await expect(focusedWidget).toBeVisible()
  await expect.poll(() => focusedWidget.evaluate((element) => element.getBoundingClientRect().width <= innerWidth - 15)).toBe(true)
  await capture(page, 'widget-focus-mobile.png')
  await page.keyboard.press('Escape')
  await expect(pageErrors).toEqual([])
})

test('영상 위젯 확대는 재생 위치와 선택을 보존하고 표시 목록을 탐색할 수 있다', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  await loginWorkspace(page)
  await page.locator('.request-journey').getByRole('button', { name: /결과 검토|상세 분석/ }).click()
  const widget = page.locator('dialog.widget-video_grid').first()
  await expect(widget).toBeVisible()
  const video = widget.locator('video').first()
  await expect.poll(() => video.evaluate((element) => element.readyState)).toBeGreaterThanOrEqual(1)
  await video.evaluate((element) => { element.pause(); element.currentTime = Math.min(1, element.duration / 2); element.dataset.focusIdentity = 'original-video' })
  const time = await video.evaluate((element) => element.currentTime)
  await widget.getByRole('button', { name: /확대 보기$/ }).click()
  await expect(widget).toHaveAttribute('aria-modal', 'true')
  await expect(video).toHaveAttribute('data-focus-identity', 'original-video')
  expect(await video.evaluate((element) => element.currentTime)).toBeCloseTo(time, 1)
  await expect(widget.locator('.drop-video-card')).toHaveCount(4)
  await expect.poll(() => widget.locator('video').evaluateAll((items) => items.every((item) => item.getBoundingClientRect().bottom <= innerHeight && item.getBoundingClientRect().top >= 0))).toBe(true)
  await capture(page, 'video-focus-desktop.png')
  await widget.getByRole('button', { name: '다음 영상', exact: true }).click()
  await expect(widget.locator('.drop-video-media-heading')).toContainText('5–8')
  await expect(widget.locator('.drop-video-card').first()).toHaveAttribute('aria-current', 'true')
  await expect.poll(() => widget.locator('video').first().evaluate((element) => element.readyState)).toBeGreaterThanOrEqual(2)
  await page.setViewportSize({ width: 390, height: 844 })
  await expect.poll(() => widget.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
  await widget.locator('.drop-video-dashboard').evaluate((element) => { element.scrollTop = 0 })
  await capture(page, 'video-focus-mobile.png')
  await page.keyboard.press('Escape')
  await expect(widget.getByRole('button', { name: /확대 보기$/ })).toBeFocused()
})
