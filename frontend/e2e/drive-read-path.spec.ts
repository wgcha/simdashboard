import { expect, test, type Page } from '@playwright/test'
import { loginWorkspace, mockResultEnvironments } from './workspace-test-helpers'

// SCX drive D2 (docs/features/scx-drive.md §8): pending drive source changes next to the folder
// auto-sync status and the read-only notice where uploads exist. Drive APIs are mocked; the e2e
// backend itself runs in none mode. Desktop viewport only.
const projectId = 'project-feature-showcase'
const requestId = 'request-showcase-compare'
const SCX_STATUS = { mode: 'scx', state: 'OK', writes_enabled: false, writes_available: false }

function syncResult(drive: Record<string, unknown> | null) {
  return { status: 'UNCHANGED', changed: false, snapshot_id: 'snapshot-1', diff: { added: 0, removed: 0, changed: 0 }, code: null, message: null,
    check_mode: 'QUICK', checked_at: new Date().toISOString(), coalesced: false, ...(drive ? { drive } : {}) }
}

const CHANGES = {
  project_id: projectId, request_id: requestId, pending_changes: 1, missing: 1, ignored: 0,
  items: [
    { id: 'drive-source-a', relative_path: 'P1/[WR-0001]_[유통_환경]/Working/CaseA/Drop/Run1/INDIVIDUAL/2_Face/result.csv', kind: 'CHANGED', review_state: 'PENDING', version_no: 1,
      registered: { size: 52, modified_at: '2026-10-07T01:00:00+00:00', token_kind: 'sha1', registered_at: '2026-10-07T01:00:05+00:00' },
      drive: { size: 71, modified_at: '2026-10-07T02:00:00+00:00', token_kind: 'sha1' }, detected_at: '2026-10-07T02:00:30+00:00' },
    { id: 'drive-source-b', relative_path: 'P1/[WR-0001]_[유통_환경]/Working/CaseA/Drop/Run1/INDIVIDUAL/3_Face/result.csv', kind: 'MISSING', review_state: 'NONE', version_no: 1,
      registered: { size: 52, modified_at: '2026-10-07T01:00:00+00:00', token_kind: 'sha1', registered_at: '2026-10-07T01:00:05+00:00' },
      drive: null, detected_at: '2026-10-07T02:00:30+00:00' },
  ],
}

async function mockScx(page: Page) {
  const decisions: Array<{ path: string; body: Record<string, unknown> }> = []
  let changes = CHANGES
  await page.route('**/api/drive/status', (route) => route.fulfill({ json: SCX_STATUS }))
  await page.route('**/api/folder-discovery/environments/sync', (route) => route.fulfill({ json: syncResult({ pending_changes: changes.pending_changes, missing: changes.missing, ignored: 0, token_kind: 'sha1', new_files: 0 }) }))
  await page.route('**/api/drive/source-changes**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (request.method() === 'GET') return route.fulfill({ json: changes })
    const body = request.postDataJSON() as Record<string, unknown>
    decisions.push({ path, body })
    changes = { ...changes, pending_changes: 0, items: changes.items.filter((item) => !(body.ids as string[]).includes(item.id)) }
    return route.fulfill({ json: { accepted: path.endsWith('/accept') ? 1 : 0, removed: 0, dismissed: path.endsWith('/dismiss') ? 1 : 0, pending_changes: 0, missing: 1, ignored: 0 } })
  })
  return decisions
}

test('드라이브 원본 변경은 자동 반영하지 않고 확인 대화상자에서 새 버전 등록한다', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 })
  await mockResultEnvironments(page, ['DISTRIBUTION'])
  const decisions = await mockScx(page)
  await loginWorkspace(page)
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption(projectId)
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption(requestId)
  await page.getByRole('navigation', { name: '의뢰 작업 여정' }).getByRole('button', { name: 'Case 결과', exact: true }).click()
  const badge = page.getByTestId('drive-changes-badge')
  await expect(badge).toHaveText('원본 변경 1 · 확인 필요 · 원본 없음 1')
  await badge.click()
  const dialog = page.getByRole('dialog', { name: '드라이브 원본 변경' })
  await expect(dialog).toBeVisible()
  await expect(dialog.locator('tbody tr')).toHaveCount(2)
  await expect(dialog.locator('tr[data-kind="CHANGED"]')).toContainText('변경됨 — 확인 필요')
  await expect(dialog.locator('tr[data-kind="MISSING"]')).toContainText('원본 없음')
  page.once('dialog', (confirm) => void confirm.accept())
  await dialog.locator('tr[data-kind="CHANGED"]').getByRole('button', { name: '새 버전 등록' }).click()
  await expect.poll(() => decisions.length).toBe(1)
  expect(decisions[0]).toEqual({ path: '/api/drive/source-changes/accept', body: { project_id: projectId, request_id: requestId, ids: ['drive-source-a'] } })
  await expect(dialog.locator('tbody tr')).toHaveCount(1)
  await dialog.getByRole('button', { name: '닫기' }).click()
  await expect(dialog).toHaveCount(0)
})

test('드라이브 읽기 전용이면 결과 등록의 폴더 만들기·파일 올리기를 끄고 이유를 보여 준다', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 })
  await mockResultEnvironments(page, ['DISTRIBUTION'])
  await mockScx(page)
  const WORKING = 'P1/[WR-0001]_[유통_환경]/Working'
  await page.route('**/api/result-registration/**', (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api/result-registration', '')
    if (path === '/drop-target') {
      return route.fulfill({ json: { project_id: projectId, request_id: requestId, environment: 'DISTRIBUTION', request_relative_path: 'P1/[WR-0001]_[유통_환경]',
        working_relative_path: WORKING, working_display_path: 'scx://scx.example.test/SPDM/P1', display_root: 'scx://scx.example.test/SPDM',
        levels: [{ level: 1, role: 'WORKING', label: 'Working' }, { level: 2, role: 'SIMULATION_CASE', label: 'Case' }], nodes: [], truncated: false,
        chunk_bytes: 8, blocked_extensions: ['.bat'], active_uploads: 0 } })
    }
    if (path === '/drop-uploads') return route.fulfill({ json: { sessions: [] } })
    if (path === '/drafts') return route.fulfill({ json: { drafts: [], truncated: false } })
    return route.fulfill({ status: 404, json: { detail: 'unmocked' } })
  })
  await loginWorkspace(page)
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption(projectId)
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption(requestId)
  await page.getByRole('navigation', { name: '의뢰 작업 여정' }).getByRole('button', { name: '결과 등록', exact: true }).click()
  const workspace = page.getByTestId('result-drop-workspace')
  await expect(workspace).toBeVisible()
  await expect(workspace.getByTestId('drive-read-only')).toContainText('드라이브 읽기 전용')
  await expect(workspace.getByRole('button', { name: '파일 선택' })).toBeDisabled()
  await expect(workspace.getByRole('button', { name: '폴더 선택' })).toBeDisabled()
  await expect(workspace.getByRole('button', { name: '새 폴더 만들기' })).toBeDisabled()
})
