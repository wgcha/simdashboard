import { expect, test } from '@playwright/test'
import { createHash } from 'node:crypto'
import { mkdirSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { loginWorkspace } from './workspace-test-helpers'

test('도우미 미설치 PC에서도 서버 배포본을 확인하고 설치 파일을 받는다', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.route('http://127.0.0.1:8766/**', (route) => route.abort('connectionrefused'))
  const metadata = await page.request.get('/api/local-helper/distribution')
  expect(metadata.ok()).toBe(true)
  const release = await metadata.json() as { status: string; artifact_url: string; sha256: string; size_bytes: number }
  expect(release.status).toBe('ready')
  const archive = await page.request.get(release.artifact_url)
  expect(archive.ok()).toBe(true)
  const payload = await archive.body()
  expect(payload.byteLength).toBe(release.size_bytes)
  expect(createHash('sha256').update(payload).digest('hex')).toBe(release.sha256)

  await loginWorkspace(page, 'e2e-admin', '/workspace/settings/local-pc')
  await expect(page).toHaveURL(/\/workspace\/settings\/local-pc/)
  await expect(page.getByRole('heading', { name: '내 PC 설정', exact: true })).toBeVisible()
  const install = page.getByRole('button', { name: 'PC 도우미 설치 파일 받기', exact: true })
  await expect(install).toBeEnabled()
  const downloaded = page.waitForEvent('download')
  await install.click()
  const download = await downloaded
  expect(download.suggestedFilename()).toBe('SimulationWorkbench-local-helper-setup.bat')
  expect(await download.failure()).toBeNull()
  await expect(page.getByText('설치 파일을 받았습니다.', { exact: false })).toBeVisible()
  const evidence = path.join(os.tmpdir(), 'workbench-helper-evidence')
  mkdirSync(evidence, { recursive: true })
  await page.screenshot({ path: path.join(evidence, 'download-ready-desktop.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(install).toBeVisible()
  await expect(install).toBeEnabled()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({ path: path.join(evidence, 'download-ready-mobile.png'), fullPage: true })
  expect(errors).toEqual([])
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
})
