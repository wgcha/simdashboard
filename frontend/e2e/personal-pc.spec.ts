import { expect, test } from '@playwright/test'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdirSync, readFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

test.skip(process.platform !== 'win32', 'Exercises the Windows local companion against the real central API.')
const workspace = path.resolve(process.cwd(), '..')
const python = path.join(workspace, '.venv-runtime', 'Scripts', 'python.exe')
const evidence = process.env.PC_SETTINGS_EVIDENCE_DIR ?? path.join(os.tmpdir(), 'workbench-personal-pc-evidence')
let helper: ChildProcess | undefined
test.afterAll(async () => {
  if (helper && helper.exitCode === null) {
    const done = new Promise<void>((resolve) => helper!.once('exit', () => resolve()))
    helper.kill()
    await done
  }
})

test('일반 사용자가 기본 내 PC 설정에서 도우미 준비와 개인 프로그램 관리를 한다', async ({ page, browser }) => {
  test.setTimeout(150_000)
  page.setDefaultTimeout(15_000)
  mkdirSync(evidence, { recursive: true })
  const pageErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  // No project data is needed to enter this built-in account screen.
  await page.route('**/api/projects', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/local-helper/distribution', (route) => route.fulfill({ json: {
    status: 'ready', version: 'test-1', filename: 'local-helper-windows-x64.zip',
    artifact_url: '/api/local-helper/distribution/download', sha256: 'a'.repeat(64),
    size_bytes: 1024, released_at: '2026-09-09T00:00:00Z',
  } }))
  await loginWorkspace(page, 'e2e-viewer', '/workspace/settings/local-pc')
  const settings = page.getByTestId('local-pc-settings')
  await expect(page).toHaveURL(/\/workspace\/settings\/local-pc/)
  await expect(page.getByRole('complementary', { name: '주 메뉴' }).getByRole('link', { name: '내 PC 설정', exact: true })).toBeVisible()
  await expect(settings.getByRole('heading', { name: '내 PC 설정', exact: true })).toBeVisible()
  // Wait for the negative identity probe before starting the helper: otherwise
  // its first reply can legitimately take us directly to the pairing state.
  await expect(settings.getByRole('button', { name: '다시 확인', exact: true })).toBeVisible()
  await expect(settings.getByRole('button', { name: '다시 확인', exact: true })).toBeEnabled()
  await expect(settings.getByRole('button', { name: 'PC 도우미 설치 파일 받기' })).toBeEnabled()
  const downloadEvent = page.waitForEvent('download')
  await settings.getByRole('button', { name: 'PC 도우미 설치 파일 받기' }).click()
  const download = await downloadEvent
  expect(download.suggestedFilename()).toBe('SimulationWorkbench-local-helper-setup.bat')
  const setupPath = await download.path()
  expect(setupPath).toBeTruthy()
  expect(readFileSync(setupPath!, 'utf8')).not.toContain('e2e-validation-password')
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.screenshot({ path: path.join(evidence, 'personal-pc-first-setup.png'), fullPage: false })

  const dataDir = path.join(os.tmpdir(), `workbench-personal-pc-helper-${Date.now()}`)
  let helperOutput = ''
  helper = spawn(python, [path.join(process.cwd(), 'e2e', 'fixtures', 'local_runner_server.py'), dataDir], { cwd: workspace, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] })
  helper.stdout?.on('data', (chunk) => { helperOutput += String(chunk) })
  helper.stderr?.on('data', (chunk) => { helperOutput += String(chunk) })
  await expect.poll(async () => {
    if (helper?.exitCode !== null) throw new Error(helperOutput)
    try { return (await fetch('http://127.0.0.1:8766/v1/identity')).status } catch { return 0 }
  }).toBe(200)
  await settings.getByRole('button', { name: '다시 확인', exact: true }).click()
  await settings.getByRole('button', { name: '이 PC 연결', exact: true }).click()
  await expect(settings.getByRole('heading', { name: '이 PC의 프로그램' })).toBeVisible()
  await settings.getByRole('button', { name: '직접 등록', exact: true }).click()
  await settings.getByLabel('이름 *', { exact: true }).fill('HyperMesh personal fixture')
  await settings.getByLabel('버전', { exact: true }).fill('2024.1')
  await settings.getByLabel('키워드', { exact: true }).fill('메시 수정, 전처리')
  await settings.getByLabel('실행 파일', { exact: true }).fill(python)
  await settings.getByRole('button', { name: '프로그램 등록', exact: true }).click()
  const program = settings.locator('.local-pc-program').filter({ hasText: 'HyperMesh personal fixture' })
  await expect(program).toContainText('2024.1')
  await program.getByRole('button', { name: '수정', exact: true }).click()
  await settings.getByLabel('버전', { exact: true }).fill('2025.1')
  await settings.getByRole('button', { name: '수정 저장', exact: true }).click()
  await expect(program).toContainText('2025.1')
  await settings.getByLabel('프로그램 검색', { exact: true }).fill('메시')
  await settings.getByRole('button', { name: '프로그램 검색 실행' }).click()
  await expect(program).toBeVisible()
  await page.screenshot({ path: path.join(evidence, 'personal-pc-connected-desktop.png'), fullPage: false })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(settings.getByRole('heading', { name: '내 PC 설정', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({ path: path.join(evidence, 'personal-pc-mobile.png'), fullPage: false })
  await page.setViewportSize({ width: 1440, height: 960 })

  // The same catalogue appears after returning through normal sidebar navigation.
  await page.reload()
  await expect(settings.locator('.local-pc-program')).toHaveCount(1)
  await expect(settings.getByRole('button', { name: '이 PC 연결', exact: true })).toHaveCount(0)
  const peerContext = await browser.newContext()
  const peer = await peerContext.newPage()
  await loginWorkspace(peer, 'e2e-admin', '/workspace/settings/local-pc')
  await expect(peer.getByTestId('local-pc-settings').getByRole('button', { name: '이 PC 연결', exact: true })).toBeVisible()
  await expect(peer.locator('.local-pc-program')).toHaveCount(0)
  await peerContext.close()

  page.once('dialog', (dialog) => void dialog.accept())
  await program.getByRole('button', { name: '삭제', exact: true }).click()
  await expect(settings.locator('.local-pc-program')).toHaveCount(0)
  await settings.getByRole('button', { name: '이 PC 연결 해제', exact: true }).click()
  await expect(settings.getByRole('heading', { name: '이 PC의 프로그램' })).toHaveCount(0)
  await expect(settings.getByRole('button', { name: '다시 확인', exact: true })).toBeVisible()
  expect(pageErrors).toEqual([])
  await openWorkspaceRoute(page, '/workspace/settings/local-pc')
})
