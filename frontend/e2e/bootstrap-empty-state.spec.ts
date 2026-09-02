import { expect, test } from '@playwright/test'

const globalAdmin = {
  id: 'characterization-admin',
  username: 'characterization-admin',
  display_name: '초기 설정 관리자',
  employee_id: null,
  account_status: 'ACTIVE',
  is_global_admin: true,
  memberships: [],
  company_permissions: ['company.dashboard.view', 'project.data.view', 'report.export'],
  role: 'admin',
}

const menuPolicy = {
  version: 1,
  updated_by: 'system',
  updated_at: '2026-08-13T00:00:00',
  menus: [
    { id: 'data', label: '해석 데이터 등록', required_permission: 'result.import', context_kind: 'project', sequence_no: 10, is_policy_editable: true, visibility: { general: false, power: true, admin: true } },
    { id: 'intake', label: '해석 의뢰 접수', required_permission: 'request.create', context_kind: 'project', sequence_no: 20, is_policy_editable: true, visibility: { general: false, power: true, admin: true } },
  ],
}

test('빈 데이터에서도 전역 관리자는 초기 설정과 전역 테마를 사용할 수 있다', async ({ page }) => {
  const bootstrapRequests = { health: 0, projects: 0, workflows: 0, menuPolicy: 0 }
  await page.route('**/api/auth/status', (route) => route.fulfill({ json: { mode: 'disabled', authentication_required: false, oidc_start_url: null } }))
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: globalAdmin }))
  await page.route('**/api/health', (route) => { bootstrapRequests.health += 1; return route.fulfill({ json: { status: 'ok', database_backend: 'postgresql' } }) })
  await page.route('**/api/projects', (route) => { bootstrapRequests.projects += 1; return route.fulfill({ json: [] }) })
  await page.route('**/api/workflows', (route) => { bootstrapRequests.workflows += 1; return route.fulfill({ json: [] }) })
  await page.route('**/api/navigation/menu-policy', (route) => { bootstrapRequests.menuPolicy += 1; return route.fulfill({ json: menuPolicy }) })

  await page.goto('/')

  await expect(page.getByText('초기 데이터 구성', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '새 프로젝트' })).toBeVisible()
  await expect(page.getByRole('button', { name: '프로젝트 등록' })).toBeEnabled()
  await expect(page.getByRole('button', { name: '해석 의뢰 접수' })).toBeDisabled()
  expect(bootstrapRequests).toEqual({ health: 1, projects: 1, workflows: 1, menuPolicy: 1 })

  await page.getByRole('button', { name: '라이트', exact: true }).click()
  await expect.poll(() => page.evaluate(() => ({
    theme: document.documentElement.dataset.theme,
    appBackground: getComputedStyle(document.documentElement).getPropertyValue('--color-app-bg').trim(),
    bodyBackground: getComputedStyle(document.body).backgroundColor,
  }))).toEqual({ theme: 'light', appBackground: '#f4f7fb', bodyBackground: 'rgb(244, 247, 251)' })

  await page.getByRole('button', { name: '다크', exact: true }).click()
  await expect.poll(() => page.evaluate(() => ({
    theme: document.documentElement.dataset.theme,
    appBackground: getComputedStyle(document.documentElement).getPropertyValue('--color-app-bg').trim(),
  }))).toEqual({ theme: 'dark', appBackground: '#07111d' })
})
