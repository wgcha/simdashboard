import { expect, test, type Page } from '@playwright/test'
import { spawn, spawnSync, type ChildProcess } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

test.skip(process.platform !== 'win32', 'Native Windows executable integration; portable API/executor coverage lives in local_runner/tests.')

const workspace = path.resolve(process.cwd(), '..')
const python = path.join(workspace, '.venv-runtime', 'Scripts', 'python.exe')
const origin = 'http://127.0.0.1:8766'
let token = ''
const dataDirectory = path.join(workspace, '.local-runner', `browser-test-${Date.now()}`)
const evidence = path.join(workspace, 'backups', 'local-program-qa')
const inputFile = path.join(dataDirectory, 'model_A.inp')
const failedFile = path.join(dataDirectory, 'model_fail.inp')
let helper: ChildProcess | undefined
let helperOutput = ''

async function runnerRequest(route: string, method = 'GET', body?: unknown, credential = token) {
  const response = await fetch(`${origin}/v1${route}`, {
    method,
    headers: { Authorization: `Bearer ${credential}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  const result = await response.json()
  expect(response.ok, JSON.stringify(result)).toBe(true)
  return result
}

test.beforeAll(async () => {
  mkdirSync(dataDirectory, { recursive: true })
  mkdirSync(evidence, { recursive: true })
  writeFileSync(inputFile, 'Harmless browser fixture; not an actual solver input.\n')
  writeFileSync(failedFile, 'Intentional failure fixture.\n')
  helper = spawn(python, [path.join(process.cwd(), 'e2e', 'fixtures', 'local_runner_server.py'), dataDirectory], {
    cwd: workspace, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  })
  helper.stdout?.on('data', (value) => { helperOutput += String(value) })
  helper.stderr?.on('data', (value) => { helperOutput += String(value) })
  await expect.poll(async () => {
    if (helper?.exitCode !== null) throw new Error(`Disposable helper exited: ${helperOutput}`)
    try { return (await fetch(`${origin}/v1/identity`)).status } catch { return 0 }
  }, { timeout: 20_000 }).toBe(200)
})

async function registerFixturePrograms() {
  const basePython = spawnSync(python, ['-c', 'import sys; print(sys._base_executable)'], { encoding: 'utf8', windowsHide: true }).stdout.trim()
  for (const [version, executable] of [['2024.1', python], ['2025.1', basePython]]) {
    await runnerRequest('/programs', 'POST', {
      name: 'HyperMesh', version, keywords: ['메시 수정', '메시', '전처리', 'HyperMesh'], executable_path: executable,
      arguments: ['-c', 'import pathlib,sys; sys.exit(3 if "fail" in pathlib.Path(sys.argv[1]).name else 0)', '{input}'],
    })
  }
}

test.afterAll(async () => {
  // Stop only the process this fixture owns. Leave its disposable history as QA evidence.
  if (helper && helper.exitCode === null) {
    const exited = new Promise<void>((resolve) => helper!.once('exit', () => resolve()))
    helper.kill()
    await exited
    helper = undefined
  }
})

async function openLocalPrograms(page: Page) {
  await loginWorkspace(page)
  await openWorkspaceRoute(page, '/workspace/execution')
  await expect(page.getByTestId('local-program-panel')).toBeVisible()
  const start = page.getByTestId('start-current-work')
  if (await start.isVisible()) await start.click()
  const panel = page.getByTestId('local-program-panel')
  await expect(panel).toBeVisible()
  await expect(panel.getByLabel('연결 코드', { exact: true })).toHaveCount(0)
  const sessionResponse = page.waitForResponse((response) => response.url().includes('/api/local-execution/devices/') && response.url().endsWith('/session') && response.status() === 200)
  await panel.getByRole('button', { name: '이 PC 연결', exact: true }).click()
  token = (await (await sessionResponse).json()).token
  await expect.poll(async () => (await runnerRequest('/health')).user_id).not.toBe('')
  await registerFixturePrograms()
  const search = panel.getByLabel('프로그램 검색', { exact: true })
  await search.fill('메시')
  await search.press('Enter')
  await expect(panel.getByRole('option')).toHaveCount(2)
  return panel
}

test('메시 키워드 실행과 계정별 PC 자동 연결·중앙 이력·연결 해제를 검증한다', async ({ page, browser }) => {
  test.setTimeout(150_000)
  const consoleErrors: string[] = []
  page.on('pageerror', (error) => consoleErrors.push(error.message))
  page.on('console', (message) => {
    if (message.type() !== 'error') return
    // The login bootstrap intentionally checks the unauthenticated /auth/me endpoint.
    if (message.text().includes('401') && message.location().url.includes('/api/auth/me')) return
    consoleErrors.push(message.text())
  })
  const panel = await openLocalPrograms(page)
  await expect(page).toHaveURL(/\/workspace\/execution/)
  await expect(page).not.toHaveTitle('')
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)

  const search = panel.getByLabel('프로그램 검색', { exact: true })
  await search.fill('메시 수정')
  await search.press('Enter')
  await expect(panel.getByRole('option')).toHaveCount(2)
  await expect(panel.getByRole('option', { selected: true })).toHaveCount(0)
  await panel.getByRole('option', { name: /HyperMesh.*2024\.1/ }).click()
  await expect(panel.getByRole('option', { selected: true })).toContainText('2024.1')
  await search.fill('2025.1')
  await search.press('Enter')
  await expect(panel.getByRole('option')).toHaveCount(1)
  await expect(panel.getByRole('option')).toContainText('2025.1')
  await search.fill('메시')
  await search.press('Enter')
  await expect(panel.getByRole('option')).toHaveCount(2)
  await panel.getByRole('option', { name: /HyperMesh.*2024\.1/ }).click()
  await panel.getByLabel('입력 파일', { exact: true }).first().fill(inputFile)
  await panel.getByLabel('작업 폴더', { exact: true }).first().fill(dataDirectory)
  await panel.scrollIntoViewIfNeeded()
  await page.screenshot({ path: path.join(evidence, 'local-program-selection-desktop.png'), fullPage: false })
  await panel.getByRole('button', { name: '프로그램 열고 작업 시작', exact: true }).click()
  await expect(panel.getByText('사용자 완료 필요', { exact: true })).toBeVisible({ timeout: 20_000 })
  const firstRun = (await runnerRequest('/runs'))[0]
  expect(firstRun.program_version).toBe('2024.1')
  expect(firstRun.program_snapshot.executable_path.toLowerCase()).toBe(python.toLowerCase())
  expect(firstRun.status).toBe('AWAITING_COMPLETION')
  await panel.getByPlaceholder('작업 완료 메모 (선택)').fill('메시 수정 검토 완료')
  await panel.getByRole('button', { name: '업무 완료 표시', exact: true }).click()
  await expect.poll(async () => (await runnerRequest('/runs'))[0].status).toBe('COMPLETED')

  await panel.getByRole('tab', { name: '일괄 실행', exact: true }).click()
  // Only the native dialog is substituted; catalogue, launches and history use
  // the real helper. This verifies every selected file becomes a batch item.
  await page.route(`${origin}/v1/pick`, (route) => route.fulfill({ json: { paths: [inputFile, failedFile] } }))
  await panel.getByRole('button', { name: '파일', exact: true }).first().click()
  await expect(panel.getByLabel('입력 파일', { exact: true })).toHaveCount(2)
  await expect(panel.getByLabel('입력 파일', { exact: true }).nth(1)).toHaveValue(failedFile)
  await page.unroute(`${origin}/v1/pick`)
  await panel.getByLabel('작업 폴더', { exact: true }).nth(1).fill(dataDirectory)
  await panel.getByLabel('목록 이름', { exact: true }).fill('메시 수정 비교 목록')
  await panel.getByRole('button', { name: '목록 저장', exact: true }).click()
  await expect(panel.getByRole('button', { name: /메시 수정 비교 목록/ })).toBeVisible()
  await panel.getByRole('button', { name: /선택한 2개 실행/ }).click()
  await expect.poll(async () => (await runnerRequest('/runs')).filter((run: { status: string }) => ['SUCCEEDED', 'FAILED'].includes(run.status)).length).toBe(2)
  await expect(panel.getByRole('button', { name: '실패 항목 새로 실행', exact: true })).toBeVisible()
  const batchRuns = (await runnerRequest('/runs')).filter((run: { mode: string }) => run.mode === 'BATCH')
  expect(new Set(batchRuns.map((run: { batch_id: string }) => run.batch_id)).size).toBe(1)
  expect(batchRuns.map((run: { status: string }) => run.status).sort()).toEqual(['FAILED', 'SUCCEEDED'])

  // Editing the live registration and selecting a different version must not
  // change the executable or arguments used by an explicit historical retry.
  const failedRun = batchRuns.find((run: { status: string }) => run.status === 'FAILED')
  await runnerRequest(`/programs/${failedRun.program_snapshot.id}`, 'PUT', {
    name: 'HyperMesh', version: 'changed-after-run', keywords: ['메시 수정'],
    executable_path: python, arguments: ['-c', 'pass'],
  })
  await panel.getByRole('option', { name: /HyperMesh.*2025\.1/ }).click()
  await panel.getByRole('button', { name: '실패 항목 새로 실행', exact: true }).click()
  await expect.poll(async () => (await runnerRequest('/runs')).find((run: { source_run_id?: string }) => run.source_run_id === failedRun.id)?.status).toBe('FAILED')
  const retried = (await runnerRequest('/runs')).find((run: { source_run_id?: string }) => run.source_run_id === failedRun.id)
  expect(retried.program_version).toBe('2024.1')
  expect(retried.exit_code).toBe(3)
  expect(retried.program_snapshot.arguments).toEqual(failedRun.program_snapshot.arguments)

  await page.setViewportSize({ width: 390, height: 844 })
  await panel.scrollIntoViewIfNeeded()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await expect.poll(() => panel.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
  await expect.poll(() => panel.locator('input, button').evaluateAll((elements) => elements.filter((element) => {
    const rect = element.getBoundingClientRect()
    return rect.width > 0 && (rect.left < 0 || rect.right > innerWidth + 1)
  }).length)).toBe(0)
  await page.screenshot({ path: path.join(evidence, 'local-program-batch-mobile.png'), fullPage: false })
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.getByRole('button', { name: '라이트', exact: true }).click()
  await panel.locator('.local-mode-tabs').scrollIntoViewIfNeeded()
  await page.screenshot({ path: path.join(evidence, 'local-program-light-desktop.png'), fullPage: false })
  expect(consoleErrors).toEqual([])

  // Reopening the page uses the logged-in account's binding without another
  // pairing request. Only the short-lived device session is renewed.
  const pairRequests: string[] = []
  page.on('request', (request) => {
    if (request.url().endsWith('/api/local-execution/pairing')) pairRequests.push(request.url())
  })
  const restoredSession = page.waitForResponse((response) => response.url().includes('/api/local-execution/devices/') && response.url().endsWith('/session') && response.ok())
  await page.reload()
  token = (await (await restoredSession).json()).token
  await expect(page.getByTestId('local-program-panel').getByRole('option')).toHaveCount(2)
  expect(pairRequests).toEqual([])
  const bindingId = (await runnerRequest('/health')).binding_id
  const historyQuery = new URLSearchParams({ request_id: firstRun.context.request_id, work_item_id: firstRun.context.work_item_id })
  await expect.poll(async () => {
    const response = await page.request.get(`/api/local-execution/runs?${historyQuery}`)
    expect(response.ok()).toBe(true)
    return (await response.json()).filter((run: { status: string }) => ['COMPLETED', 'SUCCEEDED', 'FAILED'].includes(run.status)).length
  }, { timeout: 20_000 }).toBe(4)

  // A separate company account on this very same helper cannot reuse the
  // first account's binding and gets its own empty catalogue after approval.
  const peerContext = await browser.newContext()
  const peer = await peerContext.newPage()
  await loginWorkspace(peer, 'e2e-viewer')
  const otherSession = await peer.request.post(`/api/local-execution/devices/${bindingId}/session`, { data: {} })
  expect([403, 404]).toContain(otherSession.status())
  await openWorkspaceRoute(peer, '/workspace/execution')
  const peerPanel = peer.getByTestId('local-program-panel')
  const peerSessionResponse = peer.waitForResponse((response) => response.url().includes('/api/local-execution/devices/') && response.url().endsWith('/session') && response.ok())
  await peerPanel.getByRole('button', { name: '이 PC 연결', exact: true }).click()
  const peerSession = await (await peerSessionResponse).json()
  await expect.poll(async () => (await runnerRequest('/health', 'GET', undefined, peerSession.token)).binding_id).toBe(peerSession.binding_id)
  expect(await runnerRequest('/programs', 'GET', undefined, peerSession.token)).toEqual([])
  expect((await runnerRequest('/programs')).length).toBe(2)
  const sharedHistory = await peer.request.get(`/api/local-execution/runs?${historyQuery}`)
  expect(sharedHistory.ok()).toBe(true)
  expect((await sharedHistory.json()).length).toBe(4)
  expect((await peer.request.post(`/api/local-execution/devices/${peerSession.binding_id}/revoke`, { data: {} })).ok()).toBe(true)
  const revoked = await fetch(`${origin}/v1/programs`, { headers: { Authorization: `Bearer ${peerSession.token}` } })
  expect([401, 403]).toContain(revoked.status)
  await peerContext.close()

  // The central history survives closing the companion process entirely.
  if (helper && helper.exitCode === null) {
    const exited = new Promise<void>((resolve) => helper!.once('exit', () => resolve()))
    helper.kill()
    await exited
    helper = undefined
  }
  await page.reload()
  await expect(page.getByTestId('local-program-panel').locator('.local-run')).toHaveCount(4)
  await page.getByTestId('local-program-panel').locator('.local-run').filter({ hasText: '메시 수정 검토 완료' }).getByText('실행 상세 보기').click()
  await expect(page.getByTestId('local-program-panel').getByText('메시 수정 검토 완료')).toBeVisible()
  await page.getByTestId('local-program-panel').scrollIntoViewIfNeeded()
  await page.screenshot({ path: path.join(evidence, 'managed-local-central-history-offline.png'), fullPage: false })
})
