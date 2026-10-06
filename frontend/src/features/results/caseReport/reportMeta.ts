/** W7: optional report information (import-free so the builders and node self-tests can use it). */
export type CaseReportMeta = { author: string; developmentStage: string; reviewConditions: string; reviewConclusion: string }

/** Non-empty fields as label/value rows (HTML scope block, PPTX cover options). */
export function reportMetaRows(meta: CaseReportMeta | undefined): Array<{ label: string; value: string }> {
  if (!meta) return []
  return ([['작성자', meta.author], ['개발단계', meta.developmentStage], ['검토조건', meta.reviewConditions], ['결론', meta.reviewConclusion]] as const)
    .map(([label, value]) => ({ label, value: value.trim() }))
    .filter((row) => row.value)
}
