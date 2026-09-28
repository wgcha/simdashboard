import { expect, test } from '@playwright/test'

test('personal-only accounts fall back to the no-project workspace and retain password settings', async ({ page }) => {
  const helperRequests: string[] = []
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/local-helper/') || url.pathname.startsWith('/api/local-execution/')) helperRequests.push(url.pathname)
    if (url.origin === 'http://127.0.0.1:8766') helperRequests.push(url.pathname)
  })
  await page.route('**/api/auth/status', (route) => route.fulfill({ json: {
    mode: 'password', authentication_required: true, registration_enabled: false, setup_required: false, oidc_start_url: null,
  } }))
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: {
    id: 'e2e-personal-only', username: 'e2e-personal-only', display_name: 'E2E 개인 계정', employee_id: null,
    account_status: 'ACTIVE', is_global_admin: false, memberships: [], company_permissions: [],
  } }))

  await page.goto('/workspace/settings/local-pc')
  await expect(page).toHaveURL(/\/workspace\/overview(?:\?|$)/)
  await expect(page.getByText('접근 가능한 프로젝트가 없습니다.', { exact: true })).toBeVisible()
  await expect(page.getByTestId('local-pc-settings')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '내 PC 설정', exact: true })).toHaveCount(0)
  expect(helperRequests).toEqual([])

  await page.getByRole('button', { name: '계정 비밀번호 변경', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '계정 보안 설정' })
  await expect(dialog.getByRole('heading', { name: '비밀번호 변경', exact: true })).toBeVisible()
  await dialog.getByLabel('현재 비밀번호').fill('synthetic-current')
  await dialog.getByRole('button', { name: '계정 보안 설정 닫기' }).click()
  await expect(dialog).toBeHidden()
  await page.getByRole('button', { name: '계정 비밀번호 변경', exact: true }).click()
  await expect(page.getByLabel('현재 비밀번호')).toHaveValue('')
  expect(helperRequests).toEqual([])
})
