import { semanticMappingApi } from '../../shared/api/semanticMapping'

type ContextIdentity = { projectId: string; requestId: string; loadCaseId: string }

export async function refreshCurrentLoadCaseResults({
  context,
  activeView,
  workspacePage,
  canImport,
  beginIntent,
  isCurrentIntent,
  currentContext,
  selectRun,
  openReview,
  setBusy,
  setNotice,
  setError,
}: {
  context: ContextIdentity
  activeView: string
  workspacePage: string
  canImport: boolean
  beginIntent: () => number
  isCurrentIntent: (intent: number) => boolean
  currentContext: () => ContextIdentity
  selectRun: (runId: string) => Promise<boolean>
  openReview: (projectId: string, requestId: string, loadCaseId: string, runId: string) => Promise<void>
  setBusy: (busy: boolean) => void
  setNotice: (notice: string) => void
  setError: (error: string) => void
}) {
  if (!context.loadCaseId || !canImport) return
  const intent = beginIntent()
  const contextAtStart = { ...context }
  const stillCurrent = () => {
    const current = currentContext()
    return isCurrentIntent(intent) && current.projectId === contextAtStart.projectId && current.requestId === contextAtStart.requestId && current.loadCaseId === contextAtStart.loadCaseId
  }
  setBusy(true)
  try {
    const refreshed = await semanticMappingApi.refreshLoadCaseResults(contextAtStart.loadCaseId)
    if (!stillCurrent()) return
    if (!refreshed.display_run_id) {
      setNotice(refreshed.partial ? '일부 결과 파일 처리가 보류되었습니다. 저장된 결과를 확인하세요.' : '처리할 새 결과 파일이 없습니다.')
      return
    }
    const selected = await selectRun(refreshed.display_run_id)
    if (!selected) throw new Error('결과 파일은 처리되었지만 새 Run을 현재 결과 문맥에 선택하지 못했습니다.')
    if (!stillCurrent()) return
    if (activeView === 'workflow' || workspacePage !== 'dashboard') {
      await openReview(contextAtStart.projectId, contextAtStart.requestId, contextAtStart.loadCaseId, refreshed.display_run_id)
      return
    }
    if (stillCurrent()) setNotice(`결과 파일을 확인하고 Run ${refreshed.display_run_id}을(를) 선택했습니다.`)
  } catch (reason) {
    if (stillCurrent()) setError(reason instanceof Error ? reason.message : '결과 파일을 확인하지 못했습니다.')
  } finally {
    setBusy(false)
  }
}
