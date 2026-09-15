import { expect, test } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { loginWorkspace } from './workspace-test-helpers'

function records<T>(value: unknown, key: string): T[] {
  if (Array.isArray(value)) return value as T[]
  if (value && typeof value === 'object' && Array.isArray((value as Record<string, unknown>)[key])) return (value as Record<string, unknown>)[key] as T[]
  return []
}

test('folder discovery creates real project, request and load case then keeps them on reapply', async ({ page }, testInfo) => {
  test.setTimeout(180_000)
  const suffix = `${Date.now()}${testInfo.workerIndex}`
  const root = testInfo.outputPath('folder-core-qa', 'source')
  const project = `P${suffix}_ActualProject`
  const request = `R${suffix}_ActualRequest`
  const loadCase = `L${suffix}_DropLoad`
  const projectName = 'ActualProject'
  const requestName = 'ActualRequest'
  const loadCaseName = 'DropLoad'
  mkdirSync(join(root, project, request, loadCase), { recursive: true })

  await loginWorkspace(page, 'e2e-admin', '/workspace/catalog/schemas')
  await page.getByRole('button', { name: '폴더 조사·업무 생성', exact: true }).click()
  const screen = page.getByTestId('folder-discovery-workspace')
  await expect(screen).toBeVisible()
  const rootInput = screen.getByLabel('저장 폴더 경로')
  await expect(rootInput).toBeVisible()
  await rootInput.fill(root)
  await screen.getByRole('button', { name: '저장', exact: true }).click()
  await screen.getByRole('button', { name: '최상위 폴더 선택', exact: true }).click()
  let picker = page.getByRole('dialog', { name: '최상위 폴더 선택' })
  await picker.press('Escape')
  await expect(picker).toBeHidden()
  await screen.getByRole('button', { name: '최상위 폴더 선택', exact: true }).click()
  picker = page.getByRole('dialog', { name: '최상위 폴더 선택' })
  await picker.getByRole('button', { name: project, exact: true }).click()
  await picker.getByRole('button', { name: 'root', exact: true }).click()
  await picker.getByRole('button', { name: '이 위치 선택', exact: true }).click()
  await picker.getByRole('button', { name: '선택 완료', exact: true }).click()
  await screen.getByRole('button', { name: '전체 트리 조사', exact: true }).click()
  await expect(screen).toContainText(loadCase)
  await screen.getByRole('button', { name: '규칙 저장', exact: true }).click()
  await screen.getByRole('button', { name: '업무 생성 미리보기', exact: true }).click()
  await expect(screen.getByRole('cell', { name: 'CREATE', exact: true })).toHaveCount(3)
  await screen.getByRole('button', { name: '검토한 업무 생성 적용', exact: true }).click()
  await expect(screen.getByRole('status')).toContainText('프로젝트 1')
  const projects = await page.request.get('/api/projects')
  expect(projects.ok()).toBe(true)
  const projectRecord = records<{ id: string; name: string }>(await projects.json(), 'projects').find((item) => item.name === projectName)
  expect(projectRecord).toBeTruthy()
  const requests = await page.request.get(`/api/projects/${projectRecord?.id}/requests`)
  expect(requests.ok()).toBe(true)
  const requestRecord = records<{ id: string; title: string }>(await requests.json(), 'requests').find((item) => item.title === requestName)
  expect(requestRecord).toBeTruthy()
  const loadCases = await page.request.get(`/api/requests/${requestRecord?.id}/load-cases`)
  expect(loadCases.ok()).toBe(true)
  expect(records<{ name: string }>(await loadCases.json(), 'loadCases').some((item) => item.name === loadCaseName)).toBe(true)
  await screen.getByRole('button', { name: '업무 생성 미리보기', exact: true }).click()
  await expect(screen.getByRole('cell', { name: 'KEEP', exact: true })).toHaveCount(3)
  for (const [theme, label] of [['light', '라이트'], ['dark', '다크']] as const) {
    await page.getByRole('button', { name: label, exact: true }).click()
    await expect(page.locator('.app-shell')).toHaveAttribute('data-theme', theme)
    await page.screenshot({ path: testInfo.outputPath(`folder-discovery-${theme}.png`), fullPage: true })
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await screen.scrollIntoViewIfNeeded()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(391)
  await page.screenshot({ path: testInfo.outputPath('folder-discovery-mobile.png'), fullPage: true })
})
