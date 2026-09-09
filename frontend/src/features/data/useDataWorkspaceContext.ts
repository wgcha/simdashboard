import { useEffect } from 'react'

import { api } from '../../api'
import type { AnalysisRequest, LoadCase, Project } from '../../types'

type Params = {
  embedded: boolean
  initialLoadCaseId?: string
  initialProjectId: string
  initialRequestId?: string
  projectId: string
  projects: Project[]
  requestId: string
  setFormError: (message: string) => void
  setLoadCaseId: (value: string | ((current: string) => string)) => void
  setLoadCases: (items: LoadCase[]) => void
  setManagedProjects: (items: Project[]) => void
  setProjectId: (value: string | ((current: string) => string)) => void
  setRequestId: (value: string | ((current: string) => string)) => void
  setRequests: (items: AnalysisRequest[]) => void
  resetImportState: () => void
}

/** Keeps local form controls aligned to the request shell without emitting transient IDs upstream. */
export function useDataWorkspaceContext({ embedded, initialLoadCaseId, initialProjectId, initialRequestId, projectId, projects, requestId, setFormError, setLoadCaseId, setLoadCases, setManagedProjects, setProjectId, setRequestId, setRequests, resetImportState }: Params) {
  useEffect(() => {
    setManagedProjects(projects)
    if (embedded) { setProjectId(initialProjectId || ''); return }
    setProjectId((current) => current && projects.some((project) => project.id === current) ? current : initialProjectId || projects[0]?.id || '')
  }, [embedded, initialProjectId, projects, setManagedProjects, setProjectId])

  useEffect(() => {
    let active = true
    if (!projectId) { setRequests([]); setRequestId(''); return () => { active = false } }
    api.requests(projectId).then((items) => {
      if (!active) return
      setRequests(items)
      setRequestId((current) => embedded ? items.some((item) => item.id === initialRequestId) ? initialRequestId || '' : '' : items.some((item) => item.id === initialRequestId) ? initialRequestId || '' : items.some((item) => item.id === current) ? current : items[0]?.id || '')
    }).catch((reason) => { if (active) setFormError(reason instanceof Error ? reason.message : '의뢰 목록을 불러오지 못했습니다.') })
    return () => { active = false }
  }, [embedded, initialRequestId, projectId, setFormError, setRequestId, setRequests])

  useEffect(() => {
    let active = true
    if (!requestId) { setLoadCases([]); setLoadCaseId(''); return () => { active = false } }
    api.loadCases(requestId).then((items) => {
      if (!active) return
      setLoadCases(items)
      setLoadCaseId((current) => embedded ? items.some((item) => item.id === initialLoadCaseId) ? initialLoadCaseId || '' : '' : items.some((item) => item.id === initialLoadCaseId) ? initialLoadCaseId || '' : items.some((item) => item.id === current) ? current : items[0]?.id || '')
      resetImportState()
    }).catch((reason) => { if (active) setFormError(reason instanceof Error ? reason.message : '하중 경우 목록을 불러오지 못했습니다.') })
    return () => { active = false }
  }, [embedded, initialLoadCaseId, requestId, resetImportState, setFormError, setLoadCaseId, setLoadCases])
}
