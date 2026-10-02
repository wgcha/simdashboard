import { useEffect, useState } from 'react'
import { RotateCw } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { SimulationDashboard } from './SimulationDashboard'
import { MaterialsDashboard } from '../materials/MaterialsDashboard'
import { FOLDER_SCHEMA_FILE_BUSY, useFolderAutoSync } from '../../shared/hooks/useFolderAutoSync'
import './RequestCaseResultsWorkspace.css'

type Props = { projectId: string; requestId: string; canManageFolders?: boolean; canRefreshSchema?: boolean; activeTab?: 'case_results' | 'materials'; refreshToken?: number }

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
export function RequestCaseResultsWorkspace({ projectId, requestId, canManageFolders = false, canRefreshSchema = false, activeTab = 'case_results', refreshToken = 0 }: Props) {
  const [searchParams] = useSearchParams()
  const environment = activeTab === 'materials' ? 'DISTRIBUTION' : searchParams.get('result_environment') === 'DISTRIBUTION' ? 'DISTRIBUTION' : 'USAGE'
  const sync = useFolderAutoSync({ projectId, requestId, environment })
  const [now, setNow] = useState(() => Date.now())
  const [notice, setNotice] = useState('')
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
  const dashboardToken = refreshToken + sync.revision
  const syncStatus = <div className="request-case-results__sync" role="group" aria-label="폴더 자동 확인">
    {notice ? <span className="request-case-results__notice" role="status">{notice}</span> : null}
    <span className={`request-case-results__status request-case-results__status--${label.tone}`} title={label.title} aria-live="polite">{label.text}</span>
    <button type="button" className="request-case-results__check" aria-label="지금 확인" title="지금 확인" disabled={sync.busy || !projectId || !requestId} onClick={sync.checkNow}><RotateCw aria-hidden="true" /></button>
  </div>
  // One tabbed page for both routes: the 소재·물성 tab renders the materials
  // dashboard inside the Case results page and shares its path row.
  return <div className="request-case-results">
    <SimulationDashboard projectId={projectId} requestId={requestId} canManageFolders={canManageFolders} canRefreshSchema={canRefreshSchema} refreshToken={dashboardToken} activeTab={activeTab} headerExtra={syncStatus}
      renderMaterials={(pathTarget) => <MaterialsDashboard projectId={projectId} requestId={requestId} refreshToken={dashboardToken} embedded pathTarget={pathTarget} />} />
  </div>
}
