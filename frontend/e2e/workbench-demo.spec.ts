import { expect, test, type Page } from '@playwright/test'

async function login(page: Page, role: 'admin' | 'viewer' = 'admin') {
  await page.goto('/')
  await page.getByLabel('사용자 이름').fill(role === 'admin' ? 'e2e-admin' : 'e2e-viewer')
  await page.getByLabel('비밀번호').fill('e2e-validation-password')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByRole('link', { name: '운영 대시보드', exact: true })).toBeVisible()
}

test('DOE 의뢰를 접수하고 배정 작업을 명시적으로 시작·완료한다', async ({ page }) => {
  await login(page)
  const title = `E2E DOE 의뢰 ${Date.now()}`

  await page.getByRole('link', { name: '의뢰 접수', exact: true }).click()
  await expect(page.getByTestId('request-intake-page')).toBeVisible()
  await page.getByRole('button', { name: /설계 DOE 탐색/ }).click()

  const preview = page.locator('.intake-work-preview')
  await expect(preview.locator('strong')).toHaveText([
    'CAD 작업',
    'DOE 파일 생성',
    'HPC 수행',
    '결과 후처리',
    '최적화 분석',
    '해석 DB 저장',
    '설계 성능 순위 평가',
  ])

  await page.getByLabel('의뢰 출처 상세').fill('E2E PLM Gateway')
  await page.locator('label').filter({ hasText: '요청자' }).locator('input').fill('E2E 요청자')
  await page.locator('label').filter({ hasText: '의뢰 제목' }).locator('input').fill(title)
  await expect(page.getByLabel('담당 수행자')).not.toHaveValue('')
  await page.getByTestId('submit-request-intake').click()

  const success = page.getByTestId('intake-success')
  await expect(success).toContainText(`${title} 접수 완료`)
  await expect(success).toContainText('설계 DOE 탐색 · 상태 READY · 진행률 0%')
  await success.getByRole('button', { name: '배정 작업 열기' }).click()

  const workbench = page.getByTestId('simulation-workbench')
  await expect(workbench).toBeVisible()
  await expect(page.getByLabel('배정 작업 대상 의뢰').locator('option:checked')).toContainText(title)
  await expect(page.locator('.assigned-request-card')).toContainText(title)
  await expect(page.locator('.assigned-request-card')).toContainText('0 / 7')
  await expect(page.locator('.assigned-work-list article.ready')).toContainText('CAD 작업')
  await expect(page.locator('.assigned-work-list article.waiting')).toHaveCount(6)
  await expect(page.getByTestId('task-execution-widget-CAD_PREPARE')).toContainText('형상 준비 실행 위젯')
  await expect(page.getByTestId('execute-selected-task')).toHaveText(/형상 준비 시작/)

  await page.locator('.assigned-work-list article.waiting').first().click()
  await expect(page.getByTestId('task-execution-widget-DOE_GENERATE')).toContainText('DOE 생성 실행 위젯')
  await expect(page.getByTestId('execute-selected-task')).toBeDisabled()
  await expect(page.getByText('현재 순서의 작업을 완료한 뒤 실행할 수 있습니다.')).toBeVisible()
  await page.locator('.assigned-work-list article.ready').click()

  await page.getByTestId('start-current-work').click()
  await expect(page.getByTestId('complete-current-work')).toBeVisible()
  await expect(page.locator('.assigned-work-list article.in_progress')).toContainText('CAD 작업')
  await expect(page.getByTestId('execute-selected-task')).toHaveText(/형상 준비 데모 실행·완료/)
  await page.getByLabel('작업 진행도').fill('20')
  await page.getByRole('button', { name: '진행도 저장' }).click()
  await expect(page.locator('.assigned-work-list article.in_progress')).toContainText('20%')

  await page.getByTestId('complete-current-work').click()
  await expect(page.locator('.assigned-request-card')).toContainText('1 / 7')
  await expect(page.locator('.assigned-work-list article.completed')).toContainText('CAD 작업')
  await expect(page.locator('.assigned-work-list article.ready')).toContainText('DOE 파일 생성')
  await expect(page.getByTestId('start-current-work')).toHaveText(/작업 시작/)
  await expect(page.locator('.workbench-run-detail')).toContainText('CAD 작업 데모 수행')
  await expect(page.getByLabel('텍스트 데모 파일 내용')).toBeVisible()

  await page.getByRole('link', { name: '해석 의뢰 현황', exact: true }).click()
  await expect(page.getByTestId('selected-request-progress-tab')).toContainText('14%')
  const monitoringLane = page.locator('.workflow-lane').filter({ hasText: title })
  await expect(monitoringLane).toContainText('1 / 7 작업 완료')
  await expect(monitoringLane.locator('.workflow-lane-meta')).toContainText('14%')

  await page.getByRole('link', { name: '운영 대시보드', exact: true }).click()
  const portfolioRow = page.locator('.portfolio-row').filter({ hasText: title })
  await expect(portfolioRow).toContainText('14%')
  await expect(portfolioRow).toContainText('DOE 파일 생성')

  await page.getByRole('link', { name: '해석 작업 실행', exact: true }).click()
  await page.getByTestId('start-current-work').click()
  await page.getByTestId('complete-current-work').click()
  await expect(page.locator('.assigned-work-list article.ready')).toContainText('HPC 수행')
  await page.getByTestId('start-current-work').click()
  await page.locator('.assigned-work-list article.in_progress').click()
  await expect(page.getByLabel('배치 경로 프로필')).toBeVisible()
  await page.getByRole('button', { name: '배치 실행 기록 생성' }).click()

  const attempt = page.locator('.batch-attempt-list article').first()
  await expect(attempt).toContainText('SUCCEEDED · 100%')
  await expect(attempt.getByRole('progressbar')).toHaveAttribute('value', '100')
  await expect(attempt.getByRole('list', { name: '배치 상태 이벤트' })).toContainText('PREFLIGHT · 0%')
  await expect(attempt.getByRole('list', { name: '배치 상태 이벤트' })).toContainText('QUEUED · 10%')
  await expect(attempt.getByRole('list', { name: '배치 상태 이벤트' })).toContainText('SUCCEEDED · 100%')
  await expect(attempt.getByLabel('실행 시점 배치 명령 snapshot')).toBeVisible()
})

test('관리자가 제공한 활성 업무 유형을 선택하고 선택 당시 버전으로 접수한다', async ({ page }) => {
  await login(page)
  const suffix = Date.now()
  const requestTypeId = `custom-intake-e2e-${suffix}`
  const title = `E2E 사용자 정의 의뢰 ${suffix}`
  const requestType = {
    id: requestTypeId,
    display_name: 'E2E 관리자 제공 검토',
    description: '수행자에게 제공하는 사용자 정의 검토 시나리오',
    allowed_task_types: [{ id: 'cad-prepare', version: 1 }],
    default_workflow: {
      nodes: [{
        node_key: 'review-cad',
        task_type_id: 'cad-prepare',
        task_type_version: 1,
        depends_on: [],
      }],
    },
    match_rules: {},
    is_active: true,
  }
  const versionOne = await page.request.post('/api/admin/workbench/request-types', { data: requestType })
  expect(versionOne.status(), await versionOne.text()).toBe(201)

  await page.getByRole('link', { name: '의뢰 접수', exact: true }).click()
  await expect(page.getByTestId('request-intake-page')).toBeVisible()
  await expect(page.getByTestId('active-request-type-count')).toHaveText('3')

  const option = page.getByTestId(`request-type-option-${requestTypeId}-1`)
  await expect(option).toContainText('E2E 관리자 제공 검토')
  await expect(option).toContainText('WORK TYPE · v1')
  await option.click()
  await expect(option).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('.intake-work-preview strong')).toHaveText(['cad-prepare'])
  await expect(page.locator('.intake-summary')).toContainText('E2E 관리자 제공 검토 · v1')

  const versionTwo = await page.request.post('/api/admin/workbench/request-types', {
    data: { ...requestType, display_name: 'E2E 관리자 제공 검토 변경본' },
  })
  expect(versionTwo.status(), await versionTwo.text()).toBe(201)
  await expect(option).toHaveAttribute('aria-pressed', 'true')

  await page.getByLabel('의뢰 출처 상세').fill('E2E SPDM Gateway')
  await page.locator('label').filter({ hasText: '요청자' }).locator('input').fill('E2E 요청자')
  await page.locator('label').filter({ hasText: '의뢰 제목' }).locator('input').fill(title)
  await expect(page.getByLabel('담당 수행자')).not.toHaveValue('')
  await page.getByTestId('submit-request-intake').click()

  const success = page.getByTestId('intake-success')
  await expect(success).toContainText(`${title} 접수 완료`)
  await expect(success).toContainText('E2E 관리자 제공 검토 · 상태 READY · 진행률 0%')
  await expect(success).not.toContainText('변경본')
})

test('Viewer는 배정 작업과 진행 상태를 보되 시작·완료할 수 없다', async ({ page }) => {
  await login(page, 'viewer')
  await page.getByRole('link', { name: '해석 작업 실행', exact: true }).click()
  await expect(page.getByTestId('simulation-workbench')).toBeVisible()
  await expect(page.getByRole('link', { name: '작업 유형 관리', exact: true })).toHaveCount(0)

  const start = page.getByTestId('start-current-work')
  const complete = page.getByTestId('complete-current-work')
  if (await start.count()) await expect(start).toBeDisabled()
  if (await complete.count()) await expect(complete).toBeDisabled()
  await expect(page.getByText(/작업 담당자\(.+\)만 실행할 수 있습니다\./)).toBeVisible()
  await expect(page.getByLabel('배치 명령 미리보기')).toHaveCount(0)
})

test('작업 유형 관리에서 배치 경로 정의를 별도 내부 탭으로 연다', async ({ page }) => {
  await login(page)
  await page.getByRole('link', { name: '작업 유형 관리', exact: true }).click()

  const requestTypesTab = page.getByRole('tab', { name: /^작업 유형/ })
  const batchPathsTab = page.getByRole('tab', { name: /배치 경로 정의/ })
  await expect(requestTypesTab).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('tabpanel', { name: /^작업 유형/ })).toBeVisible()
  await expect(page.getByRole('form', { name: '배치 경로 프로필 편집' })).toHaveCount(0)

  await batchPathsTab.click()
  await expect(batchPathsTab).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('tabpanel', { name: /배치 경로 정의/ })).toBeVisible()
  await expect(page.getByRole('form', { name: '배치 경로 프로필 편집' })).toBeVisible()
  await expect(page.getByLabel('Solver 실행 파일')).toBeVisible()

  await batchPathsTab.press('ArrowLeft')
  await expect(requestTypesTab).toBeFocused()
  await expect(requestTypesTab).toHaveAttribute('aria-selected', 'true')
})

test('새 작업 유형 ID 규칙을 안내하고 기본 실행 방식을 읽기 쉽게 선택한다', async ({ page }) => {
  await login(page)
  await page.getByRole('link', { name: '작업 유형 관리', exact: true }).click()

  const form = page.getByRole('form', { name: '새 작업 유형 작성' })
  const typeId = form.getByLabel('유형 ID')
  await expect(form).toContainText('{domain}-{purpose}')
  await expect(form).toContainText('drop-reliability-validation')

  await typeId.fill('99 Crash / Safety ++')
  await typeId.blur()
  await expect(typeId).toHaveValue('type-99-crash-safety')
  await expect(typeId).toHaveAttribute('aria-invalid', 'false')

  await typeId.fill('ab')
  await typeId.blur()
  await expect(typeId).toHaveAttribute('aria-invalid', 'true')
  await expect(form.getByRole('button', { name: '새 작업 유형 저장' })).toBeDisabled()

  await form.getByLabel('관리 유형 표시 이름').fill('Reliability Validation')
  await form.getByLabel('관리 자동 추천 분석 유형').fill('DROP')
  await form.getByRole('button', { name: 'ID 추천' }).click()
  await expect(typeId).toHaveValue('drop-reliability-validation')
  await expect(typeId).toHaveAttribute('aria-invalid', 'false')

  await expect(form.getByRole('radio', { name: /순차 실행/ })).toBeChecked()
  await expect(form.getByText('이전 작업 완료 후 다음 작업을 시작합니다.')).toBeVisible()
  await form.getByRole('radio', { name: /독립·병렬 실행/ }).check()
  await expect(form.getByText('선행 관계 없이 선택한 작업을 각각 시작할 수 있습니다.')).toBeVisible()
})

test('작업 유형을 라벨과 시나리오로 편집하고 접수 필터에서 선택한 뒤 삭제한다', async ({ page }) => {
  await login(page)
  const suffix = Date.now()
  const requestTypeId = `labeled-work-e2e-${suffix}`
  const displayName = `E2E 라벨 작업 ${suffix}`

  await page.getByRole('link', { name: '작업 유형 관리', exact: true }).click()
  const createForm = page.getByRole('form', { name: '새 작업 유형 작성' })
  await createForm.getByLabel('유형 ID').fill(requestTypeId)
  await createForm.getByLabel('관리 유형 표시 이름').fill(displayName)
  await createForm.getByLabel('관리 유형 설명').fill('초기 단일 작업 시나리오')
  await createForm.getByLabel('작업 유형 라벨 *').fill('충돌해석')
  await createForm.getByLabel('작업 유형 라벨 *').press('Enter')
  await page.locator('.workbench-admin-task-picker').getByRole('button', { name: /CAD\/형상 준비/ }).click()
  await createForm.getByRole('button', { name: '새 작업 유형 저장' }).click()
  await expect(page.locator('.workbench-admin-notice')).toContainText(`${displayName} v1`)

  let typeCard = page.locator('.workbench-admin-types article').filter({ hasText: displayName })
  await expect(typeCard).toContainText('#SPDM')
  await expect(typeCard).toContainText('#부서')
  await expect(typeCard).toContainText('#충돌해석')
  await typeCard.getByRole('button', { name: '편집', exact: true }).click()

  const editForm = page.getByRole('form', { name: '작업 유형 편집' })
  await expect(editForm.getByLabel('유형 ID')).toBeDisabled()
  await editForm.getByLabel('관리 유형 설명').fill('DOE 작업을 추가한 편집 시나리오')
  await editForm.getByRole('button', { name: '부서 라벨 삭제' }).click()
  await page.locator('.workbench-admin-task-picker').getByRole('button', { name: /DOE 생성/ }).click()
  await editForm.getByRole('button', { name: '변경 내용을 새 버전으로 저장' }).click()
  await expect(page.locator('.workbench-admin-notice')).toContainText(`${displayName} v2`)

  typeCard = page.locator('.workbench-admin-types article').filter({ hasText: displayName })
  await expect(typeCard).toContainText(`${requestTypeId} · v2`)
  await expect(typeCard).toContainText('DOE 작업을 추가한 편집 시나리오')
  await expect(typeCard).not.toContainText('#부서')
  await expect(typeCard).toContainText('DOE 생성')

  await page.getByRole('link', { name: '의뢰 접수', exact: true }).click()
  const labelFilter = page.getByTestId('request-type-label-filter-충돌해석')
  await expect(labelFilter).toBeVisible()
  await labelFilter.click()
  await expect(page.locator('.intake-scenario-options button')).toHaveCount(1)
  await expect(page.getByTestId(`request-type-option-${requestTypeId}-2`)).toContainText(displayName)

  await page.getByRole('link', { name: '작업 유형 관리', exact: true }).click()
  typeCard = page.locator('.workbench-admin-types article').filter({ hasText: displayName })
  page.once('dialog', (dialog) => dialog.accept())
  await typeCard.getByRole('button', { name: '삭제', exact: true }).click()
  await expect(page.locator('.workbench-admin-notice')).toContainText('과거 버전 이력은 보존됩니다')
  await expect(typeCard).toHaveCount(0)

  await page.getByRole('link', { name: '의뢰 접수', exact: true }).click()
  await expect(page.getByTestId('request-type-label-filter-충돌해석')).toHaveCount(0)
  await expect(page.getByTestId(`request-type-option-${requestTypeId}-2`)).toHaveCount(0)
})

test('작업 유형 선택 카드를 3열 3행 이후 내부 스크롤로 탐색한다', async ({ page }) => {
  await login(page)
  const suffix = Date.now()
  const ids = Array.from({ length: 10 }, (_, index) => `scroll-work-e2e-${suffix}-${index}`)
  for (const [index, id] of ids.entries()) {
    const response = await page.request.post('/api/admin/workbench/request-types', {
      data: {
        id,
        display_name: `스크롤 검증 작업 ${index + 1}`,
        description: '3열 3행 스크롤 검증용 작업 유형',
        allowed_task_types: [{ id: 'cad-prepare', version: 1 }],
        default_workflow: { nodes: [{ node_key: 'prepare', task_type_id: 'cad-prepare', task_type_version: 1, depends_on: [] }] },
        match_rules: { labels: ['스크롤검증'] },
        is_active: true,
      },
    })
    expect(response.status(), await response.text()).toBe(201)
  }

  await page.getByRole('link', { name: '의뢰 접수', exact: true }).click()
  await page.getByTestId('request-type-label-filter-스크롤검증').click()
  const options = page.locator('.intake-scenario-options')
  await expect(options.getByRole('button')).toHaveCount(10)
  const layout = await options.evaluate((element) => ({
    columns: getComputedStyle(element).gridTemplateColumns.split(' ').length,
    overflowY: getComputedStyle(element).overflowY,
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }))
  expect(layout.columns).toBe(3)
  expect(layout.overflowY).toBe('auto')
  expect(layout.scrollHeight).toBeGreaterThan(layout.clientHeight)

  for (const id of ids) {
    const response = await page.request.delete(`/api/admin/workbench/request-types/${id}`)
    expect(response.status(), await response.text()).toBe(200)
  }
})
