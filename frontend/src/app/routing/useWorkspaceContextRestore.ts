import { useEffect } from 'react'
import { api } from '../../api'
import { visiblePages, type ActiveView } from '../../features/analysis/pageSelection'
import type { AnalysisRequest, LoadCase, Overview, Project } from '../../types'
import type { WorkspaceContextQuery } from './useWorkspaceNavigation'

type RestoreTarget = { projectId?: string; requests?: AnalysisRequest[]; requestId?: string; loadCases?: LoadCase[] }

export function useWorkspaceContextRestore({ enabled, context, projects, beginIntent, isCurrentIntent, onInvalid, onMonitoringContext, onAnalysisContext, onVirtualResultLayout, onSettled }: {
  enabled: boolean; context: WorkspaceContextQuery; projects: Project[]; beginIntent: () => number; isCurrentIntent: (intent: number) => boolean
  onInvalid: (message: string, target?: RestoreTarget) => void; onMonitoringContext: (projectId: string, requestId: string, loadCaseId?: string) => Promise<unknown>
  onAnalysisContext: (projectId: string, requestId: string, view: ActiveView, loadCaseId?: string, pageId?: string, runId?: string) => Promise<unknown>; onVirtualResultLayout: () => void; onSettled: () => void
}) {
  useEffect(() => {
    if (!enabled || !context.projectId) return
    const preferredView: ActiveView = context.view === 'open_cell' || context.view === 'chassis' || context.view === 'custom' || context.view === 'compare' || context.view === 'workflow' ? context.view : 'workflow'
    void (async () => {
      const intent = beginIntent()
      const reject = (message: string, target?: RestoreTarget) => { if (isCurrentIntent(intent)) onInvalid(message, target) }
      try {
        const projectId = context.projectId!
        if (!projects.some((project) => project.id === projectId)) return reject('요청한 의뢰 문맥을 열 수 없습니다. 의뢰 개요로 이동했습니다.')
        const requests = await api.requests(projectId)
        const request = requests.find((item) => item.id === context.requestId)
        if (!request) return reject('요청한 의뢰 문맥을 열 수 없습니다. 의뢰 개요로 이동했습니다.', { projectId, requests })
        const loadCases = await api.loadCases(request.id)
        const loadCase = context.loadCaseId ? loadCases.find((item) => item.id === context.loadCaseId) : undefined
        if (context.loadCaseId && !loadCase) return reject('요청한 의뢰 문맥을 열 수 없습니다. 의뢰 개요로 이동했습니다.', { projectId, requests, requestId: request.id, loadCases })
        if ((context.runId || context.pageId) && !loadCase) return reject('요청한 결과 문맥을 열 수 없습니다. 의뢰 개요로 이동했습니다.', { projectId, requests, requestId: request.id, loadCases })
        const virtualResultLayout = preferredView === 'custom' && !context.pageId
        if (virtualResultLayout && context.runId) return reject('요청한 결과 문맥을 열 수 없습니다. 의뢰 개요로 이동했습니다.', { projectId, requests, requestId: request.id, loadCases })
        let overview: Overview | null = null
        if (loadCase && context.runId) {
          const runs = await api.analysisRuns(loadCase.id)
          if (!runs.some((run) => run.id === context.runId)) return reject('요청한 Run을 열 수 없습니다. 의뢰 개요로 이동했습니다.', { projectId, requests, requestId: request.id, loadCases })
          overview = await api.overview(loadCase.id, context.runId)
        }
        if (loadCase && context.pageId) {
          const [currentOverview, pages] = await Promise.all([overview ? Promise.resolve(overview) : api.overview(loadCase.id), api.dashboardPages(loadCase.id)])
          if (!visiblePages(pages, currentOverview).some((page) => page.id === context.pageId)) return reject('요청한 분석 페이지를 열 수 없습니다. 의뢰 개요로 이동했습니다.', { projectId, requests, requestId: request.id, loadCases })
        }
        if (!isCurrentIntent(intent)) return
        if (preferredView === 'workflow') await onMonitoringContext(projectId, request.id, context.loadCaseId)
        else {
          await onAnalysisContext(projectId, request.id, preferredView, context.loadCaseId, context.pageId, context.runId)
          if (virtualResultLayout && isCurrentIntent(intent)) onVirtualResultLayout()
        }
      } catch (reason) { if (isCurrentIntent(intent)) onInvalid(reason instanceof Error ? reason.message : '의뢰 문맥을 복원하지 못했습니다.') }
      finally { if (isCurrentIntent(intent)) onSettled() }
    })()
  }, [context.loadCaseId, context.pageId, context.projectId, context.requestId, context.runId, context.view, enabled])
}
