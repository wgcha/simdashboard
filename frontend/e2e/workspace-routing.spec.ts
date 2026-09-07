import { openWorkspaceRoute, revealControl } from './workspace-test-helpers'
import { expect, test, type Page } from '@playwright/test'

const password = 'e2e-validation-password'

async function login(page: Page, username = 'e2e-admin') {
  await page.goto('/')
  await page.getByLabel('사용자 이름').fill(username)
  await page.getByLabel('비밀번호').fill(password)
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '주 메뉴' })).toBeVisible()
}

test('workspace menu navigation updates the URL and browser history restores the active menu', async ({ page }) => {
  await login(page)
  await expect(page).toHaveURL(/\/workspace\/overview(?:\?|$)/)

  await openWorkspaceRoute(page, '/workspace/help')
  await expect(page).toHaveURL(/\/workspace\/help(?:\?|$)/)
  await expect(page.getByRole('link', { name: '도움말', exact: true })).toHaveAttribute('aria-current', 'page')

  await page.goBack()
  await expect(page).toHaveURL(/\/workspace\/overview(?:\?|$)/)
  await expect(page.getByRole('link', { name: '결과 대시보드', exact: true })).toHaveAttribute('aria-current', 'page')

  await page.goForward()
  await expect(page).toHaveURL(/\/workspace\/help(?:\?|$)/)
})

test('a direct workspace URL survives refresh and unknown paths show a 404 page', async ({ page }) => {
  await page.goto('/workspace/help')
  await page.getByLabel('사용자 이름').fill('e2e-admin')
  await page.getByLabel('비밀번호').fill(password)
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page).toHaveURL(/\/workspace\/help(?:\?|$)/)
  await expect(page.getByRole('heading', { name: /사용 도움말/ })).toBeVisible()

  await page.reload()
  await expect(page).toHaveURL(/\/workspace\/help(?:\?|$)/)
  await expect(page.getByRole('heading', { name: /사용 도움말/ })).toBeVisible()

  await page.goto('/workspace/not-found')
  await expect(page.getByTestId('workspace-not-found')).toBeVisible()
})

test('a direct route without permission falls back to the first allowed workspace route', async ({ page }) => {
  await page.goto('/workspace/admin/audit')
  await page.getByLabel('사용자 이름').fill('e2e-viewer')
  await page.getByLabel('비밀번호').fill(password)
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page).toHaveURL(/\/workspace\/overview(?:\?|$)/)
  await expect(page.getByRole('link', { name: '결과 대시보드', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('link', { name: '감사로그', exact: true })).toHaveCount(0)
})

test('빈 프로젝트를 선택해도 설정 상태에서 의뢰 접수로 이어지고 예제 프로젝트는 정상 로드된다', async ({ page }) => {
  await login(page)
  await openWorkspaceRoute(page, '/workspace/data')
  await expect(page.getByRole('heading', { name: '해석 데이터 등록' })).toBeVisible()

  const projectName = `E2E 빈 프로젝트 ${Date.now()}`
  const projectResponsePromise = page.waitForResponse((response) => response.url().endsWith('/api/projects') && response.request().method() === 'POST')
  await revealControl(page.getByLabel('프로젝트 이름'))
  await page.getByLabel('프로젝트 이름').fill(projectName)
  await page.getByLabel('제품 모델명').fill('E2E-EMPTY-PROJECT')
  await page.getByRole('button', { name: '프로젝트 등록' }).click()
  const projectResponse = await projectResponsePromise
  expect(projectResponse.status(), await projectResponse.text()).toBe(201)
  const createdProject = await projectResponse.json() as { id: string }
  await expect(page.getByText('프로젝트 등록이 완료되었습니다.')).toBeVisible()

  await openWorkspaceRoute(page, '/workspace/requests')
  await expect(page.getByLabel('프로젝트 선택')).toBeVisible()
  await page.getByLabel('프로젝트 선택').selectOption(createdProject.id)
  await expect(page.getByTestId('project-setup-state')).toBeVisible()
  await expect(page.getByTestId('project-setup-state')).toContainText(`${projectName}에 아직 의뢰가 없습니다.`)
  await expect(page.locator('.full-state.error')).toHaveCount(0)
  await expect(page.getByLabel('의뢰 선택')).toBeDisabled()
  await expect(page.getByLabel('의뢰 선택').locator('option')).toHaveText('의뢰 접수 후 선택')
  await expect(page.getByLabel('하중 경우 선택')).toBeDisabled()
  await expect(page.getByTestId('project-setup-state').getByRole('button', { name: '해석 의뢰 접수' })).toBeVisible()

  await page.getByTestId('project-setup-state').getByRole('button', { name: '해석 의뢰 접수' }).click()
  await expect(page.getByTestId('request-intake-page')).toBeVisible()

  // The initial-data shell intentionally owns the intake flow. Return to the
  // canonical monitoring route to verify that a complete example can replace
  // the empty project context without a reload.
  await page.goto('/workspace/requests')
  await expect(page.getByLabel('프로젝트 선택')).toBeVisible()
  await page.getByLabel('프로젝트 선택').selectOption('project-feature-showcase')
  await expect(page.getByTestId('project-setup-state')).toHaveCount(0)
  await expect(page.getByLabel('의뢰 선택')).not.toBeDisabled()
  await expect(page.getByLabel('의뢰 선택').locator('option')).not.toHaveCount(0)
  await expect(page.getByLabel('하중 경우 선택')).not.toBeDisabled()
  await expect(page.getByLabel('하중 경우 선택').locator('option')).not.toHaveCount(0)
})

test('상세 대시보드는 결과 버전을 선택하고 결과 없는 하중 경우는 선택기를 비활성화한다', async ({ page }) => {
  await login(page)
  await openWorkspaceRoute(page, '/workspace/requests')
  await expect(page.getByLabel('프로젝트 선택')).toBeVisible()
  await page.getByLabel('프로젝트 선택').selectOption('project-feature-showcase')
  await expect(page.getByLabel('의뢰 선택')).not.toBeDisabled()
  await page.getByLabel('의뢰 선택').selectOption('request-showcase-compare')
  await page.getByLabel('하중 경우 선택').selectOption('loadcase-showcase-compare')
  await openWorkspaceRoute(page, '/workspace/overview')
  const results = page.getByTestId('result-overview-dashboard')
  await results.getByLabel('의뢰 제목, 하중 경우 검색').fill('Run 비교: 회귀와 개선')
  await results.getByRole('button', { name: '결과 검토', exact: true }).click()
  await expect(page.getByRole('region', { name: '현재 Run 핵심 결과' })).toBeVisible()

  const versionSelector = page.getByLabel('결과 버전 선택')
  await expect(versionSelector).toBeVisible()
  await expect(versionSelector.locator('option')).toHaveCount(3)
  const runIds = await versionSelector.locator('option').evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value))
  const latestRunId = await versionSelector.inputValue()
  const historicalRunId = runIds.find((runId) => runId !== latestRunId) ?? runIds[0]
  const overviewRequest = page.waitForRequest((request) => request.url().includes('/api/load-cases/loadcase-showcase-compare/overview') && request.url().includes(`run_id=${historicalRunId}`))
  await versionSelector.selectOption(historicalRunId)
  await overviewRequest

  await page.getByLabel('의뢰 선택').selectOption('request-showcase-waiting')
  await page.getByLabel('하중 경우 선택').selectOption('loadcase-showcase-waiting')
  await expect(page.getByLabel('결과 버전 선택')).toBeDisabled()
  await expect(page.getByLabel('결과 버전 선택').locator('option')).toHaveText('결과 없음')
})
