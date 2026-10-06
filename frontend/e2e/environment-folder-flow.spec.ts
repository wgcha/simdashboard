import { expect, test } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { loginWorkspace } from './workspace-test-helpers'

const profile = { id: 'usage-default', environment: 'USAGE', name: '사용환경 기본', revision: 1, rules: { rules: [{ role_kind: 'SIMULATION_CASE', parent_role: 'REQUEST', pattern: 'Assy*', match_mode: 'glob' }] }, active: true }
const nodes = [
  { id: 'n-project', name: 'Project_A', relative_path: 'Project_A', parent_path: '', depth: 0, file_count: 0, allowed_roles: ['PROJECT'], role_kind: 'PROJECT', status: 'CONFIRMED' },
  { id: 'n-request', name: 'WR_1042_SimType1', relative_path: 'Project_A/WR_1042_SimType1', parent_path: 'Project_A', depth: 1, file_count: 0, allowed_roles: ['REQUEST'], role_kind: 'REQUEST', status: 'CONFIRMED' },
  { id: 'n-case', name: 'Assy_RES_Main', relative_path: 'Project_A/WR_1042_SimType1/Assy_RES_Main', parent_path: 'Project_A/WR_1042_SimType1', depth: 2, file_count: 1, allowed_roles: ['SIMULATION_CASE'], role_kind: 'SIMULATION_CASE', status: 'CONFIRMED' },
]

test.describe('환경 폴더 연결', () => {
  test('깊이 스키마 역할은 읽기 전용이고 편차 배지를 보인 뒤 긴 한국어 경로 미리보기가 상태와 겹치지 않는다', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 })
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    const caseName = 'Package_한국어_재료_열변형_복합시험_매우_긴_Case_이름_SetCase1'
    const runName = '한국어_시트_조립체_정적하중_전체_결과_재현용_매우_긴_Run_이름'
    const root = 'Project_9301_Preview/WR_9301_SimType1'
    const casePath = `${root}/${caseName}`
    const scenePath = `${casePath}/Drop/${runName}/Individual/2_Face_Drop_Scene01_후면_좌측_상세_장면`
    const nodes = [
      { id: 'n-project', name: 'Project_9301_Preview', relative_path: 'Project_9301_Preview', parent_path: '', depth: 0, allowed_roles: ['PROJECT'], role_kind: 'PROJECT', status: 'CONFIRMED' },
      { id: 'n-request', name: 'WR_9301_SimType1', relative_path: root, parent_path: 'Project_9301_Preview', depth: 1, level: 2, segment: 'UPPER', role_basis: 'DEPTH_SCHEMA', allowed_roles: ['REQUEST'], role_kind: 'REQUEST', status: 'CONFIRMED' },
      // DEPTH_V1: a warning deviation (non-blocking) is shown as a badge; the role cannot be changed in place.
      { id: 'n-final-other', name: 'Other', relative_path: `${root}/Final/Other`, parent_path: `${root}/Final`, depth: 3, level: 2, segment: 'FINAL', role_basis: 'DEPTH_SCHEMA', allowed_roles: [], role_kind: null, status: 'UNRESOLVED', deviation: { code: 'UNEXPECTED_FINAL_CHILD', message: 'Final 아래에는 CAE·Reports·CAD만 둘 수 있습니다.' } },
      { id: 'n-case', name: caseName, relative_path: casePath, parent_path: root, depth: 2, allowed_roles: ['SIMULATION_CASE'], role_kind: 'SIMULATION_CASE', status: 'CONFIRMED' },
      { id: 'n-load', name: 'Drop', relative_path: `${casePath}/Drop`, parent_path: casePath, depth: 3, allowed_roles: ['LOAD_CASE'], role_kind: 'LOAD_CASE', status: 'CONFIRMED' },
      { id: 'n-run', name: runName, relative_path: `${casePath}/Drop/${runName}`, parent_path: `${casePath}/Drop`, depth: 4, allowed_roles: ['EXECUTION_RUN'], role_kind: 'EXECUTION_RUN', status: 'CONFIRMED' },
      { id: 'n-option', name: 'Individual', relative_path: `${casePath}/Drop/${runName}/Individual`, parent_path: `${casePath}/Drop/${runName}`, depth: 5, allowed_roles: ['RUN_OPTION'], role_kind: 'RUN_OPTION', status: 'CONFIRMED' },
      { id: 'n-scene', name: '2_Face_Drop_Scene01_후면_좌측_상세_장면', relative_path: scenePath, parent_path: `${casePath}/Drop/${runName}/Individual`, depth: 6, allowed_roles: ['SCENE'], role_kind: 'SCENE', status: 'CONFIRMED' },
      { id: 'n-results', name: 'RESULTS', relative_path: `${scenePath}/RESULTS`, parent_path: scenePath, depth: 7, allowed_roles: ['RESULTS'], role_kind: 'RESULTS', status: 'CONFIRMED' },
    ]
    await page.route('**/api/folder-discovery/environments', (route) => route.fulfill({ json: { items: [] } }))
    await page.route('**/api/projects', (route) => route.fulfill({ json: [{ id: 'project-tv-001', name: 'Demo Project' }] }))
    await page.route('**/api/projects/project-tv-001/requests', (route) => route.fulfill({ json: [] }))
    await page.route('**/api/folder-discovery/saved-rules**', (route) => route.fulfill({ json: { items: [], offset: 0, limit: 100, total: 0 } }))
    await page.route('**/api/folder-discovery/environments/history**', (route) => route.fulfill({ json: { items: [], offset: 0, limit: 50, total: 0 } }))
    await page.route('**/api/folder-discovery/environments/scan', (route) => route.fulfill({ json: { id: 'scan-preview-long', environment: 'DISTRIBUTION', relative_path: '', status: 'COMPLETE', nodes, issues: [] } }))
    await page.route('**/api/folder-discovery/environments/previews', async (route) => {
      const body = route.request().postDataJSON()
      // Roles come from the depth schema: nothing was assigned manually.
      expect(body.assignments).toEqual([])
      const plan = nodes.filter((node) => ['PROJECT', 'REQUEST', 'SIMULATION_CASE', 'LOAD_CASE', 'EXECUTION_RUN', 'RUN_OPTION', 'SCENE'].includes(node.role_kind ?? ''))
        .map((node) => ({ ...node, status: 'CONFIRMED' }))
      await route.fulfill({ json: { id: 'preview-long', scan_id: 'scan-preview-long', environment: 'DISTRIBUTION', can_apply: true, rows: plan, summary: { new: 3, existing: 0 }, unresolved_count: 0 } })
    })
    await loginWorkspace(page, 'e2e-admin', '/workspace/catalog/schemas')
    // The login helper deliberately probes /auth/me before authentication.
    // Monitor console errors for the authenticated folder workflow below.
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
    await page.goto('/workspace/catalog/schemas?result_environment=DISTRIBUTION')
    const screen = page.locator('.folder-environment-workspace')
    await expect(page).toHaveURL(/\/workspace\/catalog\/schemas\?result_environment=DISTRIBUTION/)
    await expect(page).toHaveTitle('VD simulation workbench')
    await expect(screen).toBeVisible()
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)
    await screen.getByRole('button', { name: '조사 시작' }).click()
    await expect(screen).toContainText('구조 확인')
    await screen.locator('.folder-node-button').filter({ hasText: 'WR_9301_SimType1' }).click()
    const detail = screen.locator('.folder-node-detail')
    await expect(detail.getByLabel('선택 폴더 역할')).toHaveText('의뢰')
    // Read-only role: no role select remains in the selected-folder detail.
    await expect(detail.locator('select[aria-label="선택 폴더 역할"]')).toHaveCount(0)
    await expect(detail).toContainText('L2')
    const deviated = screen.locator('.folder-node-button').filter({ hasText: 'Other' })
    await expect(deviated.locator('.folder-deviation-badge.warning')).toHaveText('Final 아래 CAE·Reports·CAD 외 폴더')
    await deviated.click()
    await expect(detail.getByLabel('선택 폴더 역할')).toHaveText('확인 필요')
    await expect(detail.locator('.folder-deviation-badge.warning')).toBeVisible()
    await expect(detail.getByRole('alert')).toHaveText('Final 아래에는 CAE·Reports·CAD만 둘 수 있습니다.')
    await expect(detail).toContainText('“저장된 규칙” 탭에서 깊이별 역할을 수정하세요')
    await screen.getByRole('button', { name: '등록 내용 확인' }).click()
    await expect(screen).toContainText('확인 필요 0')
    await expect(screen).toContainText('확정됨')
    await expect(screen).toContainText(caseName)
    await expect(screen).toContainText(runName)
    await expect(screen.locator('.folder-preview-row--preview').filter({ hasText: 'Final/Other' })).toHaveCount(0)
    const overlaps = async () => screen.locator('.folder-preview-row--preview').evaluateAll((rows) => rows.map((row) => {
      const cells = Array.from(row.querySelectorAll(':scope > span, :scope > b, :scope > em')).map((cell) => cell.getBoundingClientRect())
      return cells.some((left, index) => cells.slice(index + 1).some((right) => Math.min(left.right, right.right) - Math.max(left.left, right.left) > 0.5))
    }))
    const darkOverlaps = await overlaps()
    expect(darkOverlaps).toEqual(darkOverlaps.map(() => false))
    await expect(screen.locator('.folder-preview-row--preview').first()).toHaveJSProperty('scrollWidth', await screen.locator('.folder-preview-row--preview').first().evaluate((row) => row.clientWidth))
    await page.screenshot({ path: join(tmpdir(), 'environment-folder-preview-long-desktop.png'), fullPage: false })
    await page.getByRole('button', { name: '라이트', exact: true }).click()
    await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).toBe('light')
    await expect(page.locator('.app-shell[data-theme="light"]')).toBeVisible()
    const lightOverlaps = await overlaps()
    expect(lightOverlaps).toEqual(lightOverlaps.map(() => false))
    await expect(screen.locator('.folder-preview-row--preview').first()).toHaveJSProperty('scrollWidth', await screen.locator('.folder-preview-row--preview').first().evaluate((row) => row.clientWidth))
    await page.screenshot({ path: join(tmpdir(), 'environment-folder-preview-long-light-desktop.png'), fullPage: false })
    expect(errors).toEqual([])
  })

  test('데스크톱 Refresh는 선택한 의뢰의 역할 판정 근거를 표시한다', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 })
    await page.route('**/api/folder-discovery/environments', (route) => route.fulfill({ json: { items: [profile] } }))
    await page.route('**/api/projects', (route) => route.fulfill({ json: [{ id: 'project-tv-001', name: 'Demo Project' }] }))
    await page.route('**/api/projects/project-tv-001/requests', (route) => route.fulfill({ json: [{ id: 'request-drop-001', project_id: 'project-tv-001', title: 'WR-1042 의뢰', status: 'READY' }] }))
    await page.route('**/api/folder-discovery/environments/refresh', (route) => route.fulfill({ json: {
      snapshot_id: 'folder-refresh-1', project_id: 'project-tv-001', request_id: 'request-drop-001', environment: 'DISTRIBUTION',
      status: 'REFRESHED', changed: true, structure_fingerprint: 'structure-1', content_fingerprint: 'content-1',
      diff: { added: 1, removed: 0, changed: 0 },
      nodes: [{ relative_path: 'Project_A/WR_1042_SimType2/Case/Drop/Run/INDIVIDUAL/New Scene', role_kind: 'SCENE', status: 'CONFIRMED', role_basis: 'LEVEL' }],
    } }))
    await loginWorkspace(page, 'e2e-admin', '/workspace/catalog/schemas')
    await page.goto('/workspace/catalog/schemas?project=project-tv-001&request=request-drop-001&result_environment=DISTRIBUTION')
    const screen = page.locator('.folder-environment-workspace')
    const refreshRequest = page.waitForRequest((request) => request.url().endsWith('/api/folder-discovery/environments/refresh'))
    await screen.getByRole('button', { name: 'Refresh', exact: true }).click()
    expect((await refreshRequest).postDataJSON()).toEqual({ project_id: 'project-tv-001', request_id: 'request-drop-001', environment: 'DISTRIBUTION' })
    await screen.getByText('Refresh 역할 판정 · 1개 폴더').click()
    await expect(screen.locator('.folder-preview-row')).toContainText('Scene')
    await expect(screen.locator('.folder-preview-row')).toContainText('LEVEL')
    await page.screenshot({ path: join(tmpdir(), 'folder-schema-refresh-desktop.png'), fullPage: false })
  })

  test('completes choose → review → register flow at desktop and mobile sizes', async ({ page }) => {
    const evidence = join(tmpdir(), 'environment-folder-final-qa')
    mkdirSync(evidence, { recursive: true })
    await page.setViewportSize({ width: 1366, height: 768 })
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.route('**/api/folder-discovery/environments', (route) => route.fulfill({ json: { items: [profile] } }))
    await page.route('**/api/projects', (route) => route.fulfill({ json: [{ id: 'project-tv-001', name: 'Demo Project' }] }))
    await page.route('**/api/projects/project-tv-001/requests', (route) => route.fulfill({ json: [{ id: 'request-drop-001', project_id: 'project-tv-001', title: 'WR-1042 의뢰', status: 'READY' }] }))
    await page.route('**/api/requests/request-drop-001/load-cases', (route) => route.fulfill({ json: [] }))
    await page.route('**/api/folder-discovery/saved-rules?*', (route) => route.fulfill({ json: { items: [], offset: 0, limit: 100, total: 0 } }))
    await page.route('**/api/folder-discovery/environments/history?*', (route) => route.fulfill({ json: { items: [], offset: 0, limit: 50, total: 0 } }))
    await page.route('**/api/folder-discovery/browse?*', (route) => route.fulfill({ json: { configured: true, relative_path: '', entries: [{ name: 'Project_A', relative_path: 'Project_A', is_directory: true }] } }))
    await page.route('**/api/folder-discovery/environments/scan', (route) => route.fulfill({ json: { id: 'scan-1', environment: 'USAGE', profile_id: profile.id, relative_path: 'Project_A', status: 'COMPLETE', nodes, issues: [] } }))
    await page.route('**/api/folder-discovery/environments/previews', (route) => route.fulfill({ json: { id: 'preview-1', scan_id: 'scan-1', environment: 'USAGE', can_apply: true, rows: nodes, summary: { new: 2, existing: 1, conflicts: 0 }, unresolved_count: 0 } }))
    await page.route('**/api/folder-discovery/environments/registrations', (route) => route.fulfill({ json: { registration_id: 'registration-1', preview_id: 'preview-1', environment: 'USAGE', project_id: 'project-tv-001', request_id: 'request-drop-001', relative_path: 'Project_A', status: 'REGISTERED', created_at: '2026-09-19T00:00:00Z', capture_jobs: [{ id: 'job-1', project_id: 'project-tv-001', request_id: 'request-drop-001', case_id: 'case-1', capture_id: 'capture-1', status: 'COMPLETED' }] } }))
    await page.route('**/api/folder-discovery/environments/registrations/registration-1', (route) => route.fulfill({ json: { registration_id: 'registration-1', preview_id: 'preview-1', environment: 'USAGE', project_id: 'project-tv-001', request_id: 'request-drop-001', relative_path: 'Project_A', status: 'REGISTERED', created_at: '2026-09-19T00:00:00Z', capture_jobs: [{ id: 'job-1', project_id: 'project-tv-001', request_id: 'request-drop-001', case_id: 'case-1', capture_id: 'capture-1', status: 'COMPLETED' }] } }))

    await loginWorkspace(page, 'e2e-admin', '/workspace/catalog/schemas')
    await page.goto('/workspace/catalog/schemas?project=project-tv-001&request=request-drop-001')
    const screen = page.locator('.folder-environment-workspace')
    await expect(screen).toContainText('폴더 연결·규칙')
    await screen.locator('input[placeholder="비우면 저장소 전체"]').fill('Project_A')
    await screen.getByRole('button', { name: '조사 시작' }).click()
    await expect(screen).toContainText('구조 확인')
    await page.screenshot({ path: join(evidence, 'structure-desktop.png'), fullPage: true })
    await page.setViewportSize({ width: 390, height: 844 })
    await expect(screen.locator('.folder-tree')).toBeVisible()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: join(evidence, 'structure-mobile.png'), fullPage: true })
    await screen.locator('.folder-node-button').filter({ hasText: 'Assy_RES_Main' }).click()
    await expect(screen.getByLabel('선택 폴더 역할')).toHaveText('해석 Case')
    await expect(screen.locator('.folder-node-detail select[aria-label="선택 폴더 역할"]')).toHaveCount(0)
    await screen.getByRole('button', { name: '폴더 트리', exact: true }).click()
    await expect(screen.locator('.folder-tree')).toBeVisible()
    await page.setViewportSize({ width: 1366, height: 768 })
    await screen.getByRole('button', { name: '등록 내용 확인' }).click()
    await expect(screen).toContainText('등록·결과 확인')
    await screen.getByRole('button', { name: '등록하고 결과 읽기' }).click()
    await expect(screen.getByRole('link', { name: '결과 보기' })).toHaveAttribute('href', /project=project-tv-001.*request=request-drop-001.*view=case_results.*result_environment=USAGE.*case=case-1.*capture=capture-1/)
    await screen.getByRole('button', { name: '등록 이력' }).click()
    await expect(screen).toContainText('현재 저장소의 등록 이력이 없습니다.')
    expect(errors).toEqual([])
    await page.screenshot({ path: join(evidence, 'history-desktop.png'), fullPage: true })

    await page.setViewportSize({ width: 390, height: 844 })
    await expect(screen).toBeVisible()
    await page.screenshot({ path: join(evidence, 'history-mobile.png'), fullPage: true })
  })
})
