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
