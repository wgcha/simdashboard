import { expect, test, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'

import { loginWorkspace } from './workspace-test-helpers'

const context = {
  project: 'project-feature-showcase',
  request: 'request-showcase-compare',
  loadCase: 'loadcase-showcase-compare',
}

async function selectRequestContext(page: Page) {
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption(context.project)
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption(context.request)
  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveValue(context.loadCase)
  await expect.poll(() => new URL(page.url()).searchParams.get('request')).toBe(context.request)
}

function journey(page: Page) {
  return page.getByRole('navigation', { name: '의뢰 작업 여정' })
}

async function expectUnifiedContext(page: Page, activeStep: string, expected = context) {
  await expect(page.locator('.request-workspace-header')).toHaveCount(1)
  await expect(page.getByLabel('프로젝트 선택', { exact: true })).toHaveValue(expected.project)
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue(expected.request)
  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveValue(expected.loadCase)

  const requestJourney = journey(page)
  await expect(requestJourney).toBeVisible()
  for (const label of ['의뢰 개요', '작업 실행', '결과 등록', '결과 검토']) {
    await expect(requestJourney.getByRole('button', { name: label, exact: true })).toHaveCount(1)
  }
  await expect(requestJourney.locator('[aria-current="step"]')).toHaveCount(1)
  await expect(requestJourney.getByRole('button', { name: activeStep, exact: true })).toHaveAttribute('aria-current', 'step')

  const myWork = page.getByRole('complementary', { name: '주 메뉴' }).getByRole('link', { name: '내 작업', exact: true })
  await expect(myWork).toHaveAttribute('aria-current', 'page')
}

async function expectNoDocumentOverflow(page: Page) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
}

async function expectWideWorkbenchColumns(page: Page) {
  const workbench = page.locator('.workbench-page.assigned-only')
  await expect(workbench).toHaveCSS('display', 'grid')
  const detail = page.getByTestId('work-item-detail')
  const workList = page.locator('.assigned-work-list')
  await expect(detail).toBeVisible()
  await expect(workList).toBeVisible()
  const [detailBox, listBox] = await Promise.all([detail.boundingBox(), workList.boundingBox()])
  expect(detailBox).not.toBeNull()
  expect(listBox).not.toBeNull()
  expect(detailBox!.x + detailBox!.width).toBeLessThanOrEqual(listBox!.x + 1)
  expect(Math.abs(detailBox!.y - listBox!.y)).toBeLessThan(2)
}

async function captureQa(page: Page, filename: string) {
  const outputDirectory = path.resolve(process.cwd(), '..', 'backups', 'unified-workspace-qa')
  mkdirSync(outputDirectory, { recursive: true })
  await page.screenshot({ path: path.join(outputDirectory, filename), fullPage: false })
}

test('한 의뢰의 네 작업 탭이 같은 헤더와 문맥을 유지하고 뒤로 가기와 새로고침으로 복원된다', async ({ page }) => {
  await loginWorkspace(page)
  await selectRequestContext(page)
  await expectUnifiedContext(page, '의뢰 개요')

  await journey(page).getByRole('button', { name: '작업 실행', exact: true }).click()
  await expect(page).toHaveURL(/\/workspace\/execution(?:\?|$)/)
  await expect(page.getByTestId('simulation-workbench')).toBeVisible()
  await expectUnifiedContext(page, '작업 실행')

  await journey(page).getByRole('button', { name: '결과 등록', exact: true }).click()
  await expect(page).toHaveURL(/\/workspace\/data(?:\?|$)/)
  await expect(page.getByLabel('등록 프로젝트 선택')).toHaveCount(0)
  await expect(page.getByLabel('등록 의뢰 선택')).toHaveCount(0)
  await expect(page.getByLabel('등록 하중 경우 선택')).toHaveCount(0)
  await expect(page.getByText('등록 대상', { exact: true })).toBeVisible()
  await expectUnifiedContext(page, '결과 등록')

  await journey(page).getByRole('button', { name: '결과 검토', exact: true }).click()
  await expect(page).toHaveURL(/\/workspace\/requests(?:\?|$)/)
  await expect(page.getByTestId('pending-analysis-workspace')).toBeVisible()
  await expectUnifiedContext(page, '결과 검토')

  await page.goBack()
  await expect(page).toHaveURL(/\/workspace\/data(?:\?|$)/)
  await expectUnifiedContext(page, '결과 등록')
  await page.goBack()
  await expect(page).toHaveURL(/\/workspace\/execution(?:\?|$)/)
  await expectUnifiedContext(page, '작업 실행')

  await page.reload()
  await expect(page.getByTestId('simulation-workbench')).toBeVisible()
  await expectUnifiedContext(page, '작업 실행')
})

test('기본 의뢰도 작업 실행에서 결과 검토로 바로 이동한다', async ({ page }) => {
  await loginWorkspace(page)
  const requestId = await page.getByLabel('의뢰 선택', { exact: true }).inputValue()
  const loadCaseId = await page.getByLabel('하중 경우 선택', { exact: true }).inputValue()
  await journey(page).getByRole('button', { name: '작업 실행', exact: true }).click()
  await expect(page.getByTestId('work-item-detail')).toBeVisible()
  await expect(page.locator('.assigned-work-list')).toBeVisible()
  await journey(page).getByRole('button', { name: '결과 검토', exact: true }).click()
  await expect(page).toHaveURL(/\/workspace\/requests(?:\?|$)/)
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue(requestId)
  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveValue(loadCaseId)
  await expect(journey(page).getByRole('button', { name: '결과 검토', exact: true })).toHaveAttribute('aria-current', 'step')
})

test('하중 경우가 없는 의뢰도 실행과 등록 탭에서 의뢰 문맥을 잃지 않는다', async ({ page }) => {
  await loginWorkspace(page)
  await page.route('**/api/requests/request-showcase-workflow/load-cases', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify([]),
  }))
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption(context.project)
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption('request-showcase-workflow')
  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toBeDisabled()
  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveValue('')

  await journey(page).getByRole('button', { name: '작업 실행', exact: true }).click()
  await expect(page.getByTestId('simulation-workbench')).toBeVisible()
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue('request-showcase-workflow')
  await expect(page.getByLabel('배정 작업 대상 의뢰')).toHaveCount(0)
  await expect(journey(page).getByRole('button', { name: '작업 실행', exact: true })).toHaveAttribute('aria-current', 'step')

  await journey(page).getByRole('button', { name: '결과 등록', exact: true }).click()
  await expect(page).toHaveURL(/\/workspace\/data(?:\?|$)/)
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue('request-showcase-workflow')
  await expect(page.getByLabel('등록 의뢰 선택')).toHaveCount(0)
  await expect(page.getByLabel('등록 하중 경우 선택')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '검증된 결과 등록', exact: true })).toBeDisabled()
  await expect(journey(page).getByRole('button', { name: '결과 등록', exact: true })).toHaveAttribute('aria-current', 'step')

  await journey(page).getByRole('button', { name: '결과 검토', exact: true }).click()
  await expect(page).toHaveURL(/\/workspace\/requests(?:\?|$)/)
  await expect(page.getByTestId('pending-analysis-workspace')).toBeVisible()
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue('request-showcase-workflow')
})

test('일반 사용자는 통합 탭에서 허용된 작업만 열 수 있다', async ({ page }) => {
  await loginWorkspace(page, 'e2e-viewer')
  await selectRequestContext(page)
  const requestJourney = journey(page)
  await expect(requestJourney.getByRole('button', { name: '작업 실행', exact: true })).toBeEnabled()
  await expect(requestJourney.getByRole('button', { name: '결과 등록', exact: true })).toBeDisabled()
  await expect(page.getByRole('link', { name: '새 의뢰', includeHidden: true })).toHaveCount(0)
  await expect(page.getByRole('link', { name: '해석 데이터 등록', includeHidden: true })).toHaveCount(0)

  await page.goto(`/workspace/data?project=${context.project}&request=${context.request}&loadCase=${context.loadCase}`)
  await expect(page).toHaveURL(/\/workspace\/overview(?:\?|$)/)
  await expect(page.getByLabel('등록 의뢰 선택')).toHaveCount(0)
})

test('등록 화면에서 의뢰 문맥을 바꾸는 동안 이전 하중 경우의 등록 동작을 차단한다', async ({ page }) => {
  await loginWorkspace(page)
  await selectRequestContext(page)
  await journey(page).getByRole('button', { name: '결과 등록', exact: true }).click()
  await page.getByRole('button', { name: '예제로 검증', exact: true }).click()
  const submit = page.getByRole('button', { name: '검증된 결과 등록', exact: true })
  await expect(submit).toBeEnabled()

  let release!: () => void
  const gate = new Promise<void>((resolve) => { release = resolve })
  await page.route('**/api/requests/request-showcase-waiting/load-cases', async (route) => {
    await gate
    await route.continue()
  })
  try {
    await page.getByLabel('의뢰 선택', { exact: true }).selectOption('request-showcase-waiting')
    await expect.poll(async () => !(await submit.count()) || await submit.isDisabled()).toBe(true)
  } finally {
    release()
  }
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue('request-showcase-waiting')
  await expect(submit).toBeDisabled()
})

for (const viewport of [
  { width: 2560, height: 1440 },
  { width: 2048, height: 1152 },
  { width: 1707, height: 960 },
  { width: 390, height: 844 },
]) {
  test(`${viewport.width}px 화면에서 통합 작업공간과 글자 크기 설정이 넘치지 않는다`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await loginWorkspace(page)
    if (viewport.width === 2560 || viewport.width === 1707) {
      await journey(page).getByRole('button', { name: '작업 실행', exact: true }).click()
      await expectWideWorkbenchColumns(page)
      if (viewport.width === 2560) {
        await page.getByRole('button', { name: '라이트', exact: true }).click()
        await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).toBe('light')
        await expectWideWorkbenchColumns(page)
      }
      await captureQa(page, `execution-${viewport.width}.png`)
      await journey(page).getByRole('button', { name: '의뢰 개요', exact: true }).click()
    }
    await selectRequestContext(page)
    await expectNoDocumentOverflow(page)
    await expect(page.locator('.request-workspace-header')).toBeVisible()
    await expect(journey(page)).toBeVisible()
    if (viewport.width === 2560) await captureQa(page, 'overview-2560.png')
    if (viewport.width === 1707) await captureQa(page, 'overview-1707.png')
    if (viewport.width === 2048) await captureQa(page, 'overview-2048.png')
    if (viewport.width === 390) await captureQa(page, 'overview-mobile-390.png')

    const sidebar = page.getByRole('complementary', { name: '주 메뉴' })
    if (viewport.width <= 620) {
      await sidebar.getByRole('button', { name: '메뉴 열기', exact: true }).click()
    }
    await expect(sidebar.getByRole('link', { name: '내 작업', exact: true })).toHaveAttribute('aria-current', 'page')
    const shell = page.locator('.app-shell')
    const journeyButton = journey(page).getByRole('button', { name: '의뢰 개요', exact: true })
    const originalFontSize = await shell.evaluate((element) => getComputedStyle(element).getPropertyValue('--ui-font-size').trim())
    const originalJourneyFontPixels = Number.parseFloat(await journeyButton.evaluate((element) => getComputedStyle(element).fontSize))
    expect(originalFontSize).toBe('14pt')
    await sidebar.getByRole('button', { name: '전체 글자 크기 늘리기' }).click()
    const increasedFontSize = await shell.evaluate((element) => getComputedStyle(element).getPropertyValue('--ui-font-size').trim())
    const increasedJourneyFontPixels = Number.parseFloat(await journeyButton.evaluate((element) => getComputedStyle(element).fontSize))
    expect(increasedFontSize).toBe('15pt')
    expect(increasedJourneyFontPixels).toBeGreaterThan(originalJourneyFontPixels)

    if (viewport.width <= 620) {
      await sidebar.getByRole('button', { name: '메뉴 닫기', exact: true }).click()
    }
    await journey(page).getByRole('button', { name: '작업 실행', exact: true }).click()
    await expect(shell).toHaveCSS('--ui-font-size', increasedFontSize)
    await expectNoDocumentOverflow(page)
    if (viewport.width === 390) {
      await journey(page).getByRole('button', { name: '결과 등록', exact: true }).click()
      await expect(page.getByText('해석 결과 가져오기', { exact: true })).toBeVisible()
      await expectNoDocumentOverflow(page)
    }
    if (viewport.width === 2560) {
      await journey(page).getByRole('button', { name: '결과 등록', exact: true }).click()
      await expect(page.getByText('해석 결과 가져오기', { exact: true })).toBeVisible()
      await expect(page.locator('.result-import-meta strong')).not.toHaveText('하중 경우를 선택하세요')
      await expectNoDocumentOverflow(page)
      await captureQa(page, 'import-2560.png')
      await sidebar.getByRole('link', { name: '결과 대시보드', exact: true }).click()
      await expect(page.getByTestId('result-overview-dashboard')).toBeVisible()
      await expectNoDocumentOverflow(page)
      await captureQa(page, 'result-dashboard-2560.png')
    }
    await page.reload()
    await expect(shell).toHaveCSS('--ui-font-size', increasedFontSize)
    await expectNoDocumentOverflow(page)
  })
}
