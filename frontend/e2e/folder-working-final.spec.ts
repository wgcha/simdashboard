import { expect, test } from '@playwright/test'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { loginWorkspace } from './workspace-test-helpers'

test('Case 최종확정은 파일 미리보기 후 복사하고 확정 상태를 표시한다', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const files = [
    { source_relative_path: 'Working/Case A/Drop/Run/INDIVIDUAL/2_Face/model.rad', case_relative_path: 'Drop/Run/INDIVIDUAL/2_Face/model.rad', category: 'CAE', source_basis: 'CURRENT_CONFIRMED_SCENE', size: 40, sha256: 'a'.repeat(64) },
    { source_relative_path: 'Working/Case A/Drop/Run/INDIVIDUAL/2_Face/result.csv', case_relative_path: 'Drop/Run/INDIVIDUAL/2_Face/result.csv', category: 'Reports', source_basis: 'SELECTED_CAPTURE', size: 60, sha256: 'b'.repeat(64) },
  ]
  const common = { operation_id: 'f'.repeat(32), case_id: 'case-final', case_label: 'Case A', capture_id: 'capture-final', capture_fingerprint: 'test', folder_schema_snapshot_id: 'schema-final', files, counts: { CAE: 1, Reports: 1, input_decks: 1, rad_decks: 1, inc_decks: 0, reports: 0, results: 1 }, missing: { input_decks: false, rad_decks: false, reports: true }, excluded_capture_file_count: 0 }
  const completed = { ...common, status: 'COMPLETE', confirmed_at: '2026-10-02T01:00:00Z', created_by: 'test', output_paths: { CAE: 'Final/CAE/Case A/version', Reports: 'Final/Reports/Case A/version' } }
  let confirmed = false
  await page.route('**/api/dashboard/catalog**', (route) => route.fulfill({ json: { environment: 'DISTRIBUTION', cases: [{ id: 'case-final', label: 'Case A' }], captures: [{ id: 'capture-final', label: '수집 1', case_id: 'case-final' }], load_cases: [], execution_runs: [], run_options: [], modes: [], components: [], bases: [] } }))
  await page.route('**/api/dashboard/finalizations/status**', (route) => route.fulfill({ json: { latest: confirmed ? completed : null, selected_case_latest: confirmed ? completed : null, retryable_operations: [] } }))
  await page.route('**/api/dashboard/finalizations/preview', (route) => route.fulfill({ json: { ...common, status: 'PREVIEW', project_id: 'project-tv-001', request_id: 'request-drop-001', environment: 'DISTRIBUTION', case_path: 'Working/Case A', previewed_at: '2026-10-02T00:59:00Z', plan_sha256: 'c'.repeat(64), can_confirm: true } }))
  await page.route('**/api/dashboard/finalizations/confirm', async (route) => {
    expect(route.request().postDataJSON()).toMatchObject({ case_id: 'case-final', capture_id: 'capture-final', operation_id: common.operation_id })
    confirmed = true
    await route.fulfill({ json: completed })
  })
  await loginWorkspace(page)
  await page.goto('/workspace/requests?project=project-tv-001&request=request-drop-001&view=case_results&result_environment=DISTRIBUTION')
  await page.getByRole('button', { name: '최종확정', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '최종확정 파일 확인' })
  await expect(dialog).toContainText('model.rad')
  await expect(dialog).toContainText('result.csv')
  await expect(dialog).toContainText('Final/CAE')
  await page.screenshot({ path: join(tmpdir(), 'folder-final-preview-desktop.png'), fullPage: false })
  await dialog.getByRole('button', { name: '확정하고 파일 복사', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  await expect(page.locator('.case-finalization')).toContainText('확정 완료')
  // The view always shows the merged latest result (no per-capture context selector).
  await expect(page.locator('.simulation-dashboard').locator('label').filter({ hasText: '조회 문맥' })).toHaveCount(0)
  await expect(page.locator('.case-finalization')).toContainText('확정 완료')
  await expect(page.locator('.case-finalization')).toContainText('capture-final')
  expect(confirmed).toBe(true)
  expect(errors).toEqual([])
})

test('저장 규칙은 편집·개정 저장 후 유지되고 삭제 시 신규 선택에서 제거된다', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  let saved = { id: 'profile-test', environment: 'USAGE', name: 'Working 테스트 규칙', revision: 1,
    rules: { rules: [{ role_kind: 'SIMULATION_CASE', pattern: '', match_mode: 'level', depth: 1 }] } }
  let archived = false
  await page.route('**/api/folder-discovery/environments', (route) => route.fulfill({ json: { items: archived ? [] : [saved] } }))
  await page.route('**/api/folder-discovery/environments/profiles/profile-test**', async (route) => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON()
      expect(body.expected_revision).toBe(1)
      expect(body.rules.rules[0].depth).toBe(2)
      saved = { ...saved, name: body.name, rules: body.rules, revision: 2 }
      await route.fulfill({ json: saved })
    } else if (route.request().method() === 'DELETE') {
      expect(new URL(route.request().url()).searchParams.get('expected_revision')).toBe('2')
      archived = true
      await route.fulfill({ json: { id: saved.id, environment: saved.environment, name: saved.name, revision: 2, archived: true } })
    } else await route.fulfill({ json: saved })
  })
  await page.route('**/api/folder-discovery/saved-rules**', (route) => route.fulfill({ json: { items: [], total: 0 } }))
  await loginWorkspace(page, 'e2e-admin', '/workspace/catalog/schemas')
  const workspace = page.locator('.folder-environment-workspace')
  await expect(workspace).toBeVisible()
  await workspace.getByRole('button', { name: '저장된 규칙', exact: true }).click()
  await workspace.getByLabel('편집할 규칙').selectOption('profile-test')
  await workspace.getByLabel('규칙 이름', { exact: true }).fill('수정한 Working 규칙')
  await workspace.getByLabel('Working 기준 깊이 1').fill('2')
  await workspace.getByRole('button', { name: '새 개정 저장', exact: true }).click()
  await expect(workspace.getByLabel('규칙 이름', { exact: true })).toHaveValue('수정한 Working 규칙')
  await expect(workspace.getByLabel('편집할 규칙')).toHaveValue('profile-test')
  await expect(workspace.getByLabel('편집할 규칙').locator('option:checked')).toContainText('v2')
  page.once('dialog', (dialog) => dialog.accept())
  await workspace.getByRole('button', { name: '규칙 삭제', exact: true }).click()
  await expect(workspace.getByLabel('편집할 규칙').locator('option[value="profile-test"]')).toHaveCount(0)
  expect(archived).toBe(true)
  expect(errors).toEqual([])
  await page.screenshot({ path: join(tmpdir(), 'folder-rule-editor-desktop.png'), fullPage: false })
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
  const caseSelect = dashboard.locator('label').filter({ hasText: '해석 Case' }).locator('select').first()
  await caseSelect.selectOption('case-one')
  await expect(dashboard.locator('.simulation-dashboard__controls')).toContainText('Drop')
  await expect(dashboard.locator('.simulation-dashboard__controls')).toContainText('85qn80h_ref_organized')
  await expect(dashboard.locator('.simulation-dashboard__controls')).toContainText('INDIVIDUAL')
  await expect(dashboard).toContainText('이 Run의 수집 결과 없음')
  await expect(dashboard).toContainText('2_Face')
  await expect(dashboard).toContainText('3_Face')
  await caseSelect.selectOption('case-two')
  await expect(dashboard.locator('.simulation-dashboard__controls')).toContainText('Clamping')
  await expect(dashboard.locator('.simulation-dashboard__controls')).toContainText('Other_Run')
  await expect(dashboard.locator('.simulation-dashboard__controls')).not.toContainText('85qn80h_ref_organized')
  await expect(dashboard).toContainText('Other_Scene')
  await expect(dashboard).not.toContainText('2_Face')
  await expect(dashboard).not.toContainText('3_Face')
  expect(resultReads).toEqual([])
  expect(errors).toEqual([])
  await page.screenshot({ path: join(tmpdir(), 'folder-working-uncaptured-desktop.png'), fullPage: false })
})
