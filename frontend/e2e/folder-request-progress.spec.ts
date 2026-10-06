import { expect, test, type Page } from '@playwright/test'
import { loginWorkspace, mockResultEnvironments } from './workspace-test-helpers'

// folder-request-progress.md §3–4: folder-registered requests show folder-derived steps.
const FOLDER_PROGRESS = '**/api/projects/*/requests/*/folder-progress'

function trackProgressCalls(page: Page) {
  const urls: string[] = []
  page.on('request', (request) => { if (request.url().includes('/folder-progress')) urls.push(new URL(request.url()).pathname) })
  return urls
}

test('폴더 등록 의뢰는 폴더 상태 5단계와 Case 결과 버튼을 보여준다', async ({ page }) => {
  await page.setViewportSize({ width: 1505, height: 1045 })
  await mockResultEnvironments(page)
  const calls = trackProgressCalls(page)
  await page.route(FOLDER_PROGRESS, (route) => route.fulfill({ json: {
    applicable: true, environment: 'USAGE', completed: 2, total: 5, current_key: 'RESULTS', next_action: '결과 대기 Case 1개', checked_at: '2026-10-04T00:41:00+00:00',
    steps: [
      { key: 'REGISTERED', label: '의뢰 등록', status: 'DONE', detail: null },
      { key: 'MODELING', label: '해석 모델링', status: 'DONE', detail: 'Case 2/2 입력 있음' },
      { key: 'RESULTS', label: '해석 결과', status: 'IN_PROGRESS', detail: 'Case 1/2 결과 있음' },
      { key: 'FINAL', label: 'Final 지정', status: 'WAITING', detail: null },
      { key: 'REPORT', label: '보고서', status: 'WAITING', detail: null },
    ],
  } }))
  await loginWorkspace(page)
  const overview = page.getByTestId('focused-request-overview')
  const progress = overview.getByTestId('folder-request-progress')
  await expect(progress).toBeVisible()
  await expect(progress.getByRole('heading', { name: '결과 대기 Case 1개' })).toBeVisible()
  await expect(progress).toContainText('2 / 5 단계 완료')
  const steps = overview.getByRole('region', { name: '의뢰 진척 단계' }).locator('.focused-timeline-step')
  await expect(steps).toHaveCount(5)
  await expect(steps.nth(0)).toContainText('완료')
  await expect(steps.nth(2)).toHaveClass(/in_progress/)
  await expect(steps.nth(2)).toHaveClass(/current/)
  await expect(steps.nth(2)).toContainText('진행 중 · Case 1/2 결과 있음')
  await expect(steps.nth(4)).toContainText('대기')
  await expect(overview.getByRole('button', { name: /작업 이어하기|작업 상태 확인|작업 기록 보기/ })).toHaveCount(0)
  await expect(overview.locator('#work-history')).toHaveCount(0)
  expect(calls.length).toBeGreaterThan(0)
  expect(calls.every((pathname) => /^\/api\/projects\/[^/]+\/requests\/[^/]+\/folder-progress$/.test(pathname))).toBe(true)

  await overview.getByRole('button', { name: 'Case 결과 열기', exact: true }).click()
  await expect.poll(() => new URL(page.url()).searchParams.get('view')).toBe('case_results')
})

for (const mode of ['not-applicable', 'failure'] as const) {
  test(`구 방식 의뢰는 기존 작업 단계 화면을 유지한다 (${mode})`, async ({ page }) => {
    await page.setViewportSize({ width: 1505, height: 1045 })
    await mockResultEnvironments(page)
    await page.route(FOLDER_PROGRESS, (route) => mode === 'failure'
      ? route.fulfill({ status: 500, json: { detail: 'boom' } })
      : route.fulfill({ json: { applicable: false, environment: null, completed: 0, total: 0, current_key: null, next_action: '', steps: [], checked_at: null } }))
    await loginWorkspace(page)
    const overview = page.getByTestId('focused-request-overview')
    await expect(overview.getByText('현재 할 일', { exact: true })).toBeVisible()
    await expect(overview.getByRole('region', { name: '의뢰 작업 단계' })).toHaveCount(1)
    await expect(overview.getByText(/\d+ \/ \d+ 작업 완료/)).toBeVisible()
    await expect(overview.getByTestId('folder-request-progress')).toHaveCount(0)
  })
}
