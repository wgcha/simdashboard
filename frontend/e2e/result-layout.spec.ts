import { expect, test, type Page } from '@playwright/test'

const password = 'e2e-validation-password'
type WorkbenchTask = {
  display_name: string
  output_artifact_types: string[]
}

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('사용자 이름').fill('e2e-admin')
  await page.getByLabel('비밀번호').fill(password)
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '주 메뉴' })).toBeVisible()
}

function requestTypePayload(displayName: string, resultProfile?: Record<string, unknown>) {
  return {
    display_name: displayName,
    description: 'MVP03 결과 레이아웃 E2E 작업 유형',
    allowed_task_types: [{ id: 'cad-prepare', version: 1 }],
    default_workflow: { nodes: [{ node_key: 'cad-prepare-step', task_type_id: 'cad-prepare', task_type_version: 1, depends_on: [] }] },
    match_rules: { labels: ['result-layout-e2e'] },
    ...(resultProfile ? { result_profile: resultProfile } : {}),
    is_active: true,
  }
}

async function createRequest(page: Page, requestTypeId: string, requestTypeVersion: number, title: string) {
  const assigneesResponse = await page.request.get('/api/projects/project-tv-001/assignee-candidates')
  expect(assigneesResponse.status(), await assigneesResponse.text()).toBe(200)
  const assignees = await assigneesResponse.json() as Array<{ user_id: string }>
  expect(assignees.length).toBeGreaterThan(0)

  const response = await page.request.post('/api/projects/project-tv-001/requests', {
    data: {
      title,
      owner_user_id: assignees[0].user_id,
      due_in_days: 14,
      overall_note: 'MVP03 no-profile generic result shell',
      source_type: 'EXTERNAL_SYSTEM',
      source_reference: 'MVP03 E2E gateway',
      requested_by: 'MVP03 E2E',
      request_type_id: requestTypeId,
      request_type_version: requestTypeVersion,
    },
  })
  expect(response.status(), await response.text()).toBe(201)
  return await response.json() as { id: string }
}

test('작업 유형 결과 구성은 접수 미리보기와 generic pending result snapshot으로 이어진다', async ({ page }) => {
  await login(page)
  const suffix = Date.now()
  const templateId = `result-layout-e2e-${suffix}`
  const templateName = `E2E 충돌 결과 ${suffix}`
  const widgetId = `result-layout-summary-${suffix}`
  const widgetTitle = `충돌 요약 ${suffix}`
  const workTypeName = `E2E 결과 구성 작업 ${suffix}`
  const requestTitle = `E2E 결과 snapshot 의뢰 ${suffix}`

  const templateResponse = await page.request.post('/api/admin/workbench/analysis-templates', {
    data: {
      id: templateId,
      display_name: templateName,
      description: 'DashboardDefinition 기반 결과 템플릿',
      lifecycle_status: 'PUBLISHED',
      scope_kind: 'SYSTEM',
      page_definitions: [{
        id: `result-layout-page-${suffix}`,
        name: '충돌 결과',
        description: '12열 결과 배치',
        widgets: [
          { id: widgetId, type: 'summary', title: widgetTitle, x: 0, y: 0, w: 6, h: 3, settings: {} },
          { id: `result-layout-kpi-${suffix}`, type: 'kpi', title: '최대 응력', x: 6, y: 0, w: 6, h: 3, settings: {} },
        ],
      }],
    },
  })
  expect(templateResponse.status(), await templateResponse.text()).toBe(201)

  const taskTypesResponse = await page.request.get('/api/workbench/task-types?all_versions=true')
  expect(taskTypesResponse.status(), await taskTypesResponse.text()).toBe(200)
  const compatibleTask = (await taskTypesResponse.json() as WorkbenchTask[]).find((task) => task.output_artifact_types.includes('ANALYSIS_RUN_REFERENCE'))
  expect(compatibleTask, 'a catalog task must declare the output that satisfies LOAD_CASE and RESULT_RUN').toBeTruthy()

  await page.getByRole('link', { name: '작업 유형 관리', exact: true }).click()
  const form = page.getByRole('form', { name: '새 작업 유형 작성' })
  await form.getByLabel('관리 유형 표시 이름').fill(workTypeName)
  await page.locator('.workbench-admin-task-picker').getByRole('button', { name: /CAD\/형상 준비/ }).click()
  const templateSelect = form.getByLabel('결과 레이아웃 템플릿')
  await expect(templateSelect.locator('option').filter({ hasText: templateName })).toHaveCount(1)
  await templateSelect.selectOption(`${templateId}:1`)
  await expect(form.getByRole('checkbox', { name: new RegExp(widgetTitle) })).toBeChecked()
  const saveWorkType = form.getByRole('button', { name: '새 작업 유형 저장' })
  await expect(form.getByRole('alert')).toContainText('선택한 Workflow 출력으로 만들 수 없는 결과 데이터 계약')
  await expect(saveWorkType).toBeDisabled()
  await page.locator('.workbench-admin-task-picker').getByRole('button').filter({ hasText: compatibleTask!.display_name }).click()
  await expect(form.getByRole('alert')).toHaveCount(0)
  await expect(saveWorkType).toBeEnabled()
  await saveWorkType.click()
  await expect(page.locator('.workbench-admin-notice')).toContainText(`${workTypeName} v1`)

  const typeCard = page.locator('.workbench-admin-types article').filter({ hasText: workTypeName }).first()
  const requestTypeId = ((await typeCard.locator('code').textContent()) ?? '').split(' · ')[0]
  expect(requestTypeId).toBeTruthy()

  await page.getByRole('link', { name: '의뢰 접수', exact: true }).click()
  const requestTypeOption = page.getByTestId(`request-type-option-${requestTypeId}-1`)
  await requestTypeOption.click()
  const expectedPreview = page.getByTestId('expected-results-preview')
  await expect(expectedPreview).toContainText(templateName)
  await expect(expectedPreview).toContainText(widgetTitle)

  await page.getByLabel('의뢰 출처 상세').fill('MVP03 E2E gateway')
  await page.locator('label').filter({ hasText: '요청자' }).locator('input').fill('MVP03 E2E')
  await page.locator('label').filter({ hasText: '의뢰 제목' }).locator('input').fill(requestTitle)
  await page.getByTestId('submit-request-intake').click()
  await expect(page.getByTestId('intake-success')).toContainText(`${requestTitle} 접수 완료`)

  const requestsResponse = await page.request.get('/api/projects/project-tv-001/requests')
  expect(requestsResponse.status(), await requestsResponse.text()).toBe(200)
  const createdRequest = (await requestsResponse.json() as Array<{ id: string; title: string }>).find((item) => item.title === requestTitle)
  expect(createdRequest).toBeTruthy()

  await page.getByRole('link', { name: '해석 의뢰 현황', exact: true }).click()
  await page.getByLabel('의뢰 선택').selectOption(createdRequest!.id)
  await page.locator('.view-tabs').getByRole('button', { name: /상세 분석/ }).click()
  const pending = page.getByTestId('pending-analysis-workspace')
  await expect(pending).toContainText(templateName)
  await expect(pending.getByTestId(`result-layout-widget-${widgetId}`)).toContainText('WAITING')

  const legacyTypeResponse = await page.request.post('/api/admin/workbench/request-types', {
    data: requestTypePayload(`E2E legacy result ${suffix}`),
  })
  expect(legacyTypeResponse.status(), await legacyTypeResponse.text()).toBe(201)
  const legacyType = await legacyTypeResponse.json() as { id: string; version: number }
  const legacyTitle = `E2E legacy request ${suffix}`
  await createRequest(page, legacyType.id, legacyType.version, legacyTitle)
  await page.reload({ waitUntil: 'networkidle' })

  const legacyLane = page.getByRole('heading', { name: legacyTitle, exact: true }).locator('xpath=ancestor::section[contains(@class,"workflow-lane")]')
  await legacyLane.getByRole('button', { name: '상세 분석 열기' }).click()
  const unconfigured = page.getByTestId('result-layout-unconfigured')
  await expect(unconfigured).toBeVisible()
  await expect(unconfigured).toContainText('결과 구성 미지정')
  await expect(unconfigured.getByRole('heading', { name: /Open Cell/i })).toHaveCount(0)
  await expect(unconfigured.locator('[data-testid^="result-layout-widget-"]')).toHaveCount(0)
  await expect(unconfigured).not.toContainText('Open Cell 맵')
  await expect(unconfigured).not.toContainText('Open Cell 판정')
})
