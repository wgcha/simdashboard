import { expect, test, type Page } from '@playwright/test'

async function login(page: Page, role: 'admin' | 'viewer') {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '보안 로그인' })).toBeVisible()
  await page.getByLabel('사용자 이름').fill(role === 'admin' ? 'e2e-admin' : 'e2e-viewer')
  await page.getByLabel('비밀번호').fill('e2e-validation-password')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.locator('.portfolio-page')).toBeVisible()
}

async function openAnalysis(page: Page) {
  await page.getByRole('link', { name: '해석 의뢰 현황', exact: true }).click()
  await expect(page.locator('.content-head')).toBeVisible()
  await page.locator('.view-tabs').getByRole('button', { name: /상세 분석/ }).click()
  await expect(page.locator('.analysis-subtabs')).toBeVisible()
}

test('관리자는 사용자 분석 페이지를 만들고 위젯을 편집·게시·보고서 선택할 수 있다', async ({ page }) => {
  await login(page, 'admin')
  await openAnalysis(page)

  await page.getByRole('button', { name: '분석 페이지 관리', exact: true }).click()
  const manager = page.getByRole('dialog', { name: '상세 분석 페이지 관리' })
  await expect(manager).toBeVisible()
  const createForm = manager.locator('.analysis-page-create')
  await createForm.getByLabel('페이지 이름').fill('사용자 분석 검증')
  await createForm.getByLabel('설명').fill('하중 경우에 연결된 자유 배치 페이지')
  await createForm.getByRole('button', { name: '생성하고 위젯 배치' }).click()

  const assistant = page.locator('.assistant-drawer')
  await expect(assistant).toBeVisible()
  await assistant.getByRole('button', { name: /KPI 카드/ }).click()
  await assistant.locator('.drawer-head button').click()

  const widget = page.locator('.widget-card').filter({ hasText: 'KPI 카드' })
  await expect(widget).toBeVisible()
  await widget.getByRole('button', { name: /설정/ }).click()
  const settings = page.locator('.widget-settings-drawer')
  await settings.getByLabel('제목').fill('사용자 최대 응력')
  await settings.getByLabel('글자 크기 (px)').fill('12')
  await expect(settings.getByLabel('보고서 포함')).toBeChecked()
  await settings.getByRole('button', { name: '설정 완료' }).click()
  await expect(page.getByRole('heading', { name: '사용자 최대 응력' })).toBeVisible()

  await page.getByRole('button', { name: '레이아웃 저장', exact: true }).click()
  await expect(page.getByText(/레이아웃 v2 저장 완료/)).toBeVisible()

  await page.getByRole('button', { name: '분석 페이지 관리', exact: true }).click()
  await manager.getByRole('button', { name: '게시', exact: true }).click()
  await expect(manager.getByText('PUBLISHED', { exact: true })).toBeVisible()
  await manager.getByRole('button', { name: '닫기' }).click()

  await expect(page.locator('.analysis-subtabs').getByRole('button', { name: /사용자 분석 검증/ })).toBeVisible()
  await expect(page.getByLabel('상세 분석 페이지 선택')).toHaveValue(/dashboard-/)
  await expect(page.getByLabel('상세 분석 페이지 선택').locator('option:checked')).toHaveText('사용자 분석 검증')
  await page.getByRole('button', { name: '보고서 내보내기', exact: true }).click()
  const reportDialog = page.getByRole('dialog', { name: /보고서/ })
  await expect(reportDialog).toBeVisible()
  await expect(reportDialog.getByLabel('보고서 분석 페이지')).toHaveValue(/dashboard-/)
  await expect(reportDialog.getByLabel('보고서 분석 페이지').locator('option:checked')).toHaveText('사용자 분석 검증')
})

test('Viewer는 게시 분석 페이지를 조회하지만 페이지 관리 기능은 사용할 수 없다', async ({ page }) => {
  await login(page, 'viewer')
  await openAnalysis(page)
  await expect(page.getByRole('button', { name: '분석 페이지 관리', exact: true })).toHaveCount(0)
  await expect(page.locator('.analysis-subtabs').getByRole('button', { name: /사용자 분석 검증/ })).toBeVisible()
})

test('관리자는 Run 비교 페이지에서도 보고서·자연어·대시보드 편집을 사용한다', async ({ page }) => {
  await login(page, 'admin')
  await openAnalysis(page)

  const compareTab = page.locator('.analysis-tab').filter({ hasText: 'Run 비교' })
  await expect(compareTab).toBeVisible()
  await compareTab.click()
  await expect(page.locator('.comparison-dashboard-widget').filter({ has: page.locator('.comparison-workspace') })).toBeVisible()
  await expect(page.getByRole('button', { name: '보고서 내보내기', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '자연어로 개선', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '대시보드 편집', exact: true })).toBeVisible()
})

test('관리자는 과거 버전을 초안으로 불러오고 삭제한 뒤 custom 분석 페이지를 영구 삭제한다', async ({ page }) => {
  await login(page, 'admin')
  await openAnalysis(page)

  await page.getByRole('button', { name: '분석 페이지 관리', exact: true }).click()
  let manager = page.getByRole('dialog', { name: '상세 분석 페이지 관리' })
  const createForm = manager.locator('.analysis-page-create')
  await createForm.getByLabel('페이지 이름').fill('버전 삭제 회귀 페이지')
  await createForm.getByLabel('설명').fill('버전 초안과 영구 삭제 검증')
  await createForm.getByRole('button', { name: '생성하고 위젯 배치' }).click()
  let assistant = page.locator('.assistant-drawer')
  await assistant.getByRole('button', { name: /KPI 카드/ }).click()
  await assistant.locator('.drawer-head button').click()
  await page.getByRole('button', { name: '레이아웃 저장', exact: true }).click()
  await page.getByRole('button', { name: '분석 페이지 관리', exact: true }).click()
  manager = page.getByRole('dialog', { name: '상세 분석 페이지 관리' })
  await manager.getByRole('button', { name: '게시', exact: true }).click()
  await manager.getByRole('button', { name: '닫기' }).click()

  await page.getByRole('button', { name: '대시보드 편집', exact: true }).click()
  await page.locator('.dashboard-edit-toolbar').getByRole('button').click()
  const history = page.locator('.dashboard-version-list')
  await expect(history.locator('article')).toHaveCount(3)
  await history.locator('button:enabled', { hasText: '초안으로 불러오기' }).first().click()
  const toast = page.locator('.toast[role="status"]')
  await expect(toast).toContainText('편집 초안')
  await toast.getByRole('button', { name: '알림 닫기' }).click()
  await expect(toast).toHaveCount(0)
  const beforeDelete = await history.locator('article').count()
  await history.getByRole('button', { name: /버전 삭제/ }).first().click()
  await expect(history.locator('article')).toHaveCount(beforeDelete - 1)
  await expect(page.locator('.toast[role="status"]')).toContainText('과거 이력')
  await expect(page.locator('.toast[role="status"]')).toHaveCount(0, { timeout: 5500 })

  assistant = page.locator('.assistant-drawer')
  await assistant.locator('.drawer-head button').click()
  await page.getByRole('button', { name: '분석 페이지 관리', exact: true }).click()
  manager = page.getByRole('dialog', { name: '상세 분석 페이지 관리' })
  await manager.locator('.permanent-delete').click()
  const confirmation = page.getByRole('alertdialog', { name: '버전 삭제 회귀 페이지 영구 삭제' })
  await confirmation.getByLabel('페이지 이름 확인').fill('버전 삭제 회귀 페이지')
  await confirmation.getByRole('button', { name: '영구 삭제', exact: true }).click()
  await expect(manager).toHaveCount(0)
  await expect(page.locator('.analysis-subtabs').getByRole('button', { name: /버전 삭제 회귀 페이지/ })).toHaveCount(0)
  await expect(page.locator('.analysis-tab.active')).not.toContainText('버전 삭제 회귀 페이지')
})
