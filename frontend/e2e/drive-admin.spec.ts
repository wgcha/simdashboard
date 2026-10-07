import { expect, test, type Page } from '@playwright/test'
import { loginWorkspace } from './workspace-test-helpers'

// SCX drive D0 (docs/features/scx-drive.md): admin drive dialog under 저장소 설정 and the global
// status banner. All drive APIs are mocked; the e2e backend itself runs in none mode.
const SECRET = 'eyJhbGciOi.e2e-SECRET-token'
const BASE_STATUS = {
  mode: 'scx', state: 'AUTH_REQUIRED', server_url: 'https://scx.example.test/', drive_root: 'SPDM/Projects',
  drive_root_locked: true, credentials: { present: false, account_hint: null, obtained_at: null, updated_by: null, updated_at: null, key_matches: null },
  credentials_problem: 'MISSING', health: { state: 'UNAVAILABLE', last_success_at: null, last_error_code: null, last_error_at: null, token_refreshed_at: null, queue_depth: 0, in_flight: 0 },
  worker_version: '0.2.0', token_save_failed_at: null,
}
const OK_STATUS = {
  ...BASE_STATUS, state: 'OK', credentials_problem: null,
  credentials: { present: true, account_hint: 'spdm-shared', obtained_at: '2026-10-07T01:02:03.123456+00:00', updated_by: 'worker', updated_at: '2026-10-07T01:02:04+00:00', key_matches: true },
  health: { ...BASE_STATUS.health, state: 'OK', last_success_at: '2026-10-07T01:02:05+00:00' },
}
const REPORT = {
  test_folder: '_simdash_test', drive_path: 'SPDM/Projects/_simdash_test', started_at: '2026-10-07T01:00:00+00:00', finished_at: '2026-10-07T01:00:03+00:00', ok: true,
  steps: [
    { step: 'stat_folder', label: '시험 폴더 조회(stat)', ok: true, skipped: false, code: null, latency_ms: 120.5, observations: ['item_id=있음'] },
    { step: 'list_dir', label: '목록 조회(list_dir)', ok: true, skipped: false, code: null, latency_ms: 340.2, observations: ['항목 4개(파일 4, 폴더 0)', '목록의 sha1: 파일 4개 중 0개 (C9)'] },
    { step: 'upload_conflict', label: '같은 이름 재업로드 → 충돌 확인(C2)', ok: true, skipped: false, code: 'CONFLICT', latency_ms: 80, observations: ['예상대로 CONFLICT(덮어쓰지 않음)'] },
    { step: 'download_to', label: '작은 파일 다운로드(download_to)', ok: false, skipped: true, code: null, latency_ms: null, observations: ['다운로드할 작은 파일이 없습니다.'] },
  ],
  leftovers: ['SPDM/Projects/_simdash_test/simdash-check-20261007T010000-abc123.txt'],
  note: '드라이브에서는 아무것도 지우지 않습니다.',
}

async function mockDrive(page: Page, state: { status: typeof BASE_STATUS; user: string | null; requests: Array<{ method: string; path: string; body: string | null }> }) {
  await page.route('**/api/drive/status', (route) => route.fulfill({ json: { mode: state.user ? 'scx' : 'none', state: state.user } }))
  await page.route('**/api/admin/drive/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    state.requests.push({ method: request.method(), path, body: request.postData() })
    if (path.endsWith('/status')) return route.fulfill({ json: state.status })
    if (path.endsWith('/credentials') && request.method() === 'PUT') {
      state.status = OK_STATUS
      return route.fulfill({ json: { account_hint: 'spdm-shared', obtained_at: '2026-10-07T01:02:03.123456+00:00', test: { ok: true, latency_ms: 210, root_identity: 'scx:scx.example.test:item-1', error: null } } })
    }
    if (path.endsWith('/credentials') && request.method() === 'DELETE') { state.status = BASE_STATUS; return route.fulfill({ status: 204, body: '' }) }
    if (path.endsWith('/test')) return route.fulfill({ json: { ok: false, latency_ms: 15, root_identity: null, error: { code: 'DRIVE_AUTH_REQUIRED', message: 'no token registered' } } })
    if (path.endsWith('/check')) return route.fulfill({ json: REPORT })
    return route.fulfill({ status: 404, json: { detail: 'unmocked' } })
  })
}

async function openDriveDialog(page: Page) {
  await loginWorkspace(page, 'e2e-admin', '/workspace/overview')
  await page.getByText('저장소 설정', { exact: true }).click()
  await page.getByTestId('drive-admin-open').click()
  const dialog = page.getByRole('dialog', { name: 'SCX 드라이브' })
  await expect(dialog).toBeVisible()
  return dialog
}

test.describe('SCX 드라이브 관리', () => {
  test('토큰 등록·연결 시험·드라이브 점검·삭제를 관리 화면에서 실행한다', async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 1000 })
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    const state = { status: BASE_STATUS, user: 'AUTH_REQUIRED' as string | null, requests: [] as Array<{ method: string; path: string; body: string | null }> }
    await mockDrive(page, state)
    const dialog = await openDriveDialog(page)

    await expect(dialog.getByTestId('drive-state-badge')).toHaveText('재로그인 필요')
    await expect(dialog).toContainText('https://scx.example.test/')
    await expect(dialog).toContainText('~/SPDM/Projects (환경 설정 고정)')
    await expect(dialog).toContainText('등록된 토큰이 없습니다')

    await dialog.getByRole('button', { name: '연결 시험' }).click()
    await expect(dialog).toContainText('연결 실패 · 드라이브 인증이 필요합니다')

    await dialog.getByLabel('토큰 JSON').fill('{not json')
    await dialog.getByRole('button', { name: '토큰 등록' }).click()
    await expect(dialog.getByRole('alert')).toContainText('토큰 JSON을 해석할 수 없습니다')
    expect(state.requests.filter((item) => item.method === 'PUT')).toHaveLength(0)

    const bundle = JSON.stringify({ server_url: 'https://scx.example.test/', access_token: SECRET, refresh_token: `${SECRET}-r`, obtained_at: '2026-10-07T01:02:03.123456Z', account_hint: 'spdm-shared' })
    await dialog.getByLabel('토큰 JSON').fill(bundle)
    await dialog.getByRole('button', { name: '토큰 등록' }).click()
    await expect(dialog.getByTestId('drive-state-badge')).toHaveText('연결됨')
    await expect(dialog).toContainText('토큰을 등록했습니다(spdm-shared)')
    await expect(dialog).toContainText('연결 성공 · 210 ms · scx:scx.example.test:item-1')
    await expect(dialog.getByLabel('토큰 JSON')).toHaveValue('')
    await expect(dialog).not.toContainText(SECRET)
    expect(JSON.parse(state.requests.find((item) => item.method === 'PUT')?.body ?? '{}').account_hint).toBe('spdm-shared')

    await dialog.getByLabel('시험 폴더').fill('_simdash_test')
    await dialog.getByRole('button', { name: '점검 실행' }).click()
    const table = dialog.getByRole('table', { name: '드라이브 점검 결과' })
    await expect(table).toBeVisible()
    await expect(table.locator('tbody tr')).toHaveCount(4)
    await expect(table.locator('tr[data-step="upload_conflict"]')).toContainText('CONFLICT')
    await expect(table.locator('tr[data-step="download_to"]')).toContainText('건너뜀')
    await expect(dialog).toContainText('점검 통과 · SPDM/Projects/_simdash_test')
    expect(JSON.parse(state.requests.find((item) => item.path.endsWith('/check'))?.body ?? '{}')).toEqual({ test_folder: '_simdash_test' })

    page.once('dialog', (confirm) => void confirm.accept())
    await dialog.getByRole('button', { name: '토큰 삭제' }).click()
    await expect(dialog).toContainText('토큰을 삭제했습니다.')
    await expect(dialog.getByTestId('drive-state-badge')).toHaveText('재로그인 필요')
    expect(state.requests.some((item) => item.method === 'DELETE')).toBe(true)

    await dialog.getByRole('button', { name: '닫기' }).click()
    await expect(dialog).toHaveCount(0)
    expect(errors).toEqual([])
  })

  test('none 모드에서는 드라이브 기능을 끄고 배너를 숨긴다', async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 1000 })
    const statusCalls: string[] = []
    page.on('request', (request) => { if (new URL(request.url()).pathname === '/api/drive/status') statusCalls.push(request.url()) })
    // Real backend (none mode) for both endpoints.
    const dialog = await openDriveDialog(page)
    await expect(dialog.getByTestId('drive-mode-none')).toContainText('SIMDASH_DRIVE_GATEWAY=none')
    await expect(dialog.getByRole('button', { name: '토큰 등록' })).toHaveCount(0)
    await expect(page.getByTestId('drive-status-banner')).toHaveCount(0)
    await expect.poll(() => statusCalls.length).toBeGreaterThan(0)
  })
})

test.describe('드라이브 상태 배너', () => {
  for (const [state, text] of [['AUTH_REQUIRED', '드라이브 인증이 필요합니다. 관리자에게 문의하세요.'], ['UNAVAILABLE', '드라이브 연결이 원활하지 않습니다']] as const) {
    test(`${state}이면 일반 사용자에게 상단 배너를 보여 준다`, async ({ page }) => {
      await page.setViewportSize({ width: 1600, height: 1000 })
      await page.route('**/api/drive/status', (route) => route.fulfill({ json: { mode: 'scx', state } }))
      await loginWorkspace(page, 'e2e-viewer', '/workspace/requests')
      const banner = page.getByTestId('drive-status-banner')
      await expect(banner).toBeVisible()
      await expect(banner).toContainText(text)
      await expect(page.getByTestId('drive-admin-open')).toHaveCount(0)
    })
  }

  test('OK·DEGRADED·확인 전 상태에서는 배너를 숨긴다', async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 1000 })
    let calls = 0
    await page.route('**/api/drive/status', (route) => { calls += 1; return route.fulfill({ json: { mode: 'scx', state: ['OK', 'DEGRADED', 'UNKNOWN'][Math.min(calls - 1, 2)] } }) })
    await loginWorkspace(page, 'e2e-viewer', '/workspace/requests')
    await expect.poll(() => calls).toBeGreaterThan(0)
    await expect(page.getByTestId('drive-status-banner')).toHaveCount(0)
  })
})
