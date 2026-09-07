import { expect, test, type Page } from '@playwright/test'
import { revealControl } from './workspace-test-helpers'

const password = 'e2e-validation-password'

type HistoryItem = {
  id: string
  status: string
  source_type: string | null
  source_folder: string | null
  source_run_id: string | null
  conflict_policy: string | null
  outcome_reason: string | null
  operation: string | null
  analysis_run_id: string | null
  replaced_analysis_run_id: string | null
  source_revision: number | null
  created_at: string
  completed_at: string | null
  retryable: boolean
}

const globalAdmin = {
  id: 'e2e-admin',
  username: 'e2e-admin',
  display_name: 'E2E 관리자',
  employee_id: null,
  account_status: 'ACTIVE',
  is_global_admin: true,
  memberships: [],
  company_permissions: ['company.dashboard.view', 'project.data.view', 'report.export'],
  role: 'admin',
}

function historyItem(overrides: Partial<HistoryItem> & Pick<HistoryItem, 'id' | 'status'>): HistoryItem {
  return {
    source_type: 'MASTER_FOLDER_REFRESH',
    source_folder: `master/${overrides.id}/manifest.json`,
    source_run_id: null,
    conflict_policy: 'SKIP',
    outcome_reason: 'SOURCE_RUN_CREATED',
    operation: 'CREATED',
    analysis_run_id: `run-${overrides.id}`,
    replaced_analysis_run_id: null,
    source_revision: 1,
    created_at: '2026-08-25T03:00:00Z',
    completed_at: '2026-08-25T03:01:00Z',
    retryable: false,
    ...overrides,
  }
}

async function loginAsGlobalAdmin(page: Page) {
  // The regular E2E runner creates this dedicated global administrator.  We
  // still assert the authenticated identity before exercising the data route.
  await page.goto('/')
  await page.getByLabel('사용자 이름').fill(globalAdmin.username)
  await page.getByLabel('비밀번호').fill(password)
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '주 메뉴' })).toBeVisible()
}

test('global admin filters selected load-case import history, sees empty state, and retries only retryable jobs', async ({ page }) => {
  let getRequests = 0
  let retryPosts = 0
  const requestedQueries: URL[] = []
  const items = [
    historyItem({ id: 'job-created', status: 'COMPLETED', source_folder: 'master/created/manifest.json' }),
    historyItem({ id: 'job-skipped', status: 'SKIPPED', source_folder: 'master/skipped/manifest.json', operation: 'NOOP', outcome_reason: 'IDENTICAL_COMPLETED' }),
    historyItem({ id: 'job-rejected', status: 'REJECTED', source_folder: 'master/rejected/manifest.json', operation: 'REJECTED', outcome_reason: 'SOURCE_RUN_CHANGED_REJECTED', retryable: true, analysis_run_id: 'run-existing' }),
  ]

  await page.route(/\/api\/load-cases\/[^/]+\/result-imports(?:\?|$)/, (route) => {
    getRequests += 1
    const url = new URL(route.request().url())
    requestedQueries.push(url)
    const requestedStatus = url.searchParams.get('status')
    const visible = requestedStatus ? items.filter((item) => item.status === requestedStatus) : items
    const counts = items.reduce<Record<string, number>>((result, item) => ({ ...result, [item.status]: (result[item.status] ?? 0) + 1 }), {})
    return route.fulfill({ json: { items: visible, total: visible.length, counts } })
  })
  await page.route(/\/api\/result-imports\/job-rejected\/retry$/, async (route) => {
    expect(route.request().method()).toBe('POST')
    retryPosts += 1
    const rejected = items.find((item) => item.id === 'job-rejected')
    if (!rejected) throw new Error('retry fixture is missing')
    // A retry preserves the immutable rejected attempt and records a new
    // completed attempt. This matches the backend's append-only job history.
    items.push(historyItem({
      id: 'job-retry-completed',
      status: 'COMPLETED',
      source_folder: rejected.source_folder,
      source_run_id: rejected.source_run_id,
      operation: 'REPLACED',
      outcome_reason: 'RETRY_COMPLETED',
      analysis_run_id: 'run-retry',
      source_revision: 2,
      retryable: false,
      completed_at: '2026-08-25T03:05:00Z',
    }))
    await new Promise((resolve) => setTimeout(resolve, 150))
    return route.fulfill({ json: { manifest_path: rejected.source_folder, status: 'IMPORTED', load_case_id: 'loadcase-drop-bottom-001', analysis_run_id: 'run-retry' } })
  })

  await loginAsGlobalAdmin(page)
  await page.goto('/workspace/data')
  await expect(page).toHaveURL(/\/workspace\/data(?:\?|$)/)
  await expect(page.getByRole('heading', { name: '해석 데이터 등록', exact: true })).toBeVisible()
  const history = page.getByRole('region', { name: '결과 등록 이력', includeHidden: true })
  await revealControl(history)
  await expect(history).toContainText('3건 결과 등록 이력')
  await expect(history.getByText('master/created/manifest.json', { exact: true })).toBeVisible()
  await expect(history.getByText('master/skipped/manifest.json', { exact: true })).toBeVisible()
  await expect(history.getByText('master/rejected/manifest.json', { exact: true })).toBeVisible()
  await expect(history.getByRole('button', { name: 'job-rejected 결과 등록 재시도', exact: true })).toBeEnabled()
  await expect(history.getByRole('button', { name: 'job-created 결과 등록 재시도', exact: true })).toHaveCount(0)
  await expect.poll(() => getRequests).toBeGreaterThanOrEqual(1)
  expect(requestedQueries[0]?.searchParams.get('limit')).toBe('25')
  expect(requestedQueries[0]?.searchParams.get('offset')).toBe('0')

  const statusFilter = history.getByLabel('결과 등록 이력 상태 필터')
  await statusFilter.selectOption('FAILED')
  await expect(history.getByText('표시할 등록 이력이 없습니다.', { exact: true })).toBeVisible()
  await expect.poll(() => requestedQueries.at(-1)?.searchParams.get('status')).toBe('FAILED')

  await statusFilter.selectOption('REJECTED')
  await expect(history.getByText('master/rejected/manifest.json', { exact: true })).toBeVisible()
  await expect(history.getByRole('button', { name: 'job-rejected 결과 등록 재시도', exact: true })).toBeEnabled()
  await expect.poll(() => requestedQueries.at(-1)?.searchParams.get('status')).toBe('REJECTED')

  const getBeforeRetry = getRequests
  const retryButton = history.getByRole('button', { name: 'job-rejected 결과 등록 재시도', exact: true })
  await retryButton.evaluate((button) => {
    button.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    button.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
  await expect.poll(() => retryPosts).toBe(1)
  // The active REJECTED filter is preserved after the POST. The original
  // rejected attempt remains retryable; the new completed attempt is hidden.
  await expect(history.getByText('master/rejected/manifest.json', { exact: true })).toBeVisible()
  await expect(history.getByRole('button', { name: 'job-rejected 결과 등록 재시도', exact: true })).toBeEnabled()
  await expect.poll(() => getRequests).toBeGreaterThan(getBeforeRetry)
  expect(requestedQueries.at(-1)?.searchParams.get('status')).toBe('REJECTED')

  await statusFilter.selectOption('')
  const retriedAttempt = history.locator('.result-import-history-row').filter({ hasText: 'RETRY_COMPLETED' })
  await expect(retriedAttempt).toBeVisible()
  await expect(retriedAttempt).toContainText('COMPLETED')
  await expect(retriedAttempt.getByRole('button', { name: /결과 등록 재시도/ })).toHaveCount(0)
  await expect(history.getByRole('button', { name: 'job-rejected 결과 등록 재시도', exact: true })).toBeEnabled()
  await expect.poll(() => requestedQueries.at(-1)?.searchParams.has('status')).toBe(false)
})
