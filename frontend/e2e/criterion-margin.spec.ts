import { expect, test, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import type { CriterionMargin } from '../src/types'
import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

async function openComparison(page: Page) {
  await loginWorkspace(page)
  await openWorkspaceRoute(page, '/workspace/overview')
  const dashboard = page.getByTestId('result-overview-dashboard')
  await dashboard.getByLabel('의뢰 제목, 하중 경우 검색').fill('Run 비교: 회귀와 개선')
  await dashboard.getByRole('button', { name: '결과 검토', exact: true }).click()
  await page.locator('.analysis-tab').filter({ hasText: 'Run 비교' }).click()
  await expect(page.getByTestId('comparison-evidence-table')).toBeVisible()
}

const available = (value: number, criterion: string, meets: boolean): CriterionMargin => ({ status: 'AVAILABLE', value, unit: 'MPa', criterion_label: criterion, meets_criterion: meets, reason: null, source: 'analysis_run_metadata.result_criteria:qa-run' })

test('기록 기준의 여유·부족·엄격 경계와 저장 판정 차이를 확인한다', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.setViewportSize({ width: 1505, height: 1045 })
  await page.route('**/run-comparison?*', async route => {
    const response = await route.fetch()
    const data = await response.json()
    const row = data.scalar_comparison[0]
    row.baseline_margin = available(5, '값 ≤ 85 MPa', true)
    row.target_margin = available(-3, '값 ≥ 75 MPa', false)
    row.target_verdict = 'PASS'
    data.scalar_comparison[1].target_margin = available(0, '값 < 70 MPa', false)
    data.scalar_comparison[1].target_verdict = 'PASS'
    data.scalar_comparison[2].target_margin = { ...available(0, '', false), status: 'UNKNOWN', value: null, reason: 'CRITERION_UNIT_MISMATCH', meets_criterion: null }
    await route.fulfill({ response, json: data })
  })
  await openComparison(page)
  const rows = page.getByTestId('comparison-evidence-row')
  await rows.nth(0).click()
  const detail = page.getByTestId('criterion-margin-detail')
  await expect(detail).toContainText('+5 MPa')
  await expect(detail).toContainText('-3 MPa')
  await expect(detail).toContainText('값 ≥ 75 MPa')
  await expect(detail).toContainText('저장된 판정(PASS)과 기록 기준의 충족 여부가 다릅니다')
  await detail.getByText('기록 출처', { exact: true }).first().click()
  await expect(detail).toContainText('analysis_run_metadata.result_criteria:qa-run')
  const output = process.env.GUI_QA_OUTPUT_DIR
  await detail.scrollIntoViewIfNeeded()
  if (output) { mkdirSync(output, { recursive: true }); await page.screenshot({ path: path.join(output, 'criterion-margin-desktop.png') }) }
  await rows.nth(1).click()
  await expect(detail).toContainText('0 MPa · 경계')
  await expect(detail).toContainText('기록 기준 미충족')
  await rows.nth(2).click()
  await expect(detail).toContainText('결과와 기록 기준의 단위가 다릅니다')
  await page.setViewportSize({ width: 390, height: 844 })
  await detail.scrollIntoViewIfNeeded()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  if (output) await page.screenshot({ path: path.join(output, 'criterion-margin-mobile.png') })
  expect(errors).toEqual([])
})

test('실제 과거 기록 및 구버전 응답을 계산 불가로 유지하고 Run 전환 시 근거를 갱신한다', async ({ page }) => {
  await openComparison(page)
  const rows = page.getByTestId('comparison-evidence-row')
  await rows.first().click()
  await expect(page.getByTestId('criterion-margin-detail')).toContainText('이 Run에 판정 기준이 기록되지 않았습니다')
  await page.route('**/run-comparison?*', async route => {
    const response = await route.fetch()
    const data = await response.json()
    for (const item of data.scalar_comparison) { delete item.baseline_margin; delete item.target_margin }
    await route.fulfill({ response, json: data })
  })
  const picker = page.locator('.comparison-workspace').getByLabel('기준 Run', { exact: true })
  await picker.selectOption({ label: await picker.locator('option').filter({ hasText: 'Run 1 ' }).innerText() })
  await expect(rows.first()).toContainText('기록 여유 계산 불가')
  await rows.first().click()
  await expect(page.getByTestId('criterion-margin-detail').getByTestId('criterion-margin-card')).toHaveCount(2)
  await expect(page.getByTestId('criterion-margin-detail')).not.toContainText('기록 기준 충족')
})

test('두 Run의 공통 축은 실제 시간 간격·누락을 보존하며 축 전환 후 Run 변경에서 초기화된다', async ({ page }) => {
  await page.setViewportSize({ width: 1505, height: 1045 })
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/run-comparison?*', async route => {
    const response = await route.fetch()
    const data = await response.json()
    data.time_series.points = [
      { time_value: 10, time_unit: 'ms', baseline_value: 75, target_value: 90 },
      { time_value: -2, time_unit: 'ms', baseline_value: 50, target_value: 80 },
      { time_value: 1, time_unit: 'ms', baseline_value: null, target_value: 85 },
      { time_value: 8, time_unit: 'ms', baseline_value: 70, target_value: 88 },
      { time_value: -1, time_unit: 'ms', baseline_value: 55, target_value: 82 },
    ]
    await route.fulfill({ response, json: data })
  })
  await openComparison(page)
  const chart = page.getByTestId('comparison-series-chart')
  await chart.scrollIntoViewIfNeeded()
  const axes = page.getByTestId('comparison-series-axes')
  await expect(axes).toHaveAttribute('data-x-domain', '-2,10')
  await expect(axes).toHaveAttribute('data-y-domain', '0,90')
  await expect(chart.locator('.recharts-line-curve')).toHaveCount(2)
  await expect.poll(async () => (await chart.locator('.recharts-line-curve').first().getAttribute('d'))?.match(/M/g)?.length).toBe(2)
  // Real SVG geometry proves numeric time spacing, rather than only checking our domain attributes.
  const targetPath = await chart.locator('.recharts-line-curve').nth(1).getAttribute('d')
  const coordinates = [...(targetPath ?? '').matchAll(/[ML](-?[\d.e+]+),(-?[\d.e+]+)/g)].map(match => Number(match[1]))
  expect(coordinates).toHaveLength(5)
  expect((coordinates[1] - coordinates[0]) / (coordinates[4] - coordinates[0])).toBeCloseTo(1 / 12, 2)
  await chart.getByRole('checkbox', { name: '데이터 범위만 보기' }).check()
  await expect(axes).toHaveAttribute('data-y-domain', '50,90')
  await expect(chart).toContainText('데이터 범위')
  const output = process.env.GUI_QA_OUTPUT_DIR
  if (output) { mkdirSync(output, { recursive: true }); await page.screenshot({ path: path.join(output, 'comparison-axes-desktop.png') }) }
  const picker = page.locator('.comparison-workspace').getByLabel('기준 Run', { exact: true })
  await picker.selectOption({ label: await picker.locator('option').filter({ hasText: 'Run 1 ' }).innerText() })
  await expect(chart.getByRole('checkbox', { name: '데이터 범위만 보기' })).not.toBeChecked()
  await expect(axes).toHaveAttribute('data-y-domain', '0,90')
  await page.setViewportSize({ width: 390, height: 844 })
  await chart.scrollIntoViewIfNeeded()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  const chartBox = await chart.boundingBox()
  const legendBox = await chart.locator('.recharts-legend-wrapper').boundingBox()
  expect(chartBox && legendBox && legendBox.y + legendBox.height <= chartBox.y + chartBox.height + 1).toBeTruthy()
  if (output) await page.screenshot({ path: path.join(output, 'comparison-axes-mobile.png') })
  expect(errors).toEqual([])
})

test('누락 사이의 단독 관측값과 값이 0인 단일 시점을 숨기지 않는다', async ({ page }) => {
  let single = false
  await page.route('**/run-comparison?*', async route => {
    const response = await route.fetch()
    const data = await response.json()
    data.time_series.points = single ? [{ time_value: 3, time_unit: 'ms', baseline_value: 0, target_value: 0 }] : [
      { time_value: -1, time_unit: 'ms', baseline_value: null, target_value: 0 },
      { time_value: 0, time_unit: 'ms', baseline_value: -3, target_value: 0 },
      { time_value: 2, time_unit: 'ms', baseline_value: null, target_value: 0 },
    ]
    await route.fulfill({ response, json: data })
  })
  await openComparison(page)
  const chart = page.getByTestId('comparison-series-chart')
  await chart.scrollIntoViewIfNeeded()
  await expect(chart.locator('.recharts-line-dots circle')).toHaveCount(1)
  const dot = chart.locator('.recharts-line-dots circle').first()
  expect(Number(await dot.getAttribute('cx'))).toBeGreaterThan(0)
  expect(Number(await dot.getAttribute('cy'))).toBeGreaterThan(0)
  single = true
  const picker = page.locator('.comparison-workspace').getByLabel('기준 Run', { exact: true })
  await picker.selectOption({ label: await picker.locator('option').filter({ hasText: 'Run 1 ' }).innerText() })
  await expect(page.getByTestId('comparison-series-axes')).toHaveAttribute('data-x-domain', '2,4')
  await expect(page.getByTestId('comparison-series-axes')).toHaveAttribute('data-y-domain', '-1,1')
  await expect(chart.locator('.recharts-line-dots circle')).toHaveCount(2)
})
