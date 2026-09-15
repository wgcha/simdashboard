import { expect, test, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import type { RunComparison } from '../src/types'
import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

async function openComparison(page: Page) {
  await loginWorkspace(page)
  await openWorkspaceRoute(page, '/workspace/overview')
  const dashboard = page.getByTestId('result-overview-dashboard')
  await dashboard.getByLabel('의뢰 제목, 하중 경우 검색').fill('Run 비교: 회귀와 개선')
  await dashboard.getByRole('button', { name: '결과 검토', exact: true }).click()
  await page.locator('.analysis-tab').filter({ hasText: 'Run 비교' }).click()
  await expect(page.getByTestId('comparison-evidence-table')).toBeVisible()
  const workspace = page.locator('.comparison-workspace')
  const baseline = await workspace.getByLabel('기준 Run', { exact: true }).inputValue()
  const target = await workspace.getByLabel('대상 Run', { exact: true }).inputValue()
  const response = await page.request.get(`/api/load-cases/loadcase-showcase-compare/run-comparison?baseline_run_id=${baseline}&target_run_id=${target}`)
  expect(response.ok()).toBeTruthy()
  return { workspace, comparison: await response.json() as RunComparison, baseline, target }
}

test('문제 필터는 실제 판정과 일치하고 선택 근거를 설계 검토 의견에 저장한다', async ({ page }) => {
  await page.setViewportSize({ width: 1505, height: 1045 })
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  const { workspace, comparison, target } = await openComparison(page)
  const rows = page.getByTestId('comparison-evidence-row')
  await expect(rows).toHaveCount(comparison.scalar_comparison.length)
  await page.getByTestId('comparison-filter-target_fail').click()
  await expect(rows).toHaveCount(comparison.scalar_comparison.filter(item => item.target_verdict?.toUpperCase() === 'FAIL').length)
  await page.getByTestId('comparison-filter-regression').click()
  const regressions = comparison.scalar_comparison.filter(item => item.change === 'REGRESSION')
  expect(regressions.length).toBeGreaterThan(0)
  await expect(rows).toHaveCount(regressions.length)
  const selected = regressions[0]
  await rows.filter({ hasText: selected.variable_key }).click()
  const detail = page.getByTestId('comparison-evidence-detail')
  await expect(detail).toContainText(selected.display_name)
  if (!comparison.available_series.some(item => item.variable_key === selected.variable_key)) {
    await expect(detail).toContainText('연결된 시계열 없음')
    await expect(detail.getByRole('button', { name: '시계열 보기' })).toHaveCount(0)
  }
  await detail.getByRole('button', { name: '검토 준비' }).click()
  const body = workspace.getByLabel('검토 의견', { exact: true })
  await expect(body).toHaveValue(/관찰 사실/)
  await expect(body).toHaveValue(/원인 가설/)
  await expect(body).toHaveValue(/설계 변경 방향/)
  expect(await body.inputValue()).toContain(selected.variable_key)
  const title = `2단계 근거 검토 ${Date.now()}`
  await workspace.getByLabel('제목', { exact: true }).fill(title)
  await body.fill(`${await body.inputValue()}\n추가 확인: 체결 조건을 고정하고 재해석한다.`)
  const saved = page.waitForResponse(response => response.url().includes(`/analysis-runs/${target}/review-items`) && response.request().method() === 'POST')
  await workspace.getByRole('button', { name: '북마크 저장' }).click()
  expect((await saved).ok()).toBeTruthy()
  await expect(workspace.locator('.review-list')).toContainText(title)
  const stored = await page.request.get(`/api/analysis-runs/${target}/review-items`)
  expect((await stored.json()).some((item: { title: string; variable_key: string }) => item.title === title && item.variable_key === selected.variable_key)).toBeTruthy()
  await page.getByTestId('comparison-filter-not_comparable').click()
  const nonComparable = comparison.scalar_comparison.filter(item => !item.comparable)
  await expect(rows).toHaveCount(nonComparable.length)
  if (!nonComparable.length) await expect(page.getByTestId('comparison-evidence-empty')).toBeVisible()
  await page.getByTestId('comparison-filter-all').click()
  await page.getByTestId('comparison-evidence-table').scrollIntoViewIfNeeded()
  const output = process.env.GUI_QA_OUTPUT_DIR
  if (output) { mkdirSync(output, { recursive: true }); await page.screenshot({ path: path.join(output, 'comparison-desktop.png') }) }
  const chart = page.getByTestId('comparison-series-chart')
  await chart.scrollIntoViewIfNeeded()
  const chartBox = await chart.boundingBox()
  expect(chartBox).not.toBeNull()
  for (const tick of await chart.locator('.recharts-yAxis .recharts-cartesian-axis-tick-value').all()) {
    const box = await tick.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.x).toBeGreaterThanOrEqual(chartBox!.x)
  }
  if (output) await page.screenshot({ path: path.join(output, 'comparison-chart.png') })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByTestId('comparison-evidence-table').scrollIntoViewIfNeeded()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  if (output) await page.screenshot({ path: path.join(output, 'comparison-mobile.png') })
  expect(errors).toEqual([])
})

test('연결된 시계열로 이동하고 기존 초안과 Run 조합별 작성 내용을 보존한다', async ({ page }) => {
  await page.route('**/run-comparison?*', async route => {
    const response = await route.fetch()
    const data = await response.json() as RunComparison
    const series = data.available_series[0]
    if (series) data.scalar_comparison.push({
      variable_key: series.variable_key, display_name: '연결 근거 검증', unit: series.unit,
      baseline_value: 10, target_value: 11, baseline_verdict: 'PASS', target_verdict: 'PASS',
      delta: 1, delta_percent: 10, change: 'UNCHANGED', comparable: true,
    })
    await route.fulfill({ response, json: data })
  })
  const { workspace, comparison, baseline } = await openComparison(page)
  expect(comparison.available_series.length).toBeGreaterThan(0)
  const key = comparison.available_series[0].variable_key
  await page.getByTestId('comparison-evidence-row').filter({ hasText: '연결 근거 검증' }).click()
  const detail = page.getByTestId('comparison-evidence-detail')
  await detail.getByRole('button', { name: '시계열 보기' }).click()
  await expect(page.getByTestId('comparison-series-chart')).toBeFocused()
  await expect(workspace.getByLabel('시계열 변수 선택')).toHaveValue(key)
  await workspace.getByLabel('제목', { exact: true }).fill('작성 중인 설계 검토')
  const body = workspace.getByLabel('검토 의견', { exact: true })
  await body.fill('내 관찰: 접촉 조건을 추가로 확인한다.')
  await detail.getByRole('button', { name: '검토 준비' }).click()
  await expect(body).toBeFocused()
  expect(await body.inputValue()).toContain('내 관찰: 접촉 조건을 추가로 확인한다.')
  expect(await body.inputValue()).toContain(key)
  const draft = await body.inputValue()
  await detail.getByRole('button', { name: '검토 준비' }).click()
  await expect(body).toHaveValue(draft)
  const picker = workspace.getByLabel('기준 Run', { exact: true })
  const alternative = await picker.locator('option').evaluateAll((options, current) => options.map(option => (option as HTMLOptionElement).value).find(value => value !== current), baseline)
  expect(alternative).toBeTruthy()
  await picker.selectOption(alternative!)
  await expect(page.getByTestId('comparison-evidence-table')).toBeVisible()
  await expect(body).toHaveValue('')
  await body.fill('다른 비교 조합의 메모')
  await picker.selectOption(baseline)
  await expect(body).toHaveValue(draft)
  await expect(workspace.getByLabel('제목', { exact: true })).toHaveValue('작성 중인 설계 검토')
})

test('늦게 도착한 비교 응답이 현재 선택한 Run의 근거를 바꾸지 않는다', async ({ page }) => {
  const { workspace, baseline } = await openComparison(page)
  const picker = workspace.getByLabel('기준 Run', { exact: true })
  const alternative = await picker.locator('option').evaluateAll((options, current) => options.map(option => (option as HTMLOptionElement).value).find(value => value !== current), baseline)
  expect(alternative).toBeTruthy()
  let release: () => void = () => {}
  const gate = new Promise<void>(resolve => { release = resolve })
  let markStarted: () => void = () => {}
  const started = new Promise<void>(resolve => { markStarted = resolve })
  let markDelivered: () => void = () => {}
  const delivered = new Promise<void>(resolve => { markDelivered = resolve })
  await page.route('**/run-comparison?*', async route => {
    const response = await route.fetch()
    const data = await response.json() as RunComparison
    if (new URL(route.request().url()).searchParams.get('baseline_run_id') === alternative) {
      markStarted()
      await gate
      data.scalar_comparison[0].display_name = '이전 요청의 오래된 근거'
      await route.fulfill({ response, json: data })
      markDelivered()
    } else await route.fulfill({ response })
  })
  await picker.selectOption(alternative!)
  await started
  await expect(page.getByTestId('comparison-evidence-table')).toHaveCount(0)
  await picker.selectOption(baseline)
  await expect(page.getByTestId('comparison-evidence-table')).toBeVisible()
  release()
  await delivered
  await expect(picker).toHaveValue(baseline)
  await expect(workspace).not.toContainText('이전 요청의 오래된 근거')
})

test('단위가 다른 근거는 차이를 계산하지 않고 삭제 변수는 Run 전체 검토로 준비한다', async ({ page }) => {
  await page.route('**/run-comparison?*', async route => {
    const response = await route.fetch()
    const data = await response.json() as RunComparison
    data.scalar_comparison.push(
      { variable_key: 'qa-unit-mismatch', display_name: '단위 불일치 확인', unit: 'Pa', baseline_value: 100, target_value: 200, baseline_verdict: 'PASS', target_verdict: 'FAIL', delta: null, delta_percent: null, change: 'NOT_COMPARABLE', comparable: false },
      { variable_key: 'qa-removed', display_name: '삭제 변수 확인', unit: 'MPa', baseline_value: 5, target_value: null, baseline_verdict: 'FAIL', target_verdict: null, delta: null, delta_percent: null, change: 'REMOVED', comparable: false },
    )
    await route.fulfill({ response, json: data })
  })
  const { workspace } = await openComparison(page)
  await page.getByTestId('comparison-filter-not_comparable').click()
  await page.getByTestId('comparison-evidence-row').filter({ hasText: 'qa-unit-mismatch' }).click()
  const detail = page.getByTestId('comparison-evidence-detail')
  await expect(detail).toContainText('단위 확인 필요')
  await expect(detail).not.toContainText('100 Pa')
  await expect(detail).toContainText('비교 불가')
  await page.getByTestId('comparison-evidence-row').filter({ hasText: 'qa-removed' }).click()
  await expect(detail).toContainText('5.00 MPa')
  await detail.getByRole('button', { name: '검토 준비' }).click()
  await expect(workspace.getByLabel('결과 변수', { exact: true })).toHaveValue('')
  await expect(workspace.getByLabel('검토 의견', { exact: true })).toHaveValue(/qa-removed/)
  await expect(workspace.getByRole('status')).toContainText('Run 전체')
})

test('검토 저장 중 추가로 작성한 내용은 응답이 도착해도 보존한다', async ({ page }) => {
  const { workspace, target } = await openComparison(page)
  const title = workspace.getByLabel('제목', { exact: true })
  const body = workspace.getByLabel('검토 의견', { exact: true })
  await title.fill(`저장 중 편집 ${Date.now()}`)
  await body.fill('저장 요청에 포함한 관찰 사실')
  let release: () => void = () => {}
  const gate = new Promise<void>(resolve => { release = resolve })
  let markStarted: () => void = () => {}
  const started = new Promise<void>(resolve => { markStarted = resolve })
  await page.route(`**/analysis-runs/${target}/review-items`, async route => {
    if (route.request().method() !== 'POST') return route.continue()
    const response = await route.fetch()
    markStarted()
    await gate
    await route.fulfill({ response })
  })
  await workspace.getByRole('button', { name: '북마크 저장' }).click()
  await started
  await expect(workspace.getByRole('button', { name: '저장 중…' })).toBeDisabled()
  await body.fill('응답 대기 중에 보완한 설계 변경 방향')
  release()
  await expect(workspace.getByRole('button', { name: '북마크 저장' })).toBeEnabled()
  await expect(body).toHaveValue('응답 대기 중에 보완한 설계 변경 방향')
})

test('시계열만 바꾸면 문제 필터와 선택 근거를 유지하고 신뢰도·의견을 재조회하지 않는다', async ({ page }) => {
  let supportingReads = 0
  page.on('request', request => {
    if (request.method() === 'GET' && /\/analysis-runs\/[^/]+\/(trust|review-items)$/.test(new URL(request.url()).pathname)) supportingReads++
  })
  await page.route('**/run-comparison?*', async route => {
    const response = await route.fetch()
    const data = await response.json() as RunComparison
    const descriptor = { variable_key: 'qa-second-series', display_name: '두 번째 근거 이력', unit: 'MPa' }
    data.available_series.push(descriptor)
    if (new URL(route.request().url()).searchParams.get('variable_key') === descriptor.variable_key && data.time_series) {
      data.time_series = { ...data.time_series, ...descriptor }
    }
    await route.fulfill({ response, json: data })
  })
  const { workspace } = await openComparison(page)
  await page.getByTestId('comparison-filter-target_fail').click()
  await page.getByTestId('comparison-evidence-row').first().click()
  const selected = await page.getByTestId('comparison-evidence-detail').locator('code').textContent()
  const body = workspace.getByLabel('검토 의견', { exact: true })
  await body.fill('이력 비교 중에도 남아야 하는 설계 가설')
  const before = supportingReads
  await workspace.getByLabel('시계열 변수 선택').selectOption('qa-second-series')
  await expect(page.getByTestId('comparison-series-chart')).toHaveAttribute('data-series-key', 'qa-second-series')
  await expect(page.getByTestId('comparison-filter-target_fail')).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('comparison-evidence-detail').locator('code')).toHaveText(selected!)
  await expect(body).toHaveValue('이력 비교 중에도 남아야 하는 설계 가설')
  expect(supportingReads).toBe(before)
})
