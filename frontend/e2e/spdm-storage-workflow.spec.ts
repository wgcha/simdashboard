import { expect, test, type Page } from '@playwright/test'

import { loginWorkspace } from './workspace-test-helpers'

// W8 (2026-10-06): the staged draft flow (upload → automatic checks → approval → publish)
// was replaced by the folder guide + drag & drop screen (e2e/result-drop-upload.spec.ts).
// These checks keep the request journey honest against the real backend: the 결과 등록
// tab opens the new screen, asks the real API for the request's Working tree, never
// calls the old draft/target endpoints, and lists old drafts only read-only.

const context = { project: 'project-feature-showcase', request: 'request-showcase-compare' }

async function openRegistrationTab(page: Page) {
  await loginWorkspace(page)
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption(context.project)
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption(context.request)
  await page.getByRole('navigation', { name: '의뢰 작업 여정' }).getByRole('button', { name: '결과 등록', exact: true }).click()
  await expect(page).toHaveURL(/\/workspace\/data(?:\?|$)/)
  await expect(page.getByTestId('result-drop-workspace')).toBeVisible()
}

test('결과 등록 탭은 실제 API로 의뢰 Working 위치를 확인하고 이전 초안 흐름을 호출하지 않는다', async ({ page }) => {
  const calls: string[] = []
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/result-registration/')) calls.push(`${request.method()} ${url.pathname}`)
  })
  await openRegistrationTab(page)
  await expect.poll(() => calls.some((item) => item === 'GET /api/result-registration/drop-target')).toBe(true)
  // The showcase request has no confirmed SPDM folder binding in the e2e database:
  // the server refuses to guess a Working folder and nothing can be dropped.
  await expect(page.getByRole('alert').first()).toBeVisible()
  await expect(page.getByTestId('result-drop-zone')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /업로드하고 자동 검사|검수 완료|DB 등록/ })).toHaveCount(0)

  const history = page.getByTestId('legacy-draft-history')
  await history.locator('summary').click()
  await expect.poll(() => calls.some((item) => item === 'GET /api/result-registration/drafts')).toBe(true)
  await expect(history).toContainText(/이전 등록 초안이 없습니다|이전 등록 초안/)
  expect(calls.filter((item) => /\/(targets|folders|locations)$|\/drafts\/|\/inspect|\/approve|\/publish/.test(item))).toEqual([])
})
