import { openWorkspaceRoute } from './workspace-test-helpers'
import { expect, test, type Page } from '@playwright/test'

async function waitForDashboard(page: Page, role: 'admin' | 'viewer' = 'admin') {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '보안 로그인' })).toBeVisible()
  await page.getByLabel('사용자 이름').fill(role === 'admin' ? 'e2e-admin' : 'e2e-viewer')
  await page.getByLabel('비밀번호').fill('e2e-validation-password')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByRole('link', { name: '결과 대시보드', exact: true })).toBeVisible()
  await expect(page.locator('.portfolio-page')).toBeVisible()
}

async function openAnalysisWorkspace(page: Page) {
  await openWorkspaceRoute(page, '/workspace/requests')
  await expect(page.locator('.request-workspace-header')).toBeVisible()
  await page.locator('.request-journey').getByRole('button', { name: /결과 검토|상세 분석/ }).click()
}

test.beforeEach(async ({ page }, testInfo) => {
  await waitForDashboard(page, testInfo.title.includes('Viewer') ? 'viewer' : 'admin')
})

test('Viewer는 대시보드를 조회하지만 편집 기능은 사용할 수 없다', async ({ page }) => {
  await expect(page.locator('.signed-user')).toContainText('GENERAL')
  await expect(page.getByRole('button', { name: '대시보드 편집', exact: true })).toHaveCount(0)
  await openWorkspaceRoute(page, '/workspace/requests')
  await expect(page.getByRole('button', { name: '진행 단계 편집', exact: true })).toHaveCount(0)
})

test('운영 대시보드 편집은 취소 복원과 백엔드 버전 저장을 지원한다', async ({ page }) => {
  await page.getByRole('button', { name: '대시보드 편집', exact: true }).click()
  await expect(page.getByTestId('portfolio-layout-editor')).toBeVisible()

  const fontSize = page.getByLabel('운영 대시보드 글자 크기')
  const original = await fontSize.inputValue()
  await fontSize.press('ArrowRight')
  await expect(fontSize).not.toHaveValue(original)

  await page.getByTestId('portfolio-layout-editor').getByRole('button', { name: '편집 취소' }).click()
  await expect(page.getByTestId('portfolio-layout-editor')).toBeHidden()

  await page.getByRole('button', { name: '대시보드 편집', exact: true }).click()
  await expect(page.getByLabel('운영 대시보드 글자 크기')).toHaveValue(original)

  await page.getByLabel('운영 대시보드 글자 크기').press('ArrowRight')
  const saved = await page.getByLabel('운영 대시보드 글자 크기').inputValue()
  await page.getByRole('button', { name: '운영 설정 저장', exact: true }).click()
  await expect(page.getByTestId('portfolio-layout-editor')).toBeHidden()

  await page.reload()
  await expect(page.locator('.portfolio-page')).toBeVisible()
  await page.getByRole('button', { name: '대시보드 편집', exact: true }).click()
  await expect(page.getByLabel('운영 대시보드 글자 크기')).toHaveValue(saved)

  await page.getByLabel('운영 대시보드 글자 크기').press('ArrowLeft')
  await expect(page.getByLabel('운영 대시보드 글자 크기')).toHaveValue(original)
  await page.getByRole('button', { name: '운영 설정 저장', exact: true }).click()
})

test('의뢰 진행 상태의 레이아웃 편집과 단계 편집은 서로 독립적이다', async ({ page }) => {
  await openAnalysisWorkspace(page)
  await page.getByRole('button', { name: /의뢰 개요/ }).click()
  await page.getByLabel('프로젝트 선택').selectOption('project-feature-showcase')
  await expect(page.getByLabel('의뢰 선택').locator('option[value="request-showcase-workflow"]')).toHaveCount(1)
  await page.getByLabel('의뢰 선택').selectOption('request-showcase-workflow')

  await page.getByRole('button', { name: '대시보드 편집', exact: true }).click()
  await expect(page.getByTestId('workflow-layout')).toBeVisible()
  await expect(page.getByLabel('진행 현황 강조 색상')).toBeVisible()
  await expect(page.getByRole('button', { name: /단계 추가/ })).toHaveCount(0)
  await page.getByTestId('workflow-layout').getByRole('button', { name: '편집 취소' }).click()

  await expect(page.getByRole('button', { name: '진행 단계 편집', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '진행 단계 편집', exact: true }).click()
  await expect(page.getByTestId('workflow-stages')).toBeVisible()
  await expect(page.getByLabel('진행 현황 강조 색상')).toHaveCount(0)

  await page.getByRole('button', { name: /단계 추가/ }).first().click()
  await expect(page.getByTestId('draft-workflow-step')).toHaveCount(1)
  await expect(page.getByTestId('draft-workflow-step').getByRole('textbox', { name: /단계 이름/ })).toHaveValue('새 진행 단계')
  await page.getByTestId('workflow-stages').getByRole('button', { name: '편집 취소' }).click()
  await expect(page.getByTestId('draft-workflow-step')).toHaveCount(0)
})

test('해석 대시보드 편집은 별도 편집 세션으로 열리고 취소된다', async ({ page }) => {
  await openAnalysisWorkspace(page)
  await page.getByRole('button', { name: '대시보드 편집', exact: true }).click()
  await expect(page.getByTestId('analysis-dashboard')).toBeVisible()
  await expect(page.locator('.widget-drag-handle').first()).toBeVisible()
  await page.getByTestId('analysis-dashboard').getByRole('button', { name: '편집 취소' }).click()
  await expect(page.getByTestId('analysis-dashboard')).toBeHidden()
  await expect(page.locator('.widget-drag-handle')).toHaveCount(0)
})

test('PPT 레이아웃 편집기는 대시보드 편집 상태와 분리된다', async ({ page }) => {
  await openAnalysisWorkspace(page)
  await page.getByRole('button', { name: /보고서 내보내기/ }).first().click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByTestId('analysis-dashboard')).toHaveCount(0)

  await page.getByRole('button', { name: '레이아웃 편집·관리', exact: true }).click()
  await expect(page.getByTestId('ppt-layout-editor')).toBeVisible()
  await expect(page.getByRole('button', { name: '현재 레이아웃 새 버전 저장', exact: true })).toBeVisible()

  await page.getByRole('button', { name: '편집 닫기', exact: true }).click()
  await expect(page.getByTestId('ppt-layout-editor')).toBeHidden()
})
