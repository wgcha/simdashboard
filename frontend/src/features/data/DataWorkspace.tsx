import { ResultRegistrationWorkspace, type ResultRegistrationWorkspaceProps } from './ResultRegistrationWorkspace'

export type DataWorkspaceProps = ResultRegistrationWorkspaceProps

export function DataWorkspace(props: DataWorkspaceProps) {
  return <ResultRegistrationWorkspace {...props} />
}