import { expect, test } from '@playwright/test'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { loginWorkspace } from './workspace-test-helpers'

test('Final 지정 미리보기는 최신 결과 기준과 CAE 파일을 보이고 화면 선택 없이도 Case 전체 보고서로 확정할 수 있다', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const files = [
    { source_relative_path: 'Working/Case A/Drop/Run/INDIVIDUAL/2_Face/model.rad', case_relative_path: 'Drop/Run/INDIVIDUAL/2_Face/model.rad', category: 'CAE', source_basis: 'CURRENT_CONFIRMED_SCENE', size: 40, sha256: 'a'.repeat(64) },
    { source_relative_path: 'Working/Case A/Drop/Run/INDIVIDUAL/2_Face/result.csv', case_relative_path: 'Drop/Run/INDIVIDUAL/2_Face/result.csv', category: 'CAE', source_basis: 'SOURCE_CAPTURE', source_capture_id: 'capture-final', size: 60, sha256: 'b'.repeat(64) },
  ]
  const completed = { schema_version: 1, operation_id: 'e'.repeat(32), status: 'COMPLETE', case_id: 'case-final', case_label: 'Case A', case_path: 'Working/Case A', capture_id: 'capture-final', basis: 'CAPTURE', scene_sources: [], capture_fingerprint: 'old', folder_schema_snapshot_id: 'schema-final', output_paths: { CAE: 'Final/CAE/Case A/old', Reports: 'Final/Reports/Case A/old' }, files, reports: [], counts: { CAE: 1, Reports: 1, input_decks: 1, rad_decks: 1, inc_decks: 0, results: 1 }, missing: { input_decks: false, rad_decks: false, inc_decks: true, reports: true }, excluded_capture_file_count: 0, created_by: 'test', confirmed_at: '2026-10-01T01:00:00Z' }
  let previewBody: Record<string, unknown> | null = null
  await page.route('**/api/dashboard/catalog**', (route) => route.fulfill({ json: { environment: 'DISTRIBUTION', cases: [{ id: 'case-final', label: 'Case A' }], captures: [{ id: 'capture-final', label: '수집 1', case_id: 'case-final' }], load_cases: [], execution_runs: [], run_options: [], modes: [], components: [], bases: [] } }))
  // A version-1 record (results under Final/Reports) is still shown as history.
  await page.route('**/api/dashboard/finalizations/status**', (route) => route.fulfill({ json: { latest: completed, selected_case_latest: completed, retryable_operations: [], unverified_records: 0 } }))
  await page.route('**/api/dashboard/finalizations/preview', (route) => {
    previewBody = route.request().postDataJSON()
    return route.fulfill({ json: { schema_version: 2, operation_id: 'f'.repeat(32), status: 'PREVIEW', project_id: 'project-tv-001', request_id: 'request-drop-001', environment: 'DISTRIBUTION', case_id: 'case-final', case_label: 'Case A', case_path: 'Working/Case A', capture_id: 'latest:case-final', basis: 'LATEST', scene_sources: [{ scene_path: 'Working/Case A/Drop/Run/INDIVIDUAL/2_Face', source_capture_id: 'capture-final' }], capture_fingerprint: 'x', folder_schema_snapshot_id: 'schema-final', scene_paths: ['Working/Case A/Drop/Run/INDIVIDUAL/2_Face'], files, counts: { CAE: 2, input_decks: 1, rad_decks: 1, inc_decks: 0, results: 1, scene_reports: 0 }, missing: { input_decks: false, rad_decks: false, inc_decks: true }, excluded_capture_file_count: 0, previewed_at: '2026-10-02T00:59:00Z', plan_sha256: 'c'.repeat(64), can_confirm: true, output_paths: { CAE: 'Final/CAE/Case A/' + 'f'.repeat(32), Reports: 'Final/Reports/Case A/' + 'f'.repeat(32) }, report_files: { pptx: 'Case_A_report.pptx', html: 'Case_A_report.html' }, report_paths: { pptx: 'Final/Reports/Case A/x/Case_A_report.pptx', html: 'Final/Reports/Case A/x/Case_A_report.html' }, report_limits: { pptx: 67108864, html: 335544320 } } })
  })
  await loginWorkspace(page)
  await page.goto('/workspace/requests?project=project-tv-001&request=request-drop-001&view=case_results&result_environment=DISTRIBUTION')
  await expect(page.locator('.case-finalization')).toContainText('이전 결과로 확정', { timeout: 15_000 })
  await page.getByRole('button', { name: 'Final 지정', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Final 지정 확인' })
  await expect(dialog).toContainText('최신 결과 · Scene 1개')
  await expect(dialog.getByRole('list', { name: 'Scene 기준' })).toContainText('2_Face')
  await dialog.getByText('파일 2개 보기').click()
  await expect(dialog).toContainText('model.rad')
  await expect(dialog).toContainText('result.csv')
  await expect(dialog).toContainText('Case_A_report.pptx')
  // The Final report covers the whole Case, so no on-screen Run Case · Run Option is needed.
  await expect(dialog.getByTestId('case-final-report-range')).toContainText('Case 전체')
  await expect(dialog).not.toContainText('PDF')
  await expect(dialog.getByRole('button', { name: 'Final 지정 확정', exact: true })).toBeEnabled()
  expect(previewBody).toMatchObject({ case_id: 'case-final', capture_id: 'latest:case-final' })
  await page.screenshot({ path: join(tmpdir(), 'folder-final-preview-desktop.png'), fullPage: false })
  await dialog.getByRole('button', { name: '취소', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  await expect(page.locator('.simulation-dashboard').locator('label').filter({ hasText: '조회 문맥' })).toHaveCount(0)
  // Internal capture ids stay out of the visible text; details are in the badge tooltip.
  await expect(page.locator('.case-finalization')).not.toContainText('capture-final')
  await expect(page.locator('.case-finalization__status')).toHaveAttribute('title', /2개 파일/)
  expect(errors).toEqual([])
})

test('깊이 스키마는 확인 후 저장하면 새 스키마 세트로 바뀌고 기존 등록은 재해석 전까지 유지된다고 안내한다', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const lower = (levels: string[]) => ({ levels: levels.map((role, index) => index === 0 ? { level: 1, role, fixed_name: 'Working' } : { level: index + 1, role }), below_last: 'CONTENT' })
  let schema = {
    schema_set_id: 'dss-e2e-1', created_at: '2026-10-02T00:00:00Z', created_by: 'e2e-admin',
    upper: { levels: [{ level: 1, role: 'PROJECT' }, { level: 2, role: 'REQUEST' }] },
    environments: {
      USAGE: { profile_id: 'usage-1', environment_keyword: '사용', lower: lower(['WORKING', 'SIMULATION_CASE', 'SCENE']), usage_sources: null },
      DISTRIBUTION: { profile_id: 'distribution-1', environment_keyword: '유통', lower: lower(['WORKING', 'SIMULATION_CASE', 'LOAD_CASE', 'EXECUTION_RUN', 'RUN_OPTION', 'SCENE']), usage_sources: null },
    },
  }
  let checked = false
  let savedBody: { expected_schema_set_id: string; upper: { levels: Array<{ level: number; role: string }> } } | null = null
  await page.route('**/api/folder-discovery/environments/depth-schema', async (route) => {
    if (route.request().method() === 'PUT') {
      savedBody = route.request().postDataJSON()
      schema = { ...schema, schema_set_id: 'dss-e2e-2', upper: savedBody!.upper }
    }
    await route.fulfill({ json: schema })
  })
  await page.route('**/api/folder-discovery/environments/depth-schema/samples', (route) => route.fulfill({ json: {
    levels: [{ level: 1, folder_count: 1, samples: [{ name: 'SPDM (Admin)', count: 1 }], truncated: false },
      { level: 2, folder_count: 1, samples: [{ name: '75R9J_PV', count: 1 }], truncated: false },
      { level: 3, folder_count: 2, samples: [{ name: '[WR-0002]_[유통_환경]', count: 1 }, { name: '[WR-0001]_[사용_환경]', count: 1 }], truncated: false }],
    requests_sampled: 0 } }))
  await page.route('**/api/folder-discovery/environments/depth-schema/check', async (route) => {
    checked = true
    expect(route.request().postDataJSON().upper.levels.map((item: { role: string }) => item.role)).toEqual(['CONTAINER', 'PROJECT', 'REQUEST'])
    await route.fulfill({ json: { by_code: { UNEXPECTED_REQUEST_CHILD: 1 }, examples: [{ relative_path: 'SPDM (Admin)/75R9J_PV/[WR-0002]_[유통_환경]', code: 'UNEXPECTED_REQUEST_CHILD' }] } })
  })
  await loginWorkspace(page, 'e2e-admin', '/workspace/catalog/schemas')
  const workspace = page.locator('.folder-environment-workspace')
  await expect(workspace).toBeVisible()
  await workspace.getByRole('button', { name: '저장된 규칙', exact: true }).click()
  await expect(workspace.getByRole('heading', { name: '저장된 규칙' })).toBeVisible()
  await expect(workspace).toContainText('dss-e2e-1')
  const table = workspace.getByRole('table', { name: '상위 구조 깊이 표' })
  await expect(table).toContainText('SPDM (Admin)')
  // Root → CONTAINER → PROJECT → REQUEST: set each depth in order (L3 first extends the schema).
  await table.getByLabel('L3 역할').selectOption('REQUEST')
  await table.getByLabel('L2 역할').selectOption('PROJECT')
  await table.getByLabel('L1 역할').selectOption('CONTAINER')
  await expect(workspace.locator('.depth-schema-errors')).toHaveCount(0)
  await workspace.getByRole('button', { name: '확인', exact: true }).click()
  const result = workspace.getByRole('region', { name: '확인 결과' })
  await expect(result).toContainText('의뢰 아래 Working·Final 외 폴더')
  expect(checked).toBe(true)
  await workspace.getByRole('button', { name: '저장', exact: true }).click()
  await expect(workspace).toContainText('깊이 스키마를 저장했습니다.')
  await expect(workspace).toContainText('dss-e2e-2')
  expect(savedBody).not.toBeNull()
  expect(savedBody!.expected_schema_set_id).toBe('dss-e2e-1')
  expect(savedBody!.upper.levels.map((item) => item.role)).toEqual(['CONTAINER', 'PROJECT', 'REQUEST'])
  expect(errors).toEqual([])
  await page.screenshot({ path: join(tmpdir(), 'folder-depth-schema-editor-desktop.png'), fullPage: false })
})

test('확정 Working 계층은 수집본에 Run이 없거나 결과 미수집 상태에서도 하중경우와 Run을 표시한다', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const errors: string[] = []
  const resultReads: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('request', (request) => {
    if (request.url().includes('/api/dashboard/distribution/runs/')) resultReads.push(request.url())
  })
  await page.route('**/api/dashboard/catalog**', (route) => route.fulfill({ json: {
    environment: 'DISTRIBUTION',
    folder_schema: { status: 'AVAILABLE', snapshot_id: 'schema-synthetic-1', diagnostic: null },
    cases: [
      { id: 'case-one', label: 'Package_Model_SetCase1', source: 'FOLDER_SCHEMA', match_status: 'UNCAPTURED', capture_count: 0 },
      { id: 'case-two', label: 'Package_Model_SetCase2', source: 'FOLDER_SCHEMA', match_status: 'UNCAPTURED', capture_count: 0 },
    ],
    load_cases: [
      { id: 'load-one', label: 'Drop', case_id: 'case-one', capture_id: null, match_status: 'UNCAPTURED' },
      { id: 'load-two', label: 'Clamping', case_id: 'case-two', capture_id: null, match_status: 'UNCAPTURED' },
    ],
    execution_runs: [
      { id: 'run-one', label: '85qn80h_ref_organized', case_id: 'case-one', load_case_id: 'load-one', capture_id: null, match_status: 'UNCAPTURED' },
      { id: 'run-two', label: 'Other_Run', case_id: 'case-two', load_case_id: 'load-two', capture_id: null, match_status: 'UNCAPTURED' },
    ],
    run_options: [
      { id: 'option-one', label: 'INDIVIDUAL', case_id: 'case-one', execution_run_id: 'run-one', capture_id: null, option_status: 'PRESENT', mode: 'INDIVIDUAL', match_status: 'UNCAPTURED' },
      { id: 'option-two', label: 'CUMULATIVE', case_id: 'case-two', execution_run_id: 'run-two', capture_id: null, option_status: 'PRESENT', mode: 'CUMULATIVE', match_status: 'UNCAPTURED' },
    ],
    scenes: [
      { id: 'scene-face2', label: '2_Face', case_id: 'case-one', load_case_id: 'load-one', execution_run_id: 'run-one', run_option_id: 'option-one', capture_id: null, source: 'FOLDER_SCHEMA', match_status: 'UNCAPTURED' },
      { id: 'scene-face3', label: '3_Face', case_id: 'case-one', load_case_id: 'load-one', execution_run_id: 'run-one', run_option_id: 'option-one', capture_id: null, source: 'FOLDER_SCHEMA', match_status: 'UNCAPTURED' },
      { id: 'scene-other', label: 'Other_Scene', case_id: 'case-two', load_case_id: 'load-two', execution_run_id: 'run-two', run_option_id: 'option-two', capture_id: null, source: 'FOLDER_SCHEMA', match_status: 'UNCAPTURED' },
    ], captures: [{ id: 'capture-empty', label: 'Run 결과 없는 수집본', case_id: 'case-one' }], modes: [], components: [], bases: [],
  } }))
  await page.route('**/api/dashboard/finalizations/**', (route) => route.fulfill({ json: { latest: null, selected_case_latest: null, retryable_operations: [] } }))
  await loginWorkspace(page)
  await page.goto('/workspace/requests?project=project-tv-001&request=request-drop-001&view=case_results&result_environment=DISTRIBUTION')
  const dashboard = page.locator('.simulation-dashboard')
  // A full reload re-bootstraps the workspace; allow for a slow first render.
  await expect(dashboard).toBeVisible({ timeout: 15_000 })
  await expect(page).toHaveURL(/view=case_results/)
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  const path = dashboard.getByRole('group', { name: 'Case 경로' })
  const caseSelect = path.getByRole('combobox', { name: 'Case', exact: true })
  await caseSelect.selectOption('case-one')
  await expect(path).toContainText('Drop')
  await expect(path).toContainText('85qn80h_ref_organized')
  await expect(path).toContainText('INDIVIDUAL')
  await expect(dashboard).toContainText('이 Run의 수집 결과 없음')
  await expect(dashboard).toContainText('2_Face')
  await expect(dashboard).toContainText('3_Face')
  await caseSelect.selectOption('case-two')
  await expect(path).toContainText('Clamping')
  await expect(path).toContainText('Other_Run')
  await expect(path).not.toContainText('85qn80h_ref_organized')
  await expect(dashboard).toContainText('Other_Scene')
  await expect(dashboard).not.toContainText('2_Face')
  await expect(dashboard).not.toContainText('3_Face')
  expect(resultReads).toEqual([])
  expect(errors).toEqual([])
  await page.screenshot({ path: join(tmpdir(), 'folder-working-uncaptured-desktop.png'), fullPage: false })
})
