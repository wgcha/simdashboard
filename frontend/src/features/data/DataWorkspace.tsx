import { ResultDropWorkspace, type ResultDropWorkspaceProps } from './ResultDropWorkspace'

// W8 (2026-10-06): 결과 등록 = folder guide + drag & drop into Working. Drafts made with the
// earlier review screen stay visible read-only (LegacyDraftHistory).
export type DataWorkspaceProps = ResultDropWorkspaceProps

export function DataWorkspace(props: DataWorkspaceProps) {
  return <ResultDropWorkspace {...props} />
}
