import { expect, test } from '@playwright/test'
import { createHash } from 'node:crypto'
import { loginWorkspace } from './workspace-test-helpers'

test('PC 도우미 배포 API는 유지되고 설정 화면은 기본 메뉴에서 숨긴다', async ({ page }) => {
  const errors: string[] = []
  const helperRequests: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/local-helper/') || url.pathname.startsWith('/api/local-execution/')) helperRequests.push(url.pathname)
    if (url.origin === 'http://127.0.0.1:8766') helperRequests.push(url.pathname)
  })
  await page.route('http://127.0.0.1:8766/**', (route) => route.abort('connectionrefused'))
  const metadata = await page.request.get('/api/local-helper/distribution')
  expect(metadata.ok()).toBe(true)
  const release = await metadata.json() as { status: string; artifact_url: string; sha256: string; size_bytes: number }
  expect(release.status).toBe('ready')
  const archive = await page.request.get(release.artifact_url)
  expect(archive.ok()).toBe(true)
  const payload = await archive.body()
  expect(payload.byteLength).toBe(release.size_bytes)
  expect(createHash('sha256').update(payload).digest('hex')).toBe(release.sha256)

  await loginWorkspace(page, 'e2e-admin', '/workspace/settings/local-pc')
  await expect(page).toHaveURL(/\/workspace\/overview(?:\?|$)/)
  await expect(page.getByRole('complementary', { name: '주 메뉴' }).getByRole('link', { name: '내 PC 설정', exact: true })).toHaveCount(0)
  await expect(page.getByTestId('local-pc-settings')).toHaveCount(0)
  expect(helperRequests).toEqual([])
  expect(errors).toEqual([])
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
})
