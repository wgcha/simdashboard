import { openWorkspaceRoute } from './workspace-test-helpers'
import { expect, test, type Page } from '@playwright/test'

const password = 'e2e-validation-password'

async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('사용자 이름').fill('e2e-admin')
  await page.getByLabel('비밀번호').fill(password)
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '주 메뉴' })).toBeVisible()
}

async function expectCommonResultActions(page: Page, editingEnabled: boolean) {
  const report = page.getByRole('button', { name: '보고서 내보내기', exact: true })
  const improve = page.getByRole('button', { name: '자연어로 개선', exact: true })
  const edit = page.getByRole('button', { name: '대시보드 편집', exact: true })
  await expect(report).toBeVisible()
  await expect(report).toBeDisabled()
  await expect(improve).toBeVisible()
  await expect(edit).toBeVisible()
  if (editingEnabled) {
    await expect(improve).toBeEnabled()
    await expect(edit).toBeEnabled()
  } else {
    await expect(improve).toBeDisabled()
    await expect(edit).toBeDisabled()
  }
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
  const widgetTitle = `최대 응력 ${suffix}`
  const workTypeName = `E2E 결과 구성 작업 ${suffix}`
  const requestTitle = `E2E 결과 snapshot 의뢰 ${suffix}`

  await openWorkspaceRoute(page, '/workspace/admin/work-types')
  const form = page.getByRole('form', { name: '새 작업 유형 작성' })
  await form.getByLabel('관리 유형 표시 이름').fill(workTypeName)
  await page.locator('.workbench-admin-task-picker').getByRole('button', { name: /CAD\/형상 준비/ }).click()
  const widgetCatalog = form.getByLabel('결과 위젯 카탈로그')
  await expect(widgetCatalog.getByRole('button')).toHaveCount(18)
  await widgetCatalog.getByRole('button', { name: /KPI 카드/ }).click()
  await widgetCatalog.getByRole('button', { name: /수행자 의견/ }).click()
  await form.getByLabel('KPI 카드 표시 제목').fill(widgetTitle)
  const saveWorkType = form.getByRole('button', { name: '새 작업 유형 저장' })
  await expect(form.getByRole('alert')).toHaveCount(0)
  await expect(saveWorkType).toBeEnabled()
  await saveWorkType.click()
  await expect(page.locator('.workbench-admin-notice')).toContainText(`${workTypeName} v1`)

  const typeCard = page.locator('.workbench-admin-types article').filter({ hasText: workTypeName }).first()
  const requestTypeId = ((await typeCard.locator('code').textContent()) ?? '').split(' · ')[0]
  expect(requestTypeId).toBeTruthy()

  await openWorkspaceRoute(page, '/workspace/requests/new')
  const requestTypeOption = page.getByTestId(`request-type-option-${requestTypeId}-1`)
  await requestTypeOption.click()
  const expectedPreview = page.getByTestId('expected-results-preview')
  await expect(expectedPreview).toContainText('요청 결과')
  await expect(expectedPreview).toContainText(widgetTitle)
  await expect(expectedPreview).toContainText('수행자 의견')

  await page.getByLabel('의뢰 출처 상세').fill('MVP03 E2E gateway')
  await page.locator('label').filter({ hasText: '요청자' }).locator('input').fill('MVP03 E2E')
  await page.locator('label').filter({ hasText: '의뢰 제목' }).locator('input').fill(requestTitle)
  await page.getByTestId('submit-request-intake').click()
  await expect(page.getByTestId('intake-success')).toContainText(`${requestTitle} 접수 완료`)

  const requestsResponse = await page.request.get('/api/projects/project-tv-001/requests')
  expect(requestsResponse.status(), await requestsResponse.text()).toBe(200)
  const createdRequest = (await requestsResponse.json() as Array<{ id: string; title: string }>).find((item) => item.title === requestTitle)
  expect(createdRequest).toBeTruthy()

  await openWorkspaceRoute(page, '/workspace/requests')
  await page.getByLabel('의뢰 선택').selectOption(createdRequest!.id)
  const implicitMaterializations: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/result-layout/materialize')) implicitMaterializations.push(request.url())
  })
  await page.locator('.request-journey').getByRole('button', { name: /결과 검토|상세 분석/ }).click()
  const pending = page.getByTestId('pending-analysis-workspace')
  await expect(pending).toContainText('요청 결과')
  await expect(pending.getByTestId('result-layout-widget-request-result-kpi')).toContainText('결과 대기')
  await expect(pending.getByTestId('result-layout-widget-request-result-note')).toContainText('결과 대기')
  await expectCommonResultActions(page, false)
  await expect(page.getByRole('button', { name: '추천 배치 미리보기', exact: true })).toHaveCount(0)
  expect(implicitMaterializations, 'Viewing a result snapshot must not create a dashboard').toEqual([])

  await openWorkspaceRoute(page, '/workspace/help')
  const layoutRoute = `**/api/workbench/requests/${createdRequest!.id}/result-layout*`
  let layoutRequestCount = 0
  let signalWarmRefreshStarted!: () => void
  let releaseWarmRefresh!: () => void
  const warmRefreshStarted = new Promise<void>((resolve) => { signalWarmRefreshStarted = resolve })
  const warmRefreshGate = new Promise<void>((resolve) => { releaseWarmRefresh = resolve })
  await page.route(layoutRoute, async (route) => {
    layoutRequestCount += 1
    if (layoutRequestCount === 1) {
      signalWarmRefreshStarted()
      await warmRefreshGate
    }
    await route.continue()
  })
  await page.goBack()
  await warmRefreshStarted
  try {
    await expect(pending).toContainText(widgetTitle)
    await expect(page.getByText('상세 분석 구성 확인 중', { exact: true })).toHaveCount(0)
  } finally {
    releaseWarmRefresh()
  }
  await expect.poll(() => layoutRequestCount, { timeout: 15_000 }).toBeGreaterThan(1)
  await page.unroute(layoutRoute)

  const legacyTypeResponse = await page.request.post('/api/admin/workbench/request-types', {
    data: requestTypePayload(`E2E legacy result ${suffix}`),
  })
  expect(legacyTypeResponse.status(), await legacyTypeResponse.text()).toBe(201)
  const legacyType = await legacyTypeResponse.json() as { id: string; version: number }
  const legacyTitle = `E2E legacy request ${suffix}`
  const legacyRequest = await createRequest(page, legacyType.id, legacyType.version, legacyTitle)
  await page.reload({ waitUntil: 'networkidle' })

  await page.getByLabel('의뢰 선택', { exact: true }).selectOption(legacyRequest.id)
  await page.locator('.request-journey').getByRole('button', { name: /결과 검토|상세 분석/ }).click()
  const unconfigured = page.getByTestId('result-layout-unconfigured')
  await expect(unconfigured).toBeVisible()
  await expect(unconfigured).toContainText('결과 구성 미지정')
  await expect(unconfigured.getByRole('heading', { name: /Open Cell/i })).toHaveCount(0)
  await expect(unconfigured.locator('[data-testid^="result-layout-widget-"]')).toHaveCount(0)
  await expect(unconfigured).not.toContainText('Open Cell 맵')
  await expect(unconfigured).not.toContainText('Open Cell 판정')
  await expectCommonResultActions(page, false)

})
