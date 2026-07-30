import { expect, test } from '@playwright/test'

test('낙하 영상 비교 위젯은 기존 대시보드에서 20개 카드와 독립 오류 상태를 제공한다', async ({ page }) => {
  await page.route('**/api/drop-videos/drop-analysis/content', (route) => route.abort())
  await page.goto('/')
  await page.getByLabel('사용자 이름').fill('e2e-admin')
  await page.getByLabel('비밀번호').fill('e2e-validation-password')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.locator('.portfolio-page')).toBeVisible()

  await page.getByRole('button', { name: '해석 의뢰 현황', exact: true }).click()
  await page.locator('.view-tabs').getByRole('button', { name: /상세 분석/ }).click()

  const grid = page.getByTestId('drop-video-grid')
  await expect(grid).toBeVisible()
  await expect(grid.locator('.drop-video-card')).toHaveCount(20)
  await expect(grid.getByRole('button', { name: '전체 재생', exact: true })).toBeVisible()
  await expect(grid.getByRole('button', { name: '전체 정지', exact: true })).toBeVisible()
  await expect(grid.getByRole('button', { name: '처음부터', exact: true })).toBeVisible()
  await expect(grid.getByRole('button', { name: /반복 꺼짐/ })).toBeVisible()

  const videos = grid.locator('video')
  await expect(videos).toHaveCount(20)
  await expect(videos.first()).toHaveAttribute('preload', 'metadata')
  await expect(videos.first()).toHaveAttribute('playsinline', '')
  await expect(grid.locator('.drop-video-card').first().locator('.drop-video-error')).toBeVisible()
  await expect(grid.locator('.drop-video-card').nth(1)).toContainText('낙하 비교 Scene 01')

  await grid.getByRole('button', { name: /반복 꺼짐/ }).click()
  await expect(grid.getByRole('button', { name: /반복 켜짐/ })).toHaveAttribute('aria-pressed', 'true')
  await expect(videos.first()).toHaveAttribute('loop', '')
})
