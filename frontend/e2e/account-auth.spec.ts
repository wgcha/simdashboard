import { expect, test } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

test('개인 회원가입 신청 후 관리자 승인 안내와 로그인 화면으로 돌아간다', async ({ page }) => {
  let registration: Record<string, string> | undefined
  await page.route('**/api/auth/status', (route) => route.fulfill({ json: {
    mode: 'password', authentication_required: true, registration_enabled: true, oidc_start_url: null,
  } }))
  await page.route('**/api/auth/me', (route) => route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ detail: '로그인 필요' }) }))
  await page.route('**/api/auth/register', async (route) => {
    registration = JSON.parse(route.request().postData() ?? '{}') as Record<string, string>
    await route.fulfill({ status: 201, json: { user_id: 'pending-e2e-user', username: registration.username, account_status: 'PENDING', message: '가입 신청이 접수되었습니다.' } })
  })

  await page.goto('/')
  await page.getByRole('button', { name: '개인 회원가입', exact: true }).click()
  await expect(page.getByRole('heading', { name: '개인 회원가입', exact: true })).toBeVisible()
  await page.getByLabel('사용자 이름').fill('e2e-personal-user')
  await page.getByLabel('표시 이름').fill('E2E 개인 사용자')
  const password = page.getByLabel('비밀번호', { exact: true })
  await expect(password).toHaveAttribute('minlength', '8')
  await password.fill('test123')
  await page.getByLabel('비밀번호 확인').fill('test123')
  await page.getByRole('button', { name: '회원가입 신청', exact: true }).click()
  expect(await password.evaluate((element: HTMLInputElement) => element.validity.tooShort)).toBe(true)
  expect(registration).toBeUndefined()
  await password.fill('test1234')
  await page.getByLabel('비밀번호 확인').fill('test1234')
  await page.getByRole('button', { name: '회원가입 신청', exact: true }).click()

  await expect(page.getByRole('heading', { name: '회원 로그인', exact: true })).toBeVisible()
  await expect(page.getByRole('status')).toContainText('가입 신청이 접수되었습니다.')
  expect(registration).toEqual({ username: 'e2e-personal-user', display_name: 'E2E 개인 사용자', password: 'test1234' })
})

test('실제 계정을 승인한 뒤 비밀번호 변경과 새 비밀번호 로그인을 완료한다', async ({ page, browser }) => {
  test.setTimeout(150_000)
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const evidence = path.join(os.tmpdir(), 'workbench-personal-pc-evidence')
  mkdirSync(evidence, { recursive: true })
  const statusResponse = await page.request.get('/api/auth/status')
  const status = await statusResponse.json() as { registration_enabled?: boolean }
  if (process.env.E2E_PASSWORD_RELEASE === 'true') expect(status.registration_enabled).toBe(true)
  else test.skip(status.registration_enabled !== true, 'password registration is not enabled on this server')

  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
  const username = `e2e-account-${suffix}`
  const oldPassword = 'old12345'
  const newPassword = 'new12345'
  await page.route('http://127.0.0.1:8766/**', (route) => route.abort('connectionrefused'))
  await page.goto('/')
  await page.getByRole('button', { name: '개인 회원가입', exact: true }).click()
  await expect(page.getByRole('heading', { name: '개인 회원가입', exact: true })).toBeVisible()
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  await page.screenshot({ path: path.join(evidence, 'personal-signup-desktop.png') })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({ path: path.join(evidence, 'personal-signup-mobile.png') })
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.getByLabel('사용자 이름').fill(username)
  await page.getByLabel('표시 이름').fill(`E2E ${suffix}`)
  await page.getByLabel('비밀번호', { exact: true }).fill(oldPassword)
  await page.getByLabel('비밀번호 확인').fill(oldPassword)
  const registerResponse = page.waitForResponse('**/api/auth/register')
  await page.getByRole('button', { name: '회원가입 신청', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('관리자 승인 후 로그인할 수 있습니다.')
  const registered = await (await registerResponse).json() as { user_id: string }

  await page.getByLabel('사용자 이름').fill(username)
  await page.getByLabel('비밀번호').fill(oldPassword)
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('승인')

  const adminContext = await browser.newContext({ baseURL: 'http://127.0.0.1:15173' })
  try {
    const adminLogin = await adminContext.request.post('/api/auth/login', { data: { username: 'e2e-admin', password: 'e2e-validation-password' } })
    expect(adminLogin.ok()).toBe(true)
    const pendingUsersResponse = await adminContext.request.get('/api/admin/users?status=PENDING')
    expect(pendingUsersResponse.ok()).toBe(true)
    const pendingUsers = await pendingUsersResponse.json() as Array<{ id: string; username: string; updated_at: string }>
    const pending = pendingUsers.find((user) => user.id === registered.user_id || user.username === username)
    expect(pending).toBeTruthy()
    const approval = await adminContext.request.patch(`/api/admin/users/${pending!.id}/status`, { data: {
      account_status: 'ACTIVE', expected_updated_at: pending!.updated_at, reason: 'E2E 개인 계정 승인',
    } })
    expect(approval.ok()).toBe(true)
  } finally {
    await adminContext.close()
  }

  await page.getByLabel('비밀번호').fill(oldPassword)
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '주 메뉴' })).toBeVisible()
  await expect(page).toHaveURL(/\/workspace\/overview/)
  await expect(page.getByRole('link', { name: '결과 대시보드', exact: true })).toBeVisible()
  await page.reload()
  await expect(page).toHaveURL(/\/workspace\/overview/)
  await expect(page.getByRole('complementary', { name: '주 메뉴' })).toBeVisible()
  await expect(page.getByTestId('local-pc-settings')).toHaveCount(0)
  await openWorkspaceRoute(page, '/workspace/settings/local-pc')
  await expect(page.getByRole('heading', { name: '비밀번호 변경', exact: true })).toBeVisible()
  await page.getByLabel('현재 비밀번호').fill(oldPassword)
  await expect(page.getByLabel('새 비밀번호', { exact: true })).toHaveAttribute('minlength', '8')
  await page.getByLabel('새 비밀번호', { exact: true }).fill(newPassword)
  await page.getByLabel('새 비밀번호 확인').fill(newPassword)
  await page.getByRole('button', { name: '비밀번호 변경', exact: true }).click()
  await expect(page.locator('.change-password-form').getByRole('status')).toContainText('비밀번호를 변경했습니다.')
  await page.screenshot({ path: path.join(evidence, 'personal-password-changed.png'), fullPage: true })
  await page.getByRole('button', { name: '로그아웃', exact: true }).click()
  await page.getByLabel('사용자 이름').fill(username)
  await page.getByLabel('비밀번호').fill(oldPassword)
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await page.getByLabel('비밀번호').fill(newPassword)
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '주 메뉴' })).toBeVisible()
  expect(errors).toEqual([])
})
