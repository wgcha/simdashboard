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
  await expect(page.getByRole('button', { name: '운영 대시보드', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '해석 작업 실행', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '의뢰 접수', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '사용자·프로젝트 권한', exact: true })).toHaveCount(0)
})

test('파워 사용자는 의뢰와 결과 편집 메뉴를 본다', async ({ page }) => {
  await login(page, 'e2e-power')
  await expect(page.getByRole('button', { name: '의뢰 접수', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '해석 데이터 등록', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '사용자·프로젝트 권한', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '권한 및 메뉴 정책', exact: true })).toHaveCount(0)
})

test('프로젝트 관리자는 프로젝트 권한 메뉴까지만 본다', async ({ page }) => {
  await login(page, 'e2e-project-admin')
  await expect(page.getByRole('button', { name: '변수 카탈로그', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '사용자·프로젝트 권한', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '권한 및 메뉴 정책', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '감사로그', exact: true })).toHaveCount(0)
})

test('전역 관리자는 복구 메뉴와 시스템 메뉴를 항상 본다', async ({ page }) => {
  await login(page, 'e2e-admin')
  await expect(page.getByRole('button', { name: '권한 및 메뉴 정책', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '감사로그', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '작업 유형 관리', exact: true })).toBeVisible()
})
