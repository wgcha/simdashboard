import createClient from 'openapi-fetch'

import { apiFetch } from './auth'
import { ApiError, apiErrorMessage } from './errors'
import type { paths } from './generated/openapi'

export const apiClient = createClient<paths>({ baseUrl: '', fetch: apiFetch })

type GeneratedResult = {
  data?: unknown
  error?: unknown
  response: Response
}

export function unwrapGenerated({ data, error, response }: GeneratedResult): unknown {
  if (!response.ok || error !== undefined) {
    throw new ApiError(apiErrorMessage(error, `요청 실패 (${response.status})`), response.status, error)
  }
  if (data === undefined) throw new ApiError(`응답 본문이 없습니다. (${response.status})`, response.status, error)
  return data
}
