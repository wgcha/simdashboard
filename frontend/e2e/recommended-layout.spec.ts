import { expect, test, type Locator, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import type { DashboardDefinition } from '../src/types'
import { loginWorkspace } from './workspace-test-helpers'

const endpoint = '/api/dashboards/dashboard-drop-default'
async function enterResults(page: Page) {
  await page.locator('.request-journey').getByRole('button', { name: /결과 검토|상세 분석/ }).click()
  await expect(page.locator('.widget-video_grid').first()).toBeVisible()
}
async function setupScrambled(page: Page) {
  const original = await (await page.request.get(endpoint)).json() as DashboardDefinition
  const ordered = [...original.widgets.filter(w => w.type !== 'video_grid' && w.type !== 'video')].reverse().concat(original.widgets.filter(w => w.type === 'video_grid' || w.type === 'video'))
  let y = 0
  const positions = new Map(ordered.map(w => { const position = { x: 0, y }; y += w.h; return [w.id, position] }))
  const fixture = { ...original, widgets: original.widgets.map(w => ({ ...w, ...positions.get(w.id), ...(w.type === 'video_grid' ? { settings: { ...w.settings, videoColumns: 5, videoRows: 4 } } : {}) })) }
  const response = await page.request.put(endpoint, { data: fixture })
  expect(response.ok(), await response.text()).toBeTruthy()
  await page.reload()
  await enterResults(page)
  return { original, fixture: await (await page.request.get(endpoint)).json() as DashboardDefinition }
}
async function positions(preview: Locator) {
  return preview.locator('[data-widget-id]').evaluateAll(elements => elements.map(element => ({ id: element.getAttribute('data-widget-id')!, x: Number(element.getAttribute('data-x')), y: Number(element.getAttribute('data-y')), w: Number(element.getAttribute('data-w')), h: Number(element.getAttribute('data-h')) }))).then(items => items.sort((a, b) => a.id.localeCompare(b.id)))
}
async function capture(page: Page, name: string) {
  const directory = process.env.GUI_QA_OUTPUT_DIR
  if (directory) { mkdirSync(directory, { recursive: true }); await page.screenshot({ path: path.join(directory, name) }) }
}

test('추천 배치를 미리 보고 닫기·복원·명시적 저장 후에도 위젯 설정과 영상 5×4를 보존한다', async ({ page }) => {
  await page.setViewportSize({ width: 1505, height: 1045 })
  await loginWorkspace(page)
  const { original, fixture } = await setupScrambled(page)
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  let writes = 0
  page.on('request', request => { if (request.url().endsWith(endpoint) && request.method() === 'PUT') writes++ })
  try {
    await expect(page.getByRole('button', { name: '추천 배치 미리보기', exact: true })).toHaveCount(0)
    await page.getByRole('button', { name: '대시보드 편집', exact: true }).click()
    const trigger = page.getByRole('button', { name: '추천 배치 미리보기', exact: true })
    await trigger.click()
    const preview = page.getByTestId('recommended-layout-preview')
    const before = await positions(page.getByTestId('layout-preview-current'))
    const proposed = await positions(page.getByTestId('layout-preview-proposed'))
    expect(proposed).not.toEqual(before)
    const videosBefore = await page.locator('video').count()
    await capture(page, 'recommended-layout-desktop.png')
    await page.keyboard.press('Escape')
    await expect(preview).not.toBeVisible()
    await expect(trigger).toBeFocused()
    expect(await page.locator('video').count()).toBe(videosBefore)
    expect(writes).toBe(0)
    await trigger.click()
    expect(await positions(page.getByTestId('layout-preview-current'))).toEqual(before)
    await preview.getByRole('button', { name: '편집안에 적용', exact: true }).click()
    expect(writes).toBe(0)
    await page.getByRole('button', { name: '적용 전 배치로', exact: true }).click()
    await trigger.click()
    expect(await positions(page.getByTestId('layout-preview-current'))).toEqual(before)
    await preview.getByRole('button', { name: '편집안에 적용', exact: true }).click()
    const savedResponse = page.waitForResponse(response => response.url().endsWith(endpoint) && response.request().method() === 'PUT')
    await page.getByRole('button', { name: '레이아웃 저장', exact: true }).click()
    expect((await savedResponse).ok()).toBeTruthy()
    expect(writes).toBe(1)
    const saved = await (await page.request.get(endpoint)).json() as DashboardDefinition
    for (const widget of saved.widgets) {
      const initial = fixture.widgets.find(item => item.id === widget.id)!
      const position = proposed.find(item => item.id === widget.id)!
      expect({ ...widget, settings: widget.settings ?? {} }).toEqual({ ...initial, settings: initial.settings ?? {}, x: position.x, y: position.y })
    }
    const video = saved.widgets.find(item => item.type === 'video_grid')!
    expect(video.y).toBeGreaterThanOrEqual(Math.max(...saved.widgets.filter(item => item.type !== 'video_grid' && item.type !== 'video').map(item => item.y + item.h)))
    await page.reload()
    await enterResults(page)
    expect((await (await page.request.get(endpoint)).json()).widgets).toEqual(saved.widgets)
    await expect(page.locator('.widget-video_grid').first().locator('.drop-video-card')).toHaveCount(20)
    expect(errors).toEqual([])
  } finally { await page.request.put(endpoint, { data: original }) }
})

test('적용 후 설정을 수정하면 과거 배치 복원이 새 편집을 덮어쓰지 않으며 모바일에서 미리보기를 닫을 수 있다', async ({ page }) => {
  await loginWorkspace(page)
  const { original } = await setupScrambled(page)
  try {
    await page.getByRole('button', { name: '대시보드 편집', exact: true }).click()
    await page.getByRole('button', { name: '추천 배치 미리보기', exact: true }).click()
    await page.getByTestId('recommended-layout-preview').getByRole('button', { name: '편집안에 적용', exact: true }).click()
    const video = page.locator('.widget-video_grid').first()
    const title = await video.getByRole('heading').first().innerText()
    await video.getByRole('button', { name: `${title} 설정`, exact: true }).click()
    await page.getByLabel('영상 가로 열 수', { exact: true }).selectOption('4')
    await page.getByRole('button', { name: '설정 완료', exact: true }).click()
    const undo = page.getByRole('button', { name: '적용 전 배치로', exact: true })
    await expect.poll(async () => (await undo.count()) === 0 || await undo.isDisabled()).toBeTruthy()
    await expect(video.locator('.drop-video-card')).toHaveCount(16)
    await page.setViewportSize({ width: 390, height: 844 })
    await page.getByRole('button', { name: '추천 배치 미리보기', exact: true }).click()
    const preview = page.getByTestId('recommended-layout-preview')
    await expect(preview).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
    await capture(page, 'recommended-layout-mobile.png')
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: '편집 취소', exact: true }).click()
    await expect(page.getByRole('button', { name: '추천 배치 미리보기', exact: true })).toHaveCount(0)
  } finally { await page.request.put(endpoint, { data: original }) }
})

test('조회 권한을 유지하고 이미 정돈된 영구변형·단일 Run 비교 화면은 변경하지 않는다', async ({ page }) => {
  await loginWorkspace(page, 'e2e-viewer')
  await enterResults(page)
  await expect(page.getByRole('button', { name: '추천 배치 미리보기', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: /로그아웃/ }).click()
  await page.getByLabel('사용자 이름').fill('e2e-admin')
  await page.getByLabel('비밀번호').fill('e2e-validation-password')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await enterResults(page)
  await page.locator('.analysis-tab').filter({ hasText: '영구변형' }).click()
  await expect(page.locator('.widget-chassis_summary')).toBeVisible()
  await page.getByRole('button', { name: '대시보드 편집', exact: true }).click()
  await page.getByRole('button', { name: '추천 배치 미리보기', exact: true }).click()
  await expect(page.getByTestId('recommended-layout-preview').getByRole('button', { name: '편집안에 적용', exact: true })).toBeDisabled()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '편집 취소', exact: true }).click()
  await page.locator('.analysis-tab').filter({ hasText: 'Run 비교' }).click()
  await expect(page.getByTestId('comparison-evidence-table')).toBeVisible()
  await page.getByRole('button', { name: '대시보드 편집', exact: true }).click()
  await page.getByRole('button', { name: '추천 배치 미리보기', exact: true }).click()
  await expect(page.getByTestId('recommended-layout-preview').getByRole('button', { name: '편집안에 적용', exact: true })).toBeDisabled()
})
