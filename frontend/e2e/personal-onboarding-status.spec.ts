import { expect, test } from '@playwright/test'
import { loginWorkspace } from './workspace-test-helpers'

test('도우미 배포 파일이 없으면 설치를 시작하지 않고 준비 상태를 안내한다', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.route('**/api/projects', (route) => route.fulfill({ json: [] }))
  await page.route('http://127.0.0.1:8766/**', (route) => route.abort('connectionrefused'))
  await page.route('**/api/local-helper/distribution', (route) => route.fulfill({ json: { status: 'unavailable', reason: '도우미 설치 파일이 아직 준비되지 않았습니다.' } }))
  await loginWorkspace(page, 'e2e-viewer', '/workspace/settings/local-pc')
  await expect(page).toHaveURL(/\/workspace\/settings\/local-pc/)
  await expect(page).not.toHaveTitle('')
  await expect(page.getByRole('heading', { name: 'PC 도우미 설치', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'PC 도우미 설치 파일 받기' })).toBeDisabled()
  await expect(page.getByText('도우미 설치 파일이 아직 준비되지 않았습니다.', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '설치 파일 다시 확인' }).click()
  await expect(page.getByRole('button', { name: 'PC 도우미 설치 파일 받기' })).toBeDisabled()
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  expect(errors).toEqual([])
})

test('인증을 끈 서버는 PC 연결 요청 전에 설정 안내를 표시한다', async ({ page }) => {
  await loginWorkspace(page, 'e2e-viewer', '/workspace/settings/local-pc')
  await page.route('**/api/auth/status', (route) => route.fulfill({ json: { mode: 'disabled', authentication_required: false, registration_enabled: false, oidc_start_url: null } }))
  let deviceRequests = 0
  await page.route('**/api/local-execution/devices', (route) => { deviceRequests += 1; return route.fulfill({ json: [] }) })
  await page.reload()
  await expect(page.getByText('서버 로그인 설정이 필요합니다.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'PC 도우미 설치 파일 받기' })).toHaveCount(0)
  await page.getByRole('button', { name: '로그인 설정 다시 확인' }).click()
  await expect(page.getByText('서버 로그인 설정이 필요합니다.', { exact: true })).toBeVisible()
  expect(deviceRequests).toBe(0)
})
