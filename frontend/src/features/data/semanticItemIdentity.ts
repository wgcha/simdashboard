import type { SemanticItemDefinition, VersionedDefinition } from '../../shared/api/semanticMapping'

type Item = VersionedDefinition<SemanticItemDefinition>
export function compatibleItemMeaning(left: SemanticItemDefinition, right: SemanticItemDefinition): boolean {
  return left.key === right.key && left.kind === right.kind && left.data_type === right.data_type && left.unit === right.unit && JSON.stringify(left.dimensions) === JSON.stringify(right.dimensions) && JSON.stringify(left.components ?? []) === JSON.stringify(right.components ?? [])
}
/** Preserve existing meanings; choose a stable available key for automatic definitions. */
export function resolveAutomaticItem(definition: SemanticItemDefinition, items: Item[]): { definition: SemanticItemDefinition; item?: Item } {
  const base = definition.key.slice(0, 120)
  for (let index = 0; ; index += 1) {
    const suffix = index === 0 ? '' : `_${definition.kind}${index === 1 ? '' : `_${index}`}`
    const candidate = { ...definition, key: `${base.slice(0, 120 - suffix.length)}${suffix}` }
    const owner = items.find((item) => item.definition.key === candidate.key)
    if (!owner) return { definition: candidate }
    if (owner.lifecycle_status !== 'ARCHIVED' && compatibleItemMeaning(owner.definition, candidate)) return { definition: candidate, item: owner }
  }
}
