import { expect, test } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import type { DashboardDefinition } from '../src/types'
import { loginWorkspace } from './workspace-test-helpers'

test('영상 열·행을 5×4로 저장·복원하고 모바일에서도 설정을 보존한다', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.setViewportSize({ width: 2560, height: 1440 })
  await loginWorkspace(page)
  await page.locator('.request-journey').getByRole('button', { name: /결과 검토|상세 분석/ }).click()
  const endpoint = '/api/dashboards/dashboard-drop-default'
  const original = await (await page.request.get(endpoint)).json() as DashboardDefinition
  const originalVideo = original.widgets.find((widget) => widget.type === 'video_grid')!
  const widget = page.locator('.widget-video_grid').first()
  const title = await widget.getByRole('heading').first().innerText()
  try {
    await expect(widget.getByRole('status').filter({ hasText: '가로 2 × 세로 2' })).toBeVisible()
    await page.getByRole('button', { name: '영상 배치 편집', exact: true }).click()
    await widget.getByRole('button', { name: `${title} 설정`, exact: true }).click()
    const columns = page.getByLabel('영상 가로 열 수', { exact: true })
    const rows = page.getByLabel('영상 세로 행 수', { exact: true })
    await expect(columns).toHaveValue('2')
    await expect(rows).toHaveValue('2')
    if (process.env.GUI_QA_OUTPUT_DIR) {
      mkdirSync(process.env.GUI_QA_OUTPUT_DIR, { recursive: true })
      await page.screenshot({ path: path.join(process.env.GUI_QA_OUTPUT_DIR, 'video-grid-settings.png') })
    }
    await page.getByRole('button', { name: '5 × 4', exact: true }).click()
    await expect(columns).toHaveValue('5')
    await expect(rows).toHaveValue('4')
    await page.getByRole('button', { name: '2 × 2', exact: true }).click()
    await columns.selectOption('1')
    await rows.selectOption('3')
    await expect(widget.locator('.drop-video-card')).toHaveCount(3)
    await rows.selectOption('2')
    await columns.selectOption('5')
    await expect(rows).toHaveValue('2')
    await rows.selectOption('4')
    await expect(rows.locator('option[value="5"]')).toHaveAttribute('disabled', '')
    await expect(page.getByRole('status').filter({ hasText: '현재 배치: 가로 5 × 세로 4' })).toBeVisible()
    await page.getByRole('button', { name: '설정 완료', exact: true }).click()
    await expect(widget.locator('.drop-video-card')).toHaveCount(20)
    const savedResponse = page.waitForResponse((response) => response.url().endsWith(endpoint) && response.request().method() === 'PUT')
    await page.getByRole('button', { name: '레이아웃 저장', exact: true }).click()
    expect((await savedResponse).ok()).toBe(true)
    const saved = await (await page.request.get(endpoint)).json() as DashboardDefinition
    const savedVideo = saved.widgets.find((item) => item.id === originalVideo.id)!
    expect(savedVideo).toEqual({ ...originalVideo, settings: { ...originalVideo.settings, videoColumns: 5, videoRows: 4 } })
    await page.reload()
    await expect(page.locator('.request-journey')).toBeVisible()
    await page.locator('.request-journey').getByRole('button', { name: /결과 검토|상세 분석/ }).click()
    await expect(widget.locator('.drop-video-card')).toHaveCount(20)
    await widget.getByRole('button', { name: /확대 보기$/ }).click()
    const modalBounds = await widget.boundingBox()
    expect(modalBounds?.width).toBeGreaterThanOrEqual(2500)
    expect(modalBounds?.height).toBeGreaterThanOrEqual(1370)
    const grid = widget.locator('.drop-video-grid')
    await expect.poll(() => grid.evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(5)
    await expect.poll(() => grid.evaluate((element) => getComputedStyle(element).gridTemplateRows.split(' ').length)).toBe(4)
    await expect.poll(() => grid.evaluate((element) => element.scrollHeight <= element.clientHeight + 1)).toBe(true)
    await widget.locator('.drop-video-card').last().scrollIntoViewIfNeeded()
    await expect(widget.locator('.drop-video-card').last()).toBeInViewport()
    await grid.evaluate((element) => { element.scrollTop = 0 })
    await expect.poll(() => widget.locator('video').evaluateAll((elements) => elements.every((element) => (element as HTMLVideoElement).readyState >= 2)), { timeout: 60000 }).toBe(true)
    await expect.poll(() => widget.locator('.drop-video-frame').first().evaluate((element) => element.clientHeight)).toBeGreaterThan(130)
    if (process.env.GUI_QA_OUTPUT_DIR) {
      mkdirSync(process.env.GUI_QA_OUTPUT_DIR, { recursive: true })
      await page.screenshot({ path: path.join(process.env.GUI_QA_OUTPUT_DIR, 'video-grid-5x4.png') })
    }
    await page.setViewportSize({ width: 1366, height: 768 })
    await expect(widget.getByRole('button', { name: /원래 크기로$/ })).toBeInViewport()
    await expect(widget.locator('.drop-video-card')).toHaveCount(20)
    await page.setViewportSize({ width: 390, height: 844 })
    await expect.poll(() => grid.evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(1)
    await expect(widget.locator('.drop-video-card')).toHaveCount(20)
    await expect.poll(() => widget.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
    if (process.env.GUI_QA_OUTPUT_DIR) await page.screenshot({ path: path.join(process.env.GUI_QA_OUTPUT_DIR, 'video-grid-mobile.png') })
    await page.keyboard.press('Escape')
    expect((await (await page.request.get(endpoint)).json()).widgets.find((item: { id: string }) => item.id === originalVideo.id).settings).toMatchObject({ videoColumns: 5, videoRows: 4 })
    expect(errors).toEqual([])
  } finally {
    await page.request.put(endpoint, { data: original })
  }
})


test('조회자는 영상 배치 편집을 실행할 수 없다', async ({ page }) => {
  await loginWorkspace(page, 'e2e-viewer')
  await page.locator('.request-journey').getByRole('button', { name: /결과 검토|상세 분석/ }).click()
  await expect(page.locator('.widget-video_grid').first()).toBeVisible()
  await expect(page.getByRole('button', { name: '영상 배치 편집', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '대시보드 편집', exact: true })).toHaveCount(0)
})
