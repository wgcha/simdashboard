import { expect, test } from '@playwright/test'
import { loginWorkspace } from './workspace-test-helpers'

test('legacy local PC URL redirects to a permitted workspace without loading helper APIs', async ({ page }) => {
  const helperRequests: string[] = []
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/local-helper/') || url.pathname.startsWith('/api/local-execution/')) helperRequests.push(url.pathname)
    if (url.origin === 'http://127.0.0.1:8766') helperRequests.push(url.pathname)
  })

  await loginWorkspace(page, 'e2e-viewer', '/workspace/settings/local-pc')
  await expect(page).toHaveURL(/\/workspace\/overview(?:\?|$)/)
  await expect(page.getByRole('complementary', { name: '주 메뉴' }).getByRole('link', { name: '내 PC 설정', exact: true })).toHaveCount(0)
  await expect(page.getByTestId('local-pc-settings')).toHaveCount(0)
  expect(helperRequests).toEqual([])
})
