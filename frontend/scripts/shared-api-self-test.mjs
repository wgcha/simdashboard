import { apiErrorMessage } from '../src/shared/api/errors.ts'
import { formatServerTime, parseServerTime } from '../src/shared/api/serverTime.ts'
import { folderProgressPath, folderProgressStatusLabel, normalizeFolderProgress } from '../src/shared/api/folderProgress.ts'
import { keywordEnvironments, normalizeResultEnvironments, resolvedResultEnvironment, resultEnvironmentsPath } from '../src/shared/api/resultEnvironments.ts'
import { depthRowEditable, depthRows, errorCode, errorItems, lowerRoleOptions, setDepthRole, sumDeleteCounts, validateLowerLevels, validateUpperLevels } from '../src/shared/api/depthSchemaModel.ts'

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

// Request result environments (case-results-environment.md §2).
assert(resultEnvironmentsPath('p 1', 'r/1') === '/api/projects/p%201/requests/r%2F1/result-environments', 'result-environments path must encode ids')
const both = normalizeResultEnvironments({ environments: ['DISTRIBUTION', 'USAGE', 'OTHER'], case_counts: { USAGE: 2, DISTRIBUTION: 1 } })
assert(both.environments.join() === 'USAGE,DISTRIBUTION' && resolvedResultEnvironment(both) === null, 'two environments keep the toggle, in USAGE, DISTRIBUTION order')
assert(resolvedResultEnvironment(normalizeResultEnvironments({ environments: ['DISTRIBUTION'], case_counts: { USAGE: 0, DISTRIBUTION: 3 } })) === 'DISTRIBUTION', 'one environment is resolved')
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

console.log('Shared API self-test passed.')
