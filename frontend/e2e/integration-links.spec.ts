import { expect, test, type Page } from '@playwright/test'

import { loginWorkspace } from './workspace-test-helpers'

// Sidebar links to the same-origin sibling apps (PhysicsAI "AI 예측", ESL "등가정적하중").
// The server flags (SIMDASH_PHYSICSAI_ENABLED / SIMDASH_ESL_ENABLED) are mocked per test.
async function mockLinks(page: Page, physicsAi: boolean, esl: boolean) {
  await page.route('**/api/integrations/physicsai', (route) => route.fulfill({ json: { enabled: physicsAi, base_path: '/physicsai/' } }))
  await page.route('**/api/integrations/esl', (route) => route.fulfill({ json: { enabled: esl, base_path: '/esl/' } }))
}

const sidebar = (page: Page) => page.getByRole('complementary', { name: '주 메뉴' })

test('등가정적하중 링크는 켜졌을 때 AI 예측 바로 아래에 새 창 링크로 표시된다', async ({ page }) => {
  await mockLinks(page, true, true)
  await loginWorkspace(page)
  const physicsAi = sidebar(page).getByRole('link', { name: 'AI 예측 (새 창)' })
  const esl = sidebar(page).getByRole('link', { name: '등가정적하중 (새 창)' })
  await expect(esl).toBeVisible()
  await expect(esl).toHaveAttribute('href', '/esl/')
  await expect(esl).toHaveAttribute('target', '_blank')
  await expect(esl).toHaveAttribute('rel', 'noopener noreferrer')
  await expect(esl).toHaveClass(/nav-link nav-link-standalone/)
  await expect(physicsAi).toHaveAttribute('href', '/physicsai/')
  // ESL is the element right after the PhysicsAI link.
  await expect(physicsAi.locator('xpath=following-sibling::*[1]')).toHaveAttribute('href', '/esl/')
})

test('등가정적하중 링크는 꺼져 있으면 숨기고 AI 예측 링크와 독립적이다', async ({ page }) => {
  await mockLinks(page, true, false)
  await loginWorkspace(page)
  await expect(sidebar(page).getByRole('link', { name: 'AI 예측 (새 창)' })).toBeVisible()
  await expect(sidebar(page).getByRole('link', { name: '등가정적하중 (새 창)' })).toHaveCount(0)
})

test('등가정적하중 링크는 AI 예측이 꺼져 있어도 단독으로 표시된다', async ({ page }) => {
  await mockLinks(page, false, true)
  await loginWorkspace(page)
  await expect(sidebar(page).getByRole('link', { name: '등가정적하중 (새 창)' })).toBeVisible()
  await expect(sidebar(page).getByRole('link', { name: 'AI 예측 (새 창)' })).toHaveCount(0)
})
