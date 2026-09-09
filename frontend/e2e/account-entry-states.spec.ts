import { expect, test } from '@playwright/test'
import os from 'node:os'
import path from 'node:path'

const readyStatus = { mode: 'password', authentication_required: true, registration_enabled: true, setup_required: false, oidc_start_url: null }
const setupStatus = { mode: 'disabled', authentication_required: false, registration_enabled: false, setup_required: true, setup_reason: 'INITIAL_ADMIN_REQUIRED', oidc_start_url: null }

function rejectWorkspaceRequests(page: import('@playwright/test').Page, requests: string[]) {
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/') && !url.pathname.endsWith('/api/auth/status') && !url.pathname.endsWith('/api/auth/me')) requests.push(url.pathname)
  })
}

test('서버 최초 설정은 setup 안내를 표시하고 재확인 후 회원 로그인으로 진입한다', async ({ page }) => {
  let statusCalls = 0
  const requests: string[] = []
  rejectWorkspaceRequests(page, requests)
  await page.route('**/api/auth/status', async (route) => { await route.fulfill({ json: ++statusCalls <= 2 ? setupStatus : readyStatus }) })
  await page.route('**/api/auth/me', async (route) => { await route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ detail: '로그인 필요' }) }) })
  await page.goto('/workspace/requests')
  await expect(page.getByTestId('server-setup-screen')).toBeVisible()
  await expect(page.getByRole('heading', { name: '서버 최초 설정이 필요합니다' })).toBeVisible()
  await expect(page.getByText('deploy.bat')).toBeVisible()
  await page.screenshot({ path: path.join(os.tmpdir(), 'simulation-workbench-auth-setup-desktop.png') })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: path.join(os.tmpdir(), 'simulation-workbench-auth-setup-mobile.png') })
  await page.getByRole('button', { name: '설정 상태 다시 확인' }).click()
  await expect(page.getByRole('heading', { name: '회원 로그인' })).toBeVisible()
  expect(requests).toEqual([])
})

test('auth/status 500은 업무 로딩 대신 재시도 화면을 표시한다', async ({ page }) => {
  let calls = 0
  await page.route('**/api/auth/status', async (route) => { await route.fulfill(calls++ < 2 ? { status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'status unavailable' }) } : { json: readyStatus }) })
  await page.route('**/api/auth/me', async (route) => { await route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ detail: '로그인 필요' }) }) })
  await page.goto('/')
  await expect(page.getByTestId('auth-status-error')).toBeVisible()
  await page.getByRole('button', { name: '다시 시도' }).click()
  await expect(page.getByRole('heading', { name: '회원 로그인' })).toBeVisible()
})

test('auth/me 500은 로그인 화면 대신 재시도 화면을 표시하고 복구한다', async ({ page }) => {
  let meCalls = 0
  await page.route('**/api/auth/status', async (route) => { await route.fulfill({ json: readyStatus }) })
  await page.route('**/api/auth/me', async (route) => { await route.fulfill(meCalls++ < 1 ? { status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'me unavailable' }) } : { status: 401, contentType: 'application/json', body: JSON.stringify({ detail: '로그인 필요' }) }) })
  await page.goto('/')
  await expect(page.getByTestId('auth-status-error')).toBeVisible()
  await page.getByRole('button', { name: '다시 시도' }).click()
  await expect(page.getByRole('heading', { name: '회원 로그인' })).toBeVisible()
})

test('깊은 업무 URL에서도 인증 확인 중 workspace API를 호출하지 않는다', async ({ page }) => {
  const requests: string[] = []
  rejectWorkspaceRequests(page, requests)
  await page.route('**/api/auth/status', async (route) => { await route.fulfill({ json: setupStatus }) })
  await page.goto('/workspace/data?project=deep-link')
  await expect(page.getByTestId('server-setup-screen')).toBeVisible()
  expect(requests).toEqual([])
})

test('회원가입 아이디는 영문·숫자·점·밑줄·하이픈만 허용한다', async ({ page }) => {
  await page.route('**/api/auth/status', async (route) => { await route.fulfill({ json: readyStatus }) })
  await page.route('**/api/auth/me', async (route) => { await route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ detail: '로그인 필요' }) }) })
  await page.goto('/')
  await page.getByRole('button', { name: '개인 회원가입', exact: true }).click()
  const username = page.getByLabel('사용자 이름')
  await username.fill('a._-1')
  expect(await username.evaluate((input) => (input as HTMLInputElement).checkValidity())).toBe(true)
  await username.fill('a b')
  expect(await username.evaluate((input) => (input as HTMLInputElement).checkValidity())).toBe(false)
  await username.fill('가나다')
  expect(await username.evaluate((input) => (input as HTMLInputElement).checkValidity())).toBe(false)
})
