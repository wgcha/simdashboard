import { expect, test, type Page, type Route } from '@playwright/test'
import { loginWorkspace, mockResultEnvironments } from './workspace-test-helpers'

// SCX drive D3 (docs/features/scx-drive.md §10): drive writes through the upload queue.
// Drive and result-registration APIs are mocked (the e2e backend runs in none mode); desktop viewport only.
const projectId = 'project-feature-showcase'
const requestId = 'request-showcase-compare'
const WORKING = 'P1/[WR-0001]_[유통_환경]/Working'
const CASE = `${WORKING}/CaseA`
const SESSION = 'b'.repeat(32)
const DISPLAY = 'scx://scx.example.test/SPDM'
const SCX_WRITABLE = { mode: 'scx', state: 'OK', writes_enabled: true, writes_available: true, queue_paused: false }

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

function batch(state: string, done: number, extra: Record<string, unknown> = {}) {
  return { batch_id: SESSION, state, finished: ['DONE', 'PARTIAL', 'CONFLICT', 'FAILED', 'CANCELLED'].includes(state), paused: state === 'PAUSED',
    counts: {}, items_total: 2, files_total: 1, files_done: done, bytes_total: 4, bytes_done: done ? 4 : 0, current: done ? null : `${CASE}/a.csv`,
    current_kind: done ? null : 'FILE', next_retry_at: null, errors: [], transfer_methods: {}, origin: 'result_drop', origin_ref: SESSION,
    requested_by: 'e2e', project_id: projectId, request_id: requestId, created_at: null, updated_at: null, ...extra }
}

/** ``live.batch`` is what every poll returns; tests move it forward after each assertion. */
async function mockRegistration(page: Page, live: { batch: ReturnType<typeof batch> }) {
  const calls = { completes: 0, batchPolls: 0 }
  await page.route('**/api/drive/status', (route) => json(route, SCX_WRITABLE))
  await page.route('**/api/drive/upload-batches/**', (route) => {
    calls.batchPolls += 1
    return json(route, live.batch)
  })
  await page.route('**/api/result-registration/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace('/api/result-registration', '')
    const method = request.method()
    if (method === 'GET' && path === '/drop-target') {
      return json(route, { project_id: projectId, request_id: requestId, environment: 'DISTRIBUTION', request_relative_path: WORKING.replace('/Working', ''),
        working_relative_path: WORKING, working_display_path: `${DISPLAY}/${WORKING}`, display_root: DISPLAY,
        levels: [{ level: 1, role: 'WORKING', label: 'Working' }, { level: 2, role: 'SIMULATION_CASE', label: 'Case' }, { level: 3, role: 'SCENE', label: 'Scene' }],
        nodes: [{ relative_path: CASE, name: 'CaseA', parent_path: WORKING, level: 2, role: 'SIMULATION_CASE', display_path: `${DISPLAY}/${CASE}` }],
        truncated: false, chunk_bytes: 8, blocked_extensions: ['.bat'], active_uploads: 0 })
    }
    if (method === 'GET' && path === '/drop-uploads') return json(route, { sessions: [] })
    if (method === 'GET' && path === '/drafts') return json(route, { drafts: [], truncated: false })
    const files = [{ client_index: 0, relative_path: 'a.csv', destination_relative_path: `${CASE}/a.csv`, size: 4, role: 'SIMULATION_CASE' }]
    const plan = { project_id: projectId, request_id: requestId, environment: 'DISTRIBUTION', target_relative_path: CASE, target_display_path: `${DISPLAY}/${CASE}`,
      target_level: 2, target_role: 'SIMULATION_CASE', target_role_label: 'Case', files, folders_to_create: [], skipped: [], conflicts: [], issues: [],
      file_count: 1, folder_count: 0, total_bytes: 4, free_bytes: 10 ** 12, required_bytes: 4, can_upload: true }
    if (method === 'POST' && path === '/drop-uploads/plan') return json(route, plan)
    if (method === 'POST' && path === '/drop-uploads') {
      return json(route, { session_id: SESSION, state: 'UPLOADING', project_id: projectId, request_id: requestId, environment: 'DISTRIBUTION', target_relative_path: CASE,
        chunk_bytes: 8, file_count: 1, total_bytes: 4, received_bytes: 0, completed_files: 0,
        files: [{ index: 0, client_index: 0, relative_path: 'a.csv', destination_relative_path: `${CASE}/a.csv`, size: 4, received: 0, complete: false, published: false }],
        folders_to_create: [], skipped: [], conflicts: [], plan }, 201)
    }
    if (method === 'PUT' && path.includes('/files/')) return json(route, { index: 0, received: 4, size: 4, complete: true, sha256: 'f'.repeat(64) })
    if (method === 'POST' && path.endsWith('/complete')) {
      calls.completes += 1
      return json(route, { session_id: SESSION, state: 'QUEUED', project_id: projectId, request_id: requestId, environment: 'DISTRIBUTION', target_relative_path: CASE,
        chunk_bytes: 8, file_count: 1, total_bytes: 4, received_bytes: 4, completed_files: 1, files: [], folders_to_create: [], skipped: [], conflicts: [],
        published_files: 0, published_bytes: 0, created_folders: [], busy: [], queued_files: 1, queued_bytes: 4,
        sync: { status: 'QUEUED', message: '드라이브 반영 대기' }, cases: [{ case_relative_path: CASE, case_name: 'CaseA', case_id: 'case-a' }],
        drive: batch('QUEUED', 0) })
    }
    return json(route, { detail: `Unexpected ${method} ${path}` }, 404)
  })
  return calls
}

async function openRegistration(page: Page) {
  await mockResultEnvironments(page, ['DISTRIBUTION'])
  await loginWorkspace(page)
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption(projectId)
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption(requestId)
  await page.getByRole('navigation', { name: '의뢰 작업 여정' }).getByRole('button', { name: '결과 등록', exact: true }).click()
  const workspace = page.getByTestId('result-drop-workspace')
  await expect(workspace).toBeVisible()
  return workspace
}

async function pickFile(page: Page) {
  await page.getByRole('group', { name: '결과 등록 위치' }).getByLabel('Case', { exact: true }).selectOption(CASE)
  await page.getByLabel('올릴 파일 선택').setInputFiles({ name: 'a.csv', mimeType: 'text/csv', buffer: Buffer.from('a,1\n') })
  await expect(page.getByTestId('result-drop-plan')).toBeVisible()
  await page.getByRole('button', { name: '올리기', exact: true }).click()
}

test('드라이브 쓰기 허용이면 서버에 받은 뒤 업로드 대기열 진행·완료를 보여 준다', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 })
  const live = { batch: batch('RUNNING', 0) }
  const calls = await mockRegistration(page, live)
  const workspace = await openRegistration(page)
  await expect(workspace.getByTestId('drive-read-only')).toHaveCount(0)
  await expect(workspace.getByRole('button', { name: '경로 복사' })).toHaveCount(0)   // drive path, not an Explorer path
  await expect(workspace.getByRole('button', { name: '파일 선택' })).toBeEnabled()
  await pickFile(page)
  const queued = page.getByTestId('result-drop-queued')
  await expect(queued).toContainText('1개 파일을 서버에 받았습니다')
  await expect(queued).toContainText('드라이브 업로드 대기열')
  await expect(queued.getByTestId('drive-batch')).toHaveAttribute('data-state', 'RUNNING')
  await expect(queued.getByRole('progressbar', { name: '드라이브 반영 진행률' })).toBeVisible()
  live.batch = batch('DONE', 1)
  await expect(queued.getByTestId('drive-batch')).toHaveAttribute('data-state', 'DONE', { timeout: 10_000 })
  await expect(queued.getByTestId('drive-batch')).toContainText('드라이브 반영 완료')
  await expect(queued.getByTestId('drive-batch')).toContainText('파일 1/1')
  expect(calls.completes).toBe(1)
})

test('드라이브 충돌은 덮어쓰지 않았다고 알리고 인증이 필요하면 일시 정지를 알린다', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 })
  const live = { batch: batch('PAUSED', 0, { errors: [{ id: 'dq-1', target: `${CASE}/a.csv`, state: 'BLOCKED', code: 'DRIVE_AUTH_REQUIRED', message: '드라이브 인증이 필요합니다.' }] }) }
  await mockRegistration(page, live)
  await openRegistration(page)
  await pickFile(page)
  const progress = page.getByTestId('drive-batch')
  await expect(progress).toHaveAttribute('data-state', 'PAUSED')
  await expect(page.getByTestId('drive-batch-paused')).toContainText('토큰을 다시 등록하면 이어서 진행')
  live.batch = batch('CONFLICT', 0, { errors: [{ id: 'dq-1', target: `${CASE}/a.csv`, state: 'CONFLICT', code: 'SPDM_CONFLICT', message: null }] })
  await expect(progress).toHaveAttribute('data-state', 'CONFLICT', { timeout: 10_000 })
  await expect(page.getByTestId('drive-batch-errors')).toContainText('같은 이름의 파일이 이미 드라이브에 있습니다(덮어쓰지 않습니다).')
  await expect(progress).toContainText('덮어쓰거나 지우지 않음')
})

test('관리자는 업로드 대기열에서 충돌 항목을 다시 시도하거나 취소한다(드라이브 내용은 지우지 않음)', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 })
  const actions: string[] = []
  let item = { id: 'dq-1', batch_id: SESSION, seq: 1, kind: 'FILE', state: 'CONFLICT', target: `${CASE}/a.csv`, source: null, size: 4, attempts: 1, next_attempt_at: null,
    error_code: 'SPDM_CONFLICT', error_message: 'target exists', transfer_method: null, requested_by: 'e2e', origin: 'result_drop', origin_ref: SESSION,
    project_id: projectId, request_id: requestId, environment: 'DISTRIBUTION', created_at: null, updated_at: null, finished_at: null }
  const status = { mode: 'scx', state: 'OK', server_url: 'https://scx.example.test/', drive_root: 'SPDM', drive_root_locked: true,
    credentials: { present: true, account_hint: 'spdm-shared', obtained_at: null, updated_by: 'worker', updated_at: null, key_matches: true }, credentials_problem: null,
    health: { state: 'OK', last_success_at: null, last_error_code: null, last_error_at: null, token_refreshed_at: null, queue_depth: 0, in_flight: 0 },
    worker_version: '0.2.0', token_save_failed_at: null, writes_enabled: true, writes_available: true, version_tokens: null }
  await page.route('**/api/drive/status', (route) => json(route, SCX_WRITABLE))
  await page.route('**/api/admin/drive/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/status')) return json(route, status)
    if (path.endsWith('/queue')) return json(route, { counts: { CONFLICT: item.state === 'CONFLICT' ? 1 : 0 }, paused: false, worker_running: true, writes_enabled: true, items: [item] })
    if (path.endsWith('/retry')) { actions.push('retry'); item = { ...item, state: 'PENDING' }; return json(route, item) }
    if (path.endsWith('/cancel')) { actions.push('cancel'); item = { ...item, state: 'CANCELLED', error_code: 'DRIVE_QUEUE_CANCELLED' }; return json(route, item) }
    return json(route, { detail: 'unmocked' }, 404)
  })
  await loginWorkspace(page, 'e2e-admin', '/workspace/overview')
  await page.getByText('저장소 설정', { exact: true }).click()
  await page.getByTestId('drive-admin-open').click()
  const queue = page.getByRole('dialog', { name: 'SCX 드라이브' }).getByTestId('drive-queue')
  await expect(queue.getByRole('table', { name: '업로드 대기열' })).toContainText('충돌')
  await expect(queue).toContainText('결과 등록')
  await queue.getByRole('button', { name: '다시 시도' }).click()
  await expect(queue.locator('tbody tr').first()).toContainText('대기')
  page.once('dialog', (dialog) => { expect(dialog.message()).toContain('지우지 않습니다'); void dialog.accept() })
  await queue.getByRole('button', { name: '취소' }).click()
  await expect(queue.locator('tbody tr').first()).toContainText('취소')
  expect(actions).toEqual(['retry', 'cancel'])
})
