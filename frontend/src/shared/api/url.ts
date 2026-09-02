import type { paths } from './generated/openapi'

type PathValue = string | number
type QueryValue = string | number | boolean | null | undefined

export function apiUrl<Path extends keyof paths>(
  path: Path,
  pathParameters: Readonly<Record<string, PathValue>> = {},
  query: Readonly<Record<string, QueryValue>> = {},
): string {
  let resolved = path as string
  for (const [key, value] of Object.entries(pathParameters)) {
    resolved = resolved.replaceAll(`{${key}}`, encodeURIComponent(String(value)))
  }
  if (/\{[^}]+\}/.test(resolved)) throw new TypeError(`API path parameter가 누락되었습니다: ${resolved}`)
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null) search.set(key, String(value))
  }
  const suffix = search.toString()
  return suffix ? `${resolved}?${suffix}` : resolved
}
