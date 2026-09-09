import { AlertTriangle, CheckCircle2, Download, File, FolderOpen, Link2, LoaderCircle, RefreshCw, Save, Upload } from 'lucide-react'
import { useCallback, useEffect, useId, useRef, useState, type ChangeEvent } from 'react'
import {
  bindStorageFolder,
  fetchStorageConfig,
  fetchStorageFolders,
  fetchStorageState,
  refreshStorageFolder,
  saveStorageConfig,
  storageFileDownloadUrl,
  uploadOriginalFile,
  uploadStructuredResult,
  type StorageFile,
  type StorageRule,
  type StorageWorkspaceState,
} from '../../shared/api/storage'
import './StorageWorkspacePanel.css'
import { StorageResultDetails } from './StorageResultDetails'
import { useMemoryQuery } from '../../shared/cache/useMemoryQuery'

type UploadState = 'queued' | 'uploading' | 'stored' | 'failed'
type UploadRow = { key: string; file: File; state: UploadState; message?: string }

export type StorageWorkspacePanelProps = {
  loadCaseId?: string
  refreshToken?: number
  projectLabel?: string
  requestLabel?: string
  loadCaseLabel?: string
  canManageRoot?: boolean
  canBindFolder?: boolean
  canUpload?: boolean
  canRefresh?: boolean
  allowStructuredUpload?: boolean
  compact?: boolean
  readOnly?: boolean
  onBindingStateChange?: (bound: boolean) => void
  onChanged?: () => Promise<void> | void
}

const emptyState: StorageWorkspaceState = { config: null, binding: null, rules: [], files: [], candidate_folders: [] }

function asState(value: StorageWorkspaceState | null | undefined): StorageWorkspaceState {
  return {
    config: value?.config ?? null,
    binding: value?.binding ?? null,
    rules: Array.isArray(value?.rules) ? value.rules : [],
    files: Array.isArray(value?.files) ? value.files : [],
    candidate_folders: Array.isArray(value?.candidate_folders) ? value.candidate_folders : [],
  }
}

function fileKindLabel(kind: string) {
  if (kind === 'results') return '수치 결과'
  if (kind === 'solver') return 'Solver 원본'
  if (kind === 'media') return '미디어 원본'
  if (kind === 'inputs') return '입력 원본'
  if (kind === 'reports') return '보고서 원본'
  if (kind === 'STRUCTURED_RESULT') return '수치 결과'
  if (kind === 'MANIFEST') return '연결 정보'
  if (kind === 'ORIGINAL') return '원본 파일'
  return kind || '파일'
}

function fileStatusLabel(status: string) {
  return ({ DISCOVERED: '발견', STORED: '저장됨', IMPORTED: '결과 등록', SKIPPED: '기존 결과 유지', FAILED: '실패', PENDING: '대기', MISSING: '파일 없음', IGNORED: '지원하지 않음' } as Record<string, string>)[status] || status || '확인 필요'
}

function formatBytes(value?: number | null) {
  if (!Number.isFinite(value)) return ''
  if ((value as number) < 1024) return `${value} B`
  if ((value as number) < 1024 * 1024) return `${((value as number) / 1024).toFixed(1)} KB`
  return `${((value as number) / (1024 * 1024)).toFixed(1)} MB`
}

function originalKind(filename: string) {
  const extension = filename.slice(filename.lastIndexOf('.')).toLowerCase()
  if (extension === '.h3d') return 'solver'
  if (['.png', '.jpg', '.jpeg', '.svg', '.mp4', '.webm', '.glb', '.gltf'].includes(extension)) return 'media'
  if (['.txt', '.pkl'].includes(extension)) return 'solver'
  if (['.fem', '.rad', '.xml', '.hm', '.mdl'].includes(extension)) return 'inputs'
  if (['.pdf', '.ppt', '.pptx'].includes(extension)) return 'reports'
  return 'inputs'
}

export function StorageWorkspacePanel({
  loadCaseId,
  refreshToken = 0,
  projectLabel = '프로젝트 미선택',
  requestLabel = '의뢰 미선택',
  loadCaseLabel = '하중 경우 미선택',
  canManageRoot = false,
  canBindFolder = false,
  canUpload = false,
  canRefresh = true,
  allowStructuredUpload = true,
  compact = false,
  readOnly = false,
  onBindingStateChange,
  onChanged,
}: StorageWorkspacePanelProps) {
  const instanceId = useId().replace(/:/g, '')
  const headingId = `storage-workspace-heading-${instanceId}`
  const folderListId = `storage-candidate-folders-${instanceId}`
  const [state, setState] = useState<StorageWorkspaceState>(emptyState)
  const [rootState, setRootState] = useState<StorageWorkspaceState | null>(null)
  const [relativePath, setRelativePath] = useState('')
  const [rootPath, setRootPath] = useState('')
  const [files, setFiles] = useState<UploadRow[]>([])
  const [busy, setBusy] = useState(false)
  const [rootBusy, setRootBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [stateScope, setStateScope] = useState(loadCaseId || '')
  const epoch = useRef(0)
  const requestController = useRef<AbortController | null>(null)
  const bindingDraftDirty = useRef(false)
  const rootDraftDirty = useRef(false)

  const storageQuery = useMemoryQuery({
    key: loadCaseId ? `storage:load-case:${loadCaseId}` : 'storage:load-case:none',
    query: useCallback(() => fetchStorageState(loadCaseId as string), [loadCaseId]),
    enabled: Boolean(loadCaseId),
  })
  const rootQuery = useMemoryQuery({
    key: 'storage:root-and-folders',
    query: useCallback(async () => {
      const [next, folders] = await Promise.all([fetchStorageConfig(), fetchStorageFolders()])
      return { state: asState(next), folders }
    }, []),
    enabled: canManageRoot,
  })
  const loading = storageQuery.isLoading

  useEffect(() => {
    ++epoch.current
    requestController.current?.abort()
    bindingDraftDirty.current = false
    setStateScope(loadCaseId || '')
    setBusy(false); setFiles([]); setMessage(''); setError('')
    const next = loadCaseId && storageQuery.data ? asState(storageQuery.data) : emptyState
    setState(next); setRelativePath(next.binding?.relative_path || '')
    return () => { ++epoch.current; requestController.current?.abort() }
  }, [loadCaseId])

  useEffect(() => {
    if (!loadCaseId || busy || storageQuery.isRefreshing) return
    if (storageQuery.data) {
      const next = asState(storageQuery.data)
      setState(next); if (!bindingDraftDirty.current) setRelativePath(next.binding?.relative_path || '')
    } else if (!busy) {
      setState(emptyState); setRelativePath(''); setFiles([])
    }
  }, [busy, loadCaseId, storageQuery.data, storageQuery.isRefreshing])

  useEffect(() => {
    if (!loadCaseId || refreshToken === 0) return
    storageQuery.retry()
  }, [loadCaseId, refreshToken, storageQuery.retry])

  useEffect(() => {
    if (storageQuery.error) setError(storageQuery.error.message || '저장 폴더 정보를 불러오지 못했습니다.')
  }, [storageQuery.error])

  useEffect(() => {
    if (!canManageRoot || rootQuery.isBlocked) { rootDraftDirty.current = false; setRootState(null); setRootPath(''); return }
    const next = rootQuery.data
    if (next) {
      setRootState(next.state); if (!rootDraftDirty.current) setRootPath(next.state.config?.root_path || '')
      setState((current) => ({ ...current, candidate_folders: next.folders.length ? next.folders : current.candidate_folders }))
    }
    if (rootQuery.error) setError(rootQuery.error.message || '저장소 설정을 불러오지 못했습니다.')
  }, [canManageRoot, rootQuery.data, rootQuery.error, rootQuery.isBlocked])

  const visibleState = storageQuery.isBlocked || stateScope !== (loadCaseId || '') ? emptyState : state

  useEffect(() => {
    const bound = Boolean(loadCaseId && visibleState.binding?.load_case_id === loadCaseId && visibleState.binding.relative_path && visibleState.binding.exists !== false)
    onBindingStateChange?.(bound)
  }, [loadCaseId, onBindingStateChange, visibleState.binding])

  const runScoped = async (action: (signal: AbortSignal) => Promise<StorageWorkspaceState>, success: string) => {
    if (!loadCaseId || readOnly) return
    const expectedEpoch = epoch.current
    const controller = new AbortController()
    requestController.current?.abort(); requestController.current = controller
    setBusy(true); setError(''); setMessage('')
    try {
      const next = asState(await action(controller.signal))
      if (expectedEpoch !== epoch.current || controller.signal.aborted) return
      bindingDraftDirty.current = false
      setState(next); setRelativePath(next.binding?.relative_path || relativePath); setMessage(success)
      storageQuery.retry()
      await onChanged?.()
    } catch (reason) {
      if (expectedEpoch === epoch.current && !controller.signal.aborted) setError(reason instanceof Error ? reason.message : '저장 폴더 작업에 실패했습니다.')
    } finally {
      if (expectedEpoch === epoch.current && !controller.signal.aborted) setBusy(false)
    }
  }

  const refresh = () => void runScoped((signal) => refreshStorageFolder(loadCaseId as string, signal), '현재 폴더를 새로 확인했습니다.')
  const bind = () => void runScoped((signal) => bindStorageFolder(loadCaseId as string, relativePath.trim(), signal), '선택한 폴더를 현재 하중 경우에 연결했습니다.')

  const saveRoot = async () => {
    if (readOnly || !canManageRoot || !rootPath.trim()) return
    setRootBusy(true); setError(''); setMessage('')
    try {
      const next = asState(await saveStorageConfig(rootPath.trim()))
      rootDraftDirty.current = false
      setRootState(next); setRootPath(next.config?.root_path || rootPath.trim()); setMessage('저장소 설정을 저장했습니다.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : '저장소 설정을 저장하지 못했습니다.') }
    finally { setRootBusy(false) }
  }

  const chooseFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(event.target.files || [])
    setFiles(selected.map((file, index) => ({ key: `${file.name}-${file.lastModified}-${index}`, file, state: 'queued' })))
    event.target.value = ''
  }

  const updateFile = (key: string, patch: Partial<UploadRow>) => setFiles((items) => items.map((item) => item.key === key ? { ...item, ...patch } : item))

  const uploadFiles = async () => {
    if (!loadCaseId || !canUpload || readOnly || !visibleState.binding?.relative_path || busy || !files.some((item) => item.state === 'queued')) return
    const expectedEpoch = epoch.current
    setBusy(true); setError(''); setMessage('')
    try {
      for (const row of files.filter((item) => item.state === 'queued')) {
        if (expectedEpoch !== epoch.current) return
        updateFile(row.key, { state: 'uploading', message: undefined })
        try {
          const payload = /\.(csv|json)$/i.test(row.file.name)
            ? await uploadStructuredResult(loadCaseId, { filename: row.file.name, content: await row.file.text(), author: '해석 담당자' })
            : await uploadOriginalFile(loadCaseId, row.file, originalKind(row.file.name))
          const stored = payload.stored_file
          if (!stored?.relative_path) throw new Error('저장소가 파일 저장 결과를 확인하지 못했습니다.')
          updateFile(row.key, { state: 'stored', message: stored?.relative_path || payload.result?.relative_path || '저장 완료' })
        } catch (reason) {
          updateFile(row.key, { state: 'failed', message: reason instanceof Error ? reason.message : '파일 저장 실패' })
        }
      }
      if (expectedEpoch === epoch.current) {
        storageQuery.retry()
        setMessage('선택한 파일의 저장 작업이 끝났습니다. 상태를 확인하세요.')
        await onChanged?.()
      }
    } finally { if (expectedEpoch === epoch.current) setBusy(false) }
  }

  const bound = Boolean(visibleState.binding?.relative_path && visibleState.binding.exists !== false)
  const disabled = loading || busy || storageQuery.isBlocked || stateScope !== (loadCaseId || '') || !loadCaseId || readOnly
  const ruleExtensions = Array.from(new Set(visibleState.rules.flatMap((rule) => rule.extensions || []))).filter(Boolean)
  const fileAccept = (allowStructuredUpload ? ['.csv', '.json'] : []).concat(ruleExtensions).join(',') || undefined
  return <section className={`storage-workspace-panel${compact ? ' storage-workspace-panel-compact' : ''}`} data-testid="storage-workspace-panel" aria-labelledby={headingId} aria-busy={loading || busy}>
    <header className="storage-workspace-head">
      <div><span>RESULT SOURCE STORAGE</span><h2 id={headingId}>결과 저장 폴더</h2><p>{compact ? '현재 의뢰·작업 폴더에 연결된 원본과 수치 결과입니다.' : '파일 원본과 등록된 수치 결과를 선택한 하중 경우에 연결합니다.'}</p></div>
      <div className="storage-context-breadcrumb" aria-label="저장 대상"><b>{projectLabel}</b><span>/</span><b>{requestLabel}</b><span>/</span><b>{loadCaseLabel}</b></div>
    </header>

    {error ? <p className="storage-message error" role="alert"><AlertTriangle aria-hidden="true" />{error}</p> : null}
    {message ? <p className="storage-message success" role="status"><CheckCircle2 aria-hidden="true" />{message}</p> : null}

    {!compact && canManageRoot && !rootQuery.isBlocked ? <details className="storage-root-details">
      <summary><FolderOpen aria-hidden="true" /><span><strong>고정 결과 원본 폴더</strong><small>{rootState?.config?.root_path || '관리자 설정 필요'}</small></span></summary>
      <div className="storage-root-content">
        <label><span>서버 결과 원본 root</span><input data-testid="storage-root-path" value={rootPath} onChange={(event) => { rootDraftDirty.current = true; setRootPath(event.target.value) }} placeholder="관리자만 설정할 수 있는 서버 경로" disabled={readOnly || rootBusy || rootState?.config?.locked} /></label>
        <div className="storage-root-actions"><span className={rootState?.config?.configured ? 'storage-state ready' : 'storage-state'}>{rootState?.config?.locked ? '환경 설정 사용 중' : rootState?.config?.status || (rootState?.config?.configured ? '연결됨' : '미설정')}</span><button data-testid="storage-root-save" type="button" onClick={() => void saveRoot()} disabled={readOnly || rootBusy || rootState?.config?.locked || !rootPath.trim()}><Save aria-hidden="true" /> 저장</button></div>
      </div>
    </details> : null}

    {!loadCaseId ? <div className="storage-empty"><FolderOpen aria-hidden="true" /><strong>하중 경우를 선택하세요.</strong><span>선택한 의뢰의 결과 원본 폴더와 파일이 표시됩니다.</span></div> : <>
      {!compact && <div className="storage-binding-card">
        <div className="storage-section-title"><div><span>현재 연결</span><strong>{bound ? visibleState.binding?.relative_path : '연결된 저장 폴더 없음'}</strong></div><button data-testid="storage-folder-refresh" type="button" onClick={refresh} disabled={!canRefresh || disabled}>{busy ? <LoaderCircle className="storage-spin" aria-hidden="true" /> : <RefreshCw aria-hidden="true" />} 폴더 새로고침</button></div>
        <div className="storage-binding-form"><label><span>의뢰·작업 폴더 상대경로</span><input data-testid="storage-binding-path" value={relativePath} onChange={(event) => { bindingDraftDirty.current = true; setRelativePath(event.target.value) }} list={folderListId} placeholder="의뢰/작업 결과 폴더를 선택하세요" disabled={!canBindFolder || disabled} /></label><datalist id={folderListId}>{visibleState.candidate_folders.map((folder) => <option key={folder} value={folder} />)}</datalist><button data-testid="storage-binding-submit" type="button" onClick={bind} disabled={!canBindFolder || disabled || !relativePath.trim()}><Link2 aria-hidden="true" /> 폴더 연결</button></div>
        {!bound ? <p className="storage-binding-note">연결된 폴더가 없어 결과 등록을 잠갔습니다. 정확한 의뢰·작업 폴더를 연결한 뒤 파일을 저장하세요.</p> : null}
      </div>}

      <div className={`storage-grid${compact ? ' storage-grid-compact' : ''}`}>
        <section className="storage-upload-card"><header><div><span>UPLOAD</span><h3>결과 원본 추가</h3><p>{allowStructuredUpload ? 'CSV·JSON은 결과 등록과 원본 보관을 함께 처리하고, 그 밖의 파일은 원본으로 보관합니다.' : '해석 원본 파일을 정확한 의뢰·작업 폴더에 보관합니다.'}</p></div></header><label className="storage-file-picker"><File aria-hidden="true" /><strong>{files.length ? `${files.length}개 파일 선택됨` : '파일 선택'}</strong><input data-testid="storage-file-picker" type="file" multiple accept={fileAccept} onChange={chooseFiles} disabled={!canUpload || disabled || !bound} /></label>{files.length ? <div className="storage-upload-list">{files.map((row) => <div key={row.key}><File aria-hidden="true" /><span><strong>{row.file.name}</strong><small>{row.file.type || '파일'} · {formatBytes(row.file.size)}</small></span><b className={`storage-upload-${row.state}`}>{row.state === 'uploading' ? '저장 중' : row.state === 'stored' ? '저장됨' : row.state === 'failed' ? '실패' : '대기'}</b><small>{row.message || ''}</small></div>)}</div> : null}<button type="button" data-testid="storage-upload-submit" className="storage-upload-button" onClick={() => void uploadFiles()} disabled={!canUpload || disabled || !bound || !files.some((item) => item.state === 'queued')}><Upload aria-hidden="true" /> 선택 파일 저장</button></section>
        <details className="storage-rules-card"><summary><span>STORAGE RULES</span><strong>저장 규칙</strong><small>{visibleState.rules.length}개 규칙 · 대상 폴더 보기</small></summary>{visibleState.rules.length ? <div className="storage-rule-list">{visibleState.rules.map((rule: StorageRule, index) => <div key={`${rule.kind}-${index}`}><strong>{rule.label || rule.kind}</strong><span title={rule.folder}>{rule.folder ? `${rule.folder}/ · ` : ''}{rule.extensions?.join(', ') || '서버 규칙에 따름'}</span><small>{rule.description || ''}</small></div>)}</div> : <p className="storage-muted">서버에서 저장 규칙을 불러오는 중이거나 설정되지 않았습니다.</p>}</details>
      <section className="storage-files-card"><header><div><span>CONNECTED FILES</span><h3>현재 폴더 파일</h3></div><strong>{visibleState.files.length}개</strong></header>{visibleState.files.length ? <div className="storage-files-list" data-testid="storage-files-list">{visibleState.files.map((file: StorageFile) => <article data-testid="storage-file-row" key={file.id || `${file.relative_path}-${file.filename}`}><i><File aria-hidden="true" /></i><div><strong>{file.filename}</strong><small>{fileKindLabel(file.kind)} · {file.relative_path}{file.size_bytes ? ` · ${formatBytes(file.size_bytes)}` : ''}</small></div><b className={`storage-file-status-${file.status.toLowerCase()}`}>{fileStatusLabel(file.status)}</b><span>{file.run_no != null ? `Run #${file.run_no}` : file.run_id || file.message || ''}</span>{file.run_id ? <StorageResultDetails key={`${loadCaseId}:${file.run_id}`} loadCaseId={loadCaseId as string} runId={file.run_id} /> : null}{file.id ? <a href={file.download_url || storageFileDownloadUrl(file.id)} download aria-label={`${file.filename} 다운로드`}><Download aria-hidden="true" /> 다운로드</a> : null}</article>)}</div> : <div className="storage-empty compact"><FolderOpen aria-hidden="true" /><strong>{bound ? '현재 폴더에서 파일을 찾지 못했습니다.' : '연결된 폴더가 없습니다.'}</strong><span>폴더를 직접 복사한 뒤 새로고침하면 발견된 파일이 여기에 표시됩니다.</span></div>}</section>
      </div>
    </>}
  </section>
}
