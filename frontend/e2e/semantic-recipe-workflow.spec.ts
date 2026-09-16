import { expect, test, type Page } from '@playwright/test'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { loginWorkspace } from './workspace-test-helpers'

async function createReviewTarget(page: Page, withSnapshot: boolean) {
  const suffix = Date.now()
  let resultProfile: Record<string, unknown> | undefined
  if (withSnapshot) {
    const templateId = `semantic-review-${suffix}`
    const templateResponse = await page.request.post('/api/admin/workbench/analysis-templates', { data: {
      id: templateId, display_name: 'Existing request result layout', description: 'Exact Run snapshot regression',
      lifecycle_status: 'PUBLISHED', scope_kind: 'SYSTEM',
      page_definitions: [{ id: 'semantic-review-summary', name: '기존 결과 구성', description: '', widgets: [
        { id: 'semantic-review-summary', type: 'summary', title: '기존 실행 요약', x: 0, y: 0, w: 6, h: 3, settings: {} },
      ] }],
    } })
    expect(templateResponse.status(), await templateResponse.text()).toBe(201)
    resultProfile = { template_id: templateId, template_version: 1, included_widget_ids: null, overrides: {}, required_data_contracts: [] }
  }
  const typeResponse = await page.request.post('/api/admin/workbench/request-types', { data: {
    display_name: `Semantic review ${suffix}`, description: 'Recipe widgets without a request layout',
    allowed_task_types: [{ id: 'cad-prepare', version: 1 }],
    default_workflow: { nodes: [{ node_key: 'prepare', task_type_id: 'cad-prepare', task_type_version: 1, depends_on: [] }] },
    match_rules: { labels: ['semantic-review'] }, is_active: true,
    ...(resultProfile ? { result_profile: resultProfile } : {}),
  } })
  expect(typeResponse.status(), await typeResponse.text()).toBe(201)
  const requestType = await typeResponse.json()
  const candidates = await page.request.get('/api/projects/project-tv-001/assignee-candidates')
  expect(candidates.ok()).toBeTruthy()
  const owner = (await candidates.json())[0].user_id
  const requestResponse = await page.request.post('/api/projects/project-tv-001/requests', { data: {
    title: `Semantic review ${suffix}`, owner_user_id: owner, due_in_days: 14,
    overall_note: 'No layout required for recipe widgets', source_type: 'EXTERNAL_SYSTEM',
    source_reference: 'semantic-e2e', requested_by: 'E2E',
    request_type_id: requestType.id, request_type_version: requestType.version,
  } })
  expect(requestResponse.status(), await requestResponse.text()).toBe(201)
  const request = await requestResponse.json()
  const caseResponse = await page.request.post(`/api/requests/${request.id}/load-cases`, {
    data: { name: 'Representative CSV', analysis_type: 'DROP', parameters: {} },
  })
  expect(caseResponse.status(), await caseResponse.text()).toBe(201)
  const loadCase = await caseResponse.json()
  return { requestId: request.id as string, loadCaseId: loadCase.id as string }
}

for (const extension of ['csv', 'json']) {
  test(`issue ${extension} sample: item editing, widgets, atomic save, reopen and Run`, async ({ page }, testInfo) => {
    test.setTimeout(240_000)
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    page.on('console', (message) => { if (message.type() === 'error' && !message.text().includes('401')) errors.push(message.text()) })
    await loginWorkspace(page, 'e2e-admin', '/workspace/catalog/schemas')
    const target = await createReviewTarget(page, extension === 'json')
    await page.goto('/workspace/catalog/schemas')
    await expect(page).toHaveTitle(/.+/)
    const screen = page.locator('.semantic-page')
    await expect(screen.getByRole('heading', { name: '결과 의미 연결' })).toBeVisible()
    const inspector = screen.getByTestId('semantic-sample-inspector')
    const filename = extension === 'csv' ? 'issue_23.csv' : 'issue_24.json'
    const sample = { name: filename, mimeType: extension === 'csv' ? 'text/csv' : 'application/json', buffer: readFileSync(`../backend/tests/fixtures/semantic_samples/${filename}`) }
    await inspector.locator('input[type=file]').setInputFiles(sample)
    await expect(inspector).toContainText('-311.643')
    await expect(screen.getByLabel('원본 필드', { exact: true })).toHaveCount(0)
    const connection = inspector.getByRole('button', { name: /선택 값을 결과 항목으로 연결/ }); await expect(connection).toContainText(/\(\d+\)/); const itemCount = Number((await connection.innerText()).match(/\((\d+)\)/)![1]); expect(itemCount).toBe(35)
    await inspector.getByRole('button', { name: /선택 값을 결과 항목으로 연결/ }).click()
    const mappings = screen.getByLabel('원본 필드', { exact: true })
    await expect(mappings).toHaveCount(itemCount, { timeout: 90_000 })
        const topRow = screen.locator('.mapping-row').filter({ has: page.locator('input[aria-label="원본 필드"][value="#/Set Top Disp. (mm)"]') })
    const firstId = await topRow.getByLabel('결과 항목', { exact: true }).inputValue()
    expect(firstId).toBeTruthy()
    await screen.getByRole('button', { name: '매핑 복제', exact: true }).first().click()
    await expect(mappings).toHaveCount(itemCount + 1)
    await screen.getByRole('button', { name: '매핑 삭제', exact: true }).first().click()
    await expect(mappings).toHaveCount(itemCount)
    await screen.getByRole('button', { name: '삭제 되돌리기', exact: true }).click()
    await expect(mappings).toHaveCount(itemCount + 1)
    await screen.getByRole('button', { name: '매핑 삭제', exact: true }).first().click()
    await expect(mappings).toHaveCount(itemCount)

    await topRow.getByTitle('결과 항목 수정', { exact: true }).click()
    const dialog = page.getByRole('dialog', { name: '결과 항목 정의' })
    await dialog.getByLabel('표시명', { exact: true }).fill('상단 변위')
    await dialog.getByRole('button', { name: '결과 항목 저장', exact: true }).click()
    await expect(dialog).toBeHidden()
    await expect(topRow.getByLabel('결과 항목', { exact: true })).toHaveValue(firstId)
    await screen.getByRole('button', { name: '전체 항목 요약표 추가', exact: true }).click()
    await screen.getByRole('button', { name: '위젯 추가', exact: true }).click()
    const editors = screen.locator('.semantic-widget-editor')
    await expect(editors).toHaveCount(2)
    const summaryPicker = editors.first().getByRole('region', { name: '위젯 결과 항목' })
    await expect(summaryPicker.getByLabel('위젯 결과 항목 검색')).toBeVisible()
    await expect(summaryPicker).toContainText(`${itemCount}개 적용`)
    const removeFromSummary = summaryPicker.getByRole('group', { name: '적용 항목' }).getByRole('button', { name: / 제외$/ }).first()
    const removeName = await removeFromSummary.getAttribute('aria-label')
    expect(removeName).toBeTruthy()
    const restoredName = removeName!.replace(/ 제외$/, ' 추가')
    await removeFromSummary.click()
    await expect(summaryPicker.getByRole('button', { name: restoredName, exact: true })).toBeVisible()
    await summaryPicker.getByRole('button', { name: restoredName, exact: true }).click()
    await expect(summaryPicker).toContainText(`${itemCount}개 적용`)
    await editors.last().getByLabel('위젯 종류').selectOption('kpi')
    await editors.last().locator('select[aria-label="위젯 결과 항목"]').selectOption(firstId)
    await editors.last().getByLabel('위젯 종류').selectOption('kpi')
    const widgetTypes = await editors.last().getByLabel('위젯 종류').locator('option').evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value))
    expect(widgetTypes.sort()).toEqual(['bar', 'gauge', 'image', 'kpi', 'line', 'name_value', 'scatter', 'table', 'video'])
    await expect(editors.last().getByLabel('위젯 제목')).toHaveValue('상단 변위')
    await expect(editors.last().getByLabel('위젯 종류').locator('option[value=line]')).toHaveAttribute('disabled', '')
    await editors.first().getByLabel('표시 자릿수').fill('3')
    await editors.last().getByLabel('표시 자릿수').fill('3')
    const preview = screen.locator('.preview-panel')
    await expect(preview.locator('.semantic-result-widget.ready')).toHaveCount(2)
    await expect(preview.locator('.semantic-result-kpi strong')).toHaveText('-311.643')
    await expect(preview.locator('.semantic-result-table tbody tr')).toHaveCount(itemCount)
    const coordinate = preview.locator('.semantic-result-table tbody tr').filter({ hasText: 'Set CG Coord.' }).filter({ hasNotText: '@' })
    await expect(coordinate).toContainText('Z: -159.909')
    await expect(coordinate).toContainText('mm')
    await expect(preview).toContainText(/0\s*curve/)
    const summary = preview.locator('.parsed-summary')
    await expect(summary.locator('span').filter({ hasText: /scalar/ })).toHaveText(extension === 'csv' ? '21 scalar' : '25 scalar')
    await expect(summary.locator('span').filter({ hasText: /vector/ })).toHaveText(extension === 'csv' ? '14 vector' : '10 vector')
    await expect(summary.locator('span').filter({ hasText: /observations/ })).toHaveText('35 observations')
    await screen.getByRole('button', { name: '위젯 추가', exact: true }).click()
    await expect(editors).toHaveCount(3)
    const componentEditor = editors.last()
    await componentEditor.getByLabel('위젯 종류').selectOption('kpi')
    const picker = componentEditor.locator('select[aria-label="위젯 결과 항목"]')
    const vectorOption = await picker.locator('option').evaluateAll((options) => options.map((option) => ({ value: (option as HTMLOptionElement).value, label: option.textContent ?? '' })).find((option) => option.label.includes('Set CG Coord.') && !option.label.includes('@')))
    expect(vectorOption).toBeTruthy()
    await picker.selectOption(vectorOption!.value)
    await expect(componentEditor.getByLabel('위젯 종류')).toHaveValue('table')
    await componentEditor.getByLabel('위젯 종류').selectOption('name_value')
    await componentEditor.getByLabel('벡터 표시 성분').selectOption('Z')
    await expect(componentEditor.getByLabel('이름·값 차트 스타일')).toHaveValue('bar')
    await expect(preview.locator('.type-name_value .recharts-bar-rectangle')).toHaveCount(1)
    await componentEditor.getByLabel('이름·값 차트 스타일').selectOption('dot')
    await expect(preview.locator('.type-name_value .recharts-line-dot')).toHaveCount(1)
    await componentEditor.getByLabel('이름·값 차트 스타일').selectOption('line')
    await expect(componentEditor.getByLabel('이름·값 차트 스타일')).toHaveValue('line')
    await expect(preview.locator('.type-name_value .recharts-line-dot')).toHaveCount(1)
    await componentEditor.getByLabel('표시 자릿수').fill('3')
    await expect(preview.locator('.semantic-result-widget.ready')).toHaveCount(3)
    await expect(preview.locator('.semantic-result-kpi strong')).toHaveText('-311.643')
    const suffix = `${extension}-${Date.now()}`
    const recipeName = `Issue ${suffix}`
    const templateName = `Issue widgets ${suffix}`
    await screen.getByLabel('레시피 이름', { exact: true }).fill(recipeName)
    await screen.getByLabel('템플릿 이름', { exact: true }).fill(templateName)
    const saving = page.waitForResponse((response) => response.url().endsWith('/semantic-mapping/configurations') && response.request().method() === 'POST')
    await screen.getByRole('button', { name: '결과 설정 저장', exact: true }).click()
    const response = await saving
    expect(response.status(), await response.text()).toBe(201)
    const saved = await response.json()
    expect(saved.recipe.definition.mappings).toHaveLength(itemCount)
    expect(saved.template.definition.widgets).toHaveLength(3)
    expect(saved.template.definition.widgets[2].vector_component).toBe('Z')
    expect(saved.template.definition.widgets[2].type).toBe('name_value')
    expect(saved.template.definition.widgets[2].chart_style).toBe('line')
    expect(saved.recipe.definition.display_template_id).toBe(saved.template.id)
    await expect(preview.locator('.semantic-result-widget.ready')).toHaveCount(3)
    const impact = page.waitForResponse((response) => response.url().endsWith('/semantic-mapping/impact-bundle'))
    await screen.getByRole('button', { name: /활성화 영향 미리보기/ }).click()
    expect((await impact).status()).toBe(200)
    const activated = page.waitForResponse((response) => response.url().endsWith('/semantic-mapping/activate-bundle'))
    await page.getByRole('dialog', { name: '활성화 영향 미리보기' }).getByRole('button', { name: '검토 후 활성화', exact: true }).click()
    expect((await activated).status()).toBe(200)
    await screen.getByRole('button', { name: '2. 폴더 연결', exact: true }).click()
    await screen.getByLabel('프로젝트', { exact: true }).selectOption('project-tv-001')
    await screen.getByLabel('의뢰', { exact: true }).selectOption(target.requestId)
    await screen.getByLabel('하중 경우', { exact: true }).selectOption(target.loadCaseId)
    await screen.getByRole('checkbox', { name: new RegExp(recipeName) }).check()
    // The recipe's saved display template is sufficient; no second selection is required.
    let runId: string
    if (extension === 'csv') {
      const storageRoot = path.resolve('../output/qa/recipe-folder')
      const directory = `issue-${suffix}`
      mkdirSync(path.join(storageRoot, directory), { recursive: true })
      writeFileSync(path.join(storageRoot, directory, filename), sample.buffer)
      writeFileSync(path.join(storageRoot, directory, 'same-format.txt'), sample.buffer)
      writeFileSync(path.join(storageRoot, directory, 'unrelated.csv'), 'other,value\nhello,1\n')
      const storage = await page.request.put('/api/storage/config', { data: { root: storageRoot } })
      expect(storage.status(), await storage.text()).toBe(200)
      await screen.getByLabel('폴더 상대 경로', { exact: true }).fill(directory)
      const savingBinding = page.waitForResponse((response) => response.url().endsWith('/semantic-mapping/bindings') && response.request().method() === 'POST')
      const processing = page.waitForResponse((response) => response.url().endsWith(`/semantic-mapping/load-cases/${target.loadCaseId}/results/refresh`))
      await screen.getByRole('button', { name: '연결 저장', exact: true }).click()
      const bound = await savingBinding
      expect(bound.ok(), await bound.text()).toBeTruthy()
      expect((await bound.json()).id).toBeTruthy()
      const processed = await processing
      expect(processed.status(), await processed.text()).toBe(200)
      const results = (await processed.json()).results as Array<{ relative_path: string; run_id: string; status: string; review_available: boolean; diagnostics?: unknown[] }>
      const original = results.find((row) => row.relative_path === filename)!
      expect(original.status).toBe('IMPORTED')
      expect(original.review_available).toBe(true)
      runId = original.run_id
      expect(results.find((row) => row.relative_path === 'same-format.txt')!.review_available).toBe(true)
      expect(results.find((row) => row.relative_path === 'unrelated.csv')!.status).toBe('UNMAPPED')
      await expect(screen.getByRole('row').filter({ hasText: 'unrelated.csv' })).not.toContainText('run-')
      await expect(screen.getByRole('row').filter({ hasText: 'unrelated.csv' })).toContainText('후보 오류')
      await screen.getByRole('row').filter({ hasText: filename }).scrollIntoViewIfNeeded()
      await page.screenshot({ path: testInfo.outputPath('issue-csv-folder-processed.png') })
      const refreshing = page.waitForResponse((response) => response.url().endsWith(`/load-cases/${target.loadCaseId}/results/refresh`))
      await screen.locator('.binding-list article').filter({ hasText: directory }).getByRole('button', { name: '새로고침', exact: true }).click()
      const repeated = (await (await refreshing).json()).results
      expect(repeated.find((row: { relative_path: string }) => row.relative_path === filename).status).toBe('SKIPPED')
    } else {
      await screen.getByLabel('등록할 결과 파일').setInputFiles(sample)
      const importing = page.waitForResponse((response) => response.url().endsWith('/semantic-mapping/import'))
      await screen.getByRole('button', { name: '단일 파일 가져오기', exact: true }).click()
      const imported = await importing
      expect(imported.status(), await imported.text()).toBe(200)
      const result = await imported.json()
      expect(result.review_available).toBe(true)
      runId = result.run_id
    }
    // A later result must not change the completed import button's destination.
    const laterBuffer = Buffer.from(sample.buffer.toString().replace('-311.643', '-123.456'))
    const laterImport = await page.request.post('/api/semantic-mapping/import', { multipart: {
      file: { ...sample, buffer: laterBuffer }, recipe_id: saved.recipe.id,
      load_case_id: target.loadCaseId, template_id: saved.template.id,
    } })
    expect(laterImport.status(), await laterImport.text()).toBe(200)
    const laterRunId = (await laterImport.json()).run_id as string
    expect(laterRunId).not.toBe(runId)
    const runsResponse = await page.request.get(`/api/load-cases/${target.loadCaseId}/runs`)
    expect(runsResponse.ok(), await runsResponse.text()).toBeTruthy()
    const runNo = (await runsResponse.json()).find((run: { id: string }) => run.id === runId).run_no
    const reviewLink = extension === 'csv'
      ? screen.getByRole('row').filter({ hasText: filename }).getByRole('link', { name: '결과 검토', exact: true })
      : screen.getByLabel('등록한 결과', { exact: true }).getByRole('link', { name: '결과 검토', exact: true })
    await expect(reviewLink).toBeVisible()
    await reviewLink.click()
    const panel = page.getByRole('region', { name: '레시피로 연결한 결과' })
    await expect(panel).toHaveCount(1)
    await expect(panel.locator('.semantic-result-kpi strong')).toHaveText('-311.643')
    await expect(panel.locator('.type-name_value .recharts-line-dot')).toHaveCount(1)
    await expect(panel.locator('.semantic-result-table tbody tr')).toHaveCount(itemCount)
    await expect(page).toHaveURL(new RegExp(`run=${runId}`))
    await page.locator('.request-journey').getByRole('button', { name: /결과 검토/ }).click()
    await expect(panel.locator('.semantic-result-kpi strong')).toHaveText('-311.643')
    if (extension === 'csv') await expect(page.getByTestId('result-layout-unconfigured')).toBeVisible()
    else await expect(page.getByTestId('result-layout-widget-semantic-review-summary')).toContainText(`#${runNo}`)
    await expect(panel.locator('.semantic-result-table tbody tr').filter({ hasText: 'Set Contact Position' })).toContainText('값 없음')
    await expect(panel.locator('.semantic-result-table tbody tr').filter({ hasText: 'Set Contact Coord.' })).toContainText('값 없음')
    await page.reload()
    await expect(panel.locator('.semantic-result-kpi strong')).toHaveText('-311.643')
    const runPicker = page.getByLabel('결과 버전 선택', { exact: true })
    await runPicker.selectOption(laterRunId)
    await expect(panel.locator('.semantic-result-kpi strong')).toHaveText('-123.456')
    await runPicker.selectOption(runId)
    await expect(panel.locator('.semantic-result-kpi strong')).toHaveText('-311.643')
    await panel.scrollIntoViewIfNeeded()
    await page.screenshot({ path: testInfo.outputPath(`issue-${extension}-review.png`) })
    await page.setViewportSize({ width: 390, height: 844 })
    await panel.scrollIntoViewIfNeeded()
    const panelBounds = await panel.boundingBox()
    expect(panelBounds!.x).toBeGreaterThanOrEqual(0)
    expect(panelBounds!.x + panelBounds!.width).toBeLessThanOrEqual(391)
    await page.screenshot({ path: testInfo.outputPath(`issue-${extension}-review-mobile.png`) })
    await page.setViewportSize({ width: 1280, height: 720 })
    await page.goto('/workspace/catalog/schemas')
    const opening = page.waitForResponse((response) => response.url().endsWith(`/semantic-mapping/configurations/${saved.recipe.id}`))
    await screen.getByLabel('저장된 레시피', { exact: true }).selectOption(saved.recipe.id)
    expect((await opening).status()).toBe(200)
    await expect(editors).toHaveCount(3)
    await expect(screen.getByLabel('템플릿 이름', { exact: true })).toHaveValue(templateName)
    await expect(editors.last().getByLabel('위젯 종류')).toHaveValue('name_value')
    await expect(editors.last().getByLabel('벡터 표시 성분')).toHaveValue('Z')
    await expect(editors.last().getByLabel('이름·값 차트 스타일')).toHaveValue('line')
    await inspector.locator('input[type=file]').setInputFiles(sample)
    await expect(preview.locator('.semantic-result-kpi strong')).toHaveText('-311.643')
    await expect(preview.locator('.type-name_value .recharts-line-dot')).toHaveCount(1)
    await preview.locator('.semantic-result-grid').evaluate((element) => element.scrollIntoView({ block: 'center' }))
    for (const editor of await editors.all()) {
      const bounds = await editor.boundingBox()
      expect(bounds).not.toBeNull()
      expect(bounds!.x).toBeGreaterThanOrEqual(0)
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(page.viewportSize()!.width + 1)
    }
    await page.screenshot({ path: testInfo.outputPath(`issue-${extension}-widgets.png`) })
    await page.setViewportSize({ width: 390, height: 844 })
    await editors.last().scrollIntoViewIfNeeded()
    await expect(editors.last().getByLabel('위젯 제목')).toBeVisible()
    for (const editor of await editors.all()) {
      const bounds = await editor.boundingBox()
      expect(bounds).not.toBeNull()
      expect(bounds!.x).toBeGreaterThanOrEqual(0)
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(391)
      for (const control of await editor.locator('input,select,button').all()) {
        const controlBounds = await control.boundingBox()
        if (!controlBounds) continue
        expect(controlBounds.x).toBeGreaterThanOrEqual(bounds!.x)
        expect(controlBounds.x + controlBounds.width).toBeLessThanOrEqual(bounds!.x + bounds!.width + 1)
      }
    }
    await page.screenshot({ path: testInfo.outputPath(`issue-${extension}-mobile.png`) })
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)
    expect(errors).toEqual([])
  })
}
