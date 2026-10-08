import { expect, test, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { WORKSPACE_FONT_SIZE_STORAGE_KEY } from './workspace-test-helpers'

// Synthetic notifications served by a route mock (no user DB or running service is touched).
type Item = { id: string; type: string; type_label: string; severity: 'INFO' | 'SUCCESS' | 'WARNING' | 'ERROR'; title: string; body: string | null; link: string | null; project_id: string | null; request_id: string | null; created_at: string; read_at: string | null; read: boolean }

const TYPES = [
  { type: 'DRIVE_UPLOAD', label: '드라이브 업로드' }, { type: 'FINAL', label: 'Final 지정' },
  { type: 'NEW_RESULTS', label: '새 결과 반영' }, { type: 'DRIVE_SOURCE_CHANGED', label: '원본 변경 확인' },
]

/** Same as ``loginWorkspace`` with a longer first-render wait (request context restore under load). */
async function loginWorkspace(page: Page, username = 'e2e-admin', pathname = '/workspace/overview') {
  await page.goto(pathname)
  await page.getByLabel('사용자 이름').fill(username)
  await page.getByLabel('비밀번호').fill('e2e-validation-password')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '주 메뉴' })).toBeVisible({ timeout: 20_000 })
}

async function capture(page: Page, name: string) {
  if (!process.env.GUI_QA_OUTPUT_DIR) return
  mkdirSync(process.env.GUI_QA_OUTPUT_DIR, { recursive: true })
  await page.screenshot({ path: path.join(process.env.GUI_QA_OUTPUT_DIR, `notifications-${name}.png`) })
}

function seed(): Item[] {
  const minutes = (n: number) => new Date(Date.now() - n * 60_000).toISOString()
  const item = (id: string, type: string, severity: Item['severity'], title: string, body: string, age: number, link: string | null, read = false): Item => ({
    id, type, type_label: TYPES.find((entry) => entry.type === type)?.label ?? type, severity, title, body, link,
    project_id: 'project-tv-001', request_id: 'request-drop-001', created_at: minutes(age), read_at: read ? minutes(age) : null, read,
  })
  return [
    item('nt-1', 'NEW_RESULTS', 'INFO', '새 결과 반영 · WR-1042', '유통환경: 새 Scene 2개가 결과 화면에 반영되었습니다.', 2, '/workspace/help'),
    item('nt-2', 'FINAL', 'ERROR', 'Final 지정 실패 · WR-1042', 'Final 드라이브 반영이 멈췄습니다(FINALIZATION_SOURCE_STALE).', 30, null),
    item('nt-3', 'DRIVE_SOURCE_CHANGED', 'WARNING', '원본 변경 확인 필요 · WR-1042', '드라이브에서 바뀐 결과 파일 1개가 확인을 기다립니다.', 90, null),
    item('nt-4', 'DRIVE_UPLOAD', 'SUCCESS', '드라이브 업로드 완료 · WR-1042', '파일 3/3개를 드라이브에 올렸습니다.', 600, null, true),
  ]
}

async function mockNotifications(page: Page) {
  const items = seed()
  const reads: unknown[] = []
  const unread = () => items.filter((item) => !item.read).length
  // Match the API path only (a glob would also catch the Vite module /src/shared/api/notifications.ts).
  await page.route((url) => url.pathname.startsWith('/api/notifications'), async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/unread-count')) return route.fulfill({ json: { unread_count: unread() } })
    if (url.pathname.endsWith('/read')) {
      const body = route.request().postDataJSON() as { ids: string[] | null; all: boolean }
      reads.push(body)
      let updated = 0
      for (const item of items) {
        if (!item.read && (body.all || body.ids?.includes(item.id))) { item.read = true; item.read_at = new Date().toISOString(); updated += 1 }
      }
      return route.fulfill({ json: { updated, unread_count: unread() } })
    }
    const unreadOnly = url.searchParams.get('unread_only') === 'true'
    const type = url.searchParams.get('type')
    const limit = Number(url.searchParams.get('limit') ?? 50)
    const offset = Number(url.searchParams.get('offset') ?? 0)
    const matching = items.filter((item) => (!unreadOnly || !item.read) && (!type || item.type === type))
    return route.fulfill({ json: { items: matching.slice(offset, offset + limit), total: matching.length, unread_count: unread(), limit, offset, types: TYPES } })
  })
  return { items, reads }
}

test.describe('알림', () => {
  test.use({ viewport: { width: 1440, height: 900 } })

  test('상단 알림 아이콘은 읽지 않은 수를 보이고 최근 알림에서 관련 화면으로 이동한다', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    const state = await mockNotifications(page)
    await loginWorkspace(page)
    const bell = page.getByTestId('notification-bell')
    await expect(bell).toBeVisible()
    await expect(page.getByTestId('notification-badge')).toHaveText('3')
    await expect(bell).toHaveAttribute('aria-label', '알림, 읽지 않은 알림 3건')
    // The bell sits in the desktop top bar, not the sidebar.
    await expect(page.locator('.topbar').getByTestId('notification-bell')).toHaveCount(1)
    await expect(page.getByRole('complementary', { name: '주 메뉴' }).getByRole('link', { name: '알림' })).toHaveCount(0)

    await bell.click()
    const popover = page.getByRole('dialog', { name: '최근 알림' })
    await expect(popover).toBeVisible()
    await expect(popover.getByTestId('notification-entry')).toHaveCount(4)
    await expect(popover.getByTestId('notification-entry').first()).toContainText('새 결과 반영 · WR-1042')
    await expect(popover.getByTestId('notification-entry').first()).toContainText('분 전')
    await capture(page, 'dropdown')
    await popover.getByTestId('notification-entry').first().click()
    await expect(page).toHaveURL(/\/workspace\/help$/)
    await expect(popover).toBeHidden()
    expect(state.reads).toEqual([{ ids: ['nt-1'], all: false }])
    await expect(page.getByTestId('notification-badge')).toHaveText('2')

    await bell.click()
    await page.keyboard.press('Escape')
    await expect(popover).toBeHidden()
    expect(errors).toEqual([])
  })

  test('알림 페이지는 읽음 상태·종류로 거르고 읽음 처리한다', async ({ page }) => {
    const state = await mockNotifications(page)
    await loginWorkspace(page)
    await page.getByTestId('notification-bell').click()
    await page.getByRole('dialog', { name: '최근 알림' }).getByRole('button', { name: '모두 보기', exact: true }).click()
    await expect(page).toHaveURL(/\/workspace\/notifications$/)
    const view = page.getByTestId('notifications-page')
    await expect(view.getByRole('heading', { name: '알림', exact: true })).toBeVisible()
    await expect(page.locator('.breadcrumb')).toContainText('알림')
    await expect(view.getByTestId('notification-entry')).toHaveCount(4)
    await expect(view.getByText('4건')).toBeVisible()
    await capture(page, 'page')

    await view.getByRole('radio', { name: /읽지 않음/ }).click()
    await expect(view.getByTestId('notification-entry')).toHaveCount(3)
    await view.getByLabel('알림 종류').selectOption('FINAL')
    await expect(view.getByTestId('notification-entry')).toHaveCount(1)
    await expect(view.getByTestId('notification-entry')).toContainText('Final 지정 실패')
    await view.getByRole('button', { name: '읽음', exact: true }).click()
    await expect(view.getByText('조건에 맞는 알림이 없습니다.')).toBeVisible()
    await expect(page.getByTestId('notification-badge')).toHaveText('2')

    await view.getByLabel('알림 종류').selectOption('')
    await view.getByRole('radio', { name: '전체', exact: true }).click()
    await expect(view.getByTestId('notification-entry')).toHaveCount(4)
    await view.getByRole('button', { name: '모두 읽음', exact: true }).click()
    await expect(page.getByTestId('notification-badge')).toHaveCount(0)
    await expect(view.getByRole('button', { name: '모두 읽음', exact: true })).toBeDisabled()
    expect(state.reads.at(-1)).toEqual({ ids: null, all: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)

    // A direct URL works after a reload and the page is not a sidebar menu.
    await page.reload()
    await expect(page.getByTestId('notifications-page')).toBeVisible({ timeout: 20_000 })
    await expect(page.getByRole('complementary', { name: '주 메뉴' }).locator('a[href="/workspace/notifications"]')).toHaveCount(0)
  })

  test('알림은 실제 API에서 빈 목록으로 시작한다', async ({ page }) => {
    await loginWorkspace(page, 'e2e-viewer')
    await expect(page.getByTestId('notification-bell')).toBeVisible()
    await expect(page.getByTestId('notification-badge')).toHaveCount(0)
    await page.getByTestId('notification-bell').click()
    const popover = page.getByRole('dialog', { name: '최근 알림' })
    await expect(popover.getByText('새 알림이 없습니다.')).toBeVisible()
    await expect(popover.getByRole('button', { name: '모두 읽음' })).toBeDisabled()
  })
})

test('전체 글자 크기 조절 버튼은 숨기고 저장된 크기는 그대로 적용한다', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await loginWorkspace(page)
  const sidebar = page.getByRole('complementary', { name: '주 메뉴' })
  await expect(sidebar.getByRole('button', { name: '전체 글자 크기 늘리기' })).toHaveCount(0)
  await expect(sidebar.getByRole('button', { name: '전체 글자 크기 줄이기' })).toHaveCount(0)
  await expect(sidebar.getByLabel('전체 글자 크기 조절')).toHaveCount(0)
  await page.evaluate((key) => window.localStorage.setItem(key, '16'), WORKSPACE_FONT_SIZE_STORAGE_KEY)
  await page.reload()
  await expect(page.getByRole('complementary', { name: '주 메뉴' })).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('.app-shell')).toHaveCSS('--ui-font-size', '16pt')
  await expect.poll(() => page.evaluate(() => document.documentElement.style.getPropertyValue('font-size'))).toBe('16pt')
  expect(await page.evaluate((key) => window.localStorage.getItem(key), WORKSPACE_FONT_SIZE_STORAGE_KEY)).toBe('16')
})
