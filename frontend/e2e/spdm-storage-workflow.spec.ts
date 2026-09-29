import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { loginWorkspace } from './workspace-test-helpers'

const context = {
  project: 'project-feature-showcase',
  request: 'request-showcase-compare',
  secondRequest: 'request-showcase-workflow',
  loadCase: 'loadcase-showcase-compare',
}
const mediaFile = path.resolve(process.cwd(), '..', 'video_example', 'tv_drop_simulation_variant_01.mp4')
const mediaBytes = readFileSync(mediaFile)
const mediaName = path.basename(mediaFile)

type Environment = 'USAGE' | 'DISTRIBUTION'
type Scenario = 'usage' | 'empty' | 'distribution' | 'stale' | 'resume'

function emptyContext() {
  return { simulation_case: null, evaluation: null, load_case: null, execution_run: null, run_option: null, scene: null }
}

function usageTree(requestId: string) {
  const base = `SPDM/${requestId}/WR`
  const casePath = `${base}/Case_01`
  const evaluationPath = `${casePath}/Settle`
  const resultPath = `${evaluationPath}/results`
  const sim = { id: `simcase-${requestId}`, label: 'Case_01', relative_path: casePath }
  const evaluation = { label: 'Settle', relative_path: evaluationPath }
  const caseContext = { ...emptyContext(), simulation_case: sim }
  const evaluationContext = { ...caseContext, evaluation }
  const node = (relative_path: string, name: string, role_kind: string, value: typeof caseContext, result_state: 'PRESENT' | 'MISSING' = 'MISSING', children_available = true) => ({
    relative_path, name, role_kind, context: value, result_state, can_prepare: true,
    suggested_relative_path: role_kind === 'RESULTS' ? relative_path : `${relative_path}/results`, children_available,
  })
  return {
    requestFolder: base,
    casePath,
    resultPath,
    caseContext,
    evaluationContext,
    caseNode: node(casePath, 'Case_01', 'SIMULATION_CASE', caseContext),
    evaluationNode: node(evaluationPath, 'Settle', 'EVALUATION', evaluationContext, 'PRESENT'),
    resultsNode: node(resultPath, 'results', 'RESULTS', evaluationContext, 'PRESENT', false),
  }
}

function distributionTree(requestId: string) {
  const base = `SPDM/${requestId}/WR`
  const casePath = `${base}/Case_Distribution`
  const loadPath = `${casePath}/Drop_800mm`
  const runPath = `${loadPath}/Run_01`
  const optionPath = `${runPath}/Option_A`
  const scenePath = `${optionPath}/Front`
  const resultPath = `${scenePath}/results`
  const simulation_case = { id: `simcase-${requestId}`, label: 'Case_Distribution', relative_path: casePath }
  const load_case = { id: `load-${requestId}`, label: 'Drop_800mm', relative_path: loadPath }
  const execution_run = { id: `run-${requestId}`, label: 'Run_01', relative_path: runPath }
  const run_option = { id: `option-${requestId}`, label: 'Option_A', relative_path: optionPath, status: 'PRESENT' as const }
  const scene = { id: `scene-${requestId}`, label: 'Front', relative_path: scenePath }
  const caseContext = { ...emptyContext(), simulation_case }
  const loadContext = { ...caseContext, load_case }
  const runContext = { ...loadContext, execution_run }
  const optionContext = { ...runContext, run_option }
  const sceneContext = { ...optionContext, scene }
  const node = (relative_path: string, name: string, role_kind: string, value: typeof caseContext, result_state: 'PRESENT' | 'MISSING' = 'MISSING', children_available = true) => ({
    relative_path, name, role_kind, context: value, result_state, can_prepare: true,
    suggested_relative_path: role_kind === 'RESULTS' ? relative_path : `${relative_path}/results`, children_available,
  })
  return {
    requestFolder: base, casePath, loadPath, runPath, optionPath, scenePath, resultPath,
    caseContext, loadContext, runContext, optionContext, sceneContext,
    caseNode: node(casePath, 'Case_Distribution', 'SIMULATION_CASE', caseContext),
    loadNode: node(loadPath, 'Drop_800mm', 'LOAD_CASE', loadContext),
    runNode: node(runPath, 'Run_01', 'EXECUTION_RUN', runContext),
    optionNode: node(optionPath, 'Option_A', 'RUN_OPTION', optionContext),
    sceneNode: node(scenePath, 'Front', 'SCENE', sceneContext, 'PRESENT'),
    resultsNode: node(resultPath, 'results', 'RESULTS', sceneContext, 'PRESENT', false),
  }
}

function targetFor(requestId: string, environment: Environment, scenario: Scenario) {
  const tree = environment === 'USAGE' ? usageTree(requestId) : distributionTree(requestId)
  const hasCase = scenario !== 'empty'
  return {
    target: {
      project_id: context.project,
      project_name: 'Feature Showcase',
      request_id: requestId,
      request_name: requestId === context.request ? '비교 검토' : '작업 실행',
      spdm_project_folder: `SPDM/${requestId}`,
      spdm_request_folder: tree.requestFolder,
      status: 'READY',
      cases: hasCase ? [tree.caseNode] : [],
    },
    tree,
  }
}

function inspectionResponse(files: Array<{ relative_path: string; size: number; media_type: string }>, partial = false, exclusions: Array<{ relative_path: string; reason: string }> = []) {
  const excludedPaths = new Set(exclusions.map((item) => item.relative_path))
  const manifest = files.map((file) => ({ ...file, sha256: `sha-${file.relative_path}`, upload_status: 'READY', inspection_status: excludedPaths.has(file.relative_path) ? 'EXCLUDED' : 'READY' }))
  const media = files.filter((file) => !excludedPaths.has(file.relative_path) && (file.media_type.startsWith('video/') || file.media_type.startsWith('image/'))).map((file) => ({
    ...file,
    sha256: `sha-${file.relative_path}`,
    kind: file.media_type.startsWith('video/') ? 'VIDEO' : 'IMAGE',
    title: file.relative_path === mediaName ? 'Drop sample video' : file.relative_path,
    status: 'READY',
  }))
  return {
    draft_id: 'draft-registration-e2e', status: 'REVIEW_REQUIRED', inspection_revision: 'inspection-rev-1', source_revision: 'source-rev-1',
    manifest,
    exclusions,
    metrics: excludedPaths.has('settle.csv') ? [] : [{ evaluation: 'Settle', metric: 'Set Tilt Angle', key: 'settle_angle', value: 1.25, value_type: 'number', unit: 'deg', source_path: 'settle.csv', source_sha256: 'sha-settle', status: 'READY' }],
    media,
    issues: partial ? [{ severity: 'WARNING', code: 'PARTIAL_CAPTURE', message: '이번 업로드에는 일부 항목이 없습니다.' }] : [],
    missing_count: partial ? 1 : 0,
    blocking_count: 0,
  }
}

function json(route: import('@playwright/test').Route, value: unknown, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) })
}

async function selectRequestContext(page: Page, requestId = context.request) {
  await page.getByLabel('프로젝트 선택', { exact: true }).selectOption(context.project)
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption(requestId)
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue(requestId)
}

async function openRegistrationTab(page: Page) {
  await page.getByRole('navigation', { name: '의뢰 작업 여정' }).getByRole('button', { name: '결과 등록', exact: true }).click()
  await expect(page).toHaveURL(/\/workspace\/data(?:\?|$)/)
  await expect(page.getByTestId('result-registration-workspace')).toBeVisible()
  await expect(page.getByLabel('하중 경우 선택', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '현재 하중 경우 결과 파일 확인', exact: true })).toHaveCount(0)
  await expect(page.getByLabel('결과 버전 선택', { exact: true })).toHaveCount(0)
}

async function installRegistrationApi(page: Page, options: {
  scenario: Scenario
  requestIds?: string[]
  partial?: boolean
  inspectGate?: Promise<void>
  signalInspect?: () => void
  blockingFirstInspection?: boolean
  failFirstFileUpload?: boolean
  failFirstPublish?: boolean
  onCall?: (kind: string, body?: unknown) => void
}) {
  const requestIds = options.requestIds ?? [context.request]
  const trees = new Map<string, ReturnType<typeof usageTree> | ReturnType<typeof distributionTree>>()
  const defaultTrees = new Map<string, ReturnType<typeof usageTree> | ReturnType<typeof distributionTree>>()
  let fileList = [{ relative_path: 'settle.csv', size: 53, media_type: 'text/csv' }, { relative_path: mediaName, size: mediaBytes.byteLength, media_type: 'video/mp4' }]
  let latestExclusions: Array<{ relative_path: string; reason: string }> = []
  let inspectCount = 0
  let fileUploadCount = 0
  let publishAttemptCount = 0
  let publishFailed = false
  let lastIdempotencyKey: string | null = null
  const published = (requestId: string, environment: Environment, status = 'PUBLISHED') => {
    const tree = environment === 'USAGE' ? usageTree(requestId) : distributionTree(requestId)
    return {
      draft_id: 'draft-registration-e2e', status, case_id: tree.caseContext.simulation_case?.id ?? `simcase-${requestId}`,
      capture_id: 'capture-registration-e2e', environment, project_id: context.project, request_id: requestId,
      context: environment === 'USAGE' ? tree.evaluationContext : tree.sceneContext,
      asset_count: 1, result_count: 1, media_count: 1, image_count: 0, video_count: 1,
    }
  }
  let draftCounter = 0
  await page.route('**/api/result-registration/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const pathName = url.pathname
    const method = request.method()
    const requestId = url.searchParams.get('request_id') ?? context.request
    const environment = (url.searchParams.get('environment') ?? 'USAGE') as Environment
    const { target, tree } = targetFor(requestId, environment, options.scenario)
    trees.set(`${requestId}:${environment}`, tree)
    defaultTrees.set(`${requestId}:USAGE`, usageTree(requestId))
    defaultTrees.set(`${requestId}:DISTRIBUTION`, distributionTree(requestId))

    if (pathName.endsWith('/targets') && method === 'GET') {
      const targets = requestIds.map((id) => targetFor(id, environment, id === context.request ? options.scenario : 'usage').target)
      return json(route, { storage_root_id: 'spdm-root-e2e', environment, targets })
    }
    if (pathName.endsWith('/folders') && method === 'GET') {
      const folderTree = environment === 'USAGE' ? usageTree(requestId) : distributionTree(requestId)
      const parent = url.searchParams.get('parent_relative_path')
      if (options.scenario === 'empty' && requestId === context.request) return json(route, { storage_root_id: 'spdm-root-e2e', project_id: context.project, request_id: requestId, environment, parent_relative_path: parent, nodes: [] })
      let nodes: unknown[] = []
      if (!parent || parent === folderTree.requestFolder) nodes = [folderTree.caseNode]
      else if (parent === folderTree.casePath) nodes = environment === 'USAGE'
        ? [(folderTree as ReturnType<typeof usageTree>).evaluationNode]
        : [(folderTree as ReturnType<typeof distributionTree>).loadNode]
      else if (environment === 'USAGE' && parent === (folderTree as ReturnType<typeof usageTree>).evaluationNode.relative_path) nodes = [(folderTree as ReturnType<typeof usageTree>).resultsNode]
      else if (environment === 'DISTRIBUTION') {
        const distribution = folderTree as ReturnType<typeof distributionTree>
        if (parent === distribution.loadPath) nodes = [distribution.runNode]
        else if (parent === distribution.runPath) nodes = [distribution.optionNode]
        else if (parent === distribution.optionPath) nodes = [distribution.sceneNode]
        else if (parent === distribution.scenePath) nodes = [distribution.resultsNode]
      }
      return json(route, { storage_root_id: 'spdm-root-e2e', project_id: context.project, request_id: requestId, environment, parent_relative_path: parent, nodes })
    }
    if (pathName.endsWith('/folders/prepare') && method === 'POST') {
      options.onCall?.('prepare', request.postDataJSON())
      const body = request.postDataJSON() as { confirm_create?: boolean; project_id?: string; request_id?: string; segments?: Array<{ role_kind: string; name: string }> }
      const newTree = usageTree(body.request_id ?? context.request)
      const segments = body.segments ?? []
      const proposedPaths = segments.map((segment, index) => ({
        relative_path: `${newTree.requestFolder}/${segments.slice(0, index + 1).map((item) => item.name).join('/')}`,
        role_kind: segment.role_kind, name: segment.name, exists: false,
      }))
      const casePath = proposedPaths.find((item) => item.role_kind === 'SIMULATION_CASE')?.relative_path ?? `${newTree.requestFolder}/Case_Prepared`
      const resultPath = proposedPaths.at(-1)?.relative_path ?? `${casePath}/Settle/results`
      const evaluation = proposedPaths.find((item) => item.role_kind === 'EVALUATION')
      const contextValue = { ...emptyContext(), simulation_case: { id: `simcase-${body.request_id ?? context.request}`, label: 'Case_Prepared', relative_path: casePath }, evaluation: evaluation ? { label: evaluation.name, relative_path: evaluation.relative_path } : null }
      const preparation = {
        status: body.confirm_create ? 'PREPARED' : 'PREVIEW', case_relative_path: casePath, result_relative_path: resultPath,
        created: Boolean(body.confirm_create), created_paths: body.confirm_create ? proposedPaths.map((item) => item.relative_path) : [], context: contextValue,
        proposed_paths: proposedPaths,
      }
      return json(route, preparation)
    }
    if (pathName.endsWith('/drafts') && method === 'POST') {
      draftCounter += 1
      const body = request.postDataJSON() as { files?: Array<{ relative_path: string; size: number; media_type: string }> }
      options.onCall?.('draft', body)
      fileList = body.files ?? fileList
      return json(route, { draft_id: `draft-registration-e2e-${draftCounter}`, status: 'DRAFT', inspection_revision: null })
    }
    if (/\/drafts\/[^/]+\/files$/.test(pathName) && method === 'POST') {
      fileUploadCount += 1
      options.onCall?.('files')
      if (options.failFirstFileUpload && fileUploadCount === 1) return json(route, { detail: 'temporary upload failure' }, 500)
      return json(route, { draft_id: pathName.split('/').at(-2), status: 'UPLOADED' })
    }
    if (/\/drafts\/[^/]+\/inspect$/.test(pathName) && method === 'POST') {
      options.signalInspect?.()
      if (options.inspectGate) await options.inspectGate
      const inspectBody = request.postData() ? request.postDataJSON() as { exclusions?: Array<{ relative_path: string; reason: string }> } : null
      latestExclusions = inspectBody?.exclusions ?? []
      inspectCount += 1
      options.onCall?.('inspect', inspectBody)
      const response = inspectionResponse(fileList, options.partial, latestExclusions)
      if (options.blockingFirstInspection && inspectCount === 1) {
        response.blocking_count = 1
        response.issues.push({ severity: 'ERROR', code: 'INVALID_FILE', message: '파일 형식을 확인하세요.' })
      }
      return json(route, response)
    }
    if (/\/drafts\/[^/]+$/.test(pathName) && method === 'GET') {
      options.onCall?.('get-draft')
      const draftId = pathName.split('/').at(-1) ?? 'draft-registration-e2e'
      const tree = usageTree(requestId)
      const inspected = inspectionResponse(fileList, options.partial, latestExclusions)
      return json(route, {
        draft_id: draftId, status: publishFailed ? 'PUBLISH_FAILED' : 'INSPECTED', revision: 3, project_id: context.project, request_id: requestId, environment,
        storage_root_id: 'spdm-root-e2e', case_relative_path: tree.casePath, result_relative_path: tree.resultPath,
        context: tree.evaluationContext, inspection_revision: 'inspection-rev-1', source_revision: 'source-rev-1', manifest: inspected.manifest, exclusions: latestExclusions,
        inspection: { metrics: inspected.metrics, media: inspected.media, issues: inspected.issues, missing_count: inspected.missing_count, blocking_count: inspected.blocking_count, metric_count: inspected.metrics.length, result_count: inspected.metrics.length },
        mirror_status: null, error: null, capture_id: null, case_id: null, asset_count: 0, result_count: inspected.metrics.length,
        media_count: inspected.media.length, image_count: 0, video_count: inspected.media.filter((item) => item.kind === 'VIDEO').length,
        idempotency_key: lastIdempotencyKey, approved_by: publishFailed ? 'e2e-admin' : null, published_at: null, approved_files: [],
      })
    }
    if (/\/drafts\/[^/]+\/approve$/.test(pathName) && method === 'POST') {
      options.onCall?.('approve', request.postDataJSON())
      return json(route, { draft_id: 'draft-registration-e2e', status: 'APPROVED' })
    }
    if (/\/drafts\/[^/]+\/publish$/.test(pathName) && method === 'POST') {
      const body = request.postDataJSON() as { inspection_revision: string; idempotency_key: string }
      publishAttemptCount += 1
      lastIdempotencyKey = body.idempotency_key
      options.onCall?.('publish', body)
      if (options.failFirstPublish && publishAttemptCount === 1) {
        publishFailed = true
        return json(route, { detail: 'temporary publish failure' }, 503)
      }
      return json(route, { ...published(requestId, environment, options.scenario === 'empty' ? 'MIRROR_CONFLICT' : 'PUBLISHED'), draft_id: pathName.split('/').at(-2), inspection_revision: body.inspection_revision })
    }
    if (/\/drafts\/[^/]+\/mirror\/retry$/.test(pathName) && method === 'POST') {
      options.onCall?.('mirror-retry')
      return json(route, { ...published(requestId, environment, 'PUBLISHED'), draft_id: pathName.split('/').at(-3) })
    }
    return json(route, { detail: `Unexpected result-registration request: ${method} ${pathName}` }, 404)
  })
  await page.route('**/api/dashboard/catalog**', async (route) => {
    const url = new URL(route.request().url())
    const requestId = url.searchParams.get('request_id') ?? context.request
    const simulationCaseId = `simcase-${requestId}`
    return json(route, {
      environment: 'USAGE', cases: [{ id: simulationCaseId, label: 'Case_01' }], load_cases: [], execution_runs: [], run_options: [], modes: [],
      captures: [{ id: 'capture-registration-e2e', label: 'capture-registration-e2e', case_id: simulationCaseId }], components: [], bases: [],
    })
  })
  await page.route('**/api/dashboard/usage/cases/**', async (route) => {
    const url = new URL(route.request().url())
    const captureId = url.searchParams.get('capture_id')
    options.onCall?.('dashboard-usage', captureId)
    return json(route, {
      context: { project_id: context.project, request_id: context.request, simulation_case_id: `simcase-${context.request}`, capture_id: captureId, context_key: 'case:capture' },
      status: 'READY', quality_issues: [],
      evaluations: [{
        id: 'Settle', name: 'Settle', status: 'READY', common: { value: 1.25, unit: 'deg', value_status: 'READY', value_key: 'settle_angle' },
        front: { value: 1.25, unit: 'deg', value_status: 'READY', value_key: 'settle_angle' }, rear: { value: 1.25, unit: 'deg', value_status: 'READY', value_key: 'settle_angle' },
        media: [{ asset_id: 'asset-registration-video', kind: 'VIDEO', status: 'READY', title: 'Drop sample video', url: '/api/dashboard/assets/asset-registration-video', frame_role: 'FINAL_FRAME', frame_index: 15, time_value: 0.5, time_unit: 's', provenance: mediaName }],
      }],
    })
  })
  await page.route('**/api/dashboard/assets/asset-registration-video', async (route) => route.fulfill({ status: 200, contentType: 'video/mp4', body: mediaBytes }))
  return { defaultTrees }
}

async function selectCaseAndEvaluation(page: Page, casePath: string, evaluationLabel = 'Settle') {
  const caseSelect = page.getByLabel('SPDM 해석 Case 선택', { exact: true })
  await expect(caseSelect).toBeEnabled()
  await caseSelect.selectOption(casePath)
  const evaluation = page.locator('.result-registration-node').filter({ hasText: evaluationLabel })
  await expect(evaluation).toBeVisible()
  await evaluation.getByRole('button', { name: '문맥 선택', exact: true }).click()
  await expect(page.getByRole('button', { name: '이 기존 폴더 선택', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '이 기존 폴더 선택', exact: true }).click()
}

async function prepareActualSpdmTarget(page: Page) {
  const tempRoot = process.env.SIMDASH_SPDM_ROOT
  expect(tempRoot).toBeTruthy()
  expect(path.resolve(path.dirname(tempRoot!))).toBe(path.resolve(tmpdir()))
  expect(path.basename(tempRoot!)).toMatch(/^simulation-workbench-e2e-spdm-/)
  const rootConfig = await page.evaluate(async () => {
    const response = await fetch('/api/storage/config')
    return { status: response.status, body: await response.json() as { root?: string | null; locked?: boolean } }
  })
  expect(rootConfig.status).toBe(200)
  expect(rootConfig.body.locked).toBe(true)
  expect(path.resolve(rootConfig.body.root ?? '')).toBe(path.resolve(tempRoot!))
  const relativePath = 'Project_E2E_Result_Registration_Test/WR_E2E_ResultRegistration_SimType1/CAE/Assy_ResultRegistration/Settle'
  const binding = await page.evaluate(async ({ loadCaseId, targetPath }) => {
    const response = await fetch(`/api/load-cases/${encodeURIComponent(loadCaseId)}/storage`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ relative_path: targetPath }),
    })
    return { status: response.status, body: await response.json() as { binding?: { project_id?: string; request_id?: string } } }
  }, { loadCaseId: context.loadCase, targetPath: relativePath })
  expect(binding.status).toBe(200)
  expect(binding.body.binding?.project_id).toBe(context.project)
  expect(binding.body.binding?.request_id).toBe(context.request)
  const targetResponse = await page.evaluate(async () => {
    const response = await fetch('/api/result-registration/targets?environment=USAGE')
    return { status: response.status, body: await response.json() as { targets?: Array<{ project_id: string; request_id: string; cases: Array<{ name: string; relative_path: string }> }> } }
  })
  expect(targetResponse.status).toBe(200)
  const target = targetResponse.body.targets?.find((item) => item.project_id === context.project && item.request_id === context.request)
  expect(target).toBeTruthy()
  const casePath = target?.cases.find((item) => item.name === 'Assy_ResultRegistration')?.relative_path
  expect(casePath).toBe(`${relativePath.split('/').slice(0, -1).join('/')}`)
  return { casePath: casePath!, resultPath: `${relativePath}/results` }
}

test('기존 Case 결과를 검사·검수한 뒤에만 게시하고 같은 capture의 영상을 연다', async ({ page }) => {
  await loginWorkspace(page)
  await selectRequestContext(page)
  let publishCount = 0
  let uploadedDraft: Record<string, unknown> | null = null
  let approvedInspection: Record<string, unknown> | null = null
  let queriedCapture = ''
  await installRegistrationApi(page, {
    scenario: 'usage',
    onCall: (kind, body) => {
      if (kind === 'publish') publishCount += 1
      if (kind === 'draft') uploadedDraft = body as Record<string, unknown>
      if (kind === 'approve') approvedInspection = body as Record<string, unknown>
      if (kind === 'dashboard-usage') queriedCapture = String(body)
    },
  })
  await openRegistrationTab(page)
  await expect(page.getByText('SPDM 경로 연결', { exact: true })).toBeVisible()
  const tree = usageTree(context.request)
  await selectCaseAndEvaluation(page, tree.casePath)
  await page.locator('#registration-files').setInputFiles([
    { name: 'settle.csv', mimeType: 'text/csv', buffer: Buffer.from('evaluation,metric,value,unit\nSettle,settle_angle,1.25,deg\n') },
    { name: mediaName, mimeType: 'video/mp4', buffer: mediaBytes },
  ])
  await expect(page.getByText(mediaName, { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '업로드하고 자동 검사', exact: true }).click()
  const review = page.getByTestId('result-registration-inspection')
  await expect(review).toBeVisible()
  await expect(review.getByText('1.25', { exact: true })).toBeVisible()
  await expect(review.getByText('deg', { exact: true })).toBeVisible()
  await expect(review.getByRole('cell', { name: 'settle.csv', exact: true })).toBeVisible()
  await expect(review.getByText('Drop sample video', { exact: true })).toBeVisible()
  const reviewVideo = review.locator('.result-registration-media-list video')
  await expect(reviewVideo).toBeVisible()
  await expect.poll(() => reviewVideo.evaluate((video: HTMLVideoElement) => video.readyState)).toBeGreaterThan(0)
  await expect.poll(() => reviewVideo.evaluate((video: HTMLVideoElement) => Number.isFinite(video.duration) && video.duration > 0)).toBe(true)
  await expect(page.getByRole('button', { name: '검수 완료·DB 등록', exact: true })).toBeEnabled()
  expect(publishCount).toBe(0)
  expect(uploadedDraft?.result_relative_path).toBe(tree.resultPath)
  expect((uploadedDraft?.files as Array<{ relative_path: string }>).map((file) => file.relative_path)).toEqual(['settle.csv', mediaName])
  await page.screenshot({ path: path.join(tmpdir(), 'simulation-workbench-result-registration-review.png'), fullPage: true })

  const publishButton = page.getByRole('button', { name: '검수 완료·DB 등록', exact: true })
  await publishButton.evaluate((button) => {
    button.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }))
    button.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }))
  })
  await expect(page.getByTestId('result-registration-completion')).toContainText('DB 등록 완료')
  await expect(page.getByTestId('result-registration-completion')).toContainText('capture-registration-e2e')
  await expect(page.getByTestId('result-registration-completion')).toContainText('결과값')
  await expect(page.getByTestId('result-registration-completion')).toContainText('0 / 1')
  expect(publishCount).toBe(1)
  expect(approvedInspection?.inspection_revision).toBe('inspection-rev-1')
  const caseResultLink = page.getByRole('link', { name: 'Case 결과 보기', exact: true })
  await expect(caseResultLink).toHaveAttribute('href', /view=case_results.*capture=capture-registration-e2e/)
  await caseResultLink.click()
  await expect(page).toHaveURL(/view=case_results/)
  await expect(page.getByTestId('usage-dashboard')).toBeVisible()
  const dashboardVideo = page.locator('.simulation-dashboard__usage-media video')
  await expect(dashboardVideo).toBeVisible()
  await expect(dashboardVideo).toHaveAttribute('src', '/api/dashboard/assets/asset-registration-video')
  await expect.poll(() => dashboardVideo.evaluate((video: HTMLVideoElement) => video.readyState)).toBeGreaterThan(0)
  await expect.poll(() => dashboardVideo.evaluate((video: HTMLVideoElement) => Number.isFinite(video.duration) && video.duration > 0)).toBe(true)
  expect(queriedCapture).toBe('capture-registration-e2e')
  await page.getByRole('button', { name: '영상 재생 또는 일시정지', exact: true }).click()
  await expect.poll(() => dashboardVideo.evaluate((video: HTMLVideoElement) => video.currentTime)).toBeGreaterThan(0)
})

test('없는 환경별 경로는 전체 생성안을 확인하고 누락 인정 뒤 게시하며 mirror 충돌도 완료로 보인다', async ({ page }) => {
  await loginWorkspace(page)
  await selectRequestContext(page)
  let prepareBodies: Array<Record<string, unknown>> = []
  let approveCount = 0
  let publishCount = 0
  let mirrorRetryCount = 0
  await installRegistrationApi(page, {
    scenario: 'empty', partial: true,
    onCall: (kind, body) => {
      if (kind === 'prepare') prepareBodies = [...prepareBodies, body as Record<string, unknown>]
      if (kind === 'approve') approveCount += 1
      if (kind === 'publish') publishCount += 1
      if (kind === 'mirror-retry') mirrorRetryCount += 1
    },
  })
  await openRegistrationTab(page)
  await page.getByLabel('결과 등록 환경', { exact: true }).selectOption('USAGE')
  await expect(page.getByLabel('SPDM 해석 Case 선택', { exact: true })).toHaveValue('')
  await page.getByText('필요한 Case·환경별 하위 폴더가 없을 때', { exact: true }).click()
  await page.getByLabel('Case 폴더 이름', { exact: true }).fill('Case_Prepared')
  await page.getByRole('button', { name: '전체 경로 미리보기', exact: true }).click()
  const preview = page.getByLabel('결과 폴더 생성 경로 미리보기')
  await expect(preview).toBeVisible()
  await expect(preview.getByText('새 폴더 생성', { exact: false }).first()).toBeVisible()
  await expect(preview).toContainText('/Case_Prepared/Settle/results')
  expect(prepareBodies).toHaveLength(1)
  expect(prepareBodies[0].confirm_create).toBe(false)
  expect(prepareBodies[0].segments).toEqual([
    { role_kind: 'SIMULATION_CASE', name: 'Case_Prepared' },
    { role_kind: 'EVALUATION', name: 'Settle' },
    { role_kind: 'RESULTS', name: 'results' },
  ])
  await preview.getByLabel('표시된 결과용 하위 폴더만 생성하도록 확인했습니다.').check()
  await preview.getByRole('button', { name: '경로 확인 후 폴더 생성', exact: true }).click()
  await expect(page.getByText(/결과용 폴더를 준비했습니다/)).toBeVisible()
  expect(prepareBodies).toHaveLength(2)
  expect(prepareBodies[1].confirm_create).toBe(true)

  await page.locator('#registration-files').setInputFiles({ name: 'settle.csv', mimeType: 'text/csv', buffer: Buffer.from('evaluation,metric,value,unit\nSettle,settle_angle,1.25,deg\n') })
  await page.getByRole('button', { name: '업로드하고 자동 검사', exact: true }).click()
  await expect(page.getByTestId('result-registration-inspection')).toBeVisible()
  const confirmation = page.getByLabel('검수 승인과 DB 등록')
  const publishButton = confirmation.getByRole('button', { name: '검수 완료·DB 등록', exact: true })
  await expect(publishButton).toBeDisabled()
  expect(approveCount).toBe(0)
  await confirmation.getByLabel(/누락·제외 항목과 이번 업로드 파일만/).check()
  await expect(publishButton).toBeEnabled()
  await publishButton.click()
  await expect(page.getByTestId('result-registration-completion')).toContainText('DB 등록 완료 · 폴더 반영 충돌')
  await expect(page.getByTestId('result-registration-completion')).toContainText('기존 파일은 덮어쓰지 않았습니다')
  await expect(page.getByRole('link', { name: 'Case 결과 보기', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '저장 폴더 반영만 재시도', exact: true }).click()
  await expect(page.getByTestId('result-registration-completion').locator('header')).toContainText('DB 등록 완료')
  expect(approveCount).toBe(1)
  expect(publishCount).toBe(1)
  expect(mirrorRetryCount).toBe(1)
})

test('Run Case가 없어도 새 Run Option 폴더 이름을 정해 경로를 미리 볼 수 있다', async ({ page }) => {
  await loginWorkspace(page)
  await selectRequestContext(page)
  let previewBody: Record<string, unknown> | null = null
  await installRegistrationApi(page, {
    scenario: 'empty',
    onCall: (kind, body) => {
      if (kind === 'prepare') previewBody = body as Record<string, unknown>
    },
  })
  await openRegistrationTab(page)
  await page.getByLabel('결과 등록 환경', { exact: true }).selectOption('DISTRIBUTION')
  await page.getByText('필요한 Case·환경별 하위 폴더가 없을 때', { exact: true }).click()
  await page.getByLabel('Case 폴더 이름', { exact: true }).fill('Case_New')
  await page.getByLabel('하중경우 폴더 이름', { exact: true }).fill('Drop_New')
  await page.getByLabel('Run Case 폴더 이름', { exact: true }).fill('Run_New')
  await page.getByLabel('Run Option 결과 폴더 추가', { exact: true }).check()
  await page.getByLabel('Run Option 이름', { exact: true }).fill('Option_New')
  await page.getByLabel('Scene 폴더 이름', { exact: true }).fill('Front')
  await page.getByRole('button', { name: '전체 경로 미리보기', exact: true }).click()
  await expect(page.getByLabel('결과 폴더 생성 경로 미리보기')).toContainText('/Case_New/Drop_New/Run_New/Option_New/Front/results')
  expect(previewBody?.segments).toEqual([
    { role_kind: 'SIMULATION_CASE', name: 'Case_New' },
    { role_kind: 'LOAD_CASE', name: 'Drop_New' },
    { role_kind: 'EXECUTION_RUN', name: 'Run_New' },
    { role_kind: 'RUN_OPTION', name: 'Option_New' },
    { role_kind: 'SCENE', name: 'Front' },
    { role_kind: 'RESULTS', name: 'results' },
  ])
  await page.getByLabel('표시된 결과용 하위 폴더만 생성하도록 확인했습니다.').check()
  await page.getByRole('button', { name: '경로 확인 후 폴더 생성', exact: true }).click()
  await page.locator('.result-registration-upload').evaluate((element) => {
    const files = new DataTransfer()
    files.items.add(new File(['evaluation,metric,value,unit\nSettle,angle,1.25,deg\n'], 'dropped.csv', { type: 'text/csv' }))
    element.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: files }))
  })
  await expect(page.getByLabel('선택한 파일')).toContainText('dropped.csv')
  await expect(page.getByRole('button', { name: '업로드하고 자동 검사', exact: true })).toBeEnabled()
})

test('유통환경은 기존 Case에서 load·run·option·scene 문맥과 결과 폴더를 고른다', async ({ page }) => {
  await loginWorkspace(page)
  await selectRequestContext(page)
  await installRegistrationApi(page, { scenario: 'distribution' })
  await openRegistrationTab(page)
  await page.getByLabel('결과 등록 환경', { exact: true }).selectOption('DISTRIBUTION')
  const tree = distributionTree(context.request)
  const caseSelect = page.getByLabel('SPDM 해석 Case 선택', { exact: true })
  await expect(caseSelect).toBeEnabled()
  await caseSelect.selectOption(tree.casePath)
  const selectContext = async (name: string) => {
    const node = page.locator('.result-registration-node').filter({ hasText: name })
    await expect(node).toBeVisible()
    await node.getByRole('button', { name: '문맥 선택', exact: true }).click()
  }
  await selectContext('Drop_800mm')
  await selectContext('Run_01')
  await selectContext('Option_A')
  await selectContext('Front')
  await expect(page.getByText('하중경우 Drop_800mm', { exact: false })).toBeVisible()
  await expect(page.getByText('Run Case Run_01', { exact: false })).toBeVisible()
  await expect(page.getByText('Run Option Option_A', { exact: false })).toBeVisible()
  await expect(page.getByText('Scene Front', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '이 기존 폴더 선택', exact: true }).click()
  await expect(page.locator('.result-registration-selected-path code')).toHaveText(tree.resultPath)
  await expect(page.getByRole('button', { name: '새 하중경우 등록', exact: true })).toHaveCount(0)
})

test('새로고침 뒤에도 현재 대상의 검수 초안을 서버에서 확인해 복원한다', async ({ page }) => {
  await loginWorkspace(page)
  await selectRequestContext(page)
  let draftReads = 0
  let publishCount = 0
  await installRegistrationApi(page, {
    scenario: 'resume',
    onCall: (kind) => { if (kind === 'get-draft') draftReads += 1; if (kind === 'publish') publishCount += 1 },
  })
  await openRegistrationTab(page)
  const tree = usageTree(context.request)
  await selectCaseAndEvaluation(page, tree.casePath)
  await page.locator('#registration-files').setInputFiles({ name: 'settle.csv', mimeType: 'text/csv', buffer: Buffer.from('evaluation,metric,value,unit\nSettle,settle_angle,1.25,deg\n') })
  await page.getByRole('button', { name: '업로드하고 자동 검사', exact: true }).click()
  await expect(page.getByTestId('result-registration-inspection')).toBeVisible()
  const saved = await page.evaluate(() => sessionStorage.getItem('result-registration:last-draft'))
  expect(saved).not.toBeNull()
  const resumeRecord = JSON.parse(saved!) as Record<string, unknown>
  expect(Object.keys(resumeRecord).sort()).toEqual(['draft_id', 'environment', 'idempotency_key', 'project_id', 'request_id'])
  await page.reload()
  await expect(page.getByTestId('result-registration-workspace')).toBeVisible()
  await expect(page.getByTestId('result-registration-inspection')).toBeVisible()
  await expect(page.getByTestId('result-registration-inspection')).toContainText('1.25')
  await expect(page.getByTestId('result-registration-inspection')).toContainText('settle.csv')
  await expect.poll(() => draftReads).toBe(1)
  expect(publishCount).toBe(0)
  await page.getByRole('button', { name: '검수 완료·DB 등록', exact: true }).click()
  await expect(page.getByTestId('result-registration-completion')).toContainText('DB 등록 완료')
  expect(publishCount).toBe(1)
})

test('게시 실패 뒤 새로고침하면 승인 상태와 같은 재시도 키를 복원한다', async ({ page }) => {
  await loginWorkspace(page)
  await selectRequestContext(page)
  const publishKeys: string[] = []
  let approveCount = 0
  await installRegistrationApi(page, {
    scenario: 'resume', failFirstPublish: true,
    onCall: (kind, body) => {
      if (kind === 'approve') approveCount += 1
      if (kind === 'publish') publishKeys.push(String((body as { idempotency_key?: string }).idempotency_key))
    },
  })
  await openRegistrationTab(page)
  await selectCaseAndEvaluation(page, usageTree(context.request).casePath)
  await page.locator('#registration-files').setInputFiles({ name: 'settle.csv', mimeType: 'text/csv', buffer: Buffer.from('evaluation,metric,value,unit\nSettle,settle_angle,1.25,deg\n') })
  await page.getByRole('button', { name: '업로드하고 자동 검사', exact: true }).click()
  await expect(page.getByTestId('result-registration-inspection')).toBeVisible()
  await page.getByRole('button', { name: '검수 완료·DB 등록', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: /temporary publish failure/ })).toBeVisible()
  const saved = await page.evaluate(() => JSON.parse(sessionStorage.getItem('result-registration:last-draft') ?? 'null') as { idempotency_key?: string } | null)
  expect(saved?.idempotency_key).toBeTruthy()
  await page.reload()
  await expect(page.getByTestId('result-registration-inspection')).toBeVisible()
  await expect(page.getByRole('button', { name: 'DB 등록 다시 시도', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'DB 등록 다시 시도', exact: true }).click()
  await expect(page.getByTestId('result-registration-completion')).toContainText('DB 등록 완료')
  expect(approveCount).toBe(1)
  expect(publishKeys).toHaveLength(2)
  expect(publishKeys[0]).toBe(saved?.idempotency_key)
  expect(publishKeys[1]).toBe(saved?.idempotency_key)
})

test('필수 오류 검수에서 파일을 수정해 새 초안으로 다시 검사할 수 있다', async ({ page }) => {
  await loginWorkspace(page)
  await selectRequestContext(page)
  const draftBodies: Array<Record<string, unknown>> = []
  let publishCount = 0
  await installRegistrationApi(page, {
    scenario: 'usage', blockingFirstInspection: true,
    onCall: (kind, body) => { if (kind === 'draft') draftBodies.push(body as Record<string, unknown>); if (kind === 'publish') publishCount += 1 },
  })
  await openRegistrationTab(page)
  await selectCaseAndEvaluation(page, usageTree(context.request).casePath)
  await page.locator('#registration-files').setInputFiles({ name: 'settle.csv', mimeType: 'application/vnd.ms-excel', buffer: Buffer.from('invalid') })
  await page.getByRole('button', { name: '업로드하고 자동 검사', exact: true }).click()
  const inspection = page.getByTestId('result-registration-inspection')
  await expect(inspection).toBeVisible()
  await expect(inspection).toContainText('게시 차단 1')
  await expect(inspection).toContainText('값 인식됨')
  await expect(inspection).toContainText('값을 읽은 항목이 있어도 파일·형식 오류가 남아 있으면 DB 등록할 수 없습니다')
  await expect(page.getByRole('button', { name: '검수 완료·DB 등록', exact: true })).toBeDisabled()
  await expect(page.getByLabel('결과 파일과 영상·이미지 선택', { exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '파일 수정 후 새 검수 시작', exact: true }).click()
  await expect(inspection).toHaveCount(0)
  await expect(page.getByLabel('결과 파일과 영상·이미지 선택', { exact: true })).toBeEnabled()
  await page.locator('#registration-files').setInputFiles({ name: 'settle.csv', mimeType: 'application/vnd.ms-excel', buffer: Buffer.from('evaluation,metric,value,unit\nSettle,settle_angle,1.25,deg\n') })
  await page.getByRole('button', { name: '업로드하고 자동 검사', exact: true }).click()
  await expect(inspection).toBeVisible()
  await expect(inspection).toContainText('게시 차단 0')
  expect(draftBodies).toHaveLength(2)
  expect((draftBodies[0].files as Array<{ media_type: string }>)[0].media_type).toBe('text/csv')
  expect((draftBodies[1].files as Array<{ media_type: string }>)[0].media_type).toBe('text/csv')
  expect(publishCount).toBe(0)
})

test('파일 업로드 실패 뒤 파일을 다시 선택해 업로드를 이어갈 수 있다', async ({ page }) => {
  await loginWorkspace(page)
  await selectRequestContext(page)
  let fileUploadAttempts = 0
  let publishCount = 0
  await installRegistrationApi(page, {
    scenario: 'usage', failFirstFileUpload: true,
    onCall: (kind) => { if (kind === 'files') fileUploadAttempts += 1; if (kind === 'publish') publishCount += 1 },
  })
  await openRegistrationTab(page)
  await selectCaseAndEvaluation(page, usageTree(context.request).casePath)
  await page.locator('#registration-files').setInputFiles({ name: 'settle.csv', mimeType: 'text/csv', buffer: Buffer.from('evaluation,metric,value,unit\nSettle,settle_angle,broken,deg\n') })
  await page.getByRole('button', { name: '업로드하고 자동 검사', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: /temporary upload failure/ })).toBeVisible()
  await expect(page.getByLabel('결과 파일과 영상·이미지 선택', { exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '파일 수정 후 새 검수 시작', exact: true }).click()
  await expect(page.getByLabel('결과 파일과 영상·이미지 선택', { exact: true })).toBeEnabled()
  await page.locator('#registration-files').setInputFiles({ name: 'settle.csv', mimeType: 'text/csv', buffer: Buffer.from('evaluation,metric,value,unit\nSettle,settle_angle,1.25,deg\n') })
  await page.getByRole('button', { name: '업로드하고 자동 검사', exact: true }).click()
  await expect(page.getByTestId('result-registration-inspection')).toBeVisible()
  expect(fileUploadAttempts).toBe(2)
  expect(publishCount).toBe(0)
})

test('제외 사유는 재검사 revision에 반영하고 같은 목록만 승인한다', async ({ page }) => {
  await loginWorkspace(page)
  await selectRequestContext(page)
  const inspectBodies: Array<Record<string, unknown> | undefined> = []
  let approveBody: Record<string, unknown> | null = null
  await installRegistrationApi(page, {
    scenario: 'usage',
    onCall: (kind, body) => { if (kind === 'inspect') inspectBodies.push(body as Record<string, unknown> | undefined); if (kind === 'approve') approveBody = body as Record<string, unknown> },
  })
  await openRegistrationTab(page)
  await selectCaseAndEvaluation(page, usageTree(context.request).casePath)
  await page.locator('#registration-files').setInputFiles([
    { name: 'settle.csv', mimeType: 'text/csv', buffer: Buffer.from('evaluation,metric,value,unit\nSettle,settle_angle,1.25,deg\n') },
    { name: mediaName, mimeType: 'video/mp4', buffer: mediaBytes },
  ])
  await page.getByRole('button', { name: '업로드하고 자동 검사', exact: true }).click()
  const inspection = page.getByTestId('result-registration-inspection')
  await expect(inspection).toBeVisible()
  const exclusion = inspection.getByLabel('이번 등록에서 제외: settle.csv', { exact: true })
  await exclusion.check()
  await expect(inspection.getByRole('button', { name: '제외 반영해 다시 검사', exact: true })).toBeDisabled()
  await inspection.getByLabel('파일 제외 사유: settle.csv', { exact: true }).fill('형식이 손상되어 이번 수집에서 제외')
  await inspection.getByRole('button', { name: '제외 반영해 다시 검사', exact: true }).click()
  await expect(inspection.getByRole('cell', { name: '제외', exact: true })).toBeVisible()
  await expect(inspection).toContainText('검사 결과값이 없습니다.')
  expect(inspectBodies).toHaveLength(2)
  expect(inspectBodies[1]?.exclusions).toEqual([{ relative_path: 'settle.csv', reason: '형식이 손상되어 이번 수집에서 제외' }])
  await page.getByLabel(/누락·제외 항목과 이번 업로드 파일만/).check()
  await page.getByRole('button', { name: '검수 완료·DB 등록', exact: true }).click()
  expect(approveBody?.exclusions).toEqual([{ relative_path: 'settle.csv', reason: '형식이 손상되어 이번 수집에서 제외' }])
})

test('실제 결과등록 API에 격리 SPDM 경로를 연결해 영상 포함 결과를 게시하고 Case 화면에서 재생한다', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1200 })
  await loginWorkspace(page)
  await selectRequestContext(page)
  const apiCalls: string[] = []
  const dashboardAssetRequests: string[] = []
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/result-registration/')) apiCalls.push(`${request.method()} ${url.pathname}`)
    if (url.pathname.startsWith('/api/dashboard/assets/')) dashboardAssetRequests.push(`${request.method()} ${url.pathname}`)
  })
  const target = await prepareActualSpdmTarget(page)
  await openRegistrationTab(page)
  const caseSelect = page.getByLabel('SPDM 해석 Case 선택', { exact: true })
  await expect(caseSelect).toBeEnabled()
  await caseSelect.selectOption(target.casePath)
  const evaluationNode = page.locator('.result-registration-node').filter({ hasText: 'Settle' })
  await expect(evaluationNode).toBeVisible()
  await evaluationNode.getByRole('button', { name: '문맥 선택', exact: true }).click()
  await expect(page.getByRole('button', { name: '하위 결과 폴더 준비', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '하위 결과 폴더 준비', exact: true }).click()
  const preparation = page.getByLabel('결과 폴더 생성 경로 미리보기')
  await expect(preparation).toContainText(target.resultPath)
  await preparation.getByLabel('표시된 결과용 하위 폴더만 생성하도록 확인했습니다.').check()
  await preparation.getByRole('button', { name: '경로 확인 후 폴더 생성', exact: true }).click()
  await expect(page.locator('.result-registration-selected-path code')).toHaveText(target.resultPath)
  await page.locator('#registration-files').setInputFiles([
    { name: 'model_settle_result.json', mimeType: 'application/json', buffer: Buffer.from('{"Set Tilt Angle @ Settle (deg)":1.18}') },
    { name: mediaName, mimeType: 'video/mp4', buffer: mediaBytes },
  ])
  await page.getByRole('button', { name: '업로드하고 자동 검사', exact: true }).click()
  const inspection = page.getByTestId('result-registration-inspection')
  await expect(inspection).toBeVisible()
  await expect(inspection).toContainText('1.18')
  await expect(inspection).toContainText('Set Tilt Angle')
  await expect(inspection).toContainText('deg')
  await expect(inspection).toContainText(mediaName)
  await expect(inspection).toContainText('업로드 완료')
  await expect(inspection).toContainText('원본 없음')
  await expect(inspection).toContainText('주의')
  await expect(inspection).not.toContainText(/UPLOADED|MISSING_SOURCE|WARNING|INFO/)
  const draftRecord = JSON.parse((await page.evaluate(() => sessionStorage.getItem('result-registration:last-draft')))!) as { draft_id: string }
  const stagedDraft = await page.evaluate(async (draftId) => {
    const response = await fetch(`/api/result-registration/drafts/${encodeURIComponent(draftId)}`)
    return { status: response.status, body: await response.json() as { status?: string; capture_id?: string | null; manifest?: Array<{ relative_path: string; sha256?: string | null }> } }
  }, draftRecord.draft_id)
  expect(stagedDraft.status).toBe(200)
  expect(stagedDraft.body.status).toBe('INSPECTED')
  expect(stagedDraft.body.capture_id).toBeNull()
  expect(stagedDraft.body.manifest?.find((item) => item.relative_path === mediaName)?.sha256).toMatch(/^[a-f0-9]{64}$/)
  const reviewVideo = inspection.locator('.result-registration-media-list video')
  await expect(reviewVideo).toBeVisible()
  await expect.poll(() => reviewVideo.evaluate((video: HTMLVideoElement) => Number.isFinite(video.duration) && video.duration > 0)).toBe(true)
  await inspection.scrollIntoViewIfNeeded()
  await page.screenshot({ path: path.join(tmpdir(), 'simulation-workbench-result-registration-actual-review.png') })
  const acknowledgement = page.getByLabel(/누락·제외 항목과 이번 업로드 파일만/)
  if (await acknowledgement.count()) await acknowledgement.check()
  await page.getByRole('button', { name: '검수 완료·DB 등록', exact: true }).click()
  const completion = page.getByTestId('result-registration-completion')
  await expect(completion).toContainText('DB 등록 완료')
  await expect(completion).toContainText('영상')
  await expect(completion).toContainText('capture-')
  await completion.scrollIntoViewIfNeeded()
  await page.screenshot({ path: path.join(tmpdir(), 'simulation-workbench-result-registration-actual-completion.png') })
  const resultLink = page.getByRole('link', { name: 'Case 결과 보기', exact: true })
  await expect(resultLink).toHaveAttribute('href', /view=case_results/)
  const caseResultHref = await resultLink.getAttribute('href')
  const publishedCapture = new URL(caseResultHref!, 'http://127.0.0.1').searchParams.get('capture')
  expect(publishedCapture).toBeTruthy()
  await resultLink.click()
  await expect(page).toHaveURL(/view=case_results/)
  await expect(page.getByTestId('usage-dashboard')).toBeVisible()
  const dashboardVideo = page.locator('.simulation-dashboard__usage-media video')
  await expect(dashboardVideo).toBeVisible()
  await expect.poll(() => dashboardVideo.evaluate((video: HTMLVideoElement) => Number.isFinite(video.duration) && video.duration > 0)).toBe(true)
  await expect(dashboardVideo).toHaveAttribute('src', /\/api\/dashboard\/assets\//)
  const playbackStart = await dashboardVideo.evaluate(async (video: HTMLVideoElement) => {
    video.muted = true
    await video.play()
    return video.currentTime
  })
  await expect.poll(() => dashboardVideo.evaluate((video: HTMLVideoElement) => video.currentTime), { timeout: 5_000 }).toBeGreaterThan(playbackStart)
  await dashboardVideo.evaluate((video: HTMLVideoElement) => video.pause())
  expect(apiCalls.some((call) => call.endsWith('/publish'))).toBe(true)
  expect(apiCalls.some((call) => call.endsWith('/approve'))).toBe(true)
  expect(apiCalls.some((call) => call.endsWith('/inspect'))).toBe(true)
  expect(dashboardAssetRequests.some((call) => call.startsWith('GET /api/dashboard/assets/'))).toBe(true)
})

test('대상 전환 뒤 도착한 이전 Case의 검사 결과는 새 문맥에 붙지 않는다', async ({ page }) => {
  await loginWorkspace(page)
  await selectRequestContext(page)
  let signalInspect!: () => void
  let releaseInspect!: () => void
  const inspectStarted = new Promise<void>((resolve) => { signalInspect = resolve })
  const inspectGate = new Promise<void>((resolve) => { releaseInspect = resolve })
  await installRegistrationApi(page, { scenario: 'stale', requestIds: [context.request, context.secondRequest], inspectGate, signalInspect })
  await openRegistrationTab(page)
  const firstTree = usageTree(context.request)
  await selectCaseAndEvaluation(page, firstTree.casePath)
  await page.locator('#registration-files').setInputFiles({ name: 'settle.csv', mimeType: 'text/csv', buffer: Buffer.from('evaluation,metric,value,unit\nSettle,settle_angle,1.25,deg\n') })
  await page.getByRole('button', { name: '업로드하고 자동 검사', exact: true }).click()
  await inspectStarted
  await page.getByLabel('의뢰 선택', { exact: true }).selectOption(context.secondRequest)
  await expect(page.getByLabel('의뢰 선택', { exact: true })).toHaveValue(context.secondRequest)
  await expect(page.getByLabel('SPDM 해석 Case 선택', { exact: true })).toBeEnabled()
  releaseInspect()
  await expect(page.getByTestId('result-registration-inspection')).toHaveCount(0)
  await expect(page.getByText('1.25', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('SPDM 해석 Case 선택', { exact: true })).not.toHaveValue(firstTree.casePath)
})
