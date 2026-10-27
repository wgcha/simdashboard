import { RefreshCw, Square, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { fetchStorageConfig, saveStorageConfig } from '../../shared/api/storage'
import { folderEnvironmentApi, type FolderDiscoveryReview, type StorageRescanReinterpret, type StorageRescanState } from '../../shared/api/folderEnvironment'
import { rescanProgressDetail } from '../../shared/api/storageRescanModel'
import { DbBackupSettingsEntry } from './DbBackupPanel'
import { DriveSettingsEntry } from './DriveAdminPanel'
import { CaptureJobsPanel } from './CaptureJobsPanel'

const POLL_MS = 2000
const ENVIRONMENT_LABEL: Record<string, string> = { USAGE: '사용', DISTRIBUTION: '유통' }
/** Window event the depth-schema editor dispatches after it started a rescan itself (detail = rescan state). */
const STORAGE_RESCAN_STARTED_EVENT = 'simdash:storage-rescan-started'
/** Needs-review codes of folders at the request level: an administrator may exclude them (issue #55). */
const NOT_EXCLUDABLE = new Set([
  'ADMIN_EXCLUDED', 'FOLDER_NAME_WHITESPACE_DUPLICATE', 'EXCLUSION_REMOVED', 'AUTO_DISCOVERY_DISABLED', 'SPDM_ROOT_UNSET',
  'DISCOVERY_FAILED', 'DISCOVERY_TIME_BUDGET', 'LIST_LIMIT', 'PATH_UNAVAILABLE', 'DRIVE_READ_LIMIT', 'DRIVE_LIST_FAILED',
  'PROJECT_NAME_EXISTS', 'PROJECT_AMBIGUOUS', 'PROJECT_RENAME_AMBIGUOUS', 'PROJECT_RENAME_UNSUPPORTED', 'DEPTH_SCHEMA_MISSING',
])

function isExcludable(item: FolderDiscoveryReview): boolean {
  return Boolean(item.relative_path) && !NOT_EXCLUDABLE.has(item.code ?? '')
}

function progressLabel(state: StorageRescanState | null): string {
  if (!state || state.status !== 'RUNNING') return '저장 폴더 확인 중'
  if (state.phase === 'SYNC') return `저장 폴더 확인 중 · 의뢰 ${state.progress.done}/${state.progress.total}`
  if (state.phase === 'REINTERPRET') return `저장 폴더 확인 중 · 새 깊이 스키마 적용 ${state.progress.done}/${state.progress.total}`
  if (state.phase === 'CAPTURE') return `저장 폴더 확인 중 · 결과 받는 중 ${state.progress.done}/${state.progress.total}`
  return state.rounds > 0 ? `저장 폴더 확인 중 · 새 폴더 ${state.created_requests.length}개` : '저장 폴더 확인 중 · 새 폴더 찾기'
}

function summary(state: StorageRescanState): string {
  const excluded = state.needs_review.filter((item) => item.code === 'ADMIN_EXCLUDED').length
  const parts = [
    `새 프로젝트 ${state.created_projects.length}`, `새 의뢰 ${state.created_requests.length}`,
    `확인한 의뢰 ${state.progress.done}`, `갱신 ${state.refreshed}`,
    `확인 필요 ${state.needs_review.length - excluded}`, `실패 ${state.failed.length}`,
  ]
  if (state.reinterpreted.length || state.reinterpret_blocked.length) parts.push(`새 스키마 적용 ${state.reinterpreted.length}`, `적용 못함 ${state.reinterpret_blocked.length}`)
  if (excluded) parts.push(`제외 ${excluded}`)
  if (state.sync_deferred) parts.push(`시간 제한으로 미확인 ${state.sync_deferred}`)
  if (state.captures?.pending) parts.push(state.captures_note || `결과 받기 ${state.captures.pending}건은 백그라운드에서 계속 받습니다`)
  if (state.status === 'CANCELLED') return `저장 폴더 확인을 중지했습니다. 그때까지의 결과만 반영했습니다 (${parts.join(' · ')}).`
  return `저장 폴더를 다시 확인했습니다 (${parts.join(' · ')})${state.partial ? '. 남은 폴더가 있어 한 번 더 실행하세요' : ''}.`
}

function hasReport(state: StorageRescanState): boolean {
  return state.status === 'FAILED' || state.status === 'CANCELLED' || (state.sync_deferred ?? 0) > 0
    || state.needs_review.length > 0 || state.failed.length > 0
    || state.reinterpreted.length > 0 || state.reinterpret_blocked.length > 0
}

function ReinterpretItem({ item, blocked }: { item: StorageRescanReinterpret; blocked: boolean }) {
  return <li>
    <strong>{item.project_name ? `${item.project_name} / ` : ''}{item.request_name || item.request_id} ({ENVIRONMENT_LABEL[item.environment] ?? item.environment})</strong>
    <span>{blocked ? item.message : '현재 깊이 스키마로 다시 등록했습니다.'}</span>
    {blocked && item.deviations.map((deviation) => <small key={`${deviation.code}|${deviation.relative_path}`}>{deviation.code}{deviation.relative_path ? ` · ${deviation.relative_path}` : ''}</small>)}
    {blocked && item.deviation_count > item.deviations.length && <small>그 밖에 {item.deviation_count - item.deviations.length}건</small>}
    {blocked && item.deviations.length === 0 && item.code && <small>{item.code}</small>}
  </li>
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
  const [cancelling, setCancelling] = useState(false)
  const polling = useRef(false)
  const mounted = useRef(true)
  const callbacks = useRef({ onChanged, onMessage, onError })
  callbacks.current = { onChanged, onMessage, onError }
  const [excluding, setExcluding] = useState(false)
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
    try { await callbacks.current.onChanged() } catch { /* the summary still matters */ }
    if (!mounted.current) return
    if (state.status === 'FAILED') callbacks.current.onError(state.error || '저장 폴더를 확인하지 못했습니다.')
    else callbacks.current.onMessage(summary(state))
    setReport(hasReport(state) ? state : null)
  }
  /** [제외] / [모두 제외] / [다시 포함]: the server moves the folders in its rescan state; reload that state. */
  const changeExclusion = async (paths: string[], restore: boolean) => {
    if (excluding || paths.length === 0) return
    setExcluding(true)
    try {
      const result = restore ? await folderEnvironmentApi.restoreFolders(paths) : await folderEnvironmentApi.excludeFolders(paths)
      const done = (restore ? result.restored : result.excluded) ?? []
      const next = await folderEnvironmentApi.rescanStatus()
      if (!mounted.current) return
      setReport(next)
      const skipped = result.skipped.length ? ` (${result.skipped.length}개는 처리하지 않음: ${result.skipped[0].message})` : ''
      callbacks.current.onMessage(`${done.length}개 폴더를 ${restore ? '다시 포함' : '제외'}했습니다${skipped}.`)
      void callbacks.current.onChanged().catch(() => undefined)
    } catch (reason) {
      if (mounted.current) callbacks.current.onError(reason instanceof Error ? reason.message : '폴더 제외 설정을 저장하지 못했습니다.')
    } finally { if (mounted.current) setExcluding(false) }
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
      if (mounted.current && (state.status === 'DONE' || state.status === 'FAILED' || state.status === 'CANCELLED')) await finish(state)
    } catch (reason) {
      if (mounted.current) callbacks.current.onError(reason instanceof Error ? reason.message : '저장 폴더 확인 상태를 불러오지 못했습니다.')
    } finally {
      polling.current = false
      if (mounted.current) { setBusy(false); setRescan(null); setCancelling(false) }
    }
  }
  /** [중지]: the server stops the rescan at its next check point; the polling shows the CANCELLED result. */
  const cancel = async () => {
    if (cancelling) return
    setCancelling(true)
    try {
      const state = await folderEnvironmentApi.rescanCancel()
      if (mounted.current && state.status === 'RUNNING') setRescan(state)
    } catch (reason) {
      if (mounted.current) { setCancelling(false); callbacks.current.onError(reason instanceof Error ? reason.message : '저장 폴더 확인을 중지하지 못했습니다.') }
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
    // The depth-schema editor starts a rescan after a save: attach to it so the top bar shows the progress.
    const attach = (event: Event) => {
      const state = (event as CustomEvent<StorageRescanState | undefined>).detail
      if (state && state.status === 'RUNNING' && mounted.current) { setReport(null); void poll(state) }
    }
    window.addEventListener(STORAGE_RESCAN_STARTED_EVENT, attach)
    return () => { mounted.current = false; window.removeEventListener(STORAGE_RESCAN_STARTED_EVENT, attach) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const pending = report ? report.needs_review.filter((item) => item.code !== 'ADMIN_EXCLUDED') : []
  const excluded = report ? report.needs_review.filter((item) => item.code === 'ADMIN_EXCLUDED') : []
  const excludable = pending.filter(isExcludable)
  return <div className="storage-global-actions">
    <details className="storage-global-settings" onToggle={(event) => { if (event.currentTarget.open) void loadRoot() }}><summary aria-label="고정 결과 원본 폴더 설정">저장소 설정</summary><div><label><span>고정 결과 원본 폴더</span><input value={rootPath} onChange={(event) => setRootPath(event.target.value)} placeholder="서버 결과 원본 root 경로" disabled={rootBusy || rootLocked} /></label><button type="button" onClick={() => void saveRoot()} disabled={rootBusy || rootLocked || !rootPath.trim()}>저장</button>{rootLocked ? <small role="status">환경 설정이 사용하는 경로라 여기서 바꿀 수 없습니다.</small> : rootMessage ? <small role="status">{rootMessage}</small> : null}<DriveSettingsEntry /><DbBackupSettingsEntry /></div></details>
    <button type="button" className="ghost-button" data-testid="storage-global-refresh" disabled={busy} title="현재 깊이 스키마로 저장소 전체를 다시 확인해 새 프로젝트·의뢰를 등록하고 등록된 의뢰를 갱신합니다." onClick={() => void run()}><RefreshCw className={busy ? 'storage-spin' : undefined} /> {busy ? progressLabel(rescan) : '저장 폴더 새로고침'}</button>
    {busy && rescan?.status === 'RUNNING' && <>
      <small className="storage-rescan-progress" role="status" data-testid="storage-rescan-progress" title={rescanProgressDetail(rescan)}>{rescanProgressDetail(rescan)}</small>
      <button type="button" className="ghost-button" data-testid="storage-rescan-cancel" disabled={cancelling || rescan.cancel_requested} title="진행 중인 저장 폴더 확인을 다음 확인 지점에서 멈춥니다. 그때까지 확인한 결과는 남습니다." onClick={() => void cancel()}><Square /> {cancelling || rescan.cancel_requested ? '중지 중' : '중지'}</button>
    </>}
    <CaptureJobsPanel refreshKey={rescan?.status === 'RUNNING' ? 'running' : report?.finished_at ?? 'idle'} />
    {report && <section className="storage-rescan-report" aria-label="저장 폴더 확인 결과" data-testid="storage-rescan-report">
      <header><strong>저장 폴더 확인 결과</strong><button type="button" className="storage-rescan-close" aria-label="닫기" onClick={() => setReport(null)}><X /></button></header>
      {report.status === 'FAILED' && <p className="storage-rescan-error" role="alert">{report.error || '저장 폴더를 확인하지 못했습니다.'}</p>}
      {report.status === 'CANCELLED' && <p className="storage-rescan-error" role="status">관리자가 중지했습니다. 아래는 중지 전까지 확인한 결과입니다.</p>}
      {(report.sync_deferred ?? 0) > 0 && <p className="storage-rescan-error" role="status">시간 제한으로 {report.sync_deferred}개 의뢰는 확인하지 못했습니다. 한 번 더 실행하세요.</p>}
      {report.reinterpret_blocked.length > 0 && <><h3>새 깊이 스키마를 적용하지 못한 의뢰 {report.reinterpret_blocked.length}개</h3><ul data-testid="storage-rescan-blocked">{report.reinterpret_blocked.map((item) => <ReinterpretItem key={`${item.request_id}|${item.environment}`} item={item} blocked />)}</ul></>}
      {report.reinterpreted.length > 0 && <details data-testid="storage-rescan-reinterpreted"><summary>새 깊이 스키마를 적용한 의뢰 {report.reinterpreted.length}개</summary><ul>{report.reinterpreted.map((item) => <ReinterpretItem key={`${item.request_id}|${item.environment}`} item={item} blocked={false} />)}</ul></details>}
      {report.reinterpret_deferred > 0 && <p className="storage-rescan-error" role="status">시간 제한으로 {report.reinterpret_deferred}개 의뢰는 새 스키마를 적용하지 못했습니다. 한 번 더 실행하세요.</p>}
      {pending.length > 0 && <>
        <h3>등록하지 않은 폴더 {pending.length}개</h3>
        {excludable.length > 0 && <div className="storage-rescan-actions"><button type="button" className="ghost-button" data-testid="storage-rescan-exclude-all" disabled={excluding} title="확인할 필요가 없는 폴더를 제외합니다. 이후 새로고침에서는 '제외한 폴더'에 모아 보여줍니다." onClick={() => { if (window.confirm(`${excludable.length}개 폴더를 모두 제외할까요? 제외한 폴더는 자동 등록하지 않으며, 아래 '제외한 폴더'에서 다시 포함할 수 있습니다.`)) void changeExclusion(excludable.map((item) => item.relative_path), false) }}>모두 제외 ({excludable.length})</button></div>}
        <ul>{pending.map((item) => <li key={`${item.relative_path}|${item.code ?? ''}`}><code>{item.relative_path || '(저장소 루트)'}</code><span>{item.reason}</span>{item.code && <small>{item.code}{item.deviation_path ? ` · ${item.deviation_path}` : ''}</small>}{isExcludable(item) && <button type="button" className="ghost-button storage-rescan-item-action" disabled={excluding} onClick={() => void changeExclusion([item.relative_path], false)}>제외</button>}</li>)}</ul>
      </>}
      {excluded.length > 0 && <details data-testid="storage-rescan-excluded"><summary>제외한 폴더 {excluded.length}개</summary><ul>{excluded.map((item) => <li key={item.relative_path}><code>{item.relative_path}</code><span>{item.reason}</span>{item.restorable && <button type="button" className="ghost-button storage-rescan-item-action" disabled={excluding} onClick={() => void changeExclusion([item.relative_path], true)}>다시 포함</button>}</li>)}</ul></details>}
      {report.failed.length > 0 && <><h3>확인하지 못한 의뢰 {report.failed.length}개</h3><ul>{report.failed.map((item) => <li key={`${item.request_id}|${item.environment}`}><strong>{item.project_name ? `${item.project_name} / ` : ''}{item.request_name || item.request_id} ({ENVIRONMENT_LABEL[item.environment] ?? item.environment})</strong><span>{item.message || '확인하지 못했습니다.'}</span><small>{item.code}</small></li>)}</ul></>}
    </section>}
  </div>
}
