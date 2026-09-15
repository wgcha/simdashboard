import { expect, test, type Page } from '@playwright/test'
import { readFileSync, mkdirSync } from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import { loginWorkspace } from './workspace-test-helpers'

async function downloadBytes(page: Page, label: string) {
  const pending = page.waitForEvent('download', { timeout: 20_000 })
  await page.getByRole('button', { name: label, exact: true }).click()
  const download = await pending
  expect(await download.failure()).toBeNull()
  return { name: download.suggestedFilename(), bytes: readFileSync((await download.path())!) }
}

test('전역 관리자는 VOC에서 회원정보 자동 기록, 텍스트 등록과 CSV·JSON 다운로드를 완료한다', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.route('http://127.0.0.1:8766/**', (route) => route.abort('connectionrefused'))
  await loginWorkspace(page, 'e2e-admin', '/workspace/overview')
  const menu = page.getByRole('link', { name: 'VOC 게시판', exact: true })
  await expect(menu).toBeVisible()
  expect(await menu.evaluate((element) => element.previousElementSibling?.getAttribute('href'))).toBe('/workspace/help')
  await menu.click()
  await expect(page).toHaveURL(/\/workspace\/voc/)
  await expect(page.getByRole('heading', { name: 'VOC 게시판', exact: true })).toBeVisible()
  const content = `=VOC-${Date.now()}\n한글 개선 의견, "다운로드" 확인\n<img src="voc-xss" onerror="window.vocInjected=true">`
  const input = page.getByRole('textbox', { name: '개선 의견', exact: true })
  const submit = page.getByRole('button', { name: '등록', exact: true })
  await expect(submit).toBeDisabled()
  await input.fill('   ')
  await expect(submit).toBeDisabled()
  await input.fill(content)
  await page.route('**/api/voc/posts', (route) => route.request().method() === 'POST'
    ? route.fulfill({ status: 503, json: { detail: 'VOC 저장 재시도 테스트' } }) : route.continue())
  await submit.click()
  await expect(page.getByRole('alert')).toContainText('VOC 저장 재시도 테스트')
  await expect(input).toHaveValue(content)
  await page.unroute('**/api/voc/posts')
  const created = page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/voc/posts' && response.status() === 201)
  await submit.click()
  const row = await (await created).json() as { id: string; author_username: string; author_display_name: string; content: string }
  expect(row).toMatchObject({ author_username: 'e2e-admin', author_display_name: 'E2E 관리자', content })
  await expect(input).toHaveValue('')
  await expect(page.getByText(content, { exact: true })).toBeVisible()
  await expect(page.locator('img[src="voc-xss"]')).toHaveCount(0)
  await page.reload()
  await expect(page.getByText(content, { exact: true })).toBeVisible()

  await page.route('**/api/voc/export?format=json', (route) => route.fulfill({ status: 503, json: { detail: 'VOC 다운로드 재시도 테스트' } }))
  await page.getByRole('button', { name: 'JSON 다운로드', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('VOC 다운로드 재시도 테스트')
  await page.unroute('**/api/voc/export?format=json')
  const [jsonDownload, simultaneousList] = await Promise.all([
    downloadBytes(page, 'JSON 다운로드'),
    page.request.get('/api/voc/posts?limit=1', { timeout: 20_000 }),
  ])
  expect(simultaneousList.ok()).toBe(true)
  expect(jsonDownload.name).toMatch(/\.json$/)
  const exported = JSON.parse(jsonDownload.bytes.toString('utf8')) as Array<Record<string, unknown>>
  expect(exported.find((item) => item.id === row.id)).toMatchObject(row)
  const csvDownload = await downloadBytes(page, 'CSV 다운로드')
  expect(csvDownload.name).toMatch(/\.csv$/)
  expect([...csvDownload.bytes.subarray(0, 3)]).toEqual([239, 187, 191])
  const csv = csvDownload.bytes.toString('utf8')
  expect(csv).toContain('author_username')
  expect(csv).toContain('E2E 관리자')
  expect(csv).toContain(`'${content.split('\n')[0]}`)
  const evidence = path.join(os.tmpdir(), 'workbench-voc-evidence')
  mkdirSync(evidence, { recursive: true })
  await page.screenshot({ path: path.join(evidence, 'voc-desktop.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByRole('heading', { name: 'VOC 게시판', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'CSV 다운로드', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({ path: path.join(evidence, 'voc-mobile.png'), fullPage: true })
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  expect(errors).toEqual([])
})

test('회원은 VOC 의견을 등록할 수 있다', async ({ page }) => {
  await loginWorkspace(page, 'e2e-viewer', '/workspace/voc')
  await expect(page.getByRole('textbox', { name: '개선 의견', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'CSV 다운로드', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'JSON 다운로드', exact: true })).toHaveCount(0)
  const content = `회원 VOC 등록 ${Date.now()}`
  await page.getByRole('textbox', { name: '개선 의견', exact: true }).fill(content)
  await page.getByRole('button', { name: '등록', exact: true }).click()
  await expect(page.getByText(content, { exact: true })).toBeVisible()
})

async function assertNonAdminExportDenied(page: Page, username: string) {
  await loginWorkspace(page, username, '/workspace/voc')
  await expect(page.getByRole('heading', { name: 'VOC 게시판', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'CSV 다운로드', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'JSON 다운로드', exact: true })).toHaveCount(0)
  for (const format of ['csv', 'json']) {
    const response = await page.request.get(`/api/voc/export?format=${format}`)
    expect(response.status(), `${username} ${format} export`).toBe(403)
  }
}

test('일반 사용자는 VOC 내보내기 버튼이 없고 직접 요청도 거부된다', async ({ page }) => {
  await assertNonAdminExportDenied(page, 'e2e-viewer')
})

test('파워 사용자는 VOC 내보내기 버튼이 없고 직접 요청도 거부된다', async ({ page }) => {
  await assertNonAdminExportDenied(page, 'e2e-power')
})

test('프로젝트 관리자는 VOC 내보내기 버튼이 없고 직접 요청도 거부된다', async ({ page }) => {
  await assertNonAdminExportDenied(page, 'e2e-project-admin')
})

test('메뉴 정책에서 숨긴 VOC는 직접 주소로도 표시하지 않는다', async ({ page }) => {
  let vocRequests = 0
  page.on('request', (request) => { if (new URL(request.url()).pathname === '/api/voc/posts') vocRequests += 1 })
  await page.route('**/api/navigation/menu-policy', async (route) => {
    const response = await route.fetch()
    const policy = await response.json()
    policy.menus = policy.menus.map((menu: { id: string }) => menu.id === 'voc'
      ? { ...menu, visibility: { general: false, power: false, admin: false } } : menu)
    await route.fulfill({ response, json: policy })
  })
  await loginWorkspace(page, 'e2e-viewer', '/workspace/voc')
  await expect(page).not.toHaveURL(/\/workspace\/voc/)
  await expect(page.getByRole('heading', { name: 'VOC 게시판', exact: true })).toHaveCount(0)
  await expect(page.getByRole('link', { name: 'VOC 게시판', exact: true })).toHaveCount(0)
  expect(vocRequests).toBe(0)
})

test('프로젝트와 결과가 없는 서버에서도 VOC 게시판을 사용한다', async ({ page }) => {
  await page.route('**/api/projects', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/workflows', (route) => route.fulfill({ json: [] }))
  await loginWorkspace(page, 'e2e-viewer', '/workspace/voc')
  await expect(page.getByRole('heading', { name: 'VOC 게시판', exact: true })).toBeVisible()
  const input = page.getByRole('textbox', { name: '개선 의견', exact: true })
  const content = `프로젝트 없는 서버의 의견 ${Date.now()}`
  await input.fill(content)
  await page.getByRole('button', { name: '등록', exact: true }).click()
  await expect(page.getByText(content, { exact: true })).toBeVisible()
})
