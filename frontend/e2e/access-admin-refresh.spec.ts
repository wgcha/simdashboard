import { expect, test } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

test('권한별 배정과 사용자관리 새로고침은 도우미 없이 동작하고 조회 실패 후 복구한다', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  let helperRequests = 0
  await page.route('http://127.0.0.1:8766/**', (route) => {
    helperRequests += 1
    return route.abort('connectionrefused')
  })
  const username = `role-${Date.now()}`
  const displayName = `권한 확인 사용자`
  const registration = await page.request.post('/api/auth/register', {
    data: { username, display_name: displayName, password: 'test1234' },
  })
  expect(registration.status()).toBe(201)
  const account = await registration.json() as { user_id: string }
  await loginWorkspace(page, 'e2e-admin', '/workspace/overview')
  await openWorkspaceRoute(page, '/workspace/admin/access')
  await expect(page.getByRole('heading', { name: '사용자·프로젝트 권한', exact: true })).toBeVisible()
  const userRow = page.locator('.access-admin-card').first().locator('article').filter({ hasText: username })
  await expect(userRow).toBeVisible()
  await userRow.getByRole('button', { name: '승인', exact: true }).click()
  await expect(userRow.getByRole('button', { name: '승인', exact: true })).toBeDisabled()
  await expect(userRow.getByRole('button', { name: '확인', exact: true })).toBeDisabled()
  await userRow.getByRole('combobox').selectOption('general')
  const projectId = await page.getByRole('combobox', { name: '관리할 프로젝트', exact: true }).inputValue()
  const savedRole = async () => {
    const response = await page.request.get(`/api/projects/${projectId}/members`)
    expect(response.ok()).toBe(true)
    const members = await response.json() as Array<{ user_id: string; role: string }>
    return members.find((member) => member.user_id === account.user_id)?.role
  }
  expect(await savedRole()).toBeUndefined()
  let releaseAssignment!: () => void
  const assignmentGate = new Promise<void>((resolve) => { releaseAssignment = resolve })
  await page.route('**/api/projects/*/members', async (route) => {
    if (route.request().method() === 'POST') await assignmentGate
    await route.continue()
  })
  await userRow.getByRole('button', { name: '확인', exact: true }).click()
  await expect(page.getByRole('combobox', { name: '관리할 프로젝트', exact: true })).toBeDisabled()
  releaseAssignment()
  const generalGroup = page.getByRole('region', { name: '일반 사용자', exact: true })
  await expect(generalGroup.getByText(username, { exact: false })).toBeVisible()
  await page.unroute('**/api/projects/*/members')
  const generalRow = generalGroup.locator('article').filter({ hasText: username })
  await expect(generalRow.getByRole('button', { name: '확인', exact: true })).toBeDisabled()
  await generalRow.getByRole('combobox').selectOption('power')
  expect(await savedRole()).toBe('general')
  await generalRow.getByRole('button', { name: '취소', exact: true }).click()
  await expect(generalRow.getByRole('combobox')).toHaveValue('general')
  await expect(generalRow.getByRole('button', { name: '확인', exact: true })).toBeDisabled()
  await generalRow.getByRole('combobox').selectOption('power')
  await page.route(`**/api/projects/${projectId}/members/${account.user_id}`, (route) => route.fulfill({ status: 503, json: { detail: 'Injected role-save failure' } }))
  await generalRow.getByRole('button', { name: '확인', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Injected role-save failure')
  await expect(generalRow.getByRole('combobox')).toHaveValue('power')
  expect(await savedRole()).toBe('general')
  await page.unroute(`**/api/projects/${projectId}/members/${account.user_id}`)
  await generalRow.getByRole('button', { name: '확인', exact: true }).click()
  const powerGroup = page.getByRole('region', { name: '파워 사용자', exact: true })
  await expect(powerGroup.getByText(username, { exact: false })).toBeVisible()
  expect(await savedRole()).toBe('power')
  const powerRow = powerGroup.locator('article').filter({ hasText: username })
  await powerRow.getByRole('combobox').selectOption('admin')
  expect(await savedRole()).toBe('power')
  await powerRow.getByRole('button', { name: '확인', exact: true }).click()
  const adminGroup = page.getByRole('region', { name: '프로젝트 관리자', exact: true })
  await expect(adminGroup.getByText(username, { exact: false })).toBeVisible()
  await page.reload()
  await expect(adminGroup.getByText(username, { exact: false })).toBeVisible()

  const userResponse = await page.request.get(`/api/admin/users?q=${username}`)
  expect(userResponse.ok()).toBe(true)
  const accounts = await userResponse.json() as Array<{ id: string; is_global_admin: boolean }>
  expect(accounts).toHaveLength(1)
  expect(accounts[0]).toMatchObject({ id: account.user_id, is_global_admin: false })

  const refresh = page.getByRole('button', { name: '새로고침', exact: true })
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const refreshed = page.waitForResponse((response) => response.url().includes('/api/admin/users') && response.status() === 200)
    await refresh.click()
    await refreshed
    await expect(refresh).toBeEnabled()
    expect((await page.request.get('/api/health')).ok()).toBe(true)
  }
  await page.getByRole('textbox', { name: '사용자 검색', exact: true }).fill(username)
  const searched = page.waitForRequest((request) => new URL(request.url()).searchParams.get('q') === username)
  await page.getByRole('button', { name: '검색', exact: true }).click()
  await searched
  await expect(userRow).toBeVisible()

  const evidence = path.join(os.tmpdir(), 'workbench-access-evidence')
  mkdirSync(evidence, { recursive: true })
  await page.screenshot({ path: path.join(evidence, 'roles-desktop.png'), fullPage: true })
  await page.route('**/api/admin/users?*', (route) => route.fulfill({ status: 503, json: { detail: 'Injected user-list failure' } }))
  await refresh.click()
  await expect(page.getByRole('alert')).toContainText('사용자 목록')
  await expect(refresh).toBeEnabled()
  await expect(page.getByRole('heading', { name: '사용자·프로젝트 권한', exact: true })).toBeVisible()
  await expect(adminGroup.getByText(username, { exact: false })).toBeVisible()
  expect((await page.request.get('/api/health')).ok()).toBe(true)
  await page.screenshot({ path: path.join(evidence, 'refresh-error.png'), fullPage: true })
  await page.unroute('**/api/admin/users?*')
  await refresh.click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(userRow).toBeVisible()
  await page.clock.install()
  await page.route('**/api/admin/users?*', () => { /* Leave the request pending to exercise the deadline. */ })
  const stalled = page.waitForRequest((request) => new URL(request.url()).searchParams.get('q') === username)
  await refresh.click()
  await stalled
  await page.clock.fastForward(15_001)
  await expect(page.getByRole('alert')).toContainText('시간이 초과')
  await expect(refresh).toBeEnabled()
  await expect(userRow.getByRole('button', { name: '전역 관리자 지정', exact: true })).toBeDisabled()
  await page.unroute('**/api/admin/users?*')
  await refresh.click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByRole('heading', { name: '사용자·프로젝트 권한', exact: true })).toBeVisible()
  await expect(refresh).toBeVisible()
  await expect(refresh).toBeEnabled()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({ path: path.join(evidence, 'roles-mobile.png'), fullPage: true })
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  expect(errors).toEqual([])
  expect(helperRequests).toBe(0)
})

test('프로젝트가 없는 서버에서도 전역 관리자는 사용자 목록을 새로고침한다', async ({ page }) => {
  await page.route('**/api/projects', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/workflows', (route) => route.fulfill({ json: [] }))
  const invalidMemberRequests: string[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.includes('/projects//members')) invalidMemberRequests.push(request.url())
  })
  await loginWorkspace(page, 'e2e-admin', '/workspace/admin/access')
  await expect(page.getByRole('heading', { name: '사용자·프로젝트 권한', exact: true })).toBeVisible()
  await expect(page.getByRole('combobox', { name: '관리할 프로젝트', exact: true })).toBeDisabled()
  await expect(page.locator('.access-admin-card').first().getByText('e2e-admin', { exact: false })).toBeVisible()
  const refresh = page.getByRole('button', { name: '새로고침', exact: true })
  await refresh.click()
  await expect(refresh).toBeEnabled()
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(invalidMemberRequests).toEqual([])
})
