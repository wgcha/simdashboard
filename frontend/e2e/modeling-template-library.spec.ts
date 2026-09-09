import { expect, test, type Page } from '@playwright/test'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { execFileSync } from 'node:child_process'

async function login(page: Page) {
  await page.goto('/workspace/catalog/templates')
  await page.getByLabel('사용자 이름').fill('e2e-admin')
  await page.getByLabel('비밀번호').fill('e2e-validation-password')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '주 메뉴' })).toBeVisible()
  await page.goto('/workspace/catalog/templates')
  await expect(page.getByRole('heading', { name: '모델링 템플릿', exact: true })).toBeVisible()
}

// The normal runner uses a disposable database. Never point this write flow at the user's live DB.
test('product/load-case cards preserve CSV folder bytes, versions and searchable metadata', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 2560, height: 1440 })
  await login(page)
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  const suffix = Date.now()
  const name = `낙하 모델링 ${suffix}`
  const product = `TV_${suffix}`
  await page.getByRole('button', { name: /새 템플릿/ }).click()
  const form = page.locator('.template-modal')
  await form.getByLabel('템플릿 이름', { exact: true }).fill(name)
  await form.getByLabel('제품 이름', { exact: true }).fill(product)
  await form.getByLabel('하중 경우 이름', { exact: true }).fill('Bottom Drop 450mm')
  await form.getByRole('button', { name: '저장', exact: true }).click()
  const detail = page.locator('.template-detail')
  await expect(detail.getByRole('heading', { name, exact: true })).toBeVisible()

  const folder = testInfo.outputPath('ModelingInputs')
  await mkdir(path.join(folder, 'materials'), { recursive: true })
  const original = Buffer.from([0xef, 0xbb, 0xbf, 0x61, 0x2c, 0x62, 0x0d, 0x0a, 0x31, 0x2c, 0x30, 0x31, 0x0d, 0x0a])
  const cp949 = Buffer.from([0xc7, 0xd7, 0xb8, 0xf1, 0x2c, 0xb0, 0xaa, 0x0d, 0x0a])
  await writeFile(path.join(folder, 'model.csv'), original)
  await writeFile(path.join(folder, 'materials', 'steel.csv'), cp949)
  await writeFile(path.join(folder, 'notes.txt'), 'not part of a CSV snapshot')
  await page.locator('input[webkitdirectory]').setInputFiles(folder)
  await expect(detail).toContainText('model.csv')
  await detail.getByRole('button', { name: /새 버전 저장/ }).click()
  await expect(page.locator('.modeling-templates-page').getByRole('status')).toContainText('v2')
  await expect(detail.locator('.file-row')).toHaveCount(2)
  const zipEvent = page.waitForEvent('download')
  await detail.getByRole('button', { name: /전체 ZIP/ }).click()
  const zip = await zipEvent
  const zipPath = await zip.path()
  expect(zipPath).toBeTruthy()
  const python = process.env.E2E_PYTHON ?? path.resolve('..', '.venv-runtime', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  const archive = JSON.parse(execFileSync(python, ['-c', 'import zipfile,sys,json,base64; z=zipfile.ZipFile(sys.argv[1]); print(json.dumps({n:base64.b64encode(z.read(n)).decode() for n in z.namelist()}))', zipPath!], { encoding: 'utf8' }))
  expect(archive).toEqual({ 'ModelingInputs/model.csv': original.toString('base64'), 'ModelingInputs/materials/steel.csv': cp949.toString('base64') })
  const singleEvent = page.waitForEvent('download')
  await detail.getByRole('button', { name: 'ModelingInputs/materials/steel.csv 다운로드', exact: true }).click()
  expect(await readFile((await (await singleEvent).path())!)).toEqual(cp949)

  await page.locator('input[type="file"]:not([webkitdirectory])').setInputFiles({ name: 'solver.csv', mimeType: 'text/csv', buffer: Buffer.from('solver,version\r\nradioss,2024\r\n') })
  await detail.getByLabel('저장 폴더', { exact: true }).fill('ModelingInputs/settings')
  await detail.getByRole('button', { name: /새 버전 저장/ }).click()
  await expect(page.locator('.modeling-templates-page').getByRole('status')).toContainText('v3')
  await expect(detail.locator('.file-row')).toHaveCount(3)
  await detail.getByLabel('버전', { exact: true }).selectOption('2')
  await expect(detail.locator('.file-row')).toHaveCount(2)
  await expect(detail).not.toContainText('solver.csv')
  await detail.getByLabel('버전', { exact: true }).selectOption('3')
  await expect(detail).toContainText('ModelingInputs/settings/solver.csv')

  await page.getByLabel('템플릿 검색', { exact: true }).fill(product)
  await expect(page.locator('.modeling-card')).toHaveCount(1)
  await page.getByLabel('제품 필터', { exact: true }).selectOption(product)
  await page.getByLabel('하중 경우 필터', { exact: true }).selectOption('Bottom Drop 450mm')
  await expect(page.locator('.modeling-card')).toHaveCount(1)
  await page.getByLabel('템플릿 검색', { exact: true }).fill('검색결과없음-xyz')
  await expect(page.locator('.modeling-card')).toHaveCount(0)
  await page.getByLabel('템플릿 검색', { exact: true }).fill('')
  await expect(page.locator('.modeling-card')).toHaveCount(1)

  for (const mode of ['라이트', '다크']) {
    await page.getByRole('button', { name: mode, exact: true }).click()
    await expect(page.locator('.modeling-templates-page')).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath(`templates-${mode === '라이트' ? 'light' : 'dark'}-2560.png`), fullPage: false })
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('templates-mobile-390.png'), fullPage: true })
  expect(errors).toEqual([])
})
