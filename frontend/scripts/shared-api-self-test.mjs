import { apiErrorMessage } from '../src/shared/api/errors.ts'

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

console.log('Shared API self-test passed.')
