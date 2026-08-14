import { expect, test, type Page } from '@playwright/test'

const password = 'e2e-validation-password'

async function login(page: Page, username = 'e2e-admin') {
  await page.goto('/')
  await page.getByLabel('사용자 이름').fill(username)
  await page.getByLabel('비밀번호').fill(password)
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '주 메뉴' })).toBeVisible()
}

test('workspace menu navigation updates the URL and browser history restores the active menu', async ({ page }) => {
  await login(page)
  await expect(page).toHaveURL(/\/workspace\/overview$/)

  await page.getByRole('link', { name: '도움말', exact: true }).click()
  await expect(page).toHaveURL(/\/workspace\/help$/)
  await expect(page.getByRole('link', { name: '도움말', exact: true })).toHaveAttribute('aria-current', 'page')

  await page.goBack()
  await expect(page).toHaveURL(/\/workspace\/overview$/)
  await expect(page.getByRole('link', { name: '운영 대시보드', exact: true })).toHaveAttribute('aria-current', 'page')

  await page.goForward()
  await expect(page).toHaveURL(/\/workspace\/help$/)
})

test('a direct workspace URL survives refresh and unknown paths show a 404 page', async ({ page }) => {
  await page.goto('/workspace/help')
  await page.getByLabel('사용자 이름').fill('e2e-admin')
  await page.getByLabel('비밀번호').fill(password)
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page).toHaveURL(/\/workspace\/help$/)
  await expect(page.getByRole('heading', { name: /사용 도움말/ })).toBeVisible()

  await page.reload()
  await expect(page).toHaveURL(/\/workspace\/help$/)
  await expect(page.getByRole('heading', { name: /사용 도움말/ })).toBeVisible()

  await page.goto('/workspace/not-found')
  await expect(page.getByTestId('workspace-not-found')).toBeVisible()
})

test('a direct route without permission falls back to the first allowed workspace route', async ({ page }) => {
  await page.goto('/workspace/admin/audit')
  await page.getByLabel('사용자 이름').fill('e2e-viewer')
  await page.getByLabel('비밀번호').fill(password)
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page).toHaveURL(/\/workspace\/overview$/)
  await expect(page.getByRole('link', { name: '운영 대시보드', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('link', { name: '감사로그', exact: true })).toHaveCount(0)
})
