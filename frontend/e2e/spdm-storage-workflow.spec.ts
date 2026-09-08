import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import { loginWorkspace } from './workspace-test-helpers'

const context = {
  project: 'project-tv-001',
  request: 'request-drop-001',
  loadCase: 'loadcase-drop-bottom-001',
}

const relativeLeaf = [
  'Project_9001_E2E_pv1',
  'WR_9001_SimType2',
  'CAE',
  'Assy_SetCase1CushionCase1',
  'Drop',
  'sample_parallel',
  'INDIVIDUAL',
  '1_Face_Drop_Scene01_Face1_1st',
].join('/')

async function openDataWorkspace(page: Page) {
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption(context.project)
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption(context.request)
  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveValue(context.loadCase)
  await page.getByRole('navigation', { name: '의뢰 작업 여정' })
    .getByRole('button', { name: '결과 등록', exact: true }).click()
  await expect(page).toHaveURL(/\/workspace\/data(?:\?|$)/)
  await expect(page.getByTestId('storage-workspace-panel')).toBeVisible()
}

async function configureAndBind(page: Page, testInfo: TestInfo) {
  const configResponse = await page.request.get('/api/storage/config')
  expect(configResponse.ok()).toBe(true)
  const config = await configResponse.json() as { configured?: boolean; root?: string | null }
  const configuredRoot = config.configured && config.root ? config.root : null
  const root = configuredRoot ?? testInfo.outputPath('SPDM storage')
  const leaf = path.join(root, ...relativeLeaf.split('/'))
  mkdirSync(path.join(leaf, 'results'), { recursive: true })

  const panel = page.getByTestId('storage-workspace-panel')
  await panel.getByText('고정 결과 원본 폴더', { exact: true }).click()
  if (configuredRoot) {
    await expect(panel.getByTestId('storage-root-path')).toHaveValue(root)
  } else {
    await panel.getByTestId('storage-root-path').fill(root)
    await panel.getByTestId('storage-root-save').click()
    await expect(panel.getByRole('status')).toContainText('저장소 설정을 저장했습니다.')
  }

  await panel.getByTestId('storage-binding-path').fill(relativeLeaf)
  await panel.getByTestId('storage-binding-submit').click()
  await expect(panel.locator('.storage-section-title strong')).toHaveText(relativeLeaf)
  await expect(panel.getByTestId('storage-file-picker')).toBeEnabled()
  return { panel, leaf }
}

test('고정 저장 폴더는 업로드와 직접 복사 결과를 연결하고 반복 새로고침에도 Run을 중복 생성하지 않는다', async ({ page }, testInfo) => {
  await loginWorkspace(page)
  await openDataWorkspace(page)
  const { panel, leaf } = await configureAndBind(page, testInfo)

  const rawBytes = Buffer.from('SPDM E2E 원본 바이트\u0000\u0001')
  await panel.getByTestId('storage-file-picker').setInputFiles({
    name: '해석원본.h3d',
    mimeType: 'application/octet-stream',
    buffer: rawBytes,
  })
  await panel.getByTestId('storage-upload-submit').click()
  const rawDownload = panel.getByRole('link', { name: '해석원본.h3d 다운로드', exact: true })
  await expect(rawDownload).toBeVisible()
  const downloadEvent = page.waitForEvent('download')
  await rawDownload.click()
  const download = await downloadEvent
  expect(download.suggestedFilename()).toBe('해석원본.h3d')
  expect(readFileSync(await download.path())).toEqual(rawBytes)

  const uploadedCsv = Buffer.from(
    'record_type,variable_key,value,unit\nscalar,top_edge_max_stress,61,MPa\n',
  )
  await page.locator('#result-file').setInputFiles({
    name: 'uploaded-summary.csv', mimeType: 'text/csv', buffer: uploadedCsv,
  })
  await expect(page.getByRole('button', { name: '검증된 결과 등록', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '검증된 결과 등록', exact: true }).click()
  await expect(page.getByRole('button', { name: '분석 대시보드에서 확인', exact: true })).toBeVisible()

  const stateAfterUpload = await (await page.request.get(`/api/load-cases/${context.loadCase}/storage`)).json()
  const uploaded = stateAfterUpload.files.find((file: { name: string }) => file.name === 'uploaded-summary.csv')
  expect(uploaded?.run_id).toBeTruthy()

  const directCsv = path.join(leaf, 'results', 'direct-summary.csv')
  writeFileSync(directCsv, 'record_type,variable_key,value,unit\nscalar,top_edge_max_stress,62,MPa\n')
  await panel.getByTestId('storage-folder-refresh').click()
  await expect(panel.getByText('direct-summary.csv', { exact: true })).toBeVisible()
  const stateAfterDirectCopy = await (await page.request.get(`/api/load-cases/${context.loadCase}/storage`)).json()
  const direct = stateAfterDirectCopy.files.find((file: { name: string }) => file.name === 'direct-summary.csv')
  expect(direct?.run_id).toBeTruthy()
  expect(direct.run_id).not.toBe(uploaded.run_id)

  await panel.getByTestId('storage-folder-refresh').click()
  await expect(panel.getByTestId('storage-folder-refresh')).toBeEnabled()
  const repeated = await (await page.request.get(`/api/load-cases/${context.loadCase}/storage`)).json()
  expect(repeated.files.find((file: { name: string }) => file.name === 'uploaded-summary.csv')?.run_id).toBe(uploaded.run_id)
  expect(repeated.files.find((file: { name: string }) => file.name === 'direct-summary.csv')?.run_id).toBe(direct.run_id)
})

test('이전 하중 경우의 늦은 저장소 응답은 새 의뢰 화면에 표시되지 않는다', async ({ page }) => {
  await loginWorkspace(page)
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption(context.project)
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption(context.request)
  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveValue(context.loadCase)

  let release!: () => void
  const gate = new Promise<void>((resolve) => { release = resolve })
  await page.route(`**/api/load-cases/${context.loadCase}/storage`, async (route) => {
    await gate
    await route.fulfill({ json: {
      config: { configured: true, locked: false },
      binding: { load_case_id: context.loadCase, relative_path: relativeLeaf, exists: true },
      rules: [], candidate_folders: [],
      files: [{ id: 'stale-file', name: '이전-의뢰-결과.h3d', relative_path: 'solver/이전-의뢰-결과.h3d', kind: 'solver', status: 'READY' }],
    } })
  })

  await page.getByRole('navigation', { name: '의뢰 작업 여정' })
    .getByRole('button', { name: '결과 등록', exact: true }).click()
  await expect(page.getByTestId('storage-workspace-panel')).toBeVisible()
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption('project-feature-showcase')
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption('request-showcase-compare')
  release()

  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveValue('loadcase-showcase-compare')
  await expect(page.getByTestId('storage-workspace-panel').getByText('이전-의뢰-결과.h3d', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('저장 대상')).toContainText('Run 비교: 회귀와 개선')
})

test('저장소 background 재검증은 사용자가 작성 중인 상대경로를 덮지 않는다', async ({ page }) => {
  await loginWorkspace(page)
  await openDataWorkspace(page)
  await expect(page.getByTestId('storage-binding-path')).toBeEnabled()

  let signalRefreshStarted!: () => void
  let releaseRefresh!: () => void
  const refreshStarted = new Promise<void>((resolve) => { signalRefreshStarted = resolve })
  const refreshGate = new Promise<void>((resolve) => { releaseRefresh = resolve })
  await page.route('**/api/storage/refresh', async (route) => {
    await route.fulfill({ json: { created_bindings: [], refreshed: [] } })
  })
  await page.route(`**/api/load-cases/${context.loadCase}/storage`, async (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    signalRefreshStarted()
    await refreshGate
    await route.continue()
  })

  await page.getByRole('button', { name: '저장 폴더 새로고침', exact: true }).click()
  await refreshStarted
  const draftPath = `${relativeLeaf}/작성중`
  const input = page.getByTestId('storage-binding-path')
  try {
    await expect(input).toBeEnabled()
    await input.fill(draftPath)
  } finally {
    releaseRefresh()
  }
  await expect(input).toHaveValue(draftPath)
})
