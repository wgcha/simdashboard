import { expect, test, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { WORKSPACE_ROUTES } from '../src/features/navigation/workspaceRouteRegistry'
import { loginWorkspace, openWorkspaceRoute } from './workspace-test-helpers'

const routeHeadings: Record<string, string> = {
  portfolio: '결과 대시보드', workbench_admin: '작업 유형 관리',
  project_result_profiles: '프로젝트 결과 구성', schemas: '폴더 연결·규칙', variables: '변수 카탈로그',
  templates: '모델링 템플릿', access_admin: '사용자·프로젝트 권한', menu_policy_admin: '권한 및 좌측 메뉴 정책',
  audit_admin: '감사로그', examples: '기능 예제 갤러리', help: 'VD simulation workbench 사용 도움말', voc: 'VOC 게시판',
}

async function setFont(page: Page, from: number, to: number) {
  const button = page.getByRole('button', { name: to > from ? '전체 글자 크기 늘리기' : '전체 글자 크기 줄이기', exact: true })
  for (let i = 0; i < Math.abs(to - from); i++) await button.click()
  await expect.poll(() => page.locator('html').evaluate(e => parseFloat(getComputedStyle(e).fontSize))).toBeCloseTo(to * 4 / 3, 1)
}

for (const { width, pt, theme } of [
  { width: 1366, pt: 18, theme: '다크' },
  { width: 1920, pt: 11, theme: '라이트' },
]) {
  test(`P5 ${width}px ${pt}pt ${theme}: every canonical route renders with the root preference`, async ({ page }) => {
    test.setTimeout(240_000)
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('http://127.0.0.1:8766/**', route => route.abort('connectionrefused'))
    await page.setViewportSize({ width, height: width === 1366 ? 768 : 1080 })
    await loginWorkspace(page, 'e2e-admin', '/workspace/overview')
    await setFont(page, 14, pt)
    await page.getByRole('button', { name: theme, exact: true }).click()
    await expect.poll(() => page.getByRole('button', { name: '로그아웃', exact: true }).evaluate(e => parseFloat(getComputedStyle(e).fontSize))).toBeCloseTo(pt * 4 / 3 * .76, 1)
    await expect.poll(() => page.getByRole('link', { name: '내 작업', exact: true }).evaluate(e => parseFloat(getComputedStyle(e).fontSize))).toBeCloseTo(pt * 4 / 3 * .86, 1)
    for (const route of WORKSPACE_ROUTES.filter(({ id }) => id !== 'local_pc')) {
      await test.step(route.path, async () => {
        await openWorkspaceRoute(page, route.path)
        // URL updates before lazy route content: never certify the previous screen.
        if (routeHeadings[route.id]) await expect(page.getByRole('heading', { name: routeHeadings[route.id], exact: true })).toBeVisible()
        else if (route.id === 'intake') await expect(page.getByTestId('request-intake-page')).toBeVisible()
        else if (route.id === 'workbench') await expect(page.getByTestId('simulation-workbench')).toBeVisible()
        else if (route.id === 'data') await expect(page.locator('.data-workspace')).toBeVisible()
        else await expect(page.locator('.request-journey [aria-current="step"]')).toHaveText('의뢰 개요')
        await expect(page).toHaveURL(new RegExp(`${route.path.replaceAll('/', '\\/')}(?:\\?|$)`))
        await expect(page.locator('html')).toHaveCSS('font-size', pt === 18 ? '24px' : '14.6667px')
        await expect.poll(() => page.locator('.breadcrumb').evaluate(e => parseFloat(getComputedStyle(e).fontSize))).toBeCloseTo(pt * 4 / 3, 1)
        await expect(page.locator('vite-error-overlay')).toHaveCount(0)
        const main = page.getByRole('main').first()
        await expect(main).toBeVisible()
        await expect(main.locator('.spin')).toHaveCount(0)
        await expect(main.locator('h1,h2').first()).toBeVisible()
        await expect.poll(() => main.locator('h1,h2').first().evaluate(e => parseFloat(getComputedStyle(e).fontSize)), route.path).toBeGreaterThanOrEqual(pt * 4 / 3)
        await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), route.path).toBe(true)
        const captions: Record<string, string> = {
          workbench_admin: '.workbench-admin-task-picker>header>p', variables: '.variable-row small',
          voc: '.voc-compose>div>span',
        }
        if (captions[route.id]) {
          const ratio = route.id === 'workbench_admin' ? 1 : .875
          await expect.poll(() => page.locator(captions[route.id]).first().evaluate(e => parseFloat(getComputedStyle(e).fontSize))).toBeCloseTo(pt * 4 / 3 * ratio, 1)
        }
        if (process.env.GUI_QA_OUTPUT_DIR && ['access_admin', 'schemas', 'voc', 'help'].includes(route.id)) {
          mkdirSync(process.env.GUI_QA_OUTPUT_DIR, { recursive: true })
          await page.screenshot({ path: path.join(process.env.GUI_QA_OUTPUT_DIR, `p5-${route.id}-${width}.png`) })
        }
      })
    }
    expect(errors).toEqual([])
  })
}

test('P5 restores pre-existing root declarations on logout and keeps fractional settings across shells', async ({ page }) => {
  await page.route('http://127.0.0.1:8766/**', route => route.abort('connectionrefused'))
  await page.setViewportSize({ width: 1366, height: 768 })
  // Synthetic browser fixture only: verify cleanup does not discard host styling.
  await page.addInitScript(() => {
    localStorage.setItem('simdashboard.workspace.font-size-pt.v1', '16.25')
    const observer = new MutationObserver(install)
    function install() {
      if (!document.documentElement) return
      document.documentElement.style.setProperty('font-size', '17px', 'important')
      document.documentElement.style.setProperty('--ui-font-size', '12px', 'important')
      observer.disconnect()
    }
    observer.observe(document, { childList: true })
    install()
  })
  await loginWorkspace(page, 'e2e-admin', '/workspace/help')
  await expect(page.locator('html')).toHaveCSS('font-size', '21.6667px')
  await openWorkspaceRoute(page, '/workspace/voc')
  await expect(page.locator('html')).toHaveCSS('font-size', '21.6667px')
  await openWorkspaceRoute(page, '/workspace/help')
  await page.reload()
  await expect(page.locator('.help-center h1')).toHaveCSS('font-size', '32.5px')
  await page.getByRole('button', { name: '로그아웃', exact: true }).click()
  await expect(page.getByRole('heading', { name: '회원 로그인' })).toBeVisible()
  await expect(page.locator('html')).toHaveCSS('font-size', '17px')
  expect(await page.locator('html').evaluate(e => [e.style.getPropertyPriority('font-size'), e.style.getPropertyValue('--ui-font-size'), e.style.getPropertyPriority('--ui-font-size')])).toEqual(['important', '12px', 'important'])
})
