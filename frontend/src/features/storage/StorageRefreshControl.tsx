import { RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { fetchStorageConfig, refreshStorage, saveStorageConfig } from '../../shared/api/storage'

export function StorageRefreshControl({ onChanged, onMessage, onError }: { onChanged: () => Promise<void>; onMessage: (message: string) => void; onError: (message: string) => void }) {
  const [busy, setBusy] = useState(false)
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
  const run = async () => {
    if (busy) return
    setBusy(true)
    try {
      const result = await refreshStorage()
      await onChanged()
      const body = result && typeof result === 'object' ? result as Record<string, unknown> : {}
      const refreshed = Array.isArray(body.refreshed) ? body.refreshed.length : undefined
      const created = Array.isArray(body.created_bindings) ? body.created_bindings.length : undefined
      const outcomes = Array.isArray(body.refreshed) ? body.refreshed.flatMap((item) => item && typeof item === 'object' && Array.isArray((item as Record<string, unknown>).results) ? (item as Record<string, unknown>).results as unknown[] : []) : []
      const files = Array.isArray(body.refreshed) ? body.refreshed.flatMap((item) => item && typeof item === 'object' && Array.isArray((item as Record<string, unknown>).files) ? (item as Record<string, unknown>).files as unknown[] : []) : []
      const count = (items: unknown[], status: string) => items.filter((item) => item && typeof item === 'object' && (item as Record<string, unknown>).status === status).length
      const counts = refreshed == null ? '' : ` (${refreshed}개 폴더 확인${created ? `, ${created}개 연결` : ''}; 등록 ${count(outcomes, 'IMPORTED')} · 중복 ${count(outcomes, 'SKIPPED')} · 대기 ${count(files, 'PENDING')} · 실패 ${count(outcomes, 'FAILED')})`
      onMessage(`고정 결과 원본 폴더를 새로 확인했습니다${counts}.`)
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : '고정 결과 원본 폴더를 새로 확인하지 못했습니다.')
    } finally { setBusy(false) }
  }
  return <div className="storage-global-actions"><details className="storage-global-settings" onToggle={(event) => { if (event.currentTarget.open) void loadRoot() }}><summary aria-label="고정 결과 원본 폴더 설정">저장소 설정</summary><div><label><span>고정 결과 원본 폴더</span><input value={rootPath} onChange={(event) => setRootPath(event.target.value)} placeholder="서버 결과 원본 root 경로" disabled={rootBusy || rootLocked} /></label><button type="button" onClick={() => void saveRoot()} disabled={rootBusy || rootLocked || !rootPath.trim()}>저장</button>{rootLocked ? <small role="status">환경 설정이 사용하는 경로라 여기서 바꿀 수 없습니다.</small> : rootMessage ? <small role="status">{rootMessage}</small> : null}</div></details><button type="button" className="ghost-button" data-testid="storage-global-refresh" disabled={busy} onClick={() => void run()}><RefreshCw className={busy ? 'storage-spin' : undefined} /> {busy ? '저장 폴더 확인 중' : '저장 폴더 새로고침'}</button></div>
}
