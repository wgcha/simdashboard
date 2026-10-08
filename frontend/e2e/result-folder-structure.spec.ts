import { expect, test, type Page, type Route } from '@playwright/test'
import { loginWorkspace, mockResultEnvironments } from './workspace-test-helpers'

// 결과 등록 "폴더 구조 만들기" (docs/features/result-registration.md §2a): per environment, the empty
// request folder → Working → Case folders. Mocked result-registration API, synthetic data only.

const context = { project: 'project-feature-showcase', request: 'request-showcase-compare' }
const PROJECT = 'P1'
const DIST = `${PROJECT}/[WR-0001]_[유통_환경]`
const USAGE_NEW = '[WR-0001]_[사용_환경]'
const PLAIN = `${PROJECT}/[WR-0001]_plain`
const FOREIGN = `${PROJECT}/[WR-0009]_[사용_환경]`
const OTHER_REQUEST = `${PROJECT}/[WR-0002]_[사용_환경]`
const DISPLAY = '\\\\fileserver\\SPDM'
const display = (relative: string) => `${DISPLAY}\\${relative.replaceAll('/', '\\')}`
const SCX_READ_ONLY = { mode: 'scx', state: 'OK', writes_enabled: false, writes_available: false, queue_paused: false }
const SCX_WRITABLE = { ...SCX_READ_ONLY, writes_enabled: true, writes_available: true }
const BATCH = 'c'.repeat(32)

type Body = { environment: 'USAGE' | 'DISTRIBUTION'; request_relative_path: string | null; new_request_folder: { parent_relative_path: string; name: string } | null; case_names: string[]; confirm: boolean; confirm_other_wr?: boolean }

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

function overview() {
  return {
    project_id: context.project, request_id: context.request, wr_key: '0001', max_cases: 100, drive: false,
    project_folders: [{ relative_path: PROJECT, display_path: display(PROJECT) }],
    candidates: [
      { relative_path: DIST, name: DIST.split('/')[1], display_path: display(DIST), environment: 'DISTRIBUTION', keyword_code: null, owner: 'THIS', linked_environment: 'DISTRIBUTION', wr_match: true, wr_key: '0001', wr_other_request: false },
      { relative_path: PLAIN, name: PLAIN.split('/')[1], display_path: display(PLAIN), environment: null, keyword_code: 'ENV_KEYWORD_NONE', owner: null, linked_environment: null, wr_match: true, wr_key: '0001', wr_other_request: false },
      { relative_path: FOREIGN, name: FOREIGN.split('/')[1], display_path: display(FOREIGN), environment: 'USAGE', keyword_code: null, owner: null, linked_environment: null, wr_match: false, wr_key: '0009', wr_other_request: false },
      { relative_path: OTHER_REQUEST, name: OTHER_REQUEST.split('/')[1], display_path: display(OTHER_REQUEST), environment: 'USAGE', keyword_code: null, owner: null, linked_environment: null, wr_match: false, wr_key: '0002', wr_other_request: true },
    ],
    environments: [
      { environment: 'USAGE', keyword: '사용', status: 'MISSING', folder: null, choices: [], case_suggestions: [],
        proposal: { parent_relative_path: PROJECT, name: USAGE_NEW, relative_path: `${PROJECT}/${USAGE_NEW}`, display_path: display(`${PROJECT}/${USAGE_NEW}`) } },
      { environment: 'DISTRIBUTION', keyword: '유통', status: 'LINKED', choices: [], proposal: null,
        folder: { relative_path: DIST, name: DIST.split('/')[1], display_path: display(DIST), environment: 'DISTRIBUTION', keyword_code: null, linked: true, owner: 'THIS', working_exists: true, working_name: 'Working', existing_cases: ['CaseA'] },
        case_suggestions: [{ name: 'CaseA', source: 'REGISTERED', exists: true }, { name: 'CaseB', source: 'REGISTERED', exists: false }] },
    ],
  }
}

function tree() {
  return {
    project_id: context.project, request_id: context.request, environment: 'DISTRIBUTION', request_relative_path: DIST,
    working_relative_path: `${DIST}/Working`, working_display_path: display(`${DIST}/Working`), display_root: DISPLAY,
    levels: [{ level: 1, role: 'WORKING', label: 'Working' }, { level: 2, role: 'SIMULATION_CASE', label: 'Case' }, { level: 3, role: 'LOAD_CASE', label: '하중경우' },
      { level: 4, role: 'EXECUTION_RUN', label: 'Run Case' }, { level: 5, role: 'RUN_OPTION', label: 'Run Option' }, { level: 6, role: 'SCENE', label: 'Scene' }],
    nodes: [], truncated: false, chunk_bytes: 8, blocked_extensions: ['.bat'], active_uploads: 0,
  }
}

function created(body: Body, drive = false) {
  const request = body.new_request_folder ? `${body.new_request_folder.parent_relative_path}/${body.new_request_folder.name}` : body.request_relative_path!
  const items = [
    ...(body.new_request_folder ? [{ relative_path: request, role: 'REQUEST', name: body.new_request_folder.name }] : []),
    ...(body.environment === 'USAGE' ? [{ relative_path: `${request}/Working`, role: 'WORKING', name: 'Working' }] : []),
    ...body.case_names.filter((name) => name !== 'CaseA').map((name) => ({ relative_path: `${request}/Working/${name}`, role: 'SIMULATION_CASE', name })),
  ]
  const existing = body.environment === 'DISTRIBUTION' ? [{ relative_path: `${request}/Working`, role: 'WORKING', name: 'Working' },
    ...body.case_names.filter((name) => name === 'CaseA').map((name) => ({ relative_path: `${request}/Working/${name}`, role: 'SIMULATION_CASE', name }))] : []
  return {
    environment: body.environment, request_relative_path: request, request_display_path: display(request), working_relative_path: `${request}/Working`,
    created: drive ? [] : items, queued: drive ? items : undefined, existing, warnings: [],
    drive: drive ? { batch_id: BATCH, state: 'QUEUED', finished: false, paused: false, counts: {}, items_total: items.length, files_total: 0, files_done: 0, bytes_total: 0, bytes_done: 0, current: null, current_kind: 'MKDIR', next_retry_at: null, errors: [], transfer_methods: {}, origin: 'result_structure', origin_ref: null, requested_by: 'e2e', project_id: context.project, request_id: context.request, created_at: null, updated_at: null } : null,
    link: { status: body.environment === 'USAGE' ? (drive ? 'PENDING' : 'LINKED') : 'LINKED' }, sync: null,
  }
}

async function installApi(page: Page, options: { drive?: 'read-only' | 'writable' } = {}) {
  const calls = { creates: [] as Body[], overviews: [] as string[] }
  if (options.drive) {
    await page.route('**/api/drive/status', (route) => json(route, options.drive === 'writable' ? SCX_WRITABLE : SCX_READ_ONLY))
    await page.route('**/api/drive/upload-batches/**', (route) => json(route, { ...created({ environment: 'USAGE', request_relative_path: null, new_request_folder: { parent_relative_path: PROJECT, name: USAGE_NEW }, case_names: ['Assy_Case1'], confirm: false }, true).drive, state: 'DONE', finished: true }))
  }
  await page.route('**/api/result-registration/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace('/api/result-registration', '')
    const method = request.method()
    if (method === 'GET' && path === '/drop-target') return json(route, tree())
    if (method === 'GET' && path === '/drop-uploads') return json(route, { sessions: [] })
    if (method === 'GET' && path === '/drafts') return json(route, { drafts: [], truncated: false })
    if (method === 'GET' && path === '/drop-target/structure') {
      calls.overviews.push(url.search)
      const value = overview()
      if (url.searchParams.get('request_relative_path') === PLAIN) {
        value.environments[0] = { ...value.environments[0], status: 'SELECTED', proposal: null,
          folder: { relative_path: PLAIN, name: PLAIN.split('/')[1], display_path: display(PLAIN), environment: null, keyword_code: 'ENV_KEYWORD_NONE', linked: false, owner: null, working_exists: false, working_name: 'Working', existing_cases: [] } } as never
      }
      if (url.searchParams.get('request_relative_path') === FOREIGN) {
        value.environments[0] = { ...value.environments[0], status: 'SELECTED', proposal: null,
          folder: { relative_path: FOREIGN, name: FOREIGN.split('/')[1], display_path: display(FOREIGN), environment: 'USAGE', keyword_code: null, linked: false, owner: null, working_exists: false, working_name: 'Working', existing_cases: [] } } as never
      }
      return json(route, value)
    }
    if (method === 'POST' && path === '/drop-target/structure') {
      const body = request.postDataJSON() as Body
      calls.creates.push(body)
      if (body.case_names.includes('CON')) return json(route, { detail: { code: 'RESULT_STRUCTURE_NAME_INVALID', message: '쓸 수 없는 Case 이름이 1개 있습니다.', problems: [{ name: 'CON', message: '쓸 수 없는 문자나 이름입니다.' }] } }, 422)
      if (body.request_relative_path === FOREIGN && !body.confirm_other_wr) return json(route, { detail: { code: 'RESULT_STRUCTURE_OTHER_WR', message: "'[WR-0009]_[사용_환경]'의 의뢰번호(0009)가 이 의뢰(0001)와 다릅니다.", warnings: ['다름'] } }, 409)
      if (body.case_names.includes('Assy_case1x') && !body.confirm) return json(route, { detail: { code: 'RESULT_STRUCTURE_NAME_WARNING', message: '비슷한 이름', warnings: ["'Assy_case1x'은(는) 같은 위치의 'Assy_Case1'와 거의 같은 이름입니다."] } }, 409)
      return json(route, created(body, options.drive === 'writable'))
    }
    return json(route, { detail: `Unexpected ${method} ${path}` }, 404)
  })
  return calls
}

async function openDialog(page: Page) {
  await mockResultEnvironments(page, ['DISTRIBUTION'])
  await loginWorkspace(page)
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption(context.project)
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption(context.request)
  await page.getByRole('navigation', { name: '의뢰 작업 여정' }).getByRole('button', { name: '결과 등록', exact: true }).click()
  await expect(page.getByTestId('result-drop-workspace')).toBeVisible()
  await page.getByRole('button', { name: '폴더 구조 만들기' }).click()
  const dialog = page.getByTestId('result-structure-dialog')
  await expect(dialog).toBeVisible()
  return dialog
}

test('폴더 구조 만들기는 환경별 의뢰 폴더를 보여 주고 Case 이름을 미리 채워 Working·Case 폴더를 만든다', async ({ page }) => {
  const calls = await installApi(page)
  const dialog = await openDialog(page)
  const usage = dialog.getByTestId('result-structure-USAGE')
  const distribution = dialog.getByTestId('result-structure-DISTRIBUTION')
  // Linked distribution folder: on by default, registered Case names prefilled ("이미 있음" for existing folders).
  await expect(distribution.getByLabel('유통환경 폴더 만들기')).toBeChecked()
  await expect(distribution.getByRole('textbox', { name: '유통환경 Case 1', exact: true })).toHaveValue('CaseA')
  await expect(distribution.getByRole('textbox', { name: '유통환경 Case 2', exact: true })).toHaveValue('CaseB')
  await expect(distribution.getByText('이미 있음', { exact: true }).first()).toBeVisible()
  // Missing usage folder: the exact new name is shown before anything is created; off until the user turns it on.
  await expect(usage.getByLabel('사용환경 폴더 만들기')).not.toBeChecked()
  await expect(usage.getByLabel('사용환경 의뢰 폴더')).toHaveValue('__new__')
  await expect(usage.getByTestId('result-structure-new-USAGE')).toContainText(USAGE_NEW)
  await usage.getByLabel('사용환경 폴더 만들기').check()
  await usage.getByRole('textbox', { name: '사용환경 Case 1', exact: true }).fill('Assy_Case1')
  await usage.getByRole('button', { name: 'Case 추가' }).click()
  await usage.getByRole('textbox', { name: '사용환경 Case 2', exact: true }).fill('remove-me')
  await usage.getByRole('button', { name: '사용환경 Case 2 빼기' }).click()
  await expect(usage.getByRole('textbox', { name: '사용환경 Case 2', exact: true })).toHaveCount(0)
  await distribution.getByRole('button', { name: 'Case 추가' }).click()
  await distribution.getByRole('textbox', { name: '유통환경 Case 3', exact: true }).fill('CON')
  await dialog.getByRole('button', { name: '만들기', exact: true }).click()
  // Invalid name: shown at its line; the valid environment was created.
  await expect(distribution.getByRole('alert').filter({ hasText: '쓸 수 없는 문자나 이름입니다.' })).toBeVisible()
  await expect(usage.getByTestId('result-structure-done-USAGE')).toContainText('폴더 3개를 만들었습니다')
  await expect(usage.getByTestId('result-structure-done-USAGE')).toContainText('이 의뢰에 연결되어 있습니다')
  expect(calls.creates[0]).toMatchObject({ environment: 'USAGE', request_relative_path: null, new_request_folder: { parent_relative_path: PROJECT, name: USAGE_NEW }, case_names: ['Assy_Case1'], confirm: false })
  expect(calls.creates[1]).toMatchObject({ environment: 'DISTRIBUTION', request_relative_path: DIST, new_request_folder: null, case_names: ['CaseA', 'CaseB', 'CON'] })
  // Fix the name and run again: existing folders are reported, nothing is overwritten.
  await distribution.getByRole('button', { name: '유통환경 Case 3 빼기' }).click()
  await usage.getByLabel('사용환경 폴더 만들기').uncheck()
  await dialog.getByRole('button', { name: '만들기', exact: true }).click()
  await expect(distribution.getByTestId('result-structure-done-DISTRIBUTION')).toContainText('폴더 1개를 만들었습니다 · 이미 있음 2개')
  expect(calls.creates).toHaveLength(3)
})

test('키워드 없는 의뢰 폴더를 직접 고르면 경고를 확인한 뒤 만든다', async ({ page }) => {
  const calls = await installApi(page)
  const dialog = await openDialog(page)
  const usage = dialog.getByTestId('result-structure-USAGE')
  await usage.getByLabel('사용환경 의뢰 폴더').selectOption(PLAIN)
  await expect(usage.getByRole('note')).toContainText("'사용'가 없어 대시보드가 자동으로 읽지 못합니다")
  expect(calls.overviews.at(-1)).toContain('request_relative_path=')
  await usage.getByLabel('사용환경 폴더 만들기').check()
  await dialog.getByRole('checkbox', { name: '유통환경 폴더 만들기' }).uncheck()
  await usage.getByRole('textbox', { name: '사용환경 Case 1', exact: true }).fill('Assy_case1x')
  await dialog.getByRole('button', { name: '만들기', exact: true }).click()
  await expect(usage.getByRole('status').filter({ hasText: '거의 같은 이름' })).toBeVisible()
  await dialog.getByRole('button', { name: '그래도 만들기' }).click()
  await expect(usage.getByTestId('result-structure-done-USAGE')).toBeVisible()
  expect(calls.creates.map((body) => [body.request_relative_path, body.confirm])).toEqual([[PLAIN, false], [PLAIN, true]])
})

test('의뢰번호가 다른 폴더는 경고를 확인해야 만들고 다른 의뢰의 의뢰번호 폴더는 고를 수 없다', async ({ page }) => {
  const calls = await installApi(page)
  const dialog = await openDialog(page)
  const usage = dialog.getByTestId('result-structure-USAGE')
  const select = usage.getByLabel('사용환경 의뢰 폴더')
  await expect(select.locator('option', { hasText: '[WR-0002]' })).toHaveCount(0)
  await select.selectOption(FOREIGN)
  const warning = usage.getByTestId('result-structure-other-wr-USAGE')
  await expect(warning).toContainText('의뢰번호(0009)가 이 의뢰(0001)와 다릅니다')
  await usage.getByLabel('사용환경 폴더 만들기').check()
  await dialog.getByRole('checkbox', { name: '유통환경 폴더 만들기' }).uncheck()
  await usage.getByRole('textbox', { name: '사용환경 Case 1', exact: true }).fill('Assy_Case1')
  await dialog.getByRole('button', { name: '만들기', exact: true }).click()
  await expect(usage.getByRole('alert').filter({ hasText: '이 의뢰(0001)와 다릅니다' })).toBeVisible()
  await warning.getByLabel('사용환경 의뢰번호가 다른 폴더 확인').check()
  await dialog.getByRole('button', { name: '만들기', exact: true }).click()
  await expect(usage.getByTestId('result-structure-done-USAGE')).toBeVisible()
  expect(calls.creates.map((body) => [body.request_relative_path, body.confirm_other_wr])).toEqual([[FOREIGN, false], [FOREIGN, true]])
})

test('드라이브 쓰기 허용이 꺼져 있으면 읽기 전용 안내를 보이고 만들지 않는다', async ({ page }) => {
  const calls = await installApi(page, { drive: 'read-only' })
  const dialog = await openDialog(page)
  await expect(dialog.getByTestId('result-structure-read-only')).toBeVisible()
  await expect(dialog.getByRole('button', { name: '만들기', exact: true })).toBeDisabled()
  expect(calls.creates).toHaveLength(0)
})

test('드라이브 모드에서는 업로드 대기열로 폴더를 만들고 진행을 보여 준다', async ({ page }) => {
  const calls = await installApi(page, { drive: 'writable' })
  const dialog = await openDialog(page)
  const usage = dialog.getByTestId('result-structure-USAGE')
  await dialog.getByRole('checkbox', { name: '유통환경 폴더 만들기' }).uncheck()
  await usage.getByLabel('사용환경 폴더 만들기').check()
  await usage.getByRole('textbox', { name: '사용환경 Case 1', exact: true }).fill('Assy_Case1')
  await dialog.getByRole('button', { name: '만들기', exact: true }).click()
  const done = usage.getByTestId('result-structure-done-USAGE')
  await expect(done).toContainText('드라이브에 폴더 3개를 만드는 중')
  await expect(done.getByTestId('drive-batch')).toHaveAttribute('data-state', 'DONE')
  await expect(done).toContainText('1분 안에 이 의뢰에 연결')
  expect(calls.creates).toHaveLength(1)
})
