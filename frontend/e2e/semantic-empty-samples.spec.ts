import { expect, test } from '@playwright/test'
import { loginWorkspace } from './workspace-test-helpers'

for (const format of ['csv', 'json'] as const) {
  test(`${format} blank scalar and vector definitions remain available without inventing values`, async ({ page }) => {
    await loginWorkspace(page, 'e2e-admin', '/workspace/catalog/schemas')
    await page.goto('/workspace/catalog/schemas')
    const screen = page.locator('.semantic-page')
    const inspector = screen.getByTestId('semantic-sample-inspector')
    const suffix = `${Date.now()}_${format}`
    const zero = `zero_${suffix}`
    const falsy = `false_${suffix}`
    const whitespace = `whitespace_${suffix}`
    const absent = `absent_${suffix}`
    const source = format === 'csv'
      ? `${zero},0\n${falsy},false\n${whitespace},   \n${absent},\nStand Contact Coord. (mm),,,\n`
      : JSON.stringify({ [zero]: 0, [falsy]: false, [whitespace]: '   ', [absent]: null, 'Stand Contact Coord. (mm)': ['', ' ', null] })
    await inspector.locator('input[type=file]').setInputFiles({ name: `blank-${suffix}.${format}`, mimeType: format === 'csv' ? 'text/csv' : 'application/json', buffer: Buffer.from(source) })
    const fields = inspector.locator('.sample-field-table tbody')
    await expect(inspector.getByRole('button', { name: /선택 값을 결과 항목으로 연결/ })).toContainText('(5)')
    await expect(fields).toContainText(zero)
    await expect(fields).toContainText(falsy)
    await expect(fields).toContainText(whitespace)
    await expect(fields).toContainText('Stand Contact Coord.')
    await expect(inspector.getByRole('checkbox', { name: '빈값 필드 표시', exact: true })).toBeChecked()
    const blanks = fields.locator('tr').filter({ hasText: /whitespace_|absent_|Stand Contact Coord/ })
    await expect(blanks.first()).toContainText('값 없음')
    await expect(inspector.locator('.sample-row-table')).toContainText(whitespace)
    await inspector.getByRole('button', { name: /선택 필드 매핑 추가/ }).click()
    await expect(screen.getByLabel('원본 필드', { exact: true })).toHaveCount(5)
    await inspector.getByRole('checkbox', { name: '현재 페이지의 사용 가능한 값 선택', exact: true }).check()
    await inspector.getByRole('button', { name: /선택 값을 결과 항목으로 연결/ }).click()
    await expect(screen.getByLabel('결과 항목', { exact: true })).toHaveCount(5)
    await expect(screen.getByLabel('결과 항목', { exact: true }).first()).not.toHaveValue('')
    await expect(screen.getByLabel('결과 항목', { exact: true }).last()).not.toHaveValue('')
    const sources = await screen.getByLabel('원본 필드', { exact: true }).evaluateAll((inputs) => inputs.map((input) => (input as HTMLInputElement).value))
    expect(sources.filter((value) => /Stand Contact/.test(value))).toHaveLength(1)
    expect(sources.some((value) => /Stand Contact.*\/[012]$/.test(value))).toBe(false)
    await screen.getByRole('button', { name: '전체 항목 요약표 추가', exact: true }).click()
    const table = screen.locator('.preview-panel .semantic-result-table')
    await expect(table.locator('tbody tr')).toHaveCount(5)
    const vectorRow = table.locator('tbody tr').filter({ hasText: 'Stand Contact Coord.' })
    await expect(vectorRow).toContainText('값 없음')
    await expect(vectorRow).toContainText('X: —')
    await expect(vectorRow).toContainText('Y: —')
    await expect(vectorRow).toContainText('Z: —')
    await expect(table.locator('tbody tr').filter({ hasText: whitespace })).toContainText('값 없음')
    const saving = page.waitForResponse((response) => response.url().endsWith('/semantic-mapping/configurations') && response.request().method() === 'POST')
    await screen.getByRole('button', { name: '결과 설정 저장', exact: true }).click()
    const saved = await saving
    expect(saved.status(), await saved.text()).toBe(201)
    expect((await saved.json()).recipe.definition.mappings).toHaveLength(5)
  })
}

test('all blank definitions render complete rows and accept later values through the same saved configuration', async ({ page }, testInfo) => {
  await loginWorkspace(page, 'e2e-admin', '/workspace/catalog/schemas')
  await page.goto('/workspace/catalog/schemas')
  const screen = page.locator('.semantic-page')
  const inspector = screen.getByTestId('semantic-sample-inspector')
  const suffix = Date.now()
  const scalar = `empty_scalar_${suffix}`
  const vector = `empty_vector_${suffix}`
  const upload = async (value: Record<string, unknown>) => inspector.locator('input[type=file]').setInputFiles({ name: `empty-${suffix}.json`, mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(value)) })
  await upload({ [scalar]: null, [vector]: [null, ' ', ''] })
  await expect(inspector.getByRole('button', { name: /선택 값을 결과 항목으로 연결/ })).toContainText('(2)')
  await inspector.getByRole('button', { name: /선택 값을 결과 항목으로 연결/ }).click()
  await expect(screen.getByLabel('결과 항목', { exact: true })).toHaveCount(2)
  await screen.getByRole('button', { name: '전체 항목 요약표 추가', exact: true }).click()
  const table = screen.locator('.preview-panel .semantic-result-table')
  await expect(table.locator('tbody tr')).toHaveCount(2)
  await expect(table.locator('tbody tr').filter({ hasText: scalar })).toContainText('값 없음')
  const vectorRow = table.locator('tbody tr').filter({ hasText: vector })
  await expect(vectorRow).toContainText('값 없음')
  await expect(vectorRow).toContainText('성분 1: —')
  await expect(vectorRow).toContainText('성분 2: —')
  await expect(vectorRow).toContainText('성분 3: —')
  await table.evaluate((element) => element.scrollIntoView({ block: 'center' }))
  await page.screenshot({ path: testInfo.outputPath('empty-scalar-vector.png') })
  await screen.getByLabel('레시피 이름', { exact: true }).fill(`Empty recipe ${suffix}`)
  await screen.getByLabel('템플릿 이름', { exact: true }).fill(`Empty widgets ${suffix}`)
  const saving = page.waitForResponse((response) => response.url().endsWith('/semantic-mapping/configurations') && response.request().method() === 'POST')
  await screen.getByRole('button', { name: '결과 설정 저장', exact: true }).click()
  const savedResponse = await saving
  expect(savedResponse.status(), await savedResponse.text()).toBe(201)
  const saved = await savedResponse.json()
  await expect(screen.getByRole('button', { name: '결과 설정 저장', exact: true })).toBeEnabled()
  await upload({ [scalar]: 0, [vector]: [1.1, null, 3.3] })
  await expect(table.locator('tbody tr')).toHaveCount(2)
  await expect(table.locator('tbody tr').filter({ hasText: scalar })).toContainText('0.00')
  await expect(vectorRow).toContainText('성분 1: 1.10')
  await expect(vectorRow).toContainText('성분 2: —')
  await expect(vectorRow).toContainText('성분 3: 3.30')
  await expect(screen.getByLabel('저장된 레시피', { exact: true })).toHaveValue(saved.recipe.id)
  await expect(screen.getByLabel('저장된 템플릿', { exact: true })).toHaveValue(saved.template.id)
  await expect(screen.getByLabel('원본 필드', { exact: true })).toHaveCount(2)
  await table.evaluate((element) => element.scrollIntoView({ block: 'center' }))
  await page.screenshot({ path: testInfo.outputPath('filled-scalar-vector.png') })
})
