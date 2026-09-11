import { expect, type Locator, type Page } from '@playwright/test'

export async function revealControl(control: Locator) {
  for (const container of await control.locator('xpath=ancestor::details').all()) {
    if (await container.getAttribute('open') === null) await container.locator('summary').first().click()
  }
}

/** Navigate through the real sidebar, including its collapsed utility groups. */
export async function openWorkspaceRoute(page: Page, pathname: string) {
  const sidebar = page.getByRole('complementary', { name: '주 메뉴' })
  await expect(sidebar).toBeVisible()
  const mobileMenu = sidebar.getByRole('button', { name: '메뉴 열기', exact: true })
  if (await mobileMenu.isVisible()) await mobileMenu.click()
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
