export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export function apiErrorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object' && 'detail' in detail) {
    return apiErrorMessage(detail.detail, fallback)
  }
  if (detail && typeof detail === 'object' && 'code' in detail && typeof detail.code === 'string') {
    return `${detail.code}: ${JSON.stringify(detail)}`
  }
  if (detail && typeof detail === 'object') {
    try {
      return JSON.stringify(detail)
    } catch { /* an unexpected non-JSON error falls back to the status text */ }
  }
  return fallback
}

export async function apiErrorFromResponse(response: Response): Promise<ApiError> {
  const body = await response.text()
  let detail: unknown = body
  try {
    const parsed = JSON.parse(body) as { detail?: unknown }
    detail = parsed.detail ?? parsed
  } catch { /* the response is plain text */ }
  return new ApiError(apiErrorMessage(detail, body || `요청 실패 (${response.status})`), response.status, detail)
}
