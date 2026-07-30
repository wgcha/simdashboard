import { expect, test, type Page } from '@playwright/test'

async function login(page: Page, role: 'admin' | 'viewer' = 'admin') {
  await page.goto('/')
  await page.getByLabel('사용자 이름').fill(role === 'admin' ? 'e2e-admin' : 'e2e-viewer')
  await page.getByLabel('비밀번호').fill('e2e-validation-password')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByRole('button', { name: '운영 대시보드', exact: true })).toBeVisible()
}

test('DOE 의뢰를 접수하고 배정 작업을 명시적으로 시작·완료한다', async ({ page }) => {
  await login(page)
  const title = `E2E DOE 의뢰 ${Date.now()}`

  await page.getByRole('button', { name: '의뢰 접수', exact: true }).click()
  await expect(page.getByTestId('request-intake-page')).toBeVisible()
  await page.getByRole('button', { name: /DOE EXPLORATION/ }).click()

  const preview = page.locator('.intake-work-preview')
  await expect(preview.locator('strong')).toHaveText([
    'CAD 작업',
    'DOE 파일 생성',
    'HPC 수행',
    '결과 후처리',
    '최적화 분석',
    '해석 DB 저장',
    '설계 성능 순위 평가',
  ])

  await page.getByLabel('의뢰 출처 상세').fill('E2E PLM Gateway')
  await page.locator('label').filter({ hasText: '요청자' }).locator('input').fill('E2E 요청자')
  await page.locator('label').filter({ hasText: '의뢰 제목' }).locator('input').fill(title)
  await page.locator('label').filter({ hasText: '담당 수행자' }).locator('input').fill('E2E 수행자')
  await page.getByTestId('submit-request-intake').click()

  const success = page.getByTestId('intake-success')
  await expect(success).toContainText(`${title} 접수 완료`)
  await expect(success).toContainText('설계 DOE 탐색 · 상태 READY · 진행률 0%')
  await success.getByRole('button', { name: '배정 작업 열기' }).click()

  const workbench = page.getByTestId('simulation-workbench')
  await expect(workbench).toBeVisible()
  await expect(page.getByLabel('배정 작업 대상 의뢰').locator('option:checked')).toContainText(title)
  await expect(page.locator('.assigned-request-card')).toContainText(title)
  await expect(page.locator('.assigned-request-card')).toContainText('0 / 7')
  await expect(page.locator('.assigned-work-list article.ready')).toContainText('CAD 작업')
  await expect(page.locator('.assigned-work-list article.waiting')).toHaveCount(6)

  await page.getByTestId('start-current-work').click()
  await expect(page.getByTestId('complete-current-work')).toBeVisible()
  await expect(page.locator('.assigned-work-list article.in_progress')).toContainText('CAD 작업')

  await page.getByTestId('complete-current-work').click()
  await expect(page.locator('.assigned-request-card')).toContainText('1 / 7')
  await expect(page.locator('.assigned-work-list article.completed')).toContainText('CAD 작업')
  await expect(page.locator('.assigned-work-list article.ready')).toContainText('DOE 파일 생성')
  await expect(page.getByTestId('start-current-work')).toHaveText(/작업 시작/)
  await expect(page.locator('.workbench-run-detail')).toContainText('CAD 작업 데모 수행')
  await expect(page.getByLabel('텍스트 데모 파일 내용')).toBeVisible()

  await page.getByRole('button', { name: '해석 의뢰 현황', exact: true }).click()
  await expect(page.getByTestId('selected-request-progress-tab')).toContainText('14%')
  const monitoringLane = page.locator('.workflow-lane').filter({ hasText: title })
  await expect(monitoringLane).toContainText('1 / 7 작업 완료')
  await expect(monitoringLane.locator('.workflow-lane-meta')).toContainText('14%')

  await page.getByRole('button', { name: '운영 대시보드', exact: true }).click()
  const portfolioRow = page.locator('.portfolio-row').filter({ hasText: title })
  await expect(portfolioRow).toContainText('14%')
  await expect(portfolioRow).toContainText('DOE 파일 생성')
})

test('Viewer는 배정 작업과 진행 상태를 보되 시작·완료할 수 없다', async ({ page }) => {
  await login(page, 'viewer')
  await page.getByRole('button', { name: '해석 작업 실행', exact: true }).click()
  await expect(page.getByTestId('simulation-workbench')).toBeVisible()
  await expect(page.getByRole('button', { name: '작업 유형 관리', exact: true })).toHaveCount(0)

  const start = page.getByTestId('start-current-work')
  const complete = page.getByTestId('complete-current-work')
  if (await start.count()) await expect(start).toBeDisabled()
  if (await complete.count()) await expect(complete).toBeDisabled()
})
