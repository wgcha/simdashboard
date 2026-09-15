import { expect, test, type Page } from '@playwright/test'
import { loginWorkspace } from './workspace-test-helpers'

type SavedPair = { recipe: { id: string; version: number }; template: { id: string; version: number; definition: { widgets: Array<{ title: string }> } } }

async function scalarWorkspace(page: Page, widget = true) {
  await loginWorkspace(page, 'e2e-admin', '/workspace/catalog/schemas')
  await page.goto('/workspace/catalog/schemas')
  const screen = page.locator('.semantic-page')
  const key = `race_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`
  const inspector = screen.getByTestId('semantic-sample-inspector')
  await inspector.locator('input[type=file]').setInputFiles({ name: `${key}.json`, mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({ [key]: 1.25 })) })
  await expect(inspector.getByRole('button', { name: /선택 값을 결과 항목으로 연결/ })).toContainText('(1)')
  await inspector.getByRole('button', { name: /선택 값을 결과 항목으로 연결/ }).click()
  await expect(screen.getByLabel('결과 항목', { exact: true })).not.toHaveValue('')
  await screen.getByLabel('레시피 이름', { exact: true }).fill(`Recipe ${key}`)
  await screen.getByLabel('템플릿 이름', { exact: true }).fill(`Widgets ${key}`)
  if (widget) {
    await screen.getByRole('button', { name: '위젯 추가', exact: true }).click()
    await expect(screen.locator('.preview-panel .semantic-result-widget.ready')).toHaveCount(1)
  }
  return { screen, inspector, key }
}

async function savePair(page: Page): Promise<SavedPair> {
  const response = page.waitForResponse((item) => item.url().endsWith('/semantic-mapping/configurations') && item.request().method() === 'POST')
  await page.getByRole('button', { name: '결과 설정 저장', exact: true }).click()
  const saved = await response
  expect(saved.status(), await saved.text()).toBe(201)
  await expect(page.getByRole('button', { name: '결과 설정 저장', exact: true })).toBeEnabled()
  return saved.json()
}

test('pending real configuration saves preserve edits and advance fresh and existing CAS identities', async ({ page }) => {
  test.setTimeout(180_000)
  const { screen } = await scalarWorkspace(page)
  const title = screen.getByLabel('위젯 제목', { exact: true })
  let previous: SavedPair | undefined
  for (const committedVersion of [1, 3]) {
    let release!: () => void
    let arrived!: (saved: SavedPair) => void
    const held = new Promise<void>((resolve) => { release = resolve })
    const fetched = new Promise<SavedPair>((resolve) => { arrived = resolve })
    await page.route('**/api/semantic-mapping/configurations', async (route) => {
      const response = await route.fetch()
      const saved = await response.json() as SavedPair
      arrived(saved)
      await held
      await route.fulfill({ response })
    }, { times: 1 })
    const completed = page.waitForResponse((response) => response.url().endsWith('/semantic-mapping/configurations') && response.request().method() === 'POST')
    await screen.getByRole('button', { name: '결과 설정 저장', exact: true }).click()
    const committed = await fetched
    expect(committed.recipe.version).toBe(committedVersion)
    if (previous) { expect(committed.recipe.id).toBe(previous.recipe.id); expect(committed.template.id).toBe(previous.template.id) }
    const edited = `Edited while v${committedVersion} pending`
    await title.fill(edited)
    release()
    expect((await completed).status()).toBe(201)
    await expect(title).toHaveValue(edited)
    await expect(screen.getByRole('button', { name: '결과 설정 저장', exact: true })).toBeEnabled()
    const next = await savePair(page)
    expect(next.recipe.id).toBe(committed.recipe.id)
    expect(next.template.id).toBe(committed.template.id)
    expect(next.recipe.version).toBe(committedVersion + 1)
    expect(next.template.version).toBe(committedVersion + 1)
    expect(next.template.definition.widgets[0].title).toBe(edited)
    previous = next
  }
})

test('manual preview immediately after editing cancels the scheduled automatic preview without locking save', async ({ page }) => {
  const { screen } = await scalarWorkspace(page)
  const manual = page.waitForResponse((response) => response.url().endsWith('/semantic-mapping/preview') && response.request().method() === 'POST')
  await screen.getByLabel('위젯 제목', { exact: true }).fill('Manual preview race')
  await screen.getByRole('button', { name: '미리보기 실행', exact: true }).click()
  expect((await manual).status()).toBe(200)
  await expect(screen.getByRole('button', { name: '미리보기 실행', exact: true })).toBeEnabled()
  await expect(screen.getByRole('button', { name: '결과 설정 저장', exact: true })).toBeEnabled()
  expect((await savePair(page)).template.definition.widgets[0].title).toBe('Manual preview race')
})

test('unused item meaning edits and archive restore remain available while reset clears mapping undo', async ({ page }) => {
  const { screen, inspector, key } = await scalarWorkspace(page)
  const itemId = await screen.getByLabel('결과 항목', { exact: true }).inputValue()
  await screen.getByTitle('결과 항목 수정', { exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '결과 항목 정의' })
  const unit = dialog.getByLabel('기준 단위', { exact: true })
  await expect(unit).toBeEnabled()
  await dialog.getByText('이 항목의 사용 위치', { exact: true }).click()
  await expect(dialog).toContainText('레시피 버전 0개')
  await expect(dialog).toContainText('템플릿 버전 0개')
  await unit.fill('mm')
  await dialog.getByRole('button', { name: '결과 항목 저장', exact: true }).click()
  await expect(dialog).toBeHidden()
  await screen.getByRole('button', { name: '매핑 삭제', exact: true }).click()
  await expect(screen.getByRole('button', { name: '삭제 되돌리기', exact: true })).toBeEnabled()
  await screen.getByText('결과 항목 관리 · 보관 및 복원', { exact: true }).click()
  const manager = screen.locator('details.semantic-card').filter({ hasText: '결과 항목 관리 · 보관 및 복원' })
  const row = manager.locator('.semantic-inline-fields').filter({ hasText: key })
  const archived = page.waitForResponse((response) => response.url().endsWith(`/items/${itemId}/archive`))
  await row.getByRole('button', { name: '보관', exact: true }).click()
  expect((await archived).status()).toBe(200)
  await expect(row).toContainText('보관됨')
  const restored = page.waitForResponse((response) => response.url().endsWith(`/items/${itemId}/restore`))
  await row.getByRole('button', { name: '복원', exact: true }).click()
  expect((await restored).status()).toBe(200)
  await expect(row).toContainText('사용 가능')
  await inspector.getByRole('button', { name: '샘플 초기화', exact: true }).click()
  await expect(screen.locator('.semantic-widget-editor')).toHaveCount(0)
  await expect(screen.getByRole('button', { name: '삭제 되돌리기', exact: true })).toBeDisabled()
  await inspector.locator('input[type=file]').setInputFiles({ name: 'different.json', mimeType: 'application/json', buffer: Buffer.from('{"different_value":2.5}') })
  await expect(inspector).toContainText('different_value')
  await expect(screen.getByRole('button', { name: '삭제 되돌리기', exact: true })).toBeDisabled()
})
