import { apiErrorMessage } from '../src/shared/api/errors.ts'
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

console.log('Shared API self-test passed.')
