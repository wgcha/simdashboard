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
    { step: 'stat_file', label: '파일 조회(stat)', ok: false, skipped: true, code: null, latency_ms: null, observations: ['8 MiB 이하 파일이 없습니다.'] },
    { step: 'download_to', label: '작은 파일 다운로드(download_to)', ok: false, skipped: true, code: null, latency_ms: null, observations: ['다운로드할 작은 파일이 없습니다.'] },
    { step: 'health', label: '어댑터 상태(health)', ok: true, skipped: false, code: null, latency_ms: 0.1, observations: ['state=OK, queue_depth=0'] },
  ],
  note: '조회 전용 점검입니다. 드라이브에는 아무것도 만들거나 바꾸거나 지우지 않습니다(다운로드한 서버 사본은 삭제).',
}

async function mockDrive(page: Page, state: { status: typeof BASE_STATUS; user: string | null; requests: Array<{ method: string; path: string; body: string | null }> }) {
  await page.route('**/api/drive/status', (route) => route.fulfill({ json: { mode: state.user ? 'scx' : 'none', state: state.user } }))
  await page.route('**/api/admin/drive/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    state.requests.push({ method: request.method(), path, body: request.postData() })
    if (path.endsWith('/status')) return route.fulfill({ json: state.status })
    if (path.endsWith('/auth')) return route.fulfill({ json: {
      capabilities: { refresh: false, password: false, mfa: false }, adapter_supported: false, capabilities_error: 'DRIVE_ADAPTER_UPDATE_REQUIRED',
      connected_account: null, account_hint: null, credentials_present: false, obtained_at: null, expires_at: null, last_refresh_at: null,
      last_error_code: null, last_error_at: null, password_login: 'https_only', password_login_allowed: false, password_login_blocked: null,
      attempts_left: 3, max_attempts: 3, locked_until: null,
    } })
    if (path.endsWith('/credentials') && request.method() === 'PUT') {
      state.status = OK_STATUS
      return route.fulfill({ json: { account_hint: 'spdm-shared', obtained_at: '2026-10-07T01:02:03.123456+00:00', test: { ok: true, latency_ms: 210, root_identity: 'scx:scx.example.test:item-1', error: null } } })
    }
    if (path.endsWith('/credentials') && request.method() === 'DELETE') { state.status = BASE_STATUS; return route.fulfill({ status: 204, body: '' }) }
    if (path.endsWith('/test')) return route.fulfill({ json: { ok: false, latency_ms: 15, root_identity: null, error: { code: 'DRIVE_AUTH_REQUIRED', message: 'no token registered' } } })
    if (path.endsWith('/check')) return route.fulfill({ json: REPORT })
    if (path.endsWith('/queue')) return route.fulfill({ json: { counts: {}, paused: false, worker_running: true, writes_enabled: false, items: [] } })
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
    await expect(table.locator('tbody tr')).toHaveCount(5)
    await expect(table.locator('tr[data-step="list_dir"]')).toContainText('목록의 sha1: 파일 4개 중 0개 (C9)')
    await expect(table.locator('tr[data-step="health"]')).toContainText('성공')
    for (const writeStep of ['upload_new', 'upload_conflict', 'mkdirs', 'copy_within', 'copy_conflict']) await expect(table.locator(`tr[data-step="${writeStep}"]`)).toHaveCount(0)
    await expect(dialog.getByTestId('drive-check-note')).toContainText('조회 전용 점검')
    await expect(dialog).toContainText('아무것도 만들거나 바꾸거나 지우지 않습니다')
    await expect(dialog).not.toContainText('simdash-check-')
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

test.describe('업로드 대기열 목록 편집', () => {
  test('항목을 골라 취소하고 끝난 항목을 목록에서 지운다(드라이브 파일은 그대로)', async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 1000 })
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    const item = (id: string, state: string, target: string) => ({
      id, batch_id: `b-${id}`, seq: 0, kind: 'MKDIR', state, target, source: null, size: null, attempts: 1, next_attempt_at: null,
      error_code: state === 'FAILED' ? 'DRIVE_NOT_FOUND' : null, error_message: null, transfer_method: null, requested_by: 'e2e-admin',
      origin: 'result_drop', origin_ref: null, project_id: null, request_id: null, environment: null,
      created_at: '2026-10-12T01:00:00+00:00', updated_at: '2026-10-12T01:00:00+00:00', finished_at: null,
    })
    let items = [item('q1', 'PENDING', 'P/R/Working/A'), item('q2', 'FAILED', 'P/R/Working/B'), item('q3', 'DONE', 'P/R/Working/C'), item('q4', 'CANCELLED', 'P/R/Working/D')]
    const posted: Array<{ path: string; body: unknown }> = []
    const list = () => {
      const counts: Record<string, number> = {}
      for (const entry of items) counts[entry.state] = (counts[entry.state] ?? 0) + 1
      return { counts, paused: false, worker_running: true, writes_enabled: true, items }
    }
    await page.route('**/api/drive/status', (route) => route.fulfill({ json: { mode: 'scx', state: 'OK' } }))
    await page.route('**/api/admin/drive/**', async (route) => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      if (path.endsWith('/status')) return route.fulfill({ json: OK_STATUS })
      if (path.endsWith('/queue')) return route.fulfill({ json: list() })
      const body = request.postDataJSON() as { ids?: string[]; states?: string[]; all_filter?: unknown }
      posted.push({ path, body })
      if (path.endsWith('/queue/bulk-cancel')) {
        const ids = body.ids ?? []
        items = items.map((entry) => ids.includes(entry.id) && ['PENDING', 'FAILED', 'BLOCKED', 'CONFLICT'].includes(entry.state) ? { ...entry, state: 'CANCELLED' } : entry)
        return route.fulfill({ json: { affected: ids, skipped: [], staging_removed: 0, queue: list() } })
      }
      if (path.endsWith('/queue/purge')) {
        const ids = body.ids ?? items.filter((entry) => (body.states ?? []).includes(entry.state)).map((entry) => entry.id)
        items = items.filter((entry) => !ids.includes(entry.id))
        return route.fulfill({ json: { affected: ids, skipped: [{ id: 'q9', code: 'DRIVE_QUEUE_BATCH_OPEN', state: 'DONE' }], staging_removed: 1, queue: list() } })
      }
      return route.fulfill({ status: 404, json: { detail: 'unmocked' } })
    })
    const dialog = await openDriveDialog(page)
    const queue = dialog.getByTestId('drive-queue')
    const table = queue.getByRole('table', { name: '업로드 대기열' })
    await expect(table.locator('tbody tr')).toHaveCount(4)
    const bulkBar = queue.getByTestId('drive-queue-bulk')
    await expect(bulkBar.getByRole('button', { name: '선택 취소' })).toBeDisabled()
    await expect(bulkBar.getByRole('button', { name: '목록에서 지우기' })).toBeDisabled()

    await table.getByLabel('선택: P/R/Working/A').check()
    await table.getByLabel('선택: P/R/Working/B').check()
    await expect(bulkBar).toContainText('선택 2개')
    const cancelPrompts: string[] = []
    page.once('dialog', (prompt) => { cancelPrompts.push(prompt.message()); void prompt.accept() })
    await bulkBar.getByRole('button', { name: '선택 취소' }).click()
    await expect(queue.getByTestId('drive-queue-notice')).toContainText('선택 취소 2개')
    expect(cancelPrompts[0]).toContain('드라이브에 이미 올라간 파일·폴더는 지우지 않습니다')
    expect(posted.at(-1)).toEqual({ path: '/api/admin/drive/queue/bulk-cancel', body: { ids: ['q1', 'q2'] } })
    await expect(table.locator('tr[data-id="q1"]')).toHaveAttribute('data-state', 'CANCELLED')

    await table.getByLabel('전체 선택(현재 목록 조건)').check()
    await expect(bulkBar).toContainText('현재 조건 전체 선택')
    const purgePrompts: string[] = []
    page.once('dialog', (prompt) => { purgePrompts.push(prompt.message()); void prompt.accept() })
    await bulkBar.getByRole('button', { name: '목록에서 지우기' }).click()
    await expect(queue.getByTestId('drive-queue-notice')).toContainText('목록에서 지우기 4개 · 건너뜀 1개')
    await expect(queue.getByTestId('drive-queue-notice')).toContainText('서버 임시 파일 1곳 정리')
    expect(purgePrompts[0]).toContain('드라이브에 이미 올라간 파일·폴더는 지우지 않습니다')
    expect((posted.at(-1)?.body as { ids: string[] }).ids.sort()).toEqual(['q1', 'q2', 'q3', 'q4'])
    await expect(table.locator('tbody tr')).toHaveCount(1)
    await expect(table).toContainText('표시할 항목이 없습니다.')

    items = [item('q5', 'DONE', 'P/R/Working/E'), item('q6', 'PENDING', 'P/R/Working/F')]
    await queue.getByRole('button', { name: '새로고침' }).click()
    page.once('dialog', (prompt) => void prompt.accept())
    await bulkBar.getByRole('button', { name: '완료·취소 항목 모두 지우기' }).click()
    await expect(table.locator('tbody tr')).toHaveCount(1)
    expect(posted.at(-1)).toEqual({ path: '/api/admin/drive/queue/purge', body: { states: ['DONE', 'CANCELLED'] } })
    expect(errors).toEqual([])
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
