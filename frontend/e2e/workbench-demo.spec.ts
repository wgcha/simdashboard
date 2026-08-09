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
  await expect(page.getByTestId('task-execution-widget-CAD_PREPARE')).toContainText('형상 준비 실행 위젯')
  await expect(page.getByTestId('execute-selected-task')).toHaveText(/형상 준비 시작/)

  await page.locator('.assigned-work-list article.waiting').first().click()
  await expect(page.getByTestId('task-execution-widget-DOE_GENERATE')).toContainText('DOE 생성 실행 위젯')
  await expect(page.getByTestId('execute-selected-task')).toBeDisabled()
  await expect(page.getByText('현재 순서의 작업을 완료한 뒤 실행할 수 있습니다.')).toBeVisible()
  await page.locator('.assigned-work-list article.ready').click()

  await page.getByTestId('start-current-work').click()
  await expect(page.getByTestId('complete-current-work')).toBeVisible()
  await expect(page.locator('.assigned-work-list article.in_progress')).toContainText('CAD 작업')
  await expect(page.getByTestId('execute-selected-task')).toHaveText(/형상 준비 데모 실행·완료/)
  await page.getByLabel('작업 진행도').fill('20')
  await page.getByRole('button', { name: '진행도 저장' }).click()
  await expect(page.locator('.assigned-work-list article.in_progress')).toContainText('20%')

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

  await page.getByRole('button', { name: '해석 작업 실행', exact: true }).click()
  await page.getByTestId('start-current-work').click()
  await page.getByTestId('complete-current-work').click()
  await expect(page.locator('.assigned-work-list article.ready')).toContainText('HPC 수행')
  await page.getByTestId('start-current-work').click()
  await page.locator('.assigned-work-list article.in_progress').click()
  await expect(page.getByLabel('배치 경로 프로필')).toBeVisible()
  await page.getByRole('button', { name: '배치 실행 기록 생성' }).click()

  const attempt = page.locator('.batch-attempt-list article').first()
  await expect(attempt).toContainText('SUCCEEDED · 100%')
  await expect(attempt.getByRole('progressbar')).toHaveAttribute('value', '100')
  await expect(attempt.getByRole('list', { name: '배치 상태 이벤트' })).toContainText('PREFLIGHT · 0%')
  await expect(attempt.getByRole('list', { name: '배치 상태 이벤트' })).toContainText('QUEUED · 10%')
  await expect(attempt.getByRole('list', { name: '배치 상태 이벤트' })).toContainText('SUCCEEDED · 100%')
  await expect(attempt.getByLabel('실행 시점 배치 명령 snapshot')).toBeVisible()
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
  await expect(page.getByText('실행 권한이 없어 작업을 수행할 수 없습니다.')).toBeVisible()
  await expect(page.getByLabel('배치 명령 미리보기')).toHaveCount(0)
})

test('작업 유형 관리에서 배치 경로 정의를 별도 내부 탭으로 연다', async ({ page }) => {
  await login(page)
  await page.getByRole('button', { name: '작업 유형 관리', exact: true }).click()

  const requestTypesTab = page.getByRole('tab', { name: /의뢰·작업 유형/ })
  const batchPathsTab = page.getByRole('tab', { name: /배치 경로 정의/ })
  await expect(requestTypesTab).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('tabpanel', { name: /의뢰·작업 유형/ })).toBeVisible()
  await expect(page.getByRole('form', { name: '배치 경로 프로필 편집' })).toHaveCount(0)

  await batchPathsTab.click()
  await expect(batchPathsTab).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('tabpanel', { name: /배치 경로 정의/ })).toBeVisible()
  await expect(page.getByRole('form', { name: '배치 경로 프로필 편집' })).toBeVisible()
  await expect(page.getByLabel('Solver 실행 파일')).toBeVisible()

  await batchPathsTab.press('ArrowLeft')
  await expect(requestTypesTab).toBeFocused()
  await expect(requestTypesTab).toHaveAttribute('aria-selected', 'true')
})
