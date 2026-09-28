import { expect, test } from '@playwright/test'
import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

test('작업 실행에서 PC도우미 화면과 연결 요청을 제공하지 않는다', async ({ page }) => {
  const helperRequests: string[] = []
  await page.route('http://127.0.0.1:8766/**', (route) => route.abort())
  await page.route('http://localhost:8766/**', (route) => route.abort())
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.port === '8766' || url.pathname.startsWith('/api/local-execution/') || url.pathname.startsWith('/api/local-helper/')) helperRequests.push(request.url())
  })
  await loginWorkspace(page)
  await openWorkspaceRoute(page, '/workspace/execution')
  await expect(page.getByTestId('work-item-detail')).toBeVisible()
  await expect(page.getByTestId('execute-selected-task')).toBeEnabled()
  await expect(page.getByTestId('local-program-panel')).toHaveCount(0)
  await expect(page.getByText('내 PC 프로그램으로 작업 실행', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: /^CAD 작업 · v1/ }).click()
  await expect(page.getByTestId('local-program-panel')).toHaveCount(0)
  await expect(page.getByTestId('work-item-detail').getByRole('heading', { name: 'CAD 작업', exact: true })).toBeVisible()
  expect(helperRequests).toEqual([])
})
