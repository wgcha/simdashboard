import { expect, test, type Page, type Route } from '@playwright/test'
import { loginWorkspace, mockResultEnvironments } from './workspace-test-helpers'

// W8 결과 등록: folder guide + drag & drop into the request's Working tree.
// Mocked result-registration API, synthetic data only.

const SHOT_DIR = '/tmp/claude-0'
const context = { project: 'project-feature-showcase', request: 'request-showcase-compare' }
const WORKING = 'P1/[WR-0001]_[유통_환경]/Working'
const CASE = `${WORKING}/CaseA`
const LOAD = `${CASE}/Drop`
const RUN = `${LOAD}/Run1`
const OPTION = `${RUN}/INDIVIDUAL`
const SCENE = `${OPTION}/2_Face`
const DISPLAY = '\\\\fileserver\\SPDM'
const display = (relative: string) => `${DISPLAY}\\${relative.replaceAll('/', '\\')}`
const ROLES = ['WORKING', 'SIMULATION_CASE', 'LOAD_CASE', 'EXECUTION_RUN', 'RUN_OPTION', 'SCENE']
const LABELS: Record<string, string> = { WORKING: 'Working', SIMULATION_CASE: 'Case', LOAD_CASE: '하중경우', EXECUTION_RUN: 'Run Case', RUN_OPTION: 'Run Option', SCENE: 'Scene', CONTENT: 'Scene 안 폴더' }

function tree() {
  const nodes = [[CASE, 2], [LOAD, 3], [RUN, 4], [OPTION, 5], [SCENE, 6]].map(([path, level]) => ({
    relative_path: path, name: String(path).split('/').pop(), parent_path: String(path).split('/').slice(0, -1).join('/'),
    level, role: ROLES[Number(level) - 1], display_path: display(String(path)),
  }))
  return {
    project_id: context.project, request_id: context.request, environment: 'DISTRIBUTION',
    request_relative_path: WORKING.replace('/Working', ''), working_relative_path: WORKING, working_display_path: display(WORKING),
    display_root: DISPLAY, levels: ROLES.map((role, index) => ({ level: index + 1, role, label: LABELS[role] })), nodes, truncated: false,
    chunk_bytes: 8, blocked_extensions: ['.bat', '.cmd', '.exe', '.js', '.ps1', '.vbs'], active_uploads: 0,
  }
}

type PlanBody = { target_relative_path: string; files: Array<{ relative_path: string; size: number }>; folders: string[] }
const levelOf = (path: string) => path.split('/').length - WORKING.split('/').length + 1

function plan(body: PlanBody) {
  const targetLevel = levelOf(body.target_relative_path)
  const skipped = body.files.map((item, index) => ({ ...item, client_index: index })).filter((item) => item.relative_path.endsWith('.bat'))
    .map((item) => ({ ...item, reason: 'BLOCKED_EXTENSION' }))
  const files = body.files.map((item, index) => ({ ...item, client_index: index })).filter((item) => !item.relative_path.endsWith('.bat'))
  const folders = new Map<string, number>()
  for (const item of [...files.map((file) => file.relative_path.split('/').slice(0, -1)), ...body.folders.map((folder) => folder.split('/'))]) {
    for (let depth = 1; depth <= item.length; depth += 1) folders.set(item.slice(0, depth).join('/'), targetLevel + depth)
  }
  const issues: Array<Record<string, unknown>> = []
  const conflicts = files.filter((item) => item.relative_path === 'existing.csv').map((item) => ({ relative_path: item.relative_path, destination_relative_path: `${body.target_relative_path}/existing.csv`, reason: 'EXISTS' }))
  if (body.target_relative_path === OPTION && [...folders.keys()].some((path) => path.startsWith('INDIVIDUAL'))) {
    issues.push({ code: 'DEPTH_REPEATED_NAME', severity: 'error', message: '같은 이름 폴더가 겹칩니다(INDIVIDUAL/INDIVIDUAL). 한 단계 위 폴더를 선택하거나 안쪽 폴더만 올리세요.', paths: ['INDIVIDUAL'], count: 1 })
    issues.push({ code: 'DEPTH_ROLE_MISMATCH', severity: 'error', message: "'INDIVIDUAL'은(는) 이 의뢰에서 Run Option 이름인데 Scene 자리(Run Option 바로 아래)에 놓입니다. 위치를 한 단계 맞춰 선택하세요.", paths: ['INDIVIDUAL'], count: 1 })
  }
  if (conflicts.length) issues.push({ code: 'CONFLICT', severity: 'error', message: '같은 이름의 파일이 이미 있습니다. 덮어쓰지 않으므로 이름을 바꾸거나 빼고 올리세요.', paths: [], count: 1 })
  const total = files.reduce((sum, item) => sum + item.size, 0)
  return {
    project_id: context.project, request_id: context.request, environment: 'DISTRIBUTION',
    target_relative_path: body.target_relative_path, target_display_path: display(body.target_relative_path), target_level: targetLevel,
    target_role: ROLES[targetLevel - 1], target_role_label: LABELS[ROLES[targetLevel - 1]],
    files: files.map((item) => ({ client_index: item.client_index, relative_path: item.relative_path, destination_relative_path: `${body.target_relative_path}/${item.relative_path}`, size: item.size, role: ROLES[targetLevel + item.relative_path.split('/').length - 2] ?? 'CONTENT' })),
    folders_to_create: [...folders].map(([path, level]) => ({ relative_path: `${body.target_relative_path}/${path}`, client_path: path, level, role: ROLES[level - 1] ?? 'CONTENT', role_label: LABELS[ROLES[level - 1] ?? 'CONTENT'] })),
    skipped, conflicts, issues, file_count: files.length, folder_count: folders.size, total_bytes: total, free_bytes: 10 ** 12, required_bytes: total, can_upload: !issues.length,
  }
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function installApi(page: Page, options: { chunkGate?: Promise<void>; partialFirst?: boolean; openSessions?: unknown[] } = {}) {
  const calls = { plans: [] as PlanBody[], chunks: [] as Array<{ index: number; offset: number; body: string; sha: string | null }>, completes: 0, aborts: 0, folders: [] as unknown[] }
  let session: ReturnType<typeof plan> | null = null
  const received = new Map<number, number>()
  await page.route('**/api/result-registration/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace('/api/result-registration', '')
    const method = request.method()
    if (method === 'GET' && path === '/drop-target') return json(route, tree())
    if (method === 'GET' && path === '/drop-uploads') return json(route, { sessions: options.openSessions ?? [] })
    if (method === 'GET' && path === '/drafts') return json(route, { drafts: [{ draft_id: 'draft-old', status: 'PUBLISHED', case_relative_path: CASE, result_relative_path: SCENE, file_count: 2, total_bytes: 2048, case_id: 'case-a', capture_id: 'capture-old', mirror_status: 'PUBLISHED', created_by: 'e2e', approved_by: 'e2e', published_at: '2026-09-20T09:00:00', created_at: '2026-09-20T08:55:00', updated_at: '2026-09-20T09:00:00' }], truncated: false })
    if (method === 'POST' && path === '/drop-target/folders') {
      const body = request.postDataJSON() as { name: string; confirm: boolean; parent_relative_path: string }
      calls.folders.push(body)
      if (body.name === '3_Face2' && !body.confirm) return json(route, { detail: { code: 'RESULT_DROP_FOLDER_NAME_WARNING', message: '비슷한 이름', warnings: ["같은 위치의 '3_Face'와 거의 같은 이름입니다. 결과가 다른 Scene로 나뉩니다."] } }, 409)
      return json(route, { relative_path: `${body.parent_relative_path}/${body.name}`, display_path: display(`${body.parent_relative_path}/${body.name}`), level: 6, role: 'SCENE', role_label: 'Scene', warnings: [], sync: { status: 'REFRESHED' } }, 201)
    }
    if (method === 'POST' && path === '/drop-uploads/plan') {
      const body = request.postDataJSON() as PlanBody
      calls.plans.push(body)
      return json(route, plan(body))
    }
    if (method === 'POST' && path === '/drop-uploads') {
      session = plan(request.postDataJSON() as PlanBody)
      return json(route, { session_id: 'a'.repeat(32), state: 'UPLOADING', project_id: context.project, request_id: context.request, environment: 'DISTRIBUTION', target_relative_path: session.target_relative_path, chunk_bytes: 8, file_count: session.file_count, total_bytes: session.total_bytes, received_bytes: 0, completed_files: 0,
        files: session.files.map((item, index) => ({ index, client_index: item.client_index, relative_path: item.relative_path, destination_relative_path: item.destination_relative_path, size: item.size, received: 0, complete: false, published: false })),
        folders_to_create: session.folders_to_create.map((item) => item.relative_path), skipped: session.skipped, conflicts: [], plan: session }, 201)
    }
    const chunk = path.match(/^\/drop-uploads\/[0-9a-f]{32}\/files\/(\d+)$/)
    if (method === 'PUT' && chunk) {
      const index = Number(chunk[1])
      const offset = Number(url.searchParams.get('offset'))
      if (options.chunkGate) await options.chunkGate
      const body = request.postDataBuffer()?.toString('utf8') ?? ''
      calls.chunks.push({ index, offset, body, sha: request.headers()['x-chunk-sha256'] ?? null })
      const next = offset + body.length
      received.set(index, next)
      const size = session!.files[index].size
      return json(route, { index, received: next, size, complete: next === size, sha256: next === size ? 'f'.repeat(64) : null })
    }
    if (method === 'POST' && path.endsWith('/complete')) {
      calls.completes += 1
      const partial = options.partialFirst && calls.completes === 1
      const busyFile = session!.files[session!.files.length - 1].relative_path
      return json(route, { session_id: 'a'.repeat(32), state: partial ? 'PARTIAL' : 'PUBLISHED', project_id: context.project, request_id: context.request, environment: 'DISTRIBUTION', target_relative_path: session!.target_relative_path, chunk_bytes: 8, file_count: session!.file_count, total_bytes: session!.total_bytes, received_bytes: session!.total_bytes, completed_files: session!.file_count, files: [], folders_to_create: [], skipped: session!.skipped, conflicts: [],
        published_files: partial ? session!.file_count - 1 : session!.file_count, published_bytes: session!.total_bytes, created_folders: session!.folders_to_create.map((item) => item.relative_path), busy: partial ? [busyFile] : [],
        sync: { status: 'REFRESHED', changed: true }, cases: [{ case_relative_path: CASE, case_name: 'CaseA', case_id: 'case-a' }] })
    }
    if (method === 'DELETE') { calls.aborts += 1; return json(route, { session_id: 'a'.repeat(32), state: 'ABORTED' }) }
    return json(route, { detail: `Unexpected ${method} ${path}` }, 404)
  })
  return calls
}

async function openRegistration(page: Page) {
  await mockResultEnvironments(page, ['DISTRIBUTION'])
  await loginWorkspace(page)
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption(context.project)
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption(context.request)
  await page.getByRole('navigation', { name: '의뢰 작업 여정' }).getByRole('button', { name: '결과 등록', exact: true }).click()
  const workspace = page.getByTestId('result-drop-workspace')
  await expect(workspace).toBeVisible()
  await expect(page.getByRole('group', { name: '결과 등록 위치' })).toBeVisible()
  return workspace
}

async function chooseRun(page: Page) {
  const path = page.getByRole('group', { name: '결과 등록 위치' })
  await path.getByLabel('Case', { exact: true }).selectOption(CASE)
  await path.getByLabel('하중경우', { exact: true }).selectOption(LOAD)
  await path.getByLabel('Run Case', { exact: true }).selectOption(RUN)
  await expect(page.getByTestId('result-drop-target-path')).toHaveText(display(RUN))
}

/** Dispatch a drop with webkitGetAsEntry-style entries (files and whole folders). */
async function dropTree(page: Page, files: Record<string, string>, emptyFolders: string[] = []) {
  await page.getByTestId('result-drop-zone').evaluate((zone, input) => {
    type Entry = { isFile: boolean; isDirectory: boolean; name: string; fullPath: string; file?: (ok: (file: File) => void) => void; createReader?: () => { readEntries: (ok: (entries: Entry[]) => void) => void } }
    const folders = new Map<string, Entry[]>()
    const roots: Entry[] = []
    const folder = (path: string): Entry[] => {
      if (!folders.has(path)) {
        const children: Entry[] = []
        folders.set(path, children)
        let served = false
        const entry: Entry = { isFile: false, isDirectory: true, name: path.split('/').pop()!, fullPath: `/${path}`, createReader: () => { served = false; return { readEntries: (ok) => { const batch = served ? [] : children; served = true; ok(batch) } } } }
        const parent = path.includes('/') ? folder(path.slice(0, path.lastIndexOf('/'))) : roots
        parent.push(entry)
      }
      return folders.get(path)!
    }
    for (const [path, content] of Object.entries(input.files)) {
      const name = path.split('/').pop()!
      const entry: Entry = { isFile: true, isDirectory: false, name, fullPath: `/${path}`, file: (ok) => ok(new File([content], name)) }
      ;(path.includes('/') ? folder(path.slice(0, path.lastIndexOf('/'))) : roots).push(entry)
    }
    for (const path of input.emptyFolders) folder(path)
    const transfer = { items: roots.map((entry) => ({ kind: 'file', webkitGetAsEntry: () => entry })), files: [], types: ['Files'], dropEffect: 'copy' }
    const event = new Event('drop', { bubbles: true, cancelable: true })
    Object.defineProperty(event, 'dataTransfer', { value: transfer })
    zone.dispatchEvent(event)
  }, { files, emptyFolders })
}

test('경로 복사는 선택한 폴더의 탐색기 경로를 복사하고 안내는 선택 깊이에 맞춘다', async ({ page, context: browser }) => {
  await browser.grantPermissions(['clipboard-read', 'clipboard-write'])
  await installApi(page)
  await openRegistration(page)
  await expect(page.getByTestId('result-drop-target-path')).toHaveText(display(WORKING))
  await chooseRun(page)
  await page.getByRole('button', { name: '경로 복사', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: '경로를 복사했습니다' })).toBeVisible()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(display(RUN))
  const guide = page.getByRole('region', { name: '어느 깊이에 넣나요?' })
  await expect(guide).toContainText('Run Case에는 Run Option 폴더를 통째로 넣으세요.')
  await expect(guide).toContainText('Run Option 폴더를 통째로 복사하려면 Run Case 폴더 아래에')
  await expect(guide).toContainText('Scene 폴더를 통째로 복사하려면 Run Option 폴더 아래에')
  await expect(guide).toContainText('1분 안에 자동 반영됩니다')
  await expect(guide.locator('li.is-current')).toContainText('Run Case')
  await expect(guide.locator('li.is-next')).toContainText('Run Option')
  // Choosing a higher level again drops the deeper selection.
  await page.getByRole('group', { name: '결과 등록 위치' }).getByLabel('하중경우', { exact: true }).selectOption('')
  await expect(page.getByTestId('result-drop-target-path')).toHaveText(display(CASE))
})

test('폴더를 끌어 놓으면 상대 경로·깊이·제외 파일을 보여 주고 조각으로 올린 뒤 Case 결과로 연결한다', async ({ page }) => {
  let release!: () => void
  const chunkGate = new Promise<void>((resolve) => { release = resolve })
  const calls = await installApi(page, { chunkGate })
  await openRegistration(page)
  await chooseRun(page)
  await dropTree(page, { 'CUMULATIVE/7_Edge/a.csv': 'Position,L1\nTOP,20\n', 'CUMULATIVE/8_Corner/b.csv': 'x', 'CUMULATIVE/run.bat': 'echo' }, ['CUMULATIVE/9_Empty'])
  const planView = page.getByTestId('result-drop-plan')
  await expect(planView).toBeVisible()
  expect(calls.plans[0].target_relative_path).toBe(RUN)
  expect(calls.plans[0].files.map((item) => item.relative_path).sort()).toEqual(['CUMULATIVE/7_Edge/a.csv', 'CUMULATIVE/8_Corner/b.csv', 'CUMULATIVE/run.bat'])
  expect(calls.plans[0].folders).toEqual(['CUMULATIVE/9_Empty'])
  await expect(planView).toContainText('파일 2개')
  await expect(planView.getByRole('table', { name: '올라갈 경로' })).toContainText('CUMULATIVE/7_Edge/a.csv')
  const folders = planView.locator('.result-drop__folders li')
  await expect(folders.filter({ hasText: /^CUMULATIVERun Option$/ })).toHaveCount(1)
  await expect(folders.filter({ hasText: '7_Edge' })).toContainText('Scene')
  await expect(page.getByTestId('result-drop-skipped')).toContainText('CUMULATIVE/run.bat(실행 파일·스크립트)')
  await page.getByRole('button', { name: '올리기', exact: true }).click()
  const progress = page.getByTestId('result-drop-progress')
  await expect(progress).toBeVisible()
  await expect(progress.getByRole('progressbar', { name: '업로드 진행률' })).toBeVisible()
  await expect(progress).toContainText('0 / 2 파일')
  release()
  const done = page.getByTestId('result-drop-complete')
  await expect(done).toContainText('2개 파일을 올렸습니다')
  await expect(done).toContainText('Case 결과에 반영했습니다')
  await expect(done.getByRole('link', { name: 'CaseA Case 결과 열기' })).toHaveAttribute('href', /view=case_results.*case=case-a/)
  // 19-byte file in 8-byte chunks: offsets 0, 8, 16, each with a SHA-256 header.
  const first = calls.chunks.filter((item) => item.index === 0)
  expect(first.map((item) => item.offset)).toEqual([0, 8, 16])
  expect(first.map((item) => item.body).join('')).toBe('Position,L1\nTOP,20\n')
  expect(first.every((item) => /^[0-9a-f]{64}$/.test(item.sha ?? ''))).toBe(true)
  expect(calls.completes).toBe(1)
  expect(calls.aborts).toBe(0)
})

test('같은 이름 폴더를 한 단계 깊게 놓으면 막고, 이미 있는 파일은 충돌로 보여 준다', async ({ page }) => {
  await installApi(page)
  await openRegistration(page)
  await chooseRun(page)
  await page.getByRole('group', { name: '결과 등록 위치' }).getByLabel('Run Option', { exact: true }).selectOption(OPTION)
  await dropTree(page, { 'INDIVIDUAL/2_Face/a.csv': 'x' })
  const planView = page.getByTestId('result-drop-plan')
  await expect(planView.locator('[data-code="DEPTH_REPEATED_NAME"]')).toContainText('INDIVIDUAL/INDIVIDUAL')
  await expect(planView.locator('[data-code="DEPTH_ROLE_MISMATCH"]')).toContainText('Scene 자리')
  await expect(planView.getByRole('button', { name: '올리기', exact: true })).toBeDisabled()
  await planView.getByRole('button', { name: '다시 선택', exact: true }).click()

  await page.getByRole('group', { name: '결과 등록 위치' }).getByLabel('Scene', { exact: true }).selectOption(SCENE)
  await dropTree(page, { 'existing.csv': 'x', 'new.csv': 'y' })
  await expect(page.getByTestId('result-drop-conflicts')).toContainText('이미 있는 파일 1개 (덮어쓰지 않음)')
  await expect(page.getByTestId('result-drop-conflicts')).toContainText('existing.csv')
  await expect(page.getByTestId('result-drop-plan').getByRole('button', { name: '올리기', exact: true })).toBeDisabled()
})

test('새 폴더 만들기는 비슷한 이름을 경고한 뒤 확인을 받아 만들고 그 폴더를 선택한다', async ({ page }) => {
  const calls = await installApi(page)
  await openRegistration(page)
  await chooseRun(page)
  await page.getByRole('group', { name: '결과 등록 위치' }).getByLabel('Run Option', { exact: true }).selectOption(OPTION)
  await page.getByRole('button', { name: '새 폴더 만들기', exact: true }).click()
  await page.getByLabel('새 폴더 이름', { exact: true }).fill('3_Face2')
  await page.getByRole('button', { name: '만들기', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: "'3_Face'와 거의 같은 이름" })).toBeVisible()
  await page.getByRole('button', { name: '그래도 만들기', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: '새 Scene 폴더를 만들었습니다' })).toBeVisible()
  expect(calls.folders).toEqual([
    { project_id: context.project, request_id: context.request, environment: 'DISTRIBUTION', parent_relative_path: OPTION, name: '3_Face2', confirm: false },
    { project_id: context.project, request_id: context.request, environment: 'DISTRIBUTION', parent_relative_path: OPTION, name: '3_Face2', confirm: true },
  ])
})

test('이전 등록 초안은 읽기 전용 이력으로만 보인다', async ({ page }) => {
  await installApi(page)
  await openRegistration(page)
  const history = page.getByTestId('legacy-draft-history')
  await history.locator('summary').click()
  const table = history.getByRole('table', { name: '이전 등록 초안' })
  await expect(table).toContainText('등록 완료')
  await expect(table).toContainText('2개 · 2.0 KB')
  await expect(table.getByRole('link', { name: 'Case 결과' })).toHaveAttribute('href', /case=case-a/)
  await expect(table.getByRole('button')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /검수 완료|DB 등록|업로드하고 자동 검사/ })).toHaveCount(0)
})

test.describe('4K 150% 화면', () => {
  test.use({ viewport: { width: 2560, height: 1440 }, deviceScaleFactor: 1.5 })
  test('위치·안내·계획·완료 화면을 캡처한다', async ({ page }) => {
    let release!: () => void
    const chunkGate = new Promise<void>((resolve) => { release = resolve })
    await installApi(page, { chunkGate })
    await openRegistration(page)
    await chooseRun(page)
    await page.screenshot({ path: `${SHOT_DIR}/w8-location-guide.png` })
    await dropTree(page, { 'CUMULATIVE/7_Edge/MAX_RESULT_Max_Stress.csv': 'Position,L1\nTOP,20\n', 'CUMULATIVE/7_Edge/scene.mp4': 'video', 'CUMULATIVE/8_Corner/MAX_RESULT_Max_Stress.csv': 'x', 'CUMULATIVE/tool.bat': 'echo' })
    await expect(page.getByTestId('result-drop-plan')).toBeVisible()
    await page.getByTestId('result-drop-plan').scrollIntoViewIfNeeded()
    await page.screenshot({ path: `${SHOT_DIR}/w8-plan.png` })
    await page.getByRole('button', { name: '올리기', exact: true }).click()
    await expect(page.getByTestId('result-drop-progress')).toBeVisible()
    await page.screenshot({ path: `${SHOT_DIR}/w8-progress.png` })
    release()
    await expect(page.getByTestId('result-drop-complete')).toBeVisible()
    await page.screenshot({ path: `${SHOT_DIR}/w8-complete.png` })
    await page.getByRole('group', { name: '결과 등록 위치' }).getByLabel('Run Option', { exact: true }).selectOption(OPTION)
    await dropTree(page, { 'INDIVIDUAL/2_Face/a.csv': 'x' })
    await expect(page.getByTestId('result-drop-plan')).toContainText('INDIVIDUAL/INDIVIDUAL')
    await page.getByTestId('result-drop-plan').scrollIntoViewIfNeeded()
    await page.screenshot({ path: `${SHOT_DIR}/w8-depth-warning.png` })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  })
})

test('일부만 옮겨지면 나머지 다시 옮기기·중지를 제공하고, 열린 업로드는 중지할 수 있다', async ({ page }) => {
  const calls = await installApi(page, { partialFirst: true, openSessions: [{ session_id: 'b'.repeat(32), state: 'PARTIAL', user_id: 'other-user', own: false, target_relative_path: OPTION, file_count: 3, total_bytes: 3000, published_files: 2, idle_seconds: 120 }] })
  await openRegistration(page)
  const open = page.getByTestId('result-drop-open-sessions')
  await expect(open).toContainText('다른 사용자(other-user)')
  await expect(open).toContainText('일부 완료 2/3')
  await chooseRun(page)
  await dropTree(page, { 'CUMULATIVE/7_Edge/a.csv': 'x', 'CUMULATIVE/7_Edge/b.csv': 'y' })
  await page.getByRole('button', { name: '올리기', exact: true }).click()
  const partial = page.getByTestId('result-drop-partial')
  await expect(partial).toContainText('사용 중: CUMULATIVE/7_Edge/b.csv')
  await partial.getByRole('button', { name: '나머지 다시 옮기기', exact: true }).click()
  await expect(page.getByTestId('result-drop-complete')).toContainText('2개 파일을 올렸습니다')
  await expect(page.getByTestId('result-drop-partial')).toHaveCount(0)
  expect(calls.completes).toBe(2)
  await open.getByRole('button', { name: '중지', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: '업로드를 중지했습니다' })).toBeVisible()
  expect(calls.aborts).toBe(1)
})
