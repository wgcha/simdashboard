import { expect, test, type Page } from '@playwright/test'
import { loginWorkspace } from './workspace-test-helpers'

// 관리 › 폴더 스키마 › 프로젝트 정리 (contract depth-schema §16). All cleanup APIs are mocked.
const NONE = { user_data: {}, user_data_total: 0, retained_data: {}, retained_total: 0, system_pages: [], acknowledge_reasons: [], requires_acknowledgement: false }
const SYSTEM_PAGES = [
  { id: 'dashboard-chassis-default', name: 'Chassis Rear 영구변형 기본 분석', versions: 1, user_versions: 0 },
  { id: 'dashboard-drop-default', name: 'Open Cell 기본 분석', versions: 3, user_versions: 1 },
]
const ORION = { ...NONE, project_id: 'project-tv-001', name: 'Orion 65 TV 포장 신뢰성', category: 'DEMO', selectable: true, requests: 2, cases: 0, runs: 3,
  retained_data: { runs: 3, results: 120, media: 21 }, retained_total: 144, system_pages: SYSTEM_PAGES, acknowledge_reasons: ['USER_DATA', 'SYSTEM_ANALYSIS_PAGES'], requires_acknowledgement: true }
const LEGACY = { ...NONE, project_id: 'project-legacy-75r9j', name: '75R9J_PV (예전)', category: 'EMPTY', selectable: true, requests: 1, cases: 0, runs: 4,
  retained_data: { runs: 4, results: 52, legacy_links: 3 }, retained_total: 59, acknowledge_reasons: ['RETAINED_DATA'], requires_acknowledgement: true }
const EMPTY = { ...NONE, project_id: 'project-empty', name: '빈 프로젝트', category: 'EMPTY', selectable: true, requests: 0, cases: 0, runs: 0 }
const REGISTERED = { ...NONE, project_id: 'project-registered', name: '75R9J_PV', category: 'REGISTERED', selectable: false, requests: 2, cases: 4, runs: 0 }
const KEEP_PROJECT = { id: 'project-registered', name: '75R9J_PV', product_name: '', description: '', created_at: '2026-10-01T00:00:00' }

const previewBody = {
  items: [{ ...ORION, deletable: true, blockers: [],
    counts: { projects: 1, analysis_requests: 2, load_cases: 3, analysis_runs: 3, dashboards: 3, drop_video_assets: 20, asset_blobs: 20 } }],
  totals: { projects: 1, analysis_requests: 2, load_cases: 3, analysis_runs: 3, dashboards: 3, drop_video_assets: 20, asset_blobs: 20, 'folder_environment_scans.project_id': 1 },
  confirm_token: 'token-1',
}

async function mockBase(page: Page, state: { deleted: boolean }) {
  await page.route('**/api/folder-discovery/environments', (route) => route.fulfill({ json: { items: [] } }))
  await page.route('**/api/folder-discovery/environments/history**', (route) => route.fulfill({ json: { items: [], offset: 0, limit: 50, total: 0 } }))
  await page.route('**/api/folder-discovery/environments/project-cleanup', (route) => route.fulfill({
    json: { items: state.deleted ? [LEGACY, EMPTY, REGISTERED] : [ORION, LEGACY, EMPTY, REGISTERED], demo_project_ids: ['project-tv-001'] } }))
  await page.route('**/api/projects', (route) => route.fulfill({
    json: state.deleted ? [KEEP_PROJECT] : [{ ...KEEP_PROJECT, id: 'project-tv-001', name: 'Orion 65 TV 포장 신뢰성' }, KEEP_PROJECT] }))
  await page.route('**/api/projects/*/requests', (route) => route.fulfill({ json: [] }))
}

async function openCleanup(page: Page) {
  await loginWorkspace(page, 'e2e-admin', '/workspace/catalog/schemas')
  await page.goto('/workspace/catalog/schemas')
  const screen = page.locator('.folder-environment-workspace')
  await screen.getByRole('button', { name: '프로젝트 정리' }).click()
  const table = screen.getByRole('table', { name: '정리할 프로젝트' })
  await expect(table).toBeVisible()
  return { screen, table }
}

test.describe('프로젝트 정리', () => {
  test('데모·등록 없는 프로젝트를 미리보기하고 확인 후 삭제한다', async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 1000 })
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    const state = { deleted: false }
    const requests: Array<{ url: string; body: unknown }> = []
    await mockBase(page, state)
    await page.route('**/api/folder-discovery/environments/project-cleanup/preview', async (route) => {
      requests.push({ url: 'preview', body: route.request().postDataJSON() }); await route.fulfill({ json: previewBody })
    })
    await page.route('**/api/folder-discovery/environments/project-cleanup/delete', async (route) => {
      requests.push({ url: 'delete', body: route.request().postDataJSON() }); state.deleted = true
      await route.fulfill({ json: { deleted: [ORION.project_id], counts: previewBody.totals } })
    })
    const { screen, table } = await openCleanup(page)

    const rows = table.locator('tbody tr')
    await expect(rows).toHaveCount(4)
    await expect(rows.nth(0)).toContainText('데모')
    await expect(rows.nth(0)).toContainText('시스템 분석 페이지 2개')
    await expect(rows.nth(1)).toContainText('등록 없음')
    await expect(rows.nth(1)).toContainText('해석 이력 4 · 결과 값 52 · 예전 연결 3')
    await expect(rows.nth(3)).toContainText('등록됨')
    await expect(rows.nth(3)).toContainText('등록 이력에서 삭제')
    // Default selection: demo projects only; legacy and even empty projects are chosen explicitly; registered is disabled.
    await expect(table.getByLabel(`${ORION.name} 선택`)).toBeChecked()
    await expect(table.getByLabel(`${LEGACY.name} 선택`)).not.toBeChecked()
    await expect(table.getByLabel(`${EMPTY.name} 선택`)).not.toBeChecked()
    await expect(table.getByLabel(`${REGISTERED.name} 선택`)).toBeDisabled()
    await expect(screen.getByText('1개 선택')).toBeVisible()

    await screen.getByRole('button', { name: '미리보기' }).click()
    const dialog = page.getByRole('dialog', { name: '프로젝트 1개 정리' })
    await expect(dialog).toBeVisible()
    await expect(dialog.locator('.folder-delete-totals')).toHaveText('프로젝트 1 · 의뢰 2 · 하중경우 3 · 해석 이력 3 · Case 0 · 분석 페이지 3')
    await expect(dialog).toContainText('SPDM 폴더·파일은 삭제되지 않습니다')
    await dialog.getByText('표별 삭제 건수 8개').click()
    await expect(dialog.getByRole('table', { name: '표별 삭제 건수' })).toContainText('영상')
    await expect(dialog.getByRole('table', { name: '표별 삭제 건수' })).toContainText('폴더 조사 이력(연결만 해제)')
    // System pages and user versions are listed; deletion needs the exact project name.
    const acknowledge = dialog.getByRole('group', { name: '프로젝트 이름 확인' })
    await expect(acknowledge).toContainText('시스템 분석 페이지')
    await expect(acknowledge).toContainText('Open Cell 기본 분석 (버전 3, 사용자 수정 1)')
    const remove = dialog.getByRole('button', { name: '삭제', exact: true })
    await expect(remove).toBeDisabled()
    await acknowledge.getByLabel(`${ORION.name} 이름 입력`).fill('Orion 65')
    await expect(remove).toBeDisabled()
    await acknowledge.getByLabel(`${ORION.name} 이름 입력`).fill(ORION.name)
    await expect(remove).toBeEnabled()
    await page.screenshot({ path: test.info().outputPath('project-cleanup-dialog-1600.png') })

    await remove.click()
    await expect(dialog).toBeHidden()
    await expect(screen.getByRole('status')).toContainText('프로젝트 1개를 정리했습니다. SPDM 폴더·파일은 그대로입니다.')
    await expect(table.locator('tbody tr')).toHaveCount(3)
    expect(requests).toEqual([
      { url: 'preview', body: { project_ids: [ORION.project_id] } },
      { url: 'delete', body: { project_ids: [ORION.project_id], confirm_token: 'token-1', acknowledge_data_project_ids: [ORION.project_id] } },
    ])
    expect(errors).toEqual([])
  })

  test('미리보기 이후 바뀌면 다시 확인하라고 알리고, 차단 사유가 있으면 삭제할 수 없다', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    const state = { deleted: false }
    let previewCalls = 0
    await mockBase(page, state)
    await page.route('**/api/folder-discovery/environments/project-cleanup/preview', async (route) => {
      previewCalls += 1
      if (previewCalls === 1) return route.fulfill({ json: previewBody })
      return route.fulfill({ json: { ...previewBody, confirm_token: 'token-2', items: [{ ...previewBody.items[0], deletable: false,
        blockers: [{ table: 'workflow_runs', id: 'wf-1', reason: 'RUNNING_EXECUTION' }] }] } })
    })
    await page.route('**/api/folder-discovery/environments/project-cleanup/delete', (route) => route.fulfill({
      status: 409, json: { detail: { code: 'DELETE_PREVIEW_STALE', message: '미리보기 이후 데이터가 바뀌었습니다. 다시 확인하세요.' } } }))
    const { screen } = await openCleanup(page)

    await screen.getByRole('button', { name: '미리보기' }).click()
    const dialog = page.getByRole('dialog', { name: '프로젝트 1개 정리' })
    await dialog.getByLabel(`${ORION.name} 이름 입력`).fill(ORION.name)
    await dialog.getByRole('button', { name: '삭제', exact: true }).click()
    await expect(dialog.getByRole('alert')).toContainText('미리보기 이후 데이터가 바뀌었습니다. 닫고 다시 미리보기를 눌러 주세요.')
    await expect(dialog.getByRole('button', { name: '삭제', exact: true })).toBeDisabled()
    await dialog.getByRole('button', { name: '취소' }).click()
    await expect(dialog).toBeHidden()

    await screen.getByRole('button', { name: '미리보기' }).click()
    await expect(dialog.locator('.folder-delete-blockers')).toContainText('실행 중인 작업이 있습니다.')
    await expect(dialog.getByRole('button', { name: '삭제', exact: true })).toBeDisabled()
  })
})
