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
    await expect(screen.getByLabel('선택 폴더 역할')).toBeVisible()
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
