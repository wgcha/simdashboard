import { apiErrorMessage } from '../src/shared/api/errors.ts'
import { formatServerTime, noteServerClock, parseServerTime, relativeServerTime, resetServerClock, serverClockOffsetMs, serverNow, serverTimeZone, SERVER_CLOCK_WINDOW_MS } from '../src/shared/api/serverTime.ts'
import { folderProgressPath, folderProgressStatusLabel, normalizeFolderProgress } from '../src/shared/api/folderProgress.ts'
import { folderDefaultEnvironment, keywordEnvironments, normalizeResultEnvironments, resolvedResultEnvironment, resultEnvironmentsPath } from '../src/shared/api/resultEnvironments.ts'
import { chunkRanges, depthRules, dropEntries, dropGuide, entryRelativePath, formatBytes as dropBytes, pickedFromInput, walkEntries } from '../src/shared/api/resultDropModel.ts'
import { depthRowEditable, depthRows, errorCode, errorItems, lowerRoleOptions, rescanLine, setDepthRole, sumDeleteCounts, validateLowerLevels, validateUpperLevels } from '../src/shared/api/depthSchemaModel.ts'
import { clearExclusions, descendants, effectivelyExcluded, excludedPaths, excludeReviewNodes, excludeSubtree, exclusionCount, includeSubtree, reviewNodes } from '../src/shared/api/folderExclusionModel.ts'
import { timeoutGuard } from '../src/shared/api/requestTimeout.ts'
import { normalizePhysicsAiLink, physicsAiNavHref, PHYSICSAI_LINK_OFF } from '../src/shared/api/physicsaiLinkModel.ts'
import { eslNavHref, ESL_LINK_OFF, normalizeEslLink } from '../src/shared/api/eslLinkModel.ts'
import { elapsedText, rescanProgressDetail } from '../src/shared/api/storageRescanModel.ts'

const validation = { detail: [{ type: 'missing', loc: ['body', 'name'], msg: 'Field required' }] }
const validationMessage = apiErrorMessage(validation, '요청 실패 (422)')
if (validationMessage !== JSON.stringify(validation.detail)) {
  throw new Error(`generated 422 detail was lost: ${validationMessage}`)
}

const coded = { code: 'REQUEST_NOT_FOUND', request_id: 'request-missing' }
if (!apiErrorMessage(coded, '요청 실패').startsWith('REQUEST_NOT_FOUND:')) {
  throw new Error('coded API error lost its code prefix')
}

const codedMessage = { code: 'RESULT_BUNDLE_ROOTS_OVERLAP', message: '결과 경로가 겹칩니다.' }
if (apiErrorMessage(codedMessage, '요청 실패') !== 'RESULT_BUNDLE_ROOTS_OVERLAP: 결과 경로가 겹칩니다.') {
  throw new Error('coded API error did not prefer its readable message')
}

if (apiErrorMessage({ detail: '정확한 오류' }, '요청 실패') !== '정확한 오류') {
  throw new Error('nested detail string was not preserved')
}

const assert = (condition, message) => { if (!condition) throw new Error(message) }
const levels = (...roles) => roles.map((role, index) => ({ level: index + 1, role }))

// Depth schema validation mirrors contract §4.3.
assert(validateUpperLevels(levels('PROJECT', 'REQUEST')).length === 0, 'default upper schema must be valid')
assert(validateUpperLevels(levels('CONTAINER', 'PROJECT', 'REQUEST')).length === 0, 'container before project must be valid')
assert(validateUpperLevels(levels('REQUEST', 'PROJECT')).length > 0, 'request before project must be rejected')
assert(validateUpperLevels(levels('PROJECT', 'REQUEST', 'CONTAINER')).length > 0, 'request must be the last upper level')
assert(validateUpperLevels(levels('PROJECT', 'PROJECT', 'REQUEST')).length > 0, 'duplicate project must be rejected')
assert(validateUpperLevels(levels(...Array(8).fill('CONTAINER'), 'PROJECT', 'REQUEST')).length > 0, 'more than 8 upper levels must be rejected')
const working = { level: 1, role: 'WORKING', fixed_name: 'Working' }
assert(validateLowerLevels('USAGE', [working, ...levels('SIMULATION_CASE', 'SCENE').map((item) => ({ ...item, level: item.level + 1 }))]).length === 0, 'usage default must be valid')
const distribution = [working, ...levels('SIMULATION_CASE', 'LOAD_CASE', 'EXECUTION_RUN', 'RUN_OPTION', 'SCENE').map((item) => ({ ...item, level: item.level + 1 }))]
assert(validateLowerLevels('DISTRIBUTION', distribution).length === 0, 'distribution default must be valid')
assert(validateLowerLevels('DISTRIBUTION', distribution.filter((item) => item.role !== 'RUN_OPTION').map((item, index) => ({ ...item, level: index + 1 }))).length > 0, 'distribution without RUN_OPTION must be rejected')
assert(validateLowerLevels('USAGE', [working, { level: 2, role: 'SIMULATION_CASE' }, { level: 3, role: 'LOAD_CASE' }, { level: 4, role: 'SCENE' }]).length > 0, 'usage must not accept distribution roles')
assert(validateLowerLevels('USAGE', [working, { level: 2, role: 'SIMULATION_CASE' }, { level: 3, role: 'CONTAINER' }]).length > 0, 'lower schema must end with SCENE')
assert(lowerRoleOptions('USAGE', 1).join() === 'WORKING' && lowerRoleOptions('DISTRIBUTION', 2).join() === 'SIMULATION_CASE', 'L1/L2 roles are fixed')
assert(!lowerRoleOptions('USAGE', 3).includes('EVALUATION'), 'EVALUATION is not offered in DEPTH_V1')

// Rows follow sampled depth; only the last row can be removed and only the next row added.
const rows = depthRows(levels('PROJECT', 'REQUEST'), [{ level: 1, folder_count: 2, samples: [], truncated: false }, { level: 3, folder_count: 5, samples: [], truncated: false }])
assert(rows.length === 3 && rows[2].inSchema === false && rows[2].sample?.folder_count === 5, 'rows must cover sampled depth beyond the schema')
assert(setDepthRole(levels('PROJECT', 'REQUEST'), 1, '').length === 2, 'non-last level must not be removable')
assert(setDepthRole(levels('PROJECT', 'REQUEST'), 2, '').length === 1, 'last level must be removable')
assert(setDepthRole(levels('PROJECT'), 2, 'REQUEST').length === 2, 'next level must be addable')
assert(setDepthRole(levels('PROJECT'), 3, 'REQUEST').length === 1, 'gaps must not be created')
assert(depthRowEditable(2, 3) && !depthRowEditable(2, 4) && !depthRowEditable(6, 1, true), 'row editability')

// Delete preview helpers (§13.6).
const totals = sumDeleteCounts([{ counts: { projects: 1, cases: 2 } }, { counts: { projects: 0, cases: 3, captures: 4 } }])
assert(totals.projects === 1 && totals.cases === 5 && totals.captures === 4, 'delete counts must be summed')
const blocked = { detail: { code: 'REGISTRATION_DELETE_BLOCKED', items: [{ registration_id: 'r1', deletable: false }] } }
assert(errorCode(blocked) === 'REGISTRATION_DELETE_BLOCKED' && errorItems(blocked).length === 1, 'blocked delete detail must be readable')
assert(errorCode({ detail: { code: 'DEPTH_SCHEMA_CONFLICT' } }) === 'DEPTH_SCHEMA_CONFLICT' && errorCode(new Error('x')) === null, 'conflict code must be readable')

// Timestamps without an offset are UTC (depth-schema.md §14.3).
assert(parseServerTime('2026-10-02T23:02:00')?.toISOString() === '2026-10-02T23:02:00.000Z', 'naive timestamp must be read as UTC')
assert(parseServerTime('2026-10-02 23:02:00.123456')?.getUTCHours() === 23, 'space-separated naive timestamp must be read as UTC')
assert(parseServerTime('2026-10-02T23:02:00Z')?.toISOString() === '2026-10-02T23:02:00.000Z', 'Z timestamp must be kept')
assert(parseServerTime('2026-10-03T08:02:00+09:00')?.toISOString() === '2026-10-02T23:02:00.000Z', 'offset timestamp must be kept')
assert(parseServerTime('') === null && parseServerTime('not a date') === null, 'invalid timestamps are null')
const kst = formatServerTime('2026-10-02T23:02:00', { hour: 'numeric', minute: '2-digit', hour12: false }, 'Asia/Seoul')
assert(kst.includes('08:02') || kst.includes('8:02'), `UTC 23:02 must show as 08:02 KST, got ${kst}`)
assert(formatServerTime('legacy') === 'legacy', 'unreadable timestamps are shown as given')

// Server clock reference (docs/features/server-time.md): relative ages follow the server clock,
// absolute times the server zone, whatever the PC clock or zone says.
{
  resetServerClock()
  assert(serverClockOffsetMs() === 0 && serverTimeZone() === undefined, 'no response yet: client clock and browser zone')
  const hour = 3_600_000
  const client = Date.now()
  const server = client - 18 * hour   // PC clock 18 h ahead of the server (the reported "18시간 전")
  const createdAt = new Date(server - 3 * 60_000)
  const naive = createdAt.toISOString().replace('Z', '')              // legacy naive UTC string
  const offset = createdAt.toISOString()                              // Z
  const kstText = new Date(createdAt.getTime() + 9 * hour).toISOString().replace('Z', '+09:00')
  assert(relativeServerTime(offset, client) === '18시간 전', 'without the server reference the PC clock skew shows')
  noteServerClock(new Headers({ 'X-Server-Time': new Date(server).toISOString(), 'X-Server-Timezone': 'Asia/Seoul', 'X-Server-UTC-Offset-Minutes': '540' }), client)
  assert(Math.abs(serverClockOffsetMs() + 18 * hour) < 1000, `offset must be −18 h, got ${serverClockOffsetMs()}`)
  assert(Math.abs(serverNow() - server) < 5_000, 'serverNow follows the server clock')
  for (const text of [naive, offset, kstText]) {
    assert(relativeServerTime(text) === '3분 전', `${text} must be 3분 전 on the server clock, got ${relativeServerTime(text)}`)
  }
  assert(relativeServerTime(new Date(server + 30_000).toISOString()) === '방금', 'a slightly future stamp is 방금')
  assert(relativeServerTime(null) === '' && relativeServerTime('garbage') === '', 'unreadable is empty')
  // A sample with more latency (smaller server − received) does not lower the estimate.
  noteServerClock(new Headers({ 'X-Server-Time': new Date(server - 400).toISOString() }), client)
  assert(Math.abs(serverClockOffsetMs() + 18 * hour) < 50, 'the largest sample in the window wins')
  // An old window is dropped so a corrected server clock is followed.
  noteServerClock(new Headers({ 'X-Server-Time': new Date(client + SERVER_CLOCK_WINDOW_MS + 1).toISOString() }), client + SERVER_CLOCK_WINDOW_MS + 1)
  assert(Math.abs(serverClockOffsetMs()) < 50, 'samples older than the window are dropped')
  // Absolute times use the server zone (run with TZ=America/Anchorage to see the browser zone is ignored).
  const shown = formatServerTime('2026-10-02T23:02:00Z', { hour: 'numeric', minute: '2-digit', hour12: false })
  assert(serverTimeZone() === 'Asia/Seoul' && (shown.includes('08:02') || shown.includes('8:02')), `server zone display must be 08:02 KST, got ${shown}`)
  resetServerClock()
  noteServerClock(new Headers({ 'X-Server-Time': new Date().toISOString(), 'X-Server-UTC-Offset-Minutes': '540' }))
  assert(serverTimeZone() === 'Etc/GMT-9', `offset-only server zone must be Etc/GMT-9, got ${serverTimeZone()}`)
  noteServerClock(new Headers({ 'X-Server-Timezone': 'Not/AZone' }))
  assert(serverTimeZone() === 'Etc/GMT-9', 'an invalid zone name is ignored')
  noteServerClock(undefined)
  noteServerClock({})
  resetServerClock()
}

// Request result environments (case-results-environment.md §2).
assert(resultEnvironmentsPath('p 1', 'r/1') === '/api/projects/p%201/requests/r%2F1/result-environments', 'result-environments path must encode ids')
const both = normalizeResultEnvironments({ environments: ['DISTRIBUTION', 'USAGE', 'OTHER'], case_counts: { USAGE: 2, DISTRIBUTION: 1 } })
assert(both.environments.join() === 'USAGE,DISTRIBUTION' && resolvedResultEnvironment(both) === null, 'two environments keep the toggle, in USAGE, DISTRIBUTION order')
assert(resolvedResultEnvironment(normalizeResultEnvironments({ environments: ['DISTRIBUTION'], case_counts: { USAGE: 0, DISTRIBUTION: 3 } })) === 'DISTRIBUTION', 'one environment is resolved')
assert(folderDefaultEnvironment(normalizeResultEnvironments({ environments: [], case_counts: { USAGE: 0, DISTRIBUTION: 0 }, folder_environments: ['USAGE'] })) === 'USAGE', 'a request without Cases defaults to its folder environment (issue #51)')
assert(folderDefaultEnvironment(normalizeResultEnvironments({ environments: [], case_counts: { USAGE: 0, DISTRIBUTION: 0 } })) === null, 'no folder environment, no default')
const none = normalizeResultEnvironments({ environments: [], case_counts: { USAGE: 0, DISTRIBUTION: 0 } })
assert(none.environments.length === 0 && resolvedResultEnvironment(none) === null && none.case_counts.USAGE === 0, 'no Cases resolve to no environment')

assert(keywordEnvironments('24-071 사용 낙하').join() === 'USAGE' && keywordEnvironments('유통 진동').join() === 'DISTRIBUTION', 'request keyword picks the sync environment')
assert(keywordEnvironments('사용 유통').join() === 'USAGE,DISTRIBUTION' && keywordEnvironments('').join() === 'USAGE,DISTRIBUTION', 'unclear keyword syncs both environments')

// Folder request progress (folder-request-progress.md §3).
assert(folderProgressPath('p 1', 'r/1') === '/api/projects/p%201/requests/r%2F1/folder-progress', 'folder-progress path must encode ids')
const progress = normalizeFolderProgress({ applicable: true, environment: 'USAGE', completed: 2, total: 5, current_key: 'RESULTS', next_action: '결과 대기 Case 1개', checked_at: '2026-10-04T00:41:00+00:00', steps: [
  { key: 'REGISTERED', label: '의뢰 등록', status: 'DONE', detail: null }, { key: 'MODELING', label: '해석 모델링', status: 'DONE', detail: 'Case 2/2 입력 있음' },
  { key: 'RESULTS', label: '해석 결과', status: 'IN_PROGRESS', detail: 'Case 1/2 결과 있음' }, { key: 'FINAL', label: 'Final 지정', status: 'BOGUS', detail: '' }, { key: 'REPORT', label: '보고서', status: 'WAITING', detail: null }] })
assert(progress.applicable && progress.completed === 2 && progress.total === 5 && progress.current_key === 'RESULTS' && progress.next_action === '결과 대기 Case 1개', 'applicable progress keeps counts and next action')
assert(progress.steps[3].status === 'WAITING' && progress.steps[3].detail === null && progress.steps[2].detail === 'Case 1/2 결과 있음', 'unknown status is WAITING and empty detail is null')
assert(!normalizeFolderProgress({ applicable: false, steps: [] }).applicable && !normalizeFolderProgress(null).applicable && !normalizeFolderProgress({ applicable: true }).applicable, 'not applicable or stepless responses keep the legacy overview')
const finished = normalizeFolderProgress({ applicable: true, current_key: null, next_action: '', steps: [{ key: 'REGISTERED', label: '의뢰 등록', status: 'DONE', detail: null }] })
assert(finished.current_key === null && finished.next_action === '완료', 'all steps done reads as 완료')
assert(folderProgressStatusLabel('DONE') === '완료' && folderProgressStatusLabel('IN_PROGRESS') === '진행 중' && folderProgressStatusLabel('WAITING') === '대기', 'status labels follow §4')

// W8 결과 등록 drag & drop (result-registration.md).
const fakeFile = (name, size = 1) => ({ name, size })
const fileEntry = (fullPath) => ({ isFile: true, isDirectory: false, name: fullPath.split('/').pop(), fullPath, file: (ok) => ok(fakeFile(fullPath.split('/').pop())) })
const dirEntry = (fullPath, children) => {
  let served = false
  return { isFile: false, isDirectory: true, name: fullPath.split('/').pop(), fullPath, createReader: () => ({ readEntries: (ok) => { const batch = served ? [] : children; served = true; ok(batch) } }) }
}
const walked = await walkEntries([dirEntry('/CUMULATIVE', [dirEntry('/CUMULATIVE/7_Edge', [fileEntry('/CUMULATIVE/7_Edge/a.csv')]), dirEntry('/CUMULATIVE/9_Empty', [])]), fileEntry('/top.csv')])
assert(walked.files.map((item) => item.relativePath).join() === 'CUMULATIVE/7_Edge/a.csv,top.csv', 'dropped folders keep their relative structure')
assert(walked.folders.join() === 'CUMULATIVE/9_Empty', 'empty dropped folders are kept')
assert(entryRelativePath({ fullPath: '/a/b.csv', name: 'b.csv' }) === 'a/b.csv', 'entry paths are relative')
assert(pickedFromInput([{ name: 'a.csv', size: 1, webkitRelativePath: 'Run/INDIVIDUAL/a.csv' }, { name: 'b.csv', size: 1 }]).files.map((item) => item.relativePath).join() === 'Run/INDIVIDUAL/a.csv,b.csv', 'folder picker keeps webkitRelativePath')
const dropped = dropEntries({ items: [{ kind: 'string' }, { kind: 'file', webkitGetAsEntry: () => fileEntry('/x.csv') }], files: [fakeFile('x.csv')] })
assert(dropped.entries.length === 1 && dropped.files.length === 0, 'drop uses entries when the browser provides them')
assert(dropEntries({ items: [], files: [fakeFile('x.csv')] }).files.length === 1, 'drop falls back to the file list')
const distributionRoles = ['WORKING', 'SIMULATION_CASE', 'LOAD_CASE', 'EXECUTION_RUN', 'RUN_OPTION', 'SCENE']
assert(dropGuide('EXECUTION_RUN', distributionRoles).put === 'Run Option 폴더를 통째로 넣으세요.', 'Run Case takes Run Option folders')
assert(dropGuide('SCENE', distributionRoles).put.startsWith('결과 파일'), 'Scene takes result files')
assert(depthRules(distributionRoles).some((rule) => rule.text === 'Run Option 폴더를 통째로 복사하려면 Run Case 폴더 아래에'), 'depth rules name the parent level')
assert(JSON.stringify(chunkRanges(10, 4)) === '[[0,4],[4,8],[8,10]]' && JSON.stringify(chunkRanges(10, 4, 4)) === '[[4,8],[8,10]]', 'chunks resume from the offset')
assert(JSON.stringify(chunkRanges(0, 4)) === '[[0,0]]' && chunkRanges(4, 4, 4).length === 0, 'empty files send one empty chunk')
assert(dropBytes(512) === '512 B' && dropBytes(1536) === '1.5 KB' && dropBytes(3 * 1024 ** 3) === '3.0 GB', 'byte formatting')

// PhysicsAI "AI 예측" sidebar link: off by default, same-origin root path, hidden when signed out.
assert(normalizePhysicsAiLink(null).enabled === false && normalizePhysicsAiLink({ enabled: 'true' }).enabled === false, 'unknown link payload must be off')
assert(normalizePhysicsAiLink({ enabled: true, base_path: 'https://evil.example/' }).basePath === '/physicsai/', 'base path must stay same-origin')
assert(normalizePhysicsAiLink({ enabled: true, base_path: '//evil.example/x' }).basePath === '/physicsai/', 'protocol-relative base path must be rejected')
assert(physicsAiNavHref(PHYSICSAI_LINK_OFF, true) === null, 'flag off must hide the sidebar link')
assert(physicsAiNavHref(normalizePhysicsAiLink({ enabled: false, base_path: '/physicsai/' }), true) === null, 'server flag off must hide the sidebar link')
assert(physicsAiNavHref(normalizePhysicsAiLink({ enabled: true, base_path: '/physicsai/' }), true) === '/physicsai/', 'flag on must show the sidebar link at /physicsai/')
assert(physicsAiNavHref(normalizePhysicsAiLink({ enabled: true, base_path: '/physicsai/' }), false) === null, 'signed-out sidebar must hide the link')

// ESL "등가정적하중" sidebar link: same rules as PhysicsAI at /esl/.
assert(normalizeEslLink(null).enabled === false && normalizeEslLink({ enabled: 'true' }).enabled === false, 'unknown ESL payload must be off')
assert(normalizeEslLink({ enabled: true, base_path: 'https://evil.example/' }).basePath === '/esl/', 'ESL base path must stay same-origin')
assert(normalizeEslLink({ enabled: true, base_path: '//evil.example/x' }).basePath === '/esl/', 'ESL protocol-relative base path must be rejected')
assert(normalizeEslLink({ enabled: true, base_path: '/esl' }).basePath === '/esl/', 'ESL base path gets a trailing slash')
assert(eslNavHref(ESL_LINK_OFF, true) === null, 'ESL flag off must hide the sidebar link')
assert(eslNavHref(normalizeEslLink({ enabled: false, base_path: '/esl/' }), true) === null, 'ESL server flag off must hide the sidebar link')
assert(eslNavHref(normalizeEslLink({ enabled: true, base_path: '/esl/' }), true) === '/esl/', 'ESL flag on must show the sidebar link at /esl/')
assert(eslNavHref(normalizeEslLink({ enabled: true, base_path: '/esl/' }), false) === null, 'signed-out sidebar must hide the ESL link')

// Structure review bulk exclusion (issue #55): "확인 필요" nodes are excluded in one action, a folder together with its subtree.
const tree = [
  { id: 'root', relative_path: '', status: 'CONFIRMED' },
  { id: 'p', relative_path: 'P', status: 'CONFIRMED' },
  { id: 'r1', relative_path: 'P/R1', status: 'UNRESOLVED' },
  { id: 'r1w', relative_path: 'P/R1/Working', status: 'UNRESOLVED' },
  { id: 'r1x', relative_path: 'P/R1/Working/X', status: 'CONFIRMED', deviation: { code: 'FINAL_VERSION_INVALID' } },
  { id: 'r2', relative_path: 'P/R2', status: 'CONFIRMED' },
  { id: 'r2x', relative_path: 'P/R2/Odd', status: 'ERROR' },
  { id: 'r10', relative_path: 'P/R10', status: 'CONFIRMED' },
]
assert(reviewNodes(tree, {}).map((node) => node.id).join() === 'r1,r1w,r1x,r2x', 'review nodes are the unresolved / error / deviating ones')
const allExcluded = excludeReviewNodes(tree, {})
assert(exclusionCount(allExcluded) === 4 && allExcluded.r1.role_kind === 'EXCLUDE' && allExcluded.r1.target_mode === 'CREATE' && allExcluded.r1.confirm === true && allExcluded.r1.propagate_same_level === false, 'bulk exclusion creates EXCLUDE assignments')
assert(reviewNodes(tree, allExcluded).length === 0, 'nothing is left to review after the bulk exclusion')
assert(JSON.stringify(excludeReviewNodes(tree, allExcluded)) === JSON.stringify(allExcluded), 'bulk exclusion is idempotent')
const keepLink = { r2: { node_id: 'r2', role_kind: 'REQUEST', target_mode: 'LINK', target_id: 'req-1', confirm: true, propagate_same_level: false } }
assert(clearExclusions({ ...allExcluded, ...keepLink }).r2.target_mode === 'LINK' && exclusionCount(clearExclusions({ ...allExcluded, ...keepLink })) === 0, '선택 해제 keeps project/request links')
const subtree = excludeSubtree(tree, {}, tree[2])
assert(exclusionCount(subtree) === 1 && subtree.r1, 'a subtree exclusion is one assignment (the server cascades)')
assert(excludedPaths(tree, subtree).join() === 'P/R1' && effectivelyExcluded(tree[3], subtree, ['P/R1']) && effectivelyExcluded(tree[4], subtree, ['P/R1']), 'descendants count as excluded')
assert(!effectivelyExcluded(tree[7], subtree, ['P/R1']) && !effectivelyExcluded(tree[5], subtree, ['P/R1']), 'siblings with a common name prefix are not excluded')
assert(reviewNodes(tree, subtree).map((node) => node.id).join() === 'r2x', 'excluding a folder removes its descendants from the review list')
assert(descendants(tree, tree[2]).map((node) => node.id).join() === 'r1w,r1x', 'descendants by path')
assert(exclusionCount(includeSubtree(tree, excludeReviewNodes(tree, {}), tree[2])) === 1, 'un-excluding a folder also drops explicit exclusions below it')
assert(exclusionCount(excludeSubtree(tree, {}, tree[0])) === tree.length, 'the scan root lists its descendants explicitly (no path prefix to cascade on)')

// Reads of the depth-schema editor never wait forever (issue #55): a deadline aborts, a caller abort is not a timeout.
const slow = timeoutGuard(undefined, 20)
await new Promise((resolve) => setTimeout(resolve, 60))
assert(slow.signal.aborted && slow.timedOut(), 'the deadline aborts the request')
slow.dispose()
const parent = new AbortController()
const linked = timeoutGuard(parent.signal, 5000)
parent.abort()
assert(linked.signal.aborted && !linked.timedOut(), 'a caller abort passes through and is not reported as a timeout')
linked.dispose()
const quick = timeoutGuard(undefined, 20)
quick.dispose()
await new Promise((resolve) => setTimeout(resolve, 60))
assert(!quick.signal.aborted, 'a finished request is not aborted later')

// [저장 폴더 새로고침] started from the depth-schema editor.
assert(rescanLine({ status: 'RUNNING', phase: 'SYNC', progress: { done: 2, total: 9 } }) === '저장 폴더 확인 중 · 의뢰 2/9', 'sync progress line')
assert(rescanLine({ status: 'RUNNING', phase: 'DISCOVER', created_requests: [1, 2] }) === '저장 폴더 확인 중 · 새 폴더 2개', 'discover progress line')
assert(rescanLine({ status: 'RUNNING', phase: 'REINTERPRET', progress: { done: 1, total: 4 } }) === '저장 폴더 확인 중 · 새 깊이 스키마 적용 1/4', 'reinterpret progress line')
assert(rescanLine({ status: 'DONE', progress: { done: 9, total: 9 }, refreshed: 7, needs_review: [], failed: [], partial: true }).includes('한 번 더'), 'partial result asks for another run')
assert(rescanLine({ status: 'FAILED', error: '실패' }) === '실패' && rescanLine(null) === '' && rescanLine({ status: 'IDLE' }) === '', 'failed / idle lines')

assert(rescanLine({ status: 'CANCELLED' }).includes('중지'), 'cancelled rescan line')
// Issue #55: live progress of a rescan stuck in DISCOVER (where, drive work, last drive error, elapsed, cancel).
const stuck = { status: 'RUNNING', phase: 'DISCOVER', rounds: 0, elapsed_seconds: 845,
  progress: { done: 0, total: 0, projects_done: 3, projects_total: 40, current_path: 'PROJ-000002_testProject' },
  drive: { calls: 130, listings: 120, errors: 5, retries: 7, last_error: { code: 'DRIVE_BUSY', operation: 'list_dir', relative_path: 'PROJ-000002_testProject/CAD' } } }
const stuckLine = rescanProgressDetail(stuck)
for (const part of ['프로젝트 3/40', 'PROJ-000002_testProject', '드라이브 목록 120건', '재시도 7회', 'DRIVE_BUSY (PROJ-000002_testProject/CAD)', '경과 14분 5초']) {
  assert(stuckLine.includes(part), `rescan progress detail lacks ${part}: ${stuckLine}`)
}
assert(rescanProgressDetail({ status: 'RUNNING', phase: 'DISCOVER', progress: { done: 0, total: 0 } }) === '폴더 목록 읽는 중', 'discover before the project list')
assert(rescanProgressDetail({ ...stuck, cancel_requested: true }).includes('중지 요청됨'), 'cancel requested is shown')
assert(rescanProgressDetail({ status: 'DONE' }) === '' && rescanProgressDetail(null) === '', 'no detail when not running')
assert(elapsedText(59) === '59초' && elapsedText(0) === '', 'elapsed text')

console.log('Shared API self-test passed.')
