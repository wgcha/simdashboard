import { useEffect, useState } from 'react'
import { RotateCw } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { SimulationDashboard } from './SimulationDashboard'
import { MaterialsDashboard } from '../materials/MaterialsDashboard'
import { FOLDER_SCHEMA_FILE_BUSY, useFolderAutoSync } from '../../shared/hooks/useFolderAutoSync'
import { folderEnvironmentApi, type DepthDeviationItem } from '../../shared/api/folderEnvironment'
import { deviationLabel } from '../../shared/api/depthSchemaModel'
import { keywordEnvironments } from '../../shared/api/simulationDashboard'
import { useRequestResultEnvironments } from './useRequestResultEnvironments'
import './RequestCaseResultsWorkspace.css'

type Props = { projectId: string; requestId: string; /** Request (folder) name; its 사용/유통 keyword picks the sync environment before any Case exists. */ requestName?: string; canManageFolders?: boolean; canRefreshSchema?: boolean; canReinterpret?: boolean; activeTab?: 'case_results' | 'materials'; refreshToken?: number }

function syncLabel(sync: ReturnType<typeof useFolderAutoSync>, now: number): { text: string; title?: string; tone: 'ok' | 'busy' | 'wait' | 'warn' } {
  if (sync.busy) return { text: '확인 중…', tone: 'busy' }
  if (sync.code === FOLDER_SCHEMA_FILE_BUSY) return { text: '파일 복사 중 · 잠시 후 다시 확인', title: sync.message ?? undefined, tone: 'wait' }
  if (sync.status === 'CONFLICT' || sync.status === 'FAILED' || sync.error) return { text: '폴더 확인 필요', title: sync.error || sync.message || undefined, tone: 'warn' }
  if (sync.lastCheckedAt === null) return { text: '확인 중…', tone: 'busy' }
  const minutes = Math.floor(Math.max(0, now - sync.lastCheckedAt) / 60_000)
  return { text: minutes < 1 ? '방금 확인' : `최신 · ${minutes}분 전`, tone: 'ok' }
}

function changeNotice(diff: { added: number; removed: number; changed: number } | undefined) {
  const parts = [diff?.added ? `Scene +${diff.added}` : '', diff?.changed ? `결과 갱신 ${diff.changed}` : '', diff?.removed ? `Scene −${diff.removed}` : ''].filter(Boolean)
  return `새 결과 반영${parts.length ? ` · ${parts.join(' · ')}` : ''}`
}

/** Request-scoped SPDM review. It intentionally has no legacy Load Case, Run, or layout dependency. */
export function RequestCaseResultsWorkspace({ projectId, requestId, requestName = '', canManageFolders = false, canRefreshSchema = false, canReinterpret = false, activeTab = 'case_results', refreshToken = 0 }: Props) {
  const [searchParams] = useSearchParams()
  // E1–E4: the request's registered Cases decide the environment; the URL only chooses when both exist.
  const [registeredToken, setRegisteredToken] = useState(0)
  const environments = useRequestResultEnvironments(projectId, requestId, refreshToken + registeredToken)
  // E3: without Cases the folder sync still runs (it is what registers new Cases into this
  // request), for the request-name keyword environment, or both when the keyword is unclear.
  const pendingEnvironments = environments?.length === 0 ? keywordEnvironments(requestName) : null
  const environment = pendingEnvironments ? pendingEnvironments[0] : environments?.length === 1 ? environments[0] : activeTab === 'materials' ? 'DISTRIBUTION' : searchParams.get('result_environment') === 'DISTRIBUTION' ? 'DISTRIBUTION' : 'USAGE'
  const sync = useFolderAutoSync({ projectId, requestId, environment, enabled: environments !== null })
  const secondSync = useFolderAutoSync({ projectId, requestId, environment: 'DISTRIBUTION', enabled: pendingEnvironments?.length === 2 })
  const syncRevision = sync.revision + secondSync.revision
  useEffect(() => { if (syncRevision && pendingEnvironments) setRegisteredToken((value) => value + 1) }, [syncRevision]) // eslint-disable-line react-hooks/exhaustive-deps
  const [now, setNow] = useState(() => Date.now())
  const [notice, setNotice] = useState('')
  const [reinterpreting, setReinterpreting] = useState(false)
  const [reinterpretToken, setReinterpretToken] = useState(0)
  const [deviations, setDeviations] = useState<{ message: string; items: DepthDeviationItem[] } | null>(null)
  useEffect(() => { setDeviations(null) }, [projectId, requestId])
  // D9: saving a depth schema never changes registered requests; only this explicit action re-applies it.
  const reinterpret = async () => {
    if (!requestId || !window.confirm('현재 깊이 스키마로 이 의뢰의 폴더를 다시 해석해 등록합니다. 이탈이 있으면 등록하지 않습니다. 진행할까요?')) return
    setReinterpreting(true); setDeviations(null)
    try {
      const result = await folderEnvironmentApi.reinterpretRequest(requestId)
      const items = result.deviations ?? []
      if (items.length) setDeviations({ message: result.message || '이탈이 있어 재해석 결과를 등록하지 않았습니다.', items })
      else { setNotice(result.message || '현재 깊이 스키마로 재해석했습니다.'); setReinterpretToken((value) => value + 1) }
    } catch (error) {
      const detail = error && typeof error === 'object' && 'detail' in error ? (error as { detail: unknown }).detail : null
      const items = detail && typeof detail === 'object' && Array.isArray((detail as { deviations?: unknown }).deviations) ? (detail as { deviations: DepthDeviationItem[] }).deviations : []
      setDeviations({ message: error instanceof Error ? error.message : '재해석하지 못했습니다.', items })
    } finally { setReinterpreting(false) }
  }
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 15_000)
    return () => window.clearInterval(timer)
  }, [])
  useEffect(() => { setNow(Date.now()) }, [sync.lastCheckedAt])
  useEffect(() => {
    if (!sync.revision) return
    setNotice(changeNotice(sync.lastResult?.diff))
    const timer = window.setTimeout(() => setNotice(''), 8_000)
    return () => window.clearTimeout(timer)
    // The notice follows a new revision only, not later unchanged checks.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sync.revision])
  const label = syncLabel(sync, now)
  // App's operational token and the auto-sync revision both only grow, so their sum changes whenever either does.
  const dashboardToken = refreshToken + sync.revision + reinterpretToken
  const syncStatus = <div className="request-case-results__sync" role="group" aria-label="폴더 자동 확인">
    {notice ? <span className="request-case-results__notice" role="status">{notice}</span> : null}
    <span className={`request-case-results__status request-case-results__status--${label.tone}`} title={label.title} aria-live="polite">{label.text}</span>
    <button type="button" className="request-case-results__check" aria-label="지금 확인" title="지금 확인" disabled={sync.busy || !projectId || !requestId} onClick={sync.checkNow}><RotateCw aria-hidden="true" /></button>
    {canReinterpret ? <button type="button" className="request-case-results__reinterpret" title="현재 깊이 스키마로 이 의뢰를 다시 해석" disabled={reinterpreting || !requestId} onClick={() => void reinterpret()}>{reinterpreting ? '재해석 중…' : '재해석'}</button> : null}
  </div>
  // One tabbed page for both routes: the 소재·물성 tab renders the materials
  // dashboard inside the Case results page and shares its path row.
  return <div className="request-case-results">
    {deviations ? <div className="request-case-results__deviations" role="alert">
      <div><strong>{deviations.message}</strong><button type="button" aria-label="닫기" onClick={() => setDeviations(null)}>×</button></div>
      {deviations.items.length ? <ul>{deviations.items.map((item, index) => <li key={`${item.code}:${item.relative_path ?? index}`}><b>{deviationLabel(item.code)}</b>{item.relative_path ? <code>{item.relative_path}</code> : null}{item.message ? <span>{item.message}</span> : null}</li>)}</ul> : null}
    </div> : null}
    <SimulationDashboard projectId={projectId} requestId={requestId} canManageFolders={canManageFolders} canRefreshSchema={canRefreshSchema} refreshToken={dashboardToken} activeTab={activeTab} resultEnvironments={environments} headerExtra={environments ? syncStatus : null}
      renderMaterials={(pathTarget) => <MaterialsDashboard projectId={projectId} requestId={requestId} refreshToken={dashboardToken} embedded pathTarget={pathTarget} />} />
  </div>
}
