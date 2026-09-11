import { expect, test, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import type { RunConditionComparisonData } from '../src/types'
import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

async function openComparison(page: Page) {
  await loginWorkspace(page)
  await openWorkspaceRoute(page, '/workspace/overview')
  const dashboard = page.getByTestId('result-overview-dashboard')
  await dashboard.getByLabel('의뢰 제목, 하중 경우 검색').fill('Run 비교: 회귀와 개선')
  await dashboard.getByRole('button', { name: '결과 검토', exact: true }).click()
  await page.locator('.analysis-tab').filter({ hasText: 'Run 비교' }).click()
  await expect(page.getByTestId('run-condition-comparison')).toBeVisible()
  return page.getByTestId('run-condition-comparison')
}

test('실제 과거 Run의 미기록 조건을 동일로 표시하지 않는다', async ({ page }) => {
  const panel = await openComparison(page)
  const workspace = page.locator('.comparison-workspace')
  const baseline = await workspace.getByLabel('기준 Run', { exact: true }).inputValue()
  const target = await workspace.getByLabel('대상 Run', { exact: true }).inputValue()
  const response = await page.request.get(`/api/load-cases/loadcase-showcase-compare/run-comparison?baseline_run_id=${baseline}&target_run_id=${target}`)
  expect(response.ok()).toBeTruthy()
  const data = (await response.json()).condition_comparison as RunConditionComparisonData
  expect(data).toBeTruthy()
  expect(data.summary.unknown).toBeGreaterThan(0)
  await expect(panel.getByTestId('condition-row')).toHaveCount(data.summary.changed + data.summary.unknown)
  await panel.getByRole('button', { name: /^전체/ }).click()
  await expect(panel.getByTestId('condition-row')).toHaveCount(data.rows.length)
  for (const row of data.rows) await expect(panel.locator(`[data-condition-key="${row.key}"]`)).toHaveAttribute('data-status', row.status)
  await expect(panel).toContainText('기록되지 않은 조건은 확인이 필요합니다')
})

test('기록된 조건 차이·단위·출처를 확인하고 전체 보기와 Run 전환을 유지한다', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.setViewportSize({ width: 1505, height: 1045 })
  await page.route('**/run-comparison?*', async route => {
    const response = await route.fetch()
    const data = await response.json()
    const switched = data.baseline_run.run_no === 1
    const source = 'manifest.metadata.run_conditions · 시험용 입력 기록'
    data.condition_comparison = {
      rows: [
        { key: 'material', label: '재료', baseline: { value: switched ? 'AL5052' : 'SPCC', unit: null, source }, target: { value: 'SPCC', unit: null, source }, status: switched ? 'CHANGED' : 'SAME', reason: '기록된 재료 비교' },
        { key: 'thickness', label: '두께', baseline: { value: 1.2, unit: 'mm', source }, target: { value: 1.5, unit: 'mm', source }, status: 'CHANGED', reason: '동일 단위의 기록된 값이 다릅니다.' },
        { key: 'load', label: '하중', baseline: { value: 0, unit: 'N', source }, target: null, status: 'UNKNOWN', reason: '대상 Run의 기록이 없습니다.' },
        { key: 'contact', label: '접촉', baseline: { value: { friction: 0, enabled: false }, unit: null, source }, target: { value: { friction: 0.2, enabled: false }, unit: null, source }, status: 'CHANGED', reason: '구조화 조건 값이 다릅니다.' },
      ], summary: { changed: switched ? 3 : 2, same: switched ? 0 : 1, unknown: 1 },
    }
    await route.fulfill({ response, json: data })
  })
  const panel = await openComparison(page)
  await expect(panel.getByTestId('condition-row')).toHaveCount(3)
  const thickness = panel.locator('[data-condition-key="thickness"]')
  await expect(thickness).toContainText('1.2 mm')
  await expect(thickness).toContainText('1.5 mm')
  await thickness.locator('summary').click()
  await expect(thickness.locator('details')).toContainText('manifest.metadata.run_conditions')
  await expect(panel.locator('[data-condition-key="load"]')).toContainText('0 N')
  await expect(panel.locator('[data-condition-key="contact"]')).toContainText('false')
  await panel.getByRole('button', { name: /^전체/ }).click()
  await expect(panel.getByTestId('condition-row')).toHaveCount(4)
  await expect(panel.locator('[data-condition-key="material"]')).toHaveAttribute('data-status', 'SAME')
  const picker = page.locator('.comparison-workspace').getByLabel('기준 Run', { exact: true })
  await picker.selectOption({ label: await picker.locator('option').filter({ hasText: 'Run 1 ' }).innerText() })
  await expect(panel.locator('[data-condition-key="material"]')).toHaveAttribute('data-status', 'CHANGED')
  await expect(panel.locator('[data-condition-key="material"]')).toContainText('AL5052')
  const output = process.env.GUI_QA_OUTPUT_DIR
  await panel.scrollIntoViewIfNeeded()
  if (output) { mkdirSync(output, { recursive: true }); await page.screenshot({ path: path.join(output, 'run-conditions-desktop.png') }) }
  await page.setViewportSize({ width: 390, height: 844 })
  await panel.scrollIntoViewIfNeeded()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  if (output) await page.screenshot({ path: path.join(output, 'run-conditions-mobile.png') })
  expect(errors).toEqual([])
})

test('모두 동일한 기록도 전체 보기에서 확인하고 구버전 응답은 확인 필요로 표시한다', async ({ page }) => {
  let missing = false
  await page.route('**/run-comparison?*', async route => {
    const response = await route.fetch()
    const data = await response.json()
    if (missing) delete data.condition_comparison
    else data.condition_comparison = { rows: [{ key: 'solver', label: '해석기', baseline: { value: 'Solver 1', unit: null, source: 'Run.solver' }, target: { value: 'Solver 1', unit: null, source: 'Run.solver' }, status: 'SAME', reason: '기록이 같습니다.' }], summary: { changed: 0, same: 1, unknown: 0 } }
    await route.fulfill({ response, json: data })
  })
  const panel = await openComparison(page)
  await expect(panel.getByTestId('run-condition-all-same')).toBeVisible()
  await panel.getByRole('button', { name: /^전체/ }).click()
  await expect(panel.getByTestId('condition-row')).toHaveCount(1)
  missing = true
  await page.reload()
  await expect(page.getByTestId('run-condition-comparison')).toContainText('확인 필요')
  await expect(page.getByTestId('run-condition-all-same')).toHaveCount(0)
})
