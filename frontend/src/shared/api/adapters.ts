export function responseRecord(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError(`${label} 응답 형식이 올바르지 않습니다.`)
  return value as Record<string, unknown>
}

export function requireStringField(value: Record<string, unknown>, key: string, label: string) {
  if (typeof value[key] !== 'string') throw new TypeError(`${label}.${key} 응답 형식이 올바르지 않습니다.`)
}

export function responseRecordArray(value: unknown, label: string): Record<string, unknown>[] {
  if (!Array.isArray(value)) throw new TypeError(`${label} 응답 형식이 올바르지 않습니다.`)
  return value.map((item) => responseRecord(item, label))
}
