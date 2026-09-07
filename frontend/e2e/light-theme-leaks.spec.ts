import { expect, test, type Page } from '@playwright/test'

const password = 'e2e-validation-password'

async function login(page: Page) {
  await page.goto('/workspace/examples')
  await page.getByLabel('사용자 이름').fill('e2e-admin')
  await page.getByLabel('비밀번호').fill(password)
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '주 메뉴' })).toBeVisible()
  await expect(page).toHaveTitle('VD simulation workbench')
}

async function switchToLight(page: Page) {
  await page.getByRole('button', { name: '라이트', exact: true }).click()
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).toBe('light')
  await expect(page.locator('.app-shell[data-theme="light"]')).toBeVisible()
}

type ColorToken = '--color-app-bg' | '--color-surface-1' | '--color-surface-2' | '--color-text' | '--color-text-muted' | '--color-text-strong' | '--color-accent' | '--color-border' | '--color-border-strong'

async function computedColorMatchesToken(page: Page, selector: string, property: 'backgroundColor' | 'color' | 'borderTopColor' | 'borderRightColor', token: ColorToken) {
  const result = await page.locator(selector).first().evaluate((element, { property, token }) => {
    const normalize = (value: string) => {
      const hex = value.trim().match(/^#([\da-f]{6})$/i)
      if (!hex) return value.trim()
      const digits = hex[1]
      return `rgb(${Number.parseInt(digits.slice(0, 2), 16)}, ${Number.parseInt(digits.slice(2, 4), 16)}, ${Number.parseInt(digits.slice(4, 6), 16)})`
    }
    const style = getComputedStyle(element)
    const root = getComputedStyle(document.documentElement)
    return { computed: style[property], expected: normalize(root.getPropertyValue(token)) }
  }, { property, token })
  expect(result.computed, `${selector} ${property} should use ${token}`).toBe(result.expected)
}

async function computedColorIsLightMuted(page: Page, selector: string) {
  const result = await page.locator(selector).first().evaluate((element) => {
    const normalize = (value: string) => {
      const hex = value.trim().match(/^#([\da-f]{6})$/i)
      if (!hex) return value.trim()
      const digits = hex[1]
      return `rgb(${Number.parseInt(digits.slice(0, 2), 16)}, ${Number.parseInt(digits.slice(2, 4), 16)}, ${Number.parseInt(digits.slice(4, 6), 16)})`
    }
    const computed = getComputedStyle(element).color
    const rootMuted = normalize(getComputedStyle(document.documentElement).getPropertyValue('--color-text-muted'))
    return { computed, allowed: [rootMuted, 'rgb(82, 97, 116)'], darkBaseMuted: 'rgb(129, 152, 173)' }
  })
  expect(result.allowed, `${selector} should use a light muted text role`).toContain(result.computed)
  expect(result.computed, `${selector} should not retain the dark muted text role`).not.toBe(result.darkBaseMuted)
}

async function expectNoFrameworkOverlay(page: Page) {
  await expect(page.locator('vite-error-overlay, #vite-error-overlay, [data-vite-error-overlay], nextjs-portal, [data-nextjs-dialog]')).toHaveCount(0)
}

test('light theme keeps feature examples and folder schema surfaces on light tokens', async ({ page }, testInfo) => {
  const consoleErrors: string[] = []
  let collectConsoleErrors = false
  page.on('console', (message) => {
    if (collectConsoleErrors && message.type() === 'error') consoleErrors.push(message.text())
  })

  await login(page)
  await switchToLight(page)
  collectConsoleErrors = true

  await page.goto('/workspace/examples')
  await expect(page).toHaveURL(/\/workspace\/examples(?:\?|$)/)
  const gallery = page.locator('.example-gallery')
  await expect(gallery).toBeVisible()
  await expectNoFrameworkOverlay(page)
  await expect(gallery.locator('.example-card').first()).toBeVisible()
  await expect(gallery.locator('.example-tags span').first()).toBeVisible()
  await expect(gallery.locator('.example-profile').first()).toBeVisible()
  await expect(gallery.locator('.example-card > section').first()).toBeVisible()

  await computedColorMatchesToken(page, '.example-gallery', 'backgroundColor', '--color-app-bg')
  await computedColorMatchesToken(page, '.example-guide', 'backgroundColor', '--color-surface-2')
  await computedColorMatchesToken(page, '.example-gallery > header aside', 'backgroundColor', '--color-surface-1')
  await computedColorMatchesToken(page, '.example-tags span', 'backgroundColor', '--color-surface-2')
  await computedColorMatchesToken(page, '.example-profile', 'backgroundColor', '--color-surface-1')
  await computedColorMatchesToken(page, '.example-card > section', 'backgroundColor', '--color-surface-1')
  await computedColorMatchesToken(page, '.example-tags span', 'color', '--color-accent')
  await computedColorMatchesToken(page, '.example-profile strong', 'color', '--color-text-strong')
  await computedColorMatchesToken(page, '.example-card > section ol', 'color', '--color-text-muted')
  await page.screenshot({ path: testInfo.outputPath('light-examples.png'), fullPage: true })

  await page.goto('/workspace/catalog/schemas')
  await expect(page).toHaveURL(/\/workspace\/catalog\/schemas(?:\?|$)/)
  const schemaPage = page.locator('.catalog-page')
  const refreshPanel = page.locator('.master-result-refresh')
  await expect(schemaPage).toBeVisible()
  await expect(refreshPanel).toBeVisible()
  await expectNoFrameworkOverlay(page)
  await expect(page.getByRole('heading', { name: '마스터 결과 폴더' })).toBeVisible()
  await expect(refreshPanel.getByRole('button', { name: /마스터 폴더 Refresh/ })).toBeVisible()

  await computedColorMatchesToken(page, '.catalog-page', 'backgroundColor', '--color-surface-1')
  await computedColorMatchesToken(page, '.master-result-refresh', 'backgroundColor', '--color-surface-1')
  await computedColorMatchesToken(page, '.master-result-refresh', 'borderTopColor', '--color-border')
  await computedColorIsLightMuted(page, '.master-result-refresh header p')
  await page.screenshot({ path: testInfo.outputPath('light-schemas.png'), fullPage: true })

  await page.getByRole('button', { name: '다크', exact: true }).click()
  await expect.poll(() => page.evaluate(() => ({
    theme: document.documentElement.dataset.theme,
    appBackground: getComputedStyle(document.documentElement).getPropertyValue('--color-app-bg').trim(),
    refreshHasDarkSurface: getComputedStyle(document.querySelector('.master-result-refresh')!).backgroundImage.includes('rgb(15, 36, 53)'),
  }))).toEqual({ theme: 'dark', appBackground: '#07111d', refreshHasDarkSurface: true })
  expect(consoleErrors, 'target routes should not emit browser console errors').toEqual([])
})
