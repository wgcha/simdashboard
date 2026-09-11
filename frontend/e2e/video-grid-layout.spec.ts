import { expect, test } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import type { DashboardDefinition } from '../src/types'
import { loginWorkspace } from './workspace-test-helpers'

test('영상 열·행을 5×4로 저장·복원하고 모바일에서도 설정을 보존한다', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.setViewportSize({ width: 1600, height: 1000 })
  await loginWorkspace(page)
  await page.locator('.request-journey').getByRole('button', { name: /결과 검토|상세 분석/ }).click()
  const endpoint = '/api/dashboards/dashboard-drop-default'
  const original = await (await page.request.get(endpoint)).json() as DashboardDefinition
  const originalVideo = original.widgets.find((widget) => widget.type === 'video_grid')!
  const widget = page.locator('.widget-video_grid').first()
  const title = await widget.getByRole('heading').first().innerText()
  try {
    await page.getByRole('button', { name: '대시보드 편집', exact: true }).click()
    await widget.getByRole('button', { name: `${title} 설정`, exact: true }).click()
    const columns = page.getByLabel('영상 가로 열 수', { exact: true })
    const rows = page.getByLabel('영상 세로 행 수', { exact: true })
    await expect(columns).toHaveValue('2')
    await expect(rows).toHaveValue('2')
    await columns.selectOption('1')
    await rows.selectOption('3')
    await expect(widget.locator('.drop-video-card')).toHaveCount(3)
    await rows.selectOption('2')
    await columns.selectOption('5')
    await expect(rows).toHaveValue('2')
    await rows.selectOption('4')
    await expect(rows.locator('option[value="5"]')).toHaveAttribute('disabled', '')
    await expect(page.getByRole('status').filter({ hasText: '5열 × 4행' })).toBeVisible()
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
    const grid = widget.locator('.drop-video-grid')
    await expect.poll(() => grid.evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(5)
    await expect.poll(() => grid.evaluate((element) => getComputedStyle(element).gridTemplateRows.split(' ').length)).toBe(4)
    await widget.locator('.drop-video-card').last().scrollIntoViewIfNeeded()
    await expect(widget.locator('.drop-video-card').last()).toBeInViewport()
    await grid.evaluate((element) => { element.scrollTop = 0 })
    await expect.poll(() => widget.locator('video').first().evaluate((element) => element.readyState)).toBeGreaterThanOrEqual(2)
    if (process.env.GUI_QA_OUTPUT_DIR) {
      mkdirSync(process.env.GUI_QA_OUTPUT_DIR, { recursive: true })
      await page.screenshot({ path: path.join(process.env.GUI_QA_OUTPUT_DIR, 'video-grid-5x4.png') })
    }
    await page.setViewportSize({ width: 390, height: 844 })
    await expect.poll(() => grid.evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(1)
    await expect(widget.locator('.drop-video-card')).toHaveCount(20)
    await expect.poll(() => widget.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
    await page.keyboard.press('Escape')
    expect((await (await page.request.get(endpoint)).json()).widgets.find((item: { id: string }) => item.id === originalVideo.id).settings).toMatchObject({ videoColumns: 5, videoRows: 4 })
    expect(errors).toEqual([])
  } finally {
    await page.request.put(endpoint, { data: original })
  }
})
