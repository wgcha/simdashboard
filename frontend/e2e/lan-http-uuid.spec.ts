import { expect, test } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'

import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

const expectInsecureHttp = process.env.E2E_EXPECT_INSECURE_HTTP === 'true'
const screenshotDirectory = process.env.E2E_QA_SCREENSHOT_DIR

async function saveScreenshot(page: import('@playwright/test').Page, name: string) {
  if (!screenshotDirectory) return
  mkdirSync(screenshotDirectory, { recursive: true })
  await page.screenshot({ path: path.join(screenshotDirectory, name), fullPage: false })
}

test('HTTP workbench remains usable without crypto.randomUUID', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => {
    if (message.type() !== 'error') return
    // The app intentionally probes this endpoint before the login session exists.
    if (message.text().includes('401') && message.location().url.includes('/api/auth/me')) return
    errors.push(message.text())
  })

  if (!expectInsecureHttp) {
    await page.addInitScript(() => {
      Object.defineProperty(globalThis.crypto, 'randomUUID', { configurable: true, value: undefined })
    })
  }
  await page.route('http://127.0.0.1:8766/v1/identity', (route) => route.fulfill({
    json: { device_id: 'e2e-http-device', host_name: 'E2E HTTP Browser', managed: false },
  }))

  await page.goto('/')
  const cryptoState = await page.evaluate(() => ({ secure: globalThis.isSecureContext, randomUuid: typeof globalThis.crypto?.randomUUID }))
  if (expectInsecureHttp) {
    expect(cryptoState.secure).toBe(false)
    expect(cryptoState.randomUuid).toBe('undefined')
  } else {
    expect(cryptoState.randomUuid).toBe('undefined')
  }

  await loginWorkspace(page, 'e2e-admin', '/workspace/requests')
  await openWorkspaceRoute(page, '/workspace/execution')

  const workbench = page.getByTestId('simulation-workbench')
  await expect(workbench).toBeVisible()
  await expect(page).toHaveTitle('VD simulation workbench')
  await expect(page).toHaveURL(/\/workspace\/execution/)
  await expect(page.getByRole('heading', { name: '배정 작업 순서', exact: true })).toBeVisible()
  const workItems = workbench.locator('.assigned-work-list article')
  let selectedItem
  for (let index = 0; index < await workItems.count(); index += 1) {
    const item = workItems.nth(index)
    if (await item.getAttribute('aria-pressed') !== 'true') {
      selectedItem = item
      break
    }
  }
  if (!selectedItem) throw new Error('Expected a work item other than the initially selected item')
  await selectedItem.click()
  await expect(selectedItem).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('work-item-detail')).toBeVisible()

  const generated = await page.evaluate(async () => {
    const [{ createClientId }, { newIdempotencyKey }] = await Promise.all([
      import('/src/shared/identity/clientId.ts'),
      import('/src/shared/api/localRunner.ts'),
    ])
    return { clientId: createClientId(), requestId: newIdempotencyKey() }
  })
  expect(generated.clientId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  expect(generated.requestId).toMatch(/^local-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)

  await saveScreenshot(page, 'lan-http-uuid-desktop.png')
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(workbench).toBeVisible()
  await saveScreenshot(page, 'lan-http-uuid-mobile.png')
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  expect(errors, errors.join('\n')).toEqual([])
})
