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
  await expect(page.getByRole('link', { name: '운영 대시보드', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '해석 작업 실행', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '의뢰 접수', exact: true })).toHaveCount(0)
  await expect(page.getByRole('link', { name: '사용자·프로젝트 권한', exact: true })).toHaveCount(0)
})

test('파워 사용자는 의뢰와 결과 편집 메뉴를 본다', async ({ page }) => {
  await login(page, 'e2e-power')
  await expect(page.getByRole('link', { name: '의뢰 접수', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '해석 데이터 등록', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '사용자·프로젝트 권한', exact: true })).toHaveCount(0)
  await expect(page.getByRole('link', { name: '권한 및 메뉴 정책', exact: true })).toHaveCount(0)
})

test('프로젝트 관리자는 프로젝트 권한 메뉴까지만 본다', async ({ page }) => {
  await login(page, 'e2e-project-admin')
  await expect(page.getByRole('link', { name: '변수 카탈로그', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '사용자·프로젝트 권한', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '권한 및 메뉴 정책', exact: true })).toHaveCount(0)
  await expect(page.getByRole('link', { name: '감사로그', exact: true })).toHaveCount(0)
})

test('전역 관리자는 복구 메뉴와 시스템 메뉴를 항상 본다', async ({ page }) => {
  await login(page, 'e2e-admin')
  const sidebar = page.getByRole('complementary', { name: '주 메뉴' })
  await expect(sidebar.locator('.nav-group-label')).toHaveText([
    '운영 개요',
    '의뢰 · 수행 흐름',
    '업무 구성',
    '운영 관리',
    '지원',
  ])
  await expect(sidebar.locator('.nav-group--workflow a')).toHaveCount(4)
  expect(await sidebar.locator('.nav-group--workflow a').evaluateAll((links) => links.map((link) => link.getAttribute('aria-label')))).toEqual([
    '해석 의뢰 현황',
    '의뢰 접수',
    '해석 작업 실행',
    '해석 데이터 등록',
  ])
  await expect(sidebar.locator('.nav-group--workflow .nav-flow-order')).toHaveText(['1', '2', '3', '4'])
  await expect(page.getByRole('link', { name: '권한 및 메뉴 정책', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '감사로그', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '작업 유형 관리', exact: true })).toBeVisible()

  await page.getByRole('button', { name: '메뉴 접기', exact: true }).click()
  await expect(page.locator('.app-shell')).toHaveClass(/sidebar-collapsed/)
  await expect(sidebar.locator('.nav-group-label:visible')).toHaveCount(0)
  await expect(sidebar.locator('.nav-flow-order:visible')).toHaveCount(0)
})
