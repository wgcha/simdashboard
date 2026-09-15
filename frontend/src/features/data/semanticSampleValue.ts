/** Literal JSON Pointer for reader v2; v1 table sources keep their exact header. */
export function semanticSampleValue(row: Record<string, unknown>, source: string, readerVersion: number | undefined): unknown {
  if (readerVersion !== 2 || !source.startsWith('#/')) return row[source]
  let value: unknown = row
  for (const part of source.slice(2).split('/')) {
    const key = part.replaceAll('~1', '/').replaceAll('~0', '~')
    if (!value || typeof value !== 'object' || !Object.hasOwn(value, key)) return undefined
    value = (value as Record<string, unknown>)[key]
  }
  return value
}

/** A present sample preserves zero/false and ignores absent or blank text. */
export function hasSampleValue(value: unknown): boolean {
  if (value == null) return false
  if (typeof value === 'string') return value.trim().length > 0
  if (Array.isArray(value)) return value.some(hasSampleValue)
  if (typeof value === 'object') return Object.values(value).some(hasSampleValue)
  return true
}

export function isNumericVectorSample(value: unknown): value is unknown[] {
  return Array.isArray(value) && value.length > 0 && value.every((entry) => entry == null || typeof entry === 'string' && !hasSampleValue(entry) || typeof entry === 'number' && Number.isFinite(entry) || typeof entry === 'string' && Number.isFinite(Number(entry)))
}
export function suggestedVectorComponents(value: unknown[], label: string): string[] {
  return value.length === 3 && /Coord/i.test(label) ? ['X', 'Y', 'Z'] : value.map((_, index) => `성분 ${index + 1}`)
}

export function formatSemanticSample(value: unknown, components?: string[]): string {
  if (Array.isArray(value)) return value.map((entry, index) => `${components?.[index] ?? `성분 ${index + 1}`}: ${hasSampleValue(entry) ? String(entry) : '값 없음'}`).join(' · ')
  return hasSampleValue(value) ? String(value) : '값 없음'
}
