import { RefreshCw, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { fetchStorageConfig, saveStorageConfig } from '../../shared/api/storage'
import { folderEnvironmentApi, type StorageRescanState } from '../../shared/api/folderEnvironment'
import { DriveSettingsEntry } from './DriveAdminPanel'

const POLL_MS = 2000
const ENVIRONMENT_LABEL: Record<string, string> = { USAGE: '사용', DISTRIBUTION: '유통' }

function progressLabel(state: StorageRescanState | null): string {
  if (!state || state.status !== 'RUNNING') return '저장 폴더 확인 중'
  if (state.phase === 'SYNC') return `저장 폴더 확인 중 · 의뢰 ${state.progress.done}/${state.progress.total}`
  return state.rounds > 0 ? `저장 폴더 확인 중 · 새 폴더 ${state.created_requests.length}개` : '저장 폴더 확인 중 · 새 폴더 찾기'
}

function summary(state: StorageRescanState): string {
  const parts = [
    `새 프로젝트 ${state.created_projects.length}`, `새 의뢰 ${state.created_requests.length}`,
    `확인한 의뢰 ${state.progress.done}`, `갱신 ${state.refreshed}`,
    `확인 필요 ${state.needs_review.length}`, `실패 ${state.failed.length}`,
  ]
  return `저장 폴더를 다시 확인했습니다 (${parts.join(' · ')})${state.partial ? '. 남은 폴더가 있어 한 번 더 실행하세요' : ''}.`
}

/**
 * [저장 폴더 새로고침] (global administrators): full rescan of the storage with the current depth schema
 * (2026-10-17). Registers new project/request folders, checks every registered request and lists why a
 * folder was not registered. It only reads the storage, so it also runs in SCX drive mode.
 */
export function StorageRefreshControl({ onChanged, onMessage, onError }: { onChanged: () => Promise<void>; onMessage: (message: string) => void; onError: (message: string) => void }) {
  const [rescan, setRescan] = useState<StorageRescanState | null>(null)
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<StorageRescanState | null>(null)
  const polling = useRef(false)
  const mounted = useRef(true)
  const [rootPath, setRootPath] = useState('')
  const [rootMessage, setRootMessage] = useState('')
  const [rootLocked, setRootLocked] = useState(false)
  const [rootBusy, setRootBusy] = useState(false)
  const loadRoot = async () => {
    if (rootPath) return
    try { const state = await fetchStorageConfig(); setRootPath(state.config?.root_path || ''); setRootLocked(Boolean(state.config?.locked)) } catch (reason) { setRootMessage(reason instanceof Error ? reason.message : '저장소 설정을 불러오지 못했습니다.') }
  }
  const saveRoot = async () => {
    if (!rootPath.trim() || rootBusy || rootLocked) return
    setRootBusy(true); setRootMessage('')
    try { const state = await saveStorageConfig(rootPath.trim()); setRootPath(state.config?.root_path || rootPath.trim()); setRootMessage('저장소 경로를 저장했습니다.') }
    catch (reason) { setRootMessage(reason instanceof Error ? reason.message : '저장소 경로를 저장하지 못했습니다.') }
    finally { setRootBusy(false) }
  }
  const finish = async (state: StorageRescanState) => {
    try { await onChanged() } catch { /* the summary still matters */ }
    if (!mounted.current) return
    if (state.status === 'FAILED') onError(state.error || '저장 폴더를 확인하지 못했습니다.')
    else onMessage(summary(state))
    setReport(state.needs_review.length || state.failed.length || state.status === 'FAILED' ? state : null)
  }
  const poll = async (initial: StorageRescanState) => {
    if (polling.current) return
    polling.current = true
    setBusy(true)
    let state = initial
    try {
      while (state.status === 'RUNNING' && mounted.current) {
        setRescan(state)
        await new Promise((resolve) => window.setTimeout(resolve, POLL_MS))
        if (!mounted.current) return
        state = await folderEnvironmentApi.rescanStatus()
      }
      if (mounted.current && (state.status === 'DONE' || state.status === 'FAILED')) await finish(state)
    } catch (reason) {
      if (mounted.current) onError(reason instanceof Error ? reason.message : '저장 폴더 확인 상태를 불러오지 못했습니다.')
    } finally {
      polling.current = false
      if (mounted.current) { setBusy(false); setRescan(null) }
    }
  }
  const run = async () => {
    if (busy) return
    setBusy(true); setReport(null)
    try {
      const state = await folderEnvironmentApi.rescanStart()
      if (state.started === false && mounted.current) onMessage('이미 저장 폴더를 확인하고 있습니다. 끝나면 결과를 표시합니다.')
      await poll(state)
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : '저장 폴더를 다시 확인하지 못했습니다.')
      setBusy(false)
    }
  }
  useEffect(() => {
    mounted.current = true
    // A rescan started earlier (another tab, a reload) is followed until it ends.
    folderEnvironmentApi.rescanStatus().then((state) => { if (mounted.current && state.status === 'RUNNING') void poll(state) }).catch(() => undefined)
    return () => { mounted.current = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return <div className="storage-global-actions">
    <details className="storage-global-settings" onToggle={(event) => { if (event.currentTarget.open) void loadRoot() }}><summary aria-label="고정 결과 원본 폴더 설정">저장소 설정</summary><div><label><span>고정 결과 원본 폴더</span><input value={rootPath} onChange={(event) => setRootPath(event.target.value)} placeholder="서버 결과 원본 root 경로" disabled={rootBusy || rootLocked} /></label><button type="button" onClick={() => void saveRoot()} disabled={rootBusy || rootLocked || !rootPath.trim()}>저장</button>{rootLocked ? <small role="status">환경 설정이 사용하는 경로라 여기서 바꿀 수 없습니다.</small> : rootMessage ? <small role="status">{rootMessage}</small> : null}<DriveSettingsEntry /></div></details>
    <button type="button" className="ghost-button" data-testid="storage-global-refresh" disabled={busy} title="현재 깊이 스키마로 저장소 전체를 다시 확인해 새 프로젝트·의뢰를 등록하고 등록된 의뢰를 갱신합니다." onClick={() => void run()}><RefreshCw className={busy ? 'storage-spin' : undefined} /> {busy ? progressLabel(rescan) : '저장 폴더 새로고침'}</button>
    {report && <section className="storage-rescan-report" aria-label="저장 폴더 확인 결과" data-testid="storage-rescan-report">
      <header><strong>저장 폴더 확인 결과</strong><button type="button" className="storage-rescan-close" aria-label="닫기" onClick={() => setReport(null)}><X /></button></header>
      {report.status === 'FAILED' && <p className="storage-rescan-error" role="alert">{report.error || '저장 폴더를 확인하지 못했습니다.'}</p>}
      {report.needs_review.length > 0 && <><h3>등록하지 않은 폴더 {report.needs_review.length}개</h3><ul>{report.needs_review.map((item) => <li key={`${item.relative_path}|${item.code ?? ''}`}><code>{item.relative_path || '(저장소 루트)'}</code><span>{item.reason}</span>{item.code && <small>{item.code}{item.deviation_path ? ` · ${item.deviation_path}` : ''}</small>}</li>)}</ul></>}
      {report.failed.length > 0 && <><h3>확인하지 못한 의뢰 {report.failed.length}개</h3><ul>{report.failed.map((item) => <li key={`${item.request_id}|${item.environment}`}><strong>{item.project_name ? `${item.project_name} / ` : ''}{item.request_name || item.request_id} ({ENVIRONMENT_LABEL[item.environment] ?? item.environment})</strong><span>{item.message || '확인하지 못했습니다.'}</span><small>{item.code}</small></li>)}</ul></>}
    </section>}
  </div>
}
