import { expect, test, type Page } from '@playwright/test'

const password = 'e2e-validation-password'

async function login(page: Page, username: string) {
  // The app can retain its in-memory user after an optimistic UI logout.
  // Clear the browser context before navigating so each role is authenticated anew.
  await page.context().clearCookies()
  await page.goto('/')
  await expect(page.getByLabel('사용자 이름')).toBeVisible()
  await page.getByLabel('사용자 이름').fill(username)
  await page.getByLabel('비밀번호').fill(password)
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '주 메뉴' })).toBeVisible()
}

async function logout(page: Page) {
  await page.getByRole('button', { name: '로그아웃', exact: true }).click()
  await page.context().clearCookies()
  await page.goto('/')
  await expect(page.getByRole('button', { name: '로그인', exact: true })).toBeVisible()
}

test('project admin binds a result profile, intake previews it, and lower/cross-project access is denied', async ({ page }) => {
  const suffix = Date.now()
  const templateId = `project-result-e2e-${suffix}`
  const templateName = `프로젝트 결과 템플릿 ${suffix}`
  const widgetId = `project-result-widget-${suffix}`
  const workTypeName = `프로젝트 결과 작업 ${suffix}`

  await login(page, 'e2e-admin')
  const template = await page.request.post('/api/admin/workbench/analysis-templates', { data: {
    id: templateId, display_name: templateName, description: 'project profile E2E template', lifecycle_status: 'PUBLISHED', scope_kind: 'SYSTEM',
    page_definitions: [{ id: `project-result-page-${suffix}`, name: '결과 요약', description: 'E2E page', widgets: [{ id: widgetId, type: 'summary', title: '프로젝트 결과 요약', x: 0, y: 0, w: 12, h: 3, settings: {} }] }],
  } })
  expect(template.status(), await template.text()).toBe(201)
  const workType = await page.request.post('/api/admin/workbench/request-types', { data: {
    display_name: workTypeName, description: 'project result binding E2E work type', allowed_task_types: [{ id: 'analysis-db-publish', version: 1 }],
    default_workflow: { nodes: [{ node_key: 'publish', task_type_id: 'analysis-db-publish', task_type_version: 1, depends_on: [] }] }, match_rules: { labels: ['project-result-e2e'] }, is_active: true,
  } })
  expect(workType.status(), await workType.text()).toBe(201)
  const createdType = await workType.json() as { id: string; version: number }
  await logout(page)

  await login(page, 'e2e-project-admin')
  await page.getByRole('link', { name: '프로젝트 결과 구성', exact: true }).click()
  const binding = page.getByTestId('project-result-profile-binding')
  await expect(binding).toBeVisible()
  const requestTypeValue = createdType.id + ":" + createdType.version
  await binding.getByLabel("프로젝트 결과 구성 작업 유형").selectOption(requestTypeValue)
  await binding.getByLabel("프로젝트 결과 구성 작업 유형").selectOption("")
  await expect(binding.getByRole("button", { name: "프로젝트 결과 구성 저장", exact: true })).toBeDisabled()
  await binding.getByLabel("프로젝트 결과 구성 작업 유형").selectOption(requestTypeValue)
  const templateSelect = binding.getByLabel('결과 레이아웃 템플릿')
  await expect(templateSelect.locator('option').filter({ hasText: templateName })).toHaveCount(1)
  await templateSelect.selectOption(templateId + ":1")
  const saveButton = binding.getByRole("button", { name: "프로젝트 결과 구성 저장", exact: true })
  await expect(saveButton).toBeEnabled()
  await saveButton.click()
  await expect(binding.getByRole('status')).toContainText('저장했습니다')

  await page.reload({ waitUntil: 'networkidle' })
  await page.getByLabel('프로젝트 결과 구성 작업 유형').selectOption(requestTypeValue)
  await expect(page.getByLabel('결과 레이아웃 템플릿')).toHaveValue(`${templateId}:1`)
  const profile = await page.request.get(`/api/workbench/request-types/${createdType.id}/${createdType.version}/result-profile?project_id=project-tv-001`)
  expect(profile.status(), await profile.text()).toBe(200)
  expect((await profile.json() as { template_id: string; profile_scope: string }).template_id).toBe(templateId)

  await page.getByRole('link', { name: '의뢰 접수', exact: true }).click()
  await page.getByTestId(`request-type-option-${createdType.id}-${createdType.version}`).click()
  await expect(page.getByTestId('expected-results-preview')).toContainText(templateName)

  const otherProject = `project-result-e2e-other-${suffix}`
  const crossProject = await page.request.put(`/api/projects/${otherProject}/admin/workbench/request-types/${createdType.id}/${createdType.version}/result-profile`, { data: { template_id: templateId, template_version: 1, included_widget_ids: [widgetId], overrides: {}, required_data_contracts: [] } })
  expect(crossProject.status()).toBe(403)
  await logout(page)

  await login(page, 'e2e-viewer')
  await expect(page.getByRole('link', { name: '프로젝트 결과 구성', exact: true })).toHaveCount(0)
  const viewerWrite = await page.request.put(`/api/projects/project-tv-001/admin/workbench/request-types/${createdType.id}/${createdType.version}/result-profile`, { data: { template_id: templateId, template_version: 1, included_widget_ids: [widgetId], overrides: {}, required_data_contracts: [] } })
  expect(viewerWrite.status()).toBe(403)
})
