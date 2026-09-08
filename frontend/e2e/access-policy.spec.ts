import { openSidebarUtilities } from './workspace-test-helpers'
import { expect, test, type Page } from '@playwright/test'

const password = 'e2e-validation-password'

async function login(page: Page, username: string) {
  await page.goto('/')
  await page.getByLabel('사용자 이름').fill(username)
  await page.getByLabel('비밀번호').fill(password)
  await page.getByRole('button', { name: '로그인' }).click()
  await expect(page.getByRole('complementary', { name: '주 메뉴' })).toBeVisible()
}

test('일반 사용자는 대시보드와 업무 실행 메뉴만 본다', async ({ page }) => {
  await login(page, 'e2e-viewer')
  await page.getByRole('link', { name: '내 작업', exact: true }).click()
  await expect(page.getByRole('navigation', { name: '의뢰 작업 여정' })).toBeVisible()
  await openSidebarUtilities(page)
  await expect(page.getByRole('link', { name: '결과 대시보드', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '내 작업', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '해석 작업 실행', exact: true })).toHaveCount(0)
  await expect(page.getByRole('navigation', { name: '의뢰 작업 여정' }).getByRole('button', { name: '작업 실행', exact: true })).toBeEnabled()
  await expect(page.getByRole('link', { name: '새 의뢰', exact: true })).toHaveCount(0)
  await expect(page.getByRole('link', { name: '사용자·프로젝트 권한', exact: true })).toHaveCount(0)
})

test('파워 사용자는 의뢰와 결과 편집 메뉴를 본다', async ({ page }) => {
  await login(page, 'e2e-power')
  await page.getByRole('link', { name: '내 작업', exact: true }).click()
  await expect(page.getByRole('navigation', { name: '의뢰 작업 여정' })).toBeVisible()
  await openSidebarUtilities(page)
  await expect(page.getByRole('link', { name: '새 의뢰', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '해석 데이터 등록', exact: true })).toHaveCount(0)
  await expect(page.getByRole('navigation', { name: '의뢰 작업 여정' }).getByRole('button', { name: '결과 등록', exact: true })).toBeEnabled()
  await expect(page.getByRole('link', { name: '사용자·프로젝트 권한', exact: true })).toHaveCount(0)
  await expect(page.getByRole('link', { name: '권한 및 메뉴 정책', exact: true })).toHaveCount(0)
})

test('프로젝트 관리자는 프로젝트 권한 메뉴까지만 본다', async ({ page }) => {
  await login(page, 'e2e-project-admin')
  await openSidebarUtilities(page)
  await expect(page.getByRole('link', { name: '변수 카탈로그', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '사용자·프로젝트 권한', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '권한 및 메뉴 정책', exact: true })).toHaveCount(0)
  await expect(page.getByRole('link', { name: '감사로그', exact: true })).toHaveCount(0)
})

test('전역 관리자는 간결한 기본 메뉴에서 설정을 펼쳐 모든 관리 기능에 접근한다', async ({ page }) => {
  await login(page, 'e2e-admin')
  const sidebar = page.getByRole('complementary', { name: '주 메뉴' })
  await expect(sidebar.getByRole('link', { name: '내 작업', exact: true })).toBeVisible()
  await expect(sidebar.getByRole('link', { name: '결과 대시보드', exact: true })).toBeVisible()
  await expect(sidebar.locator('.nav-flow-order')).toHaveCount(0)
  await expect(sidebar.getByRole('link', { name: '감사로그', exact: true, includeHidden: true })).toHaveCount(1)
  await openSidebarUtilities(page)
  await expect(page.getByRole('link', { name: '권한 및 메뉴 정책', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '감사로그', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '작업 유형 관리', exact: true })).toBeVisible()

  await page.getByRole('button', { name: '메뉴 접기', exact: true }).click()
  await expect(page.locator('.app-shell')).toHaveClass(/sidebar-collapsed/)
  await expect(sidebar.getByRole('link', { name: '내 작업', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '메뉴 펼치기', exact: true }).click()
  await expect(page.locator('.app-shell')).not.toHaveClass(/sidebar-collapsed/)
})
