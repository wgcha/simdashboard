import { expect, test, type Locator, type Page } from '@playwright/test'
import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

async function matchesToken(locator: Locator, property: 'color' | 'backgroundColor', token: string) {
  const colors = await locator.evaluate((element, { property, token }) => {
    const probe = document.createElement('i')
    probe.style.color = `var(${token})`
    element.appendChild(probe)
    const expected = getComputedStyle(probe).color
    probe.remove()
    return { actual: getComputedStyle(element)[property], expected }
  }, { property, token })
  expect(colors.actual).toBe(colors.expected)
}

async function readableButton(button: Locator) {
  const contrast = await button.evaluate(element => {
    const luminance = (color: string) => {
      const rgb = color.match(/[\d.]+/g)!.slice(0, 3).map(Number).map(value => {
        const c = value / 255
        return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4
      })
      return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722
    }
    const background = luminance(getComputedStyle(element).backgroundColor)
    const label = element.querySelector('span') ?? element
    const foreground = luminance(getComputedStyle(label).color)
    return (Math.max(background, foreground) + .05) / (Math.min(background, foreground) + .05)
  })
  expect(contrast).toBeGreaterThanOrEqual(4.5)
}

async function chooseTheme(page: Page, theme: 'light' | 'dark') {
  await page.getByRole('button', { name: theme === 'light' ? '라이트' : '다크', exact: true }).click()
  await expect(page.locator('.app-shell')).toHaveAttribute('data-theme', theme)
}

test('global theme owns result widget controls after lazy feature navigation', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.setViewportSize({ width: 1500, height: 960 })
  await loginWorkspace(page, 'e2e-admin', '/workspace/catalog/schemas')
  await expect(page.getByRole('heading', { name: '결과 의미 연결', exact: true })).toBeVisible()
  await openWorkspaceRoute(page, '/workspace/requests')
  await page.locator('.request-journey').getByRole('button', { name: /결과 검토|상세 분석/ }).click()
  const seedRun = await page.getByLabel('결과 버전 선택').locator('option').filter({ hasText: /^v1 ·/ }).getAttribute('value')
  expect(seedRun).toBeTruthy()
  await page.getByLabel('결과 버전 선택').selectOption(seedRun!)
  await expect(page).toHaveTitle('VD simulation workbench')
  const widget = page.locator('dialog.widget-card').first()
  await expect(widget).toBeVisible()
  for (const theme of ['light', 'dark'] as const) {
    await chooseTheme(page, theme)
    const button = widget.locator('.widget-focus-button')
    await page.mouse.move(0, 0)
    await matchesToken(button, 'backgroundColor', '--color-surface-interactive')
    await matchesToken(button, 'color', '--color-text')
    await matchesToken(button.locator('span'), 'color', '--color-text')
    await readableButton(button)
    await button.hover()
    await readableButton(button)
    await button.focus()
    await readableButton(button)
    await button.click()
    await expect(widget).toHaveAttribute('aria-modal', 'true')
    await matchesToken(widget, 'backgroundColor', '--color-surface-raised')
    await readableButton(widget.locator('.widget-focus-button'))
    await page.screenshot({ path: testInfo.outputPath(`widget-${theme}.png`) })
    await page.keyboard.press('Escape')
    await expect(button).toBeFocused()
  }
  await chooseTheme(page, 'light')
  await page.reload()
  await expect(widget).toBeVisible()
  await expect(page.locator('.app-shell')).toHaveAttribute('data-theme', 'light')
  await matchesToken(widget.locator('.widget-focus-button'), 'backgroundColor', '--color-surface-interactive')
  await page.setViewportSize({ width: 390, height: 844 })
  await widget.locator('.widget-focus-button').click()
  await expect(widget).toHaveAttribute('aria-modal', 'true')
  await readableButton(widget.locator('.widget-focus-button'))
  await page.screenshot({ path: testInfo.outputPath('widget-light-mobile.png') })
  await page.keyboard.press('Escape')
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  expect(errors).toEqual([])
})

test('semantic definition surfaces inherit both global palettes', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await loginWorkspace(page, 'e2e-admin', '/workspace/catalog/schemas')
  const card = page.locator('.semantic-card').first()
  await expect(card).toBeVisible()
  for (const theme of ['light', 'dark'] as const) {
    await chooseTheme(page, theme)
    await matchesToken(card, 'backgroundColor', '--color-surface-1')
    await matchesToken(page.locator('.semantic-page'), 'color', '--color-text')
    await page.screenshot({ path: testInfo.outputPath(`semantic-${theme}.png`) })
  }
  expect(errors).toEqual([])
})
