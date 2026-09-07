import { openWorkspaceRoute } from './workspace-test-helpers'
import { expect, test, type Page } from '@playwright/test'

async function loginAndOpenVideoDashboard(page: Page) {
  await page.goto('/')
  await page.getByLabel('사용자 이름').fill('e2e-admin')
  await page.getByLabel('비밀번호').fill('e2e-validation-password')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.locator('.portfolio-page')).toBeVisible()
  await openVideoDashboard(page)
}

async function openVideoDashboard(page: Page) {
  await openWorkspaceRoute(page, '/workspace/requests')
  await page.locator('.request-journey').getByRole('button', { name: /결과 검토|상세 분석/ }).click()
  await expect(page.getByTestId('drop-video-grid').first()).toBeVisible()
}

test('H.264 영상 20개를 실제 재생하고 synthetic 판정·일괄 제어·반응형 배치를 제공한다', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 })
  await loginAndOpenVideoDashboard(page)

  const grid = page.getByTestId('drop-video-grid')
  const cards = grid.locator('.drop-video-card')
  const videos = grid.locator('video')
  await expect(cards).toHaveCount(20)
  await expect(videos).toHaveCount(20)
  await expect(cards.locator('.drop-video-mini-bar')).toHaveCount(40)
  await expect(cards.filter({ hasText: 'H264 · FAST START' })).toHaveCount(20)
  await expect(cards.locator('.drop-video-evaluation-badge.pass')).toHaveCount(9)
  await expect(cards.locator('.drop-video-evaluation-badge.fail')).toHaveCount(11)
  await expect(grid.getByTestId('drop-video-summary')).toContainText('SYNTHETIC EVALUATION')
  await expect(grid.getByTestId('drop-video-summary')).toContainText('9')
  await expect(grid.getByTestId('drop-video-summary')).toContainText('11')
  await expect(videos.first()).toHaveAttribute('preload', 'metadata')
  await expect(videos.first()).toHaveAttribute('playsinline', '')

  await grid.getByRole('button', { name: '전체 재생', exact: true }).click()
  await expect.poll(async () => videos.evaluateAll((items) => items.filter((item) => {
    const video = item as HTMLVideoElement
    return video.error === null && video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && video.videoWidth > 0 && Number.isFinite(video.duration) && video.duration > 0 && video.currentTime > 0
  }).length), { timeout: 30_000 }).toBe(20)

  await grid.getByRole('button', { name: '전체 정지', exact: true }).click()
  await expect.poll(() => videos.evaluateAll((items) => items.every((item) => (item as HTMLVideoElement).paused))).toBe(true)

  await videos.evaluateAll((items) => items.forEach((item) => {
    const video = item as HTMLVideoElement
    video.currentTime = video.duration * 0.8
  }))
  await grid.getByRole('button', { name: '처음부터', exact: true }).click()
  await expect.poll(() => videos.evaluateAll((items) => items.every((item) => {
    const video = item as HTMLVideoElement
    return video.duration > 0 && video.currentTime / video.duration < 0.5
  })), { timeout: 10_000 }).toBe(true)
  await grid.getByRole('button', { name: '전체 정지', exact: true }).click()

  await grid.getByRole('button', { name: /반복 꺼짐/ }).click()
  await expect(grid.getByRole('button', { name: /반복 켜짐/ })).toHaveAttribute('aria-pressed', 'true')
  await expect.poll(() => videos.evaluateAll((items) => items.every((item) => (item as HTMLVideoElement).loop))).toBe(true)

  await cards.nth(1).focus()
  await cards.nth(1).press('Enter')
  await expect(cards.nth(1)).toHaveAttribute('aria-current', 'true')
  await expect(grid.locator('.drop-video-selected')).toContainText('낙하 비교 Scene 01')

  const columnCount = () => grid.locator('.drop-video-grid').evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(' ').length)
  await expect.poll(columnCount).toBe(4)
  await page.setViewportSize({ width: 1100, height: 900 })
  await expect.poll(columnCount).toBe(3)
  await page.setViewportSize({ width: 850, height: 900 })
  await expect.poll(columnCount).toBe(2)
  await page.setViewportSize({ width: 600, height: 900 })
  await expect.poll(columnCount).toBe(1)
})

test('PASS 평가 영상의 재생 실패는 green verdict border를 유지하고 다른 영상 재생을 막지 않는다', async ({ page }) => {
  await page.route('**/api/drop-videos/drop-analysis/content', (route) => route.abort())
  await loginAndOpenVideoDashboard(page)

  const grid = page.getByTestId('drop-video-grid')
  const cards = grid.locator('.drop-video-card')
  const first = cards.first()
  await expect(first).toHaveClass(/evaluation-pass/)
  await expect(first).toHaveClass(/media-error/)
  await expect(first.locator('.drop-video-error')).toBeVisible()
  expect(await first.evaluate((element) => getComputedStyle(element).borderTopColor)).toBe('rgb(49, 134, 106)')

  const healthy = grid.locator('video').nth(4)
  await expect.poll(() => healthy.evaluate((video) => video.videoWidth), { timeout: 20_000 }).toBeGreaterThan(0)
  await expect.poll(() => healthy.evaluate((video) => video.error?.code ?? null)).toBeNull()
  await healthy.evaluate((video) => video.play())
  await expect.poll(() => healthy.evaluate((video) => video.currentTime), { timeout: 10_000 }).toBeGreaterThan(0)
})

test('video_grid를 카탈로그에서 추가해 이동·리사이즈·저장하고 reload 후 복원한다', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 })
  await loginAndOpenVideoDashboard(page)
  const original = await page.evaluate(async () => (await fetch('/api/dashboards/dashboard-drop-default')).json())

  try {
    await page.getByRole('button', { name: '대시보드 편집', exact: true }).click()
    await page.getByRole('button', { name: '위젯 추가·버전 관리', exact: true }).click()
    await page.locator('.catalog-editor').getByRole('button', { name: '낙하 영상 비교', exact: true }).click()
    await expect(page.locator('.widget-video_grid')).toHaveCount(2)
    await page.locator('.assistant-drawer .drawer-head > button').click()

    const addedWidget = page.locator('.widget-video_grid').filter({ has: page.getByRole('heading', { name: '낙하 영상 비교', exact: true }) })
    const gridItem = addedWidget.locator('..')
    await gridItem.scrollIntoViewIfNeeded()
    const before = await gridItem.boundingBox()
    if (!before) throw new Error('추가한 video_grid의 위치를 찾지 못했습니다.')

    const resizeHandle = gridItem.locator('.react-resizable-handle')
    await resizeHandle.scrollIntoViewIfNeeded()
    const resizeBox = await resizeHandle.boundingBox()
    if (!resizeBox) throw new Error('video_grid resize handle을 찾지 못했습니다.')
    // The add-widget toast overlaps the handle's lower half, so start in its unobscured corner.
    const resizeStartX = resizeBox.x + 5
    const resizeStartY = resizeBox.y + 5
    await page.mouse.move(resizeStartX, resizeStartY)
    await page.mouse.down()
    await page.mouse.move(resizeStartX - 180, resizeStartY - 90, { steps: 12 })
    await page.mouse.up()

    await expect.poll(async () => (await gridItem.boundingBox())?.width ?? before.width).toBeLessThan(before.width)

    const dragHandle = addedWidget.getByLabel('낙하 영상 비교 이동 손잡이')
    await dragHandle.scrollIntoViewIfNeeded()
    const dragBox = await dragHandle.boundingBox()
    if (!dragBox) throw new Error('video_grid drag handle을 찾지 못했습니다.')
    await page.mouse.move(dragBox.x + dragBox.width / 2, dragBox.y + dragBox.height / 2)
    await page.mouse.down()
    await page.mouse.move(dragBox.x + 120, dragBox.y - 100, { steps: 12 })
    await page.mouse.up()

    const after = await gridItem.boundingBox()
    expect(Boolean(after && (Math.abs(after.x - before.x) > 5 || Math.abs(after.y - before.y) > 5))).toBe(true)

    const saveResponse = page.waitForResponse((response) => response.url().includes('/api/dashboards/dashboard-drop-default') && response.request().method() === 'PUT')
    await page.getByRole('button', { name: '레이아웃 저장', exact: true }).click()
    expect((await saveResponse).status()).toBe(200)

    const stored = await page.evaluate(async () => (await fetch('/api/dashboards/dashboard-drop-default')).json())
    const added = stored.widgets.find((widget: { id: string; title: string }) => widget.title === '낙하 영상 비교')
    expect(added).toMatchObject({ type: 'video_grid' })
    expect(added.w).toBeLessThan(12)
    expect(added.y).not.toBe(30)

    await page.reload()
    await expect(page).toHaveURL(/\/workspace\/requests(?:\?|$)/)
    await openVideoDashboard(page)
    await expect(page.locator('.widget-video_grid')).toHaveCount(2)
  } finally {
    await page.evaluate(async (definition) => {
      await fetch('/api/dashboards/dashboard-drop-default', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(definition),
      })
    }, original)
  }
})
