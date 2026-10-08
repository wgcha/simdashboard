import { expect, type Locator, type Page } from '@playwright/test'

export async function revealControl(control: Locator) {
  for (const container of await control.locator('xpath=ancestor::details').all()) {
    if (await container.getAttribute('open') === null) await container.locator('summary').first().click()
  }
}

/**
 * Mocks GET .../result-environments (case-results-environment.md §2). Defaults to a
 * mixed request (both environments), which keeps the legacy toggle (E4) these specs use.
 */
export async function mockResultEnvironments(page: Page, environments: Array<'USAGE' | 'DISTRIBUTION'> = ['USAGE', 'DISTRIBUTION']) {
  await page.route('**/api/projects/*/requests/*/result-environments', (route) => route.fulfill({ json: { environments, case_counts: { USAGE: environments.includes('USAGE') ? 1 : 0, DISTRIBUTION: environments.includes('DISTRIBUTION') ? 1 : 0 } } }))
}

/** Routes hidden from the sidebar (AppSidebar SIDEBAR_HIDDEN_MENUS); opened by in-app URL navigation. */
const HIDDEN_SIDEBAR_PATHS = new Set(['/workspace/requests/new', '/workspace/admin/work-types', '/workspace/project/result-layouts', '/workspace/catalog/variables', '/workspace/examples'])

/** Navigate through the real sidebar, including its collapsed utility groups. */
export async function openWorkspaceRoute(page: Page, pathname: string) {
  const sidebar = page.getByRole('complementary', { name: '주 메뉴' })
  await expect(sidebar).toBeVisible()
  const mobileMenu = sidebar.getByRole('button', { name: '메뉴 열기', exact: true })
  if (await mobileMenu.isVisible()) await mobileMenu.click()
  if (pathname === '/workspace/notifications') {
    // Not a sidebar menu: the top bar bell → 모두 보기 (2026-10-08).
    await page.getByTestId('notification-bell').first().click()
    await page.getByRole('dialog', { name: '최근 알림' }).getByRole('button', { name: '모두 보기', exact: true }).click()
    await expect(page).toHaveURL(/\/workspace\/notifications(\?|$)/)
    return
  }
  const link = sidebar.locator(`a[href="${pathname}"], a[href^="${pathname}?"]`)
  if (pathname === '/workspace/overview' || pathname === '/workspace/requests') {
    await expect(link).toHaveCount(1)
    await link.click()
    return
  }
  if (await link.count()) {
    await expect(link).toHaveCount(1)
    await revealControl(link)
    await link.click()
    return
  }
  const embeddedRequestTabs: Record<string, string> = {
    '/workspace/execution': '작업 실행',
    '/workspace/data': '결과 등록',
  }
  const requestTab = embeddedRequestTabs[pathname]
  if (!requestTab && HIDDEN_SIDEBAR_PATHS.has(pathname)) {
    // Hidden from the sidebar (SIDEBAR_HIDDEN_MENUS, 2026-10-06) but still reachable by URL:
    // navigate in-app so client state (font size, theme, login) is kept.
    await page.evaluate((target) => { window.history.pushState({}, '', target); window.dispatchEvent(new PopStateEvent('popstate')) }, pathname)
    await expect(page).toHaveURL(new RegExp(`${pathname.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(\\?|$)`))
    return
  }
  if (!requestTab) throw new Error(`No sidebar or request-workspace destination for ${pathname}`)
  const journey = page.getByRole('navigation', { name: '의뢰 작업 여정' })
  if (!(await journey.count())) {
    const myWork = sidebar.getByRole('link', { name: '내 작업', exact: true })
    await revealControl(myWork)
    await myWork.click()
    await expect(journey).toBeVisible()
  }
  const mobileClose = sidebar.getByRole('button', { name: '메뉴 닫기', exact: true })
  if (await mobileClose.isVisible()) await mobileClose.click()
  await expect(journey).toBeVisible()
  await journey.getByRole('button', { name: requestTab, exact: true }).click()
}

export async function openSidebarUtilities(page: Page) {
  const sidebar = page.getByRole('complementary', { name: '주 메뉴' })
  const mobileMenu = sidebar.getByRole('button', { name: '메뉴 열기', exact: true })
  if (await mobileMenu.isVisible()) await mobileMenu.click()
  for (const group of await sidebar.locator('details').all()) {
    if (await group.getAttribute('open') === null) await group.locator('summary').first().click()
  }
}

export async function loginWorkspace(page: Page, username = 'e2e-admin', pathname = '/workspace/requests') {
  await page.goto(pathname)
  await page.getByLabel('사용자 이름').fill(username)
  await page.getByLabel('비밀번호').fill('e2e-validation-password')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '주 메뉴' })).toBeVisible()
}

export const WORKSPACE_FONT_SIZE_STORAGE_KEY = 'simdashboard.workspace.font-size-pt.v1'

/**
 * Set the global font size (11–18 pt). The sidebar buttons are hidden (user decision 2026-10-08):
 * write the stored preference and announce it like another tab would (``storage`` event); the
 * app applies it without a reload, so layout checks still measure the same session.
 */
export async function setWorkspaceFontSize(page: Page, points: number) {
  await page.evaluate(({ key, value }) => {
    window.localStorage.setItem(key, String(value))
    window.dispatchEvent(new StorageEvent('storage', { key, newValue: String(value), storageArea: window.localStorage }))
  }, { key: WORKSPACE_FONT_SIZE_STORAGE_KEY, value: points })
  await expect.poll(() => page.evaluate(() => document.documentElement.style.getPropertyValue('--ui-font-size'))).toBe(`${points}pt`)
}

type Box = { x: number; y: number; width: number; height: number }
function overlaps(a: Box, b: Box) { return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height }

/** Desktop layout guard: no page-level horizontal scroll, path bar and header actions inside the dashboard and not overlapping. */
export async function expectCaseResultsLayout(page: Page) {
  expect(await page.evaluate(() => document.scrollingElement!.scrollWidth <= document.scrollingElement!.clientWidth)).toBe(true)
  const section = await page.getByRole('region', { name: 'SPDM 해석 결과 대시보드' }).boundingBox()
  const actions = await page.locator('.case-results-head__actions').boundingBox()
  const path = await page.locator('.shared-hierarchy-path').first().boundingBox()
  const env = await page.getByRole('group', { name: '결과 환경' }).boundingBox()
  expect(section && actions && path && env).toBeTruthy()
  for (const box of [actions!, path!]) expect(box.x + box.width).toBeLessThanOrEqual(section!.x + section!.width + 1)
  expect(overlaps(actions!, path!)).toBe(false)
  expect(overlaps(actions!, env!)).toBe(false)
}
