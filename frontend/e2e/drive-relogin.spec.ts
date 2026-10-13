import { expect, test, type Page } from '@playwright/test'
import { loginWorkspace } from './workspace-test-helpers'

// Issue #50 (docs/plans/scx-drive-relogin-spec.md §3.4): [다시 연결] from the AUTH_REQUIRED banner,
// stored-token reconnect, expired → account login form, account-change confirmation, password field
// cleared after sending. All drive APIs are mocked; the e2e backend itself runs in none mode.
const PASSWORD = 'Hpc-e2e-Pa55-NEVER-SHOWN'
const ADMIN_STATUS = {
  mode: 'scx', state: 'AUTH_REQUIRED', server_url: 'https://scx.example.test/', drive_root: 'SPDM/Projects', drive_root_locked: true,
  credentials: { present: true, account_hint: 'spdm-shared', obtained_at: '2026-10-07T01:02:03+00:00', updated_by: 'worker', updated_at: '2026-10-07T01:02:04+00:00', key_matches: true },
  credentials_problem: null, health: { state: 'AUTH_REQUIRED', last_success_at: null, last_error_code: 'AUTH_REQUIRED', last_error_at: '2026-10-13T01:00:00+00:00', token_refreshed_at: null, queue_depth: 0, in_flight: 0 },
  worker_version: '0.3.0', token_save_failed_at: null,
}
const AUTH = {
  capabilities: { refresh: true, password: true, mfa: false }, adapter_supported: true, capabilities_error: null,
  connected_account: 'spdm-shared', account_hint: 'spdm-shared', credentials_present: true, obtained_at: '2026-10-07T01:02:03+00:00',
  expires_at: null, last_refresh_at: null, last_error_code: null, last_error_at: null, password_login: 'https_only',
  password_login_allowed: true, password_login_blocked: null, attempts_left: 3, max_attempts: 3, locked_until: null,
}

type Seen = { method: string; path: string; body: string | null }

async function mock(page: Page, options: { reconnect: 'ok' | 'expired'; loginAccount: string }) {
  const seen: Seen[] = []
  await page.route('**/api/drive/status', (route) => route.fulfill({ json: { mode: 'scx', state: 'AUTH_REQUIRED', writes_enabled: true, writes_available: true, queue_paused: true, can_reconnect: true } }))
  await page.route('**/api/admin/drive/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    seen.push({ method: request.method(), path, body: request.postData() })
    if (path.endsWith('/status')) return route.fulfill({ json: ADMIN_STATUS })
    if (path.endsWith('/auth')) return route.fulfill({ json: AUTH })
    if (path.endsWith('/queue')) return route.fulfill({ json: { counts: {}, paused: true, worker_running: true, writes_enabled: true, items: [] } })
    if (path.endsWith('/reconnect')) {
      return options.reconnect === 'ok'
        ? route.fulfill({ json: { ok: true, account_hint: 'spdm-shared', obtained_at: '2026-10-13T02:00:00+00:00', account_changed: false, queue_resumed: 2 } })
        : route.fulfill({ status: 409, json: { detail: { code: 'AUTH_REFRESH_EXPIRED', message: '저장된 토큰이 만료되었습니다. 계정으로 로그인하세요.' } } })
    }
    if (path.endsWith('/login')) {
      const body = JSON.parse(request.postData() ?? '{}') as { confirm_account_change?: boolean }
      if (options.loginAccount !== 'spdm-shared' && !body.confirm_account_change) {
        return route.fulfill({ status: 409, json: { detail: { code: 'DRIVE_ACCOUNT_CHANGE_CONFIRM', message: 'change', current: 'spdm-shared', received: options.loginAccount } } })
      }
      return route.fulfill({ json: { ok: true, account_hint: options.loginAccount, obtained_at: '2026-10-13T02:00:00+00:00', account_changed: options.loginAccount !== 'spdm-shared', queue_resumed: 0 } })
    }
    return route.fulfill({ status: 404, json: { detail: 'unmocked' } })
  })
  return seen
}

async function openFromBanner(page: Page) {
  await page.setViewportSize({ width: 1600, height: 1000 })
  await loginWorkspace(page, 'e2e-admin', '/workspace/overview')
  const banner = page.getByTestId('drive-status-banner')
  await expect(banner).toContainText('드라이브 인증이 필요합니다.')
  await banner.getByTestId('drive-banner-reconnect').click()
  const dialog = page.getByRole('dialog', { name: 'SCX 드라이브' })
  await expect(dialog.getByTestId('drive-relogin')).toBeVisible()
  return dialog
}

test.describe('SCX 드라이브 다시 연결 (이슈 #50)', () => {
  test('배너의 다시 연결 → 저장된 토큰으로 다시 연결', async ({ page }) => {
    const seen = await mock(page, { reconnect: 'ok', loginAccount: 'spdm-shared' })
    const dialog = await openFromBanner(page)
    await expect(dialog.getByTestId('drive-connected-account')).toHaveText('spdm-shared')
    await dialog.getByTestId('drive-reconnect').click()
    await expect(dialog.getByTestId('drive-relogin-result')).toContainText('spdm-shared 계정으로 연결됨 · 대기열 2개 항목을 재개했습니다')
    expect(seen.filter((item) => item.path.endsWith('/reconnect'))).toHaveLength(1)
  })

  test('만료되면 계정 로그인 양식으로 넘어가고, 다른 계정은 확인 후 저장하며 비밀번호 칸을 비운다', async ({ page }) => {
    const seen = await mock(page, { reconnect: 'expired', loginAccount: 'someone-else' })
    const dialog = await openFromBanner(page)
    await dialog.getByTestId('drive-reconnect').click()
    await expect(dialog.getByTestId('drive-relogin-error')).toContainText('저장된 토큰이 만료되었습니다')
    const form = dialog.getByTestId('drive-login-form')
    await expect(form).toBeVisible()
    await expect(form.getByLabel('HPC 아이디')).toHaveValue('spdm-shared')
    const password = form.getByLabel('HPC 비밀번호')
    await expect(password).toHaveAttribute('type', 'password')
    await expect(password).toHaveAttribute('autocomplete', 'off')
    await form.getByLabel('HPC 아이디').fill('someone-else')
    await password.fill(PASSWORD)
    let asked = ''
    page.once('dialog', (prompt) => { asked = prompt.message(); void prompt.accept() })
    await form.getByRole('button', { name: '로그인' }).click()
    await expect(dialog.getByTestId('drive-relogin-result')).toContainText('someone-else 계정으로 연결됨(연결 계정 변경)')
    expect(asked).toContain('연결 계정이 spdm-shared에서 someone-else(으)로 바뀝니다')
    await expect(password).toHaveValue('')
    const logins = seen.filter((item) => item.path.endsWith('/login'))
    expect(logins).toHaveLength(2)
    expect(JSON.parse(logins[1].body ?? '{}')).toMatchObject({ username: 'someone-else', confirm_account_change: true })
    await expect(page.locator('body')).not.toContainText(PASSWORD)
  })
})
