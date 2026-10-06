import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api'
import type { AnalysisRequest, Project } from '../../types'
import { folderEnvironmentApi as service, type FolderAssignment, type FolderEnvironment, type FolderEnvironmentNode, type FolderEnvironmentPreview, type FolderEnvironmentRefresh, type FolderEnvironmentRegistration, type FolderEnvironmentScan, type UsageSourceReview } from '../../shared/api/folderEnvironment'
import { folderDiscoveryApi, type FolderDiscoveryBrowse } from '../../shared/api/folderDiscovery'
import { BLOCKING_DEVIATIONS, deviationLabel } from '../../shared/api/depthSchemaModel'
import { FolderRegistrationResults, roleLabel } from './FolderEnvironmentPanels'
import { DepthSchemaEditor } from './DepthSchemaEditor'
import { FolderRegistrationHistory } from './FolderRegistrationHistory'
import { FolderProjectCleanup } from './FolderProjectCleanup'
import { defaultUsageReviewDraft, UsageSourceReviewPanel, type UsageReviewDraft } from './UsageSourceReviewPanel'
import './FolderEnvironmentWorkspace.css'

type Props = { selectedProjectId?: string; selectedRequestId?: string; isGlobalAdmin?: boolean; onComplete?: () => void; onRegistrationsDeleted?: () => void | Promise<void> }
type Tab = 'connect' | 'profiles' | 'history' | 'cleanup'
/** §14.2 blocking reasons of a manual (one request) registration. */
const REQUEST_BLOCK_MESSAGES: Record<string, string> = {
  MULTIPLE_REQUESTS: '의뢰 폴더별로 조사하세요. 여러 의뢰는 자동 탐색이 의뢰별로 등록합니다.',
  DEPTH_SCHEMA_REQUEST_LEVEL_MISMATCH: '의뢰 폴더의 상위 폴더 깊이가 현재 깊이 스키마와 맞지 않아 프로젝트를 정할 수 없습니다. 상위 구조를 확인하세요.',
}
function previewStatusLabel(status: string) { return status === 'CONFIRMED' ? '확정됨' : status === 'UNRESOLVED' ? '확인 필요' : status === 'CONTENT' ? '내용물' : status }
/** Read-only DEPTH_V1 role badge: deviation (blocking/warning) or branch-incomplete info. */
function DeviationBadge({ node }: { node: Pick<FolderEnvironmentNode, 'deviation' | 'info'> }) {
  if (node.deviation) return <span className={`folder-deviation-badge ${BLOCKING_DEVIATIONS.has(node.deviation.code) ? 'blocking' : 'warning'}`} title={node.deviation.message}>{deviationLabel(node.deviation.code)}</span>
  if (node.info === 'BRANCH_INCOMPLETE') return <span className="folder-deviation-badge info" title="가지가 중간 깊이에서 끝났습니다. 등록은 가능합니다.">하위 없음</span>
  return null
}

export function FolderEnvironmentWorkspace({ selectedProjectId = '', selectedRequestId = '', isGlobalAdmin = false, onComplete, onRegistrationsDeleted }: Props) {
  const [tab, setTab] = useState<Tab>('connect')
  const [environment, setEnvironment] = useState<FolderEnvironment>(() => new URLSearchParams(window.location.search).get('result_environment') === 'DISTRIBUTION' ? 'DISTRIBUTION' : 'USAGE')
  const [path, setPath] = useState('')
  const [projects, setProjects] = useState<Project[]>([])
  const [requests, setRequests] = useState<AnalysisRequest[]>([])
  const [project, setProject] = useState(selectedProjectId)
  const [request, setRequest] = useState(selectedRequestId)
  const [useExisting, setUseExisting] = useState(Boolean(selectedRequestId))
  const [scan, setScan] = useState<FolderEnvironmentScan | null>(null)
  const [assignments, setAssignments] = useState<Record<string, FolderAssignment>>({})
  const [preview, setPreview] = useState<FolderEnvironmentPreview | null>(null)
  const [registration, setRegistration] = useState<FolderEnvironmentRegistration | null>(null)
  const [refreshResult, setRefreshResult] = useState<FolderEnvironmentRefresh | null>(null)
  const [browse, setBrowse] = useState<FolderDiscoveryBrowse | null>(null)
  const [step, setStep] = useState(1)
  const [nodeId, setNodeId] = useState('')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [onlyIssues, setOnlyIssues] = useState(false)
  const [treePage, setTreePage] = useState(0)
  const [mobilePane, setMobilePane] = useState<'tree' | 'detail'>('tree')
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')
  const [usageReviews, setUsageReviews] = useState<Record<string, UsageSourceReview>>({})
  const [usageReviewDrafts, setUsageReviewDrafts] = useState<Record<string, UsageReviewDraft>>({})
  const [activeUsageCasePath, setActiveUsageCasePath] = useState('')
  const epoch = useRef(0)
  const nodes = scan?.nodes ?? []
  const current = nodes.find((node) => node.id === nodeId)
  const effectiveRole = (node: FolderEnvironmentNode) => assignments[node.id]?.role_kind ?? node.role_kind ?? ''
  const excluded = (node: FolderEnvironmentNode) => assignments[node.id]?.role_kind === 'EXCLUDE'
  const needsReview = (node: FolderEnvironmentNode) => !excluded(node) && (['UNRESOLVED', 'ERROR'].includes(node.status) || Boolean(node.deviation))
  const invalidate = () => { epoch.current += 1; setScan(null); setPreview(null); setRegistration(null); setRefreshResult(null); setAssignments({}); setUsageReviews({}); setUsageReviewDrafts({}); setActiveUsageCasePath(''); setStep(1); setNotice(''); setTreePage(0) }
  async function execute<T>(name: string, operation: () => Promise<T>, accept: (value: T) => void) {
    const intent = epoch.current; setBusy(name); setNotice('')
    try { const value = await operation(); if (intent === epoch.current) accept(value) }
    catch (error) { if (intent === epoch.current) setNotice(error instanceof Error ? error.message : '처리하지 못했습니다.') }
    finally { if (intent === epoch.current) setBusy('') }
  }
  useEffect(() => { let active = true; api.projects().then((all) => { if (active) setProjects(all) }).catch((error) => { if (active) setNotice(String(error)) }); return () => { active = false; epoch.current += 1 } }, [])
  useEffect(() => { let active = true; setRequests([]); if (project) api.requests(project).then((items) => { if (active) { setRequests(items); setRequest((value) => items.some((item) => item.id === value) ? value : '') } }).catch((error) => { if (active) setNotice(String(error)) }); return () => { active = false } }, [project])
  const acceptScan = (value: FolderEnvironmentScan) => { setScan(value); setAssignments({}); setPreview(null); setRegistration(null); setNodeId(value.nodes.find((node) => node.role_kind === 'SIMULATION_CASE')?.id ?? value.nodes[0]?.id ?? ''); setStep(2); setTreePage(0); setCollapsed(new Set()); setMobilePane('tree'); setNotice(value.status === 'COMPLETE' ? `${value.nodes.length}개 폴더를 조사했습니다.` : '불완전 조사입니다. 표시된 문제를 확인하고 다시 조사하세요.') }
  const survey = () => execute('조사 중…', () => service.scan({ environment, relative_path: path.trim(), project_id: useExisting ? project || undefined : undefined, request_id: useExisting ? request || undefined : undefined }), acceptScan)
  const refresh = () => execute('스키마 갱신 중…', () => service.refresh({ project_id: project, request_id: request, environment }), (value) => { setRefreshResult(value); setScan(null); setPreview(null); setRegistration(null); setStep(1); setNotice(value.status === 'CONFLICT' ? value.message || '역할 충돌이 있어 기존 스키마를 유지했습니다. 아래 판정 근거를 확인하세요.' : value.changed ? `스키마를 갱신했습니다. 추가 ${value.diff.added} · 제거 ${value.diff.removed} · 변경 ${value.diff.changed}` : '현재 스키마와 저장소 내용이 같습니다.'); if (value.status !== 'CONFLICT') onComplete?.() })
  // DEPTH_V1: roles come from the depth schema; the only edits are EXCLUDE and PROJECT/REQUEST LINK (§6).
  const assign = (patch: Partial<FolderAssignment> | null) => { if (!current) return; setAssignments((values) => { const next = { ...values }; if (patch === null) delete next[current.id]; else next[current.id] = { ...(values[current.id] ?? { node_id: current.id, role_kind: current.role_kind ?? '', target_mode: 'CREATE' as const }), ...patch, propagate_same_level: false, confirm: true }; return next }); if (current.role_kind === 'PROJECT' && patch?.target_id) { setProject(patch.target_id); setRequest('') }; setPreview(null); setRegistration(null) }
  const visibleNodes = useMemo(() => nodes.filter((node) => (!onlyIssues || needsReview(node)) && ![...collapsed].some((parent) => node.relative_path !== parent && node.relative_path.startsWith(`${parent}/`))), [scan, onlyIssues, collapsed, assignments])
  /** After a registration delete or project cleanup: re-read projects/requests and drop a deleted selection. */
  const refreshAfterDelete = async () => { const all = await api.projects(); setProjects(all); if (!all.some((item) => item.id === project)) { setProject(''); setRequest('') } else if (project) { const items = await api.requests(project); setRequests(items); if (!items.some((item) => item.id === request)) setRequest('') } setRegistration(null); await onRegistrationsDeleted?.() }
  const showRegistration = (value: FolderEnvironmentRegistration) => { setRegistration(value); setTab('connect'); setStep(3); onComplete?.() }
  const usageCases = preview?.rows.filter((row) => row.role_kind === 'SIMULATION_CASE') ?? []
  const activeUsageCase = usageCases.find((item) => item.relative_path === activeUsageCasePath) ?? usageCases[0]
  const activeReview = activeUsageCase ? usageReviews[activeUsageCase.relative_path] : undefined
  const savedUsageSources = scan?.usage_sources
  const profileDraft = (): UsageReviewDraft => ({ ...defaultUsageReviewDraft(), selection: { ...defaultUsageReviewDraft().selection, ...savedUsageSources?.selection }, metricPaths: savedUsageSources?.metric_paths ?? {} })
  const activeDraft = activeUsageCase ? usageReviewDrafts[activeUsageCase.relative_path] ?? profileDraft() : profileDraft()
  const reviewCanPublish = usageCases.length > 0 && usageCases.every((item) => {
    const review = usageReviews[item.relative_path]; const draft = usageReviewDrafts[item.relative_path]
    const partialRequired = review && ((review.missing_count ?? 0) > 0 || Object.keys(draft?.excludes ?? {}).length > 0 || review.entries?.some((entry) => entry.metrics.some((metric) => metric.status === 'EXCLUDED')))
    return Boolean(review?.can_publish) && !draft?.dirty && (!partialRequired || Boolean(draft?.acknowledgedPartial))
  })
  const runUsageReview = () => activeUsageCase && preview ? execute('파일·값 검수 중…', () => service.usageReview(preview.id, { case_relative_path: activeUsageCase.relative_path, selection: activeDraft.selection, selected_sources: activeDraft.selectedSources, metric_paths: activeDraft.metricPaths, excludes: activeDraft.excludes, acknowledge_partial: activeDraft.acknowledgedPartial }), (value) => { const path = activeUsageCase.relative_path; setUsageReviews((current) => ({ ...current, [path]: value })); setUsageReviewDrafts((current) => ({ ...current, [path]: { ...(current[path] ?? activeDraft), dirty: false } })) }) : undefined
  const requestBlock = preview?.blocking_code && preview.blocking_code in REQUEST_BLOCK_MESSAGES ? preview.blocking_code : ''
  const jobPanel = registration && <FolderRegistrationResults value={registration} busy={Boolean(busy)} onRefresh={() => void execute('상태 조회 중…', () => service.registration(registration.registration_id), setRegistration)} onRetry={() => void execute('결과 읽는 중…', () => service.retryCapture(registration.registration_id), setRegistration)} />
  return <section className="folder-environment-workspace" data-ui-density="v1">
    <header className="folder-environment-heading"><div><h1>폴더 연결·규칙</h1><p>의뢰 폴더 1개를 조사해 바로 등록합니다. 여러 의뢰는 자동 탐색이 의뢰별로 등록합니다.</p></div><label>환경<select aria-label="폴더 환경" value={environment} disabled={Boolean(busy)} onChange={(event) => { invalidate(); setEnvironment(event.target.value as FolderEnvironment) }}><option value="USAGE">사용환경</option><option value="DISTRIBUTION">유통환경</option></select></label><button type="button" className="ghost-button" disabled={Boolean(busy) || !project || !request} onClick={() => void refresh()}>Refresh</button></header>
    <nav className="saved-work-tabs" aria-label="폴더 규칙 작업">{([['connect', '폴더 연결'], ['profiles', '저장된 규칙'], ['history', '등록 이력'], ...(isGlobalAdmin ? [['cleanup', '프로젝트 정리'] as const] : [])] as const).map(([key, title]) => <button type="button" key={key} disabled={Boolean(busy)} className={tab === key ? 'active' : ''} aria-current={tab === key ? 'page' : undefined} onClick={() => setTab(key)}>{title}</button>)}</nav>
    {notice && <div className="folder-environment-notice info" role="status">{notice}</div>}
    {refreshResult && <details className="folder-environment-card"><summary>Refresh 역할 판정 · {refreshResult.nodes.length}개 폴더</summary><div className="folder-preview-table">{refreshResult.nodes.map((node) => <div className="folder-preview-row folder-preview-row--refresh" key={node.relative_path}><span>{node.relative_path}</span><b>{roleLabel(node.role_kind)}</b><em>{previewStatusLabel(node.status)} · {node.role_basis}</em><DeviationBadge node={node} /></div>)}</div></details>}
    {busy && <p role="status">{busy}</p>}
    {tab === 'profiles' && <DepthSchemaEditor onSaved={() => invalidate()} />}
    {tab === 'history' && <FolderRegistrationHistory isAdmin={isGlobalAdmin} busy={Boolean(busy)} onOpen={(item) => void execute('이력 조회 중…', () => service.registration(item.registration_id), showRegistration)} onDeleted={refreshAfterDelete} />}
    {tab === 'cleanup' && isGlobalAdmin && <FolderProjectCleanup busy={Boolean(busy)} onDeleted={refreshAfterDelete} />}
    {tab === 'connect' && <>
      <nav className="folder-steps" aria-label="폴더 연결 단계">{['폴더 선택', '구조 확인', '등록·결과 확인'].map((title, i) => <button type="button" key={title} disabled={Boolean(busy) || (i === 1 && !scan) || (i === 2 && !preview && !registration)} aria-current={step === i + 1 ? 'step' : undefined} className={step === i + 1 ? 'active' : ''} onClick={() => setStep(i + 1)}><b>{i + 1}</b>{title}</button>)}</nav>
      {step === 1 && <article className="folder-environment-card"><div className="folder-environment-form">
        <label>업무 연결<select value={useExisting ? 'existing' : 'tree'} disabled={Boolean(busy)} onChange={(event) => { invalidate(); setUseExisting(event.target.value === 'existing') }}><option value="tree">폴더에서 프로젝트·의뢰 찾기</option><option value="existing">기존 프로젝트·의뢰에 연결</option></select></label>
        {useExisting && <><label>프로젝트<select aria-label="연결 프로젝트" value={project} disabled={Boolean(busy)} onChange={(event) => { invalidate(); setProject(event.target.value); setRequest('') }}><option value="">선택</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label><label>의뢰<select aria-label="연결 의뢰" value={request} disabled={Boolean(busy) || !project} onChange={(event) => { invalidate(); setRequest(event.target.value) }}><option value="">선택</option>{requests.map((r) => <option key={r.id} value={r.id}>{r.title}</option>)}</select></label></>}
        <label className="wide">저장소 안 조사 경로<div className="path-entry"><input value={path} disabled={Boolean(busy)} placeholder="비우면 저장소 전체" onChange={(event) => { invalidate(); setPath(event.target.value) }} /><button type="button" className="ghost-button" disabled={Boolean(busy)} onClick={() => void execute('폴더 목록 조회 중…', () => folderDiscoveryApi.browse(path), setBrowse)}>폴더 찾아보기</button></div></label>
        {browse && <div className="folder-browser wide"><strong>{browse.relative_path || '저장소 전체'}</strong>{!browse.configured ? <p>관리자 저장소 설정에서 서버 경로를 먼저 연결하세요.</p> : <><div className="folder-environment-footer"><button type="button" onClick={() => { invalidate(); setPath(browse.relative_path); setBrowse(null) }}>이 폴더 선택</button><button type="button" disabled={Boolean(busy) || !browse.relative_path} onClick={() => void execute('폴더 목록 조회 중…', () => folderDiscoveryApi.browse(browse.relative_path.split('/').slice(0, -1).join('/')), setBrowse)}>상위 폴더</button></div>{browse.entries.filter((item) => item.is_directory).map((item) => <button type="button" key={item.relative_path} disabled={Boolean(busy)} onClick={() => void execute('폴더 목록 조회 중…', () => folderDiscoveryApi.browse(item.relative_path), setBrowse)}>{item.name} ›</button>)}</>}</div>}
      </div><footer className="folder-environment-footer"><span>서버 저장소에서 읽기 전용으로 조사합니다.</span><button type="button" className="primary-button" disabled={Boolean(busy) || (useExisting && (!project || !request))} onClick={() => void survey()}>조사 시작</button></footer></article>}
      {step === 2 && scan && <article className="folder-environment-card"><div className="folder-environment-card-head"><h2>구조 확인</h2><span>{scan.relative_path || '저장소 전체'} · {nodes.length}개 폴더</span></div>
        {scan.issues.length > 0 && <div role="alert">{scan.issues.map((issue, i) => <p key={i}>{issue.message}</p>)}</div>}
        <div className="folder-environment-footer"><label><input type="checkbox" checked={onlyIssues} onChange={(event) => { setOnlyIssues(event.target.checked); setTreePage(0) }} /> 확인 필요만 보기</label><span>{nodes.filter(needsReview).length}개 확인 필요</span></div>
        <div className="folder-mobile-panes"><button type="button" aria-pressed={mobilePane === 'tree'} onClick={() => setMobilePane('tree')}>폴더 트리</button><button type="button" disabled={!current} aria-pressed={mobilePane === 'detail'} onClick={() => setMobilePane('detail')}>선택 폴더 설정</button></div><div className={`folder-tree-layout mobile-${mobilePane}`}><div className="folder-tree" aria-label="조사한 폴더">{visibleNodes.slice(treePage * 100, (treePage + 1) * 100).map((node) => <div className={`folder-tree-row ${node.id === nodeId ? 'selected' : ''}`} key={node.id} style={{ paddingLeft: 8 + Math.min(node.depth, 6) * 12 }}>
          <button type="button" className="folder-fold" aria-label={`${node.name} ${collapsed.has(node.relative_path) ? '펼치기' : '접기'}`} onClick={() => { setCollapsed((value) => { const next = new Set(value); if (next.has(node.relative_path)) next.delete(node.relative_path); else next.add(node.relative_path); return next }); setTreePage(0) }}>{collapsed.has(node.relative_path) ? '›' : '⌄'}</button>
          <button type="button" className="folder-node-button" title={node.relative_path} aria-pressed={node.id === nodeId} onClick={() => { setNodeId(node.id); setMobilePane('detail') }}><span>{node.name || '저장소 전체'}</span><em>{roleLabel(effectiveRole(node))}</em>{node.deviation && <DeviationBadge node={node} />}</button></div>)}<div className="folder-environment-footer"><button type="button" disabled={!treePage} onClick={() => setTreePage(treePage - 1)}>이전</button><span>{treePage + 1}페이지</span><button type="button" disabled={(treePage + 1) * 100 >= visibleNodes.length} onClick={() => setTreePage(treePage + 1)}>다음</button></div></div>
          <div className="folder-node-detail">{current && <><h3>{current.name || '저장소 전체'}</h3><code>{current.relative_path || '/'}</code><dl className="folder-node-role"><dt>역할</dt><dd><b aria-label="선택 폴더 역할">{roleLabel(current.role_kind)}</b> <small>{previewStatusLabel(current.status)}{current.level != null ? ` · L${current.level}` : ''}</small> <DeviationBadge node={current} /></dd></dl>
          {current.deviation && <p role="alert">{current.deviation.message}</p>}
          <label className="folder-node-check"><input type="checkbox" checked={excluded(current)} disabled={Boolean(busy)} onChange={(event) => assign(event.target.checked ? { role_kind: 'EXCLUDE', target_mode: 'CREATE', target_id: null } : null)} /> 이번 등록에서 제외</label>
          {!excluded(current) && ['PROJECT', 'REQUEST'].includes(current.role_kind ?? '') && <label>기존 업무 연결<select aria-label="기존 업무 연결" value={assignments[current.id]?.target_mode === 'LINK' ? assignments[current.id]?.target_id || '' : ''} disabled={Boolean(busy)} onChange={(event) => assign(event.target.value ? { role_kind: current.role_kind ?? '', target_mode: 'LINK', target_id: event.target.value } : null)}><option value="">폴더 이름으로 등록·기존 연결 유지</option>{(current.role_kind === 'PROJECT' ? projects.map((p) => ({ id: p.id, name: p.name })) : requests.map((r) => ({ id: r.id, name: r.title }))).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>{current.role_kind === 'REQUEST' && !requests.length && <small>먼저 프로젝트 폴더를 기존 프로젝트에 연결하거나 1단계에서 프로젝트를 선택하세요.</small>}</label>}
          {current.message && current.message !== current.deviation?.message && <p>{current.message}</p>}<p>역할은 저장된 깊이 스키마로 정해집니다. 바꾸려면 “저장된 규칙” 탭에서 깊이별 역할을 수정하세요.</p></>}</div></div>
        <footer className="folder-environment-footer"><button type="button" className="ghost-button" disabled={Boolean(busy)} onClick={() => setStep(1)}>이전</button><button type="button" className="primary-button" disabled={Boolean(busy) || scan.status !== 'COMPLETE'} onClick={() => void execute('미리보기 만드는 중…', () => service.preview({ scan_id: scan.id, assignments: Object.values(assignments), require_usage_review: environment === 'USAGE' }), (value) => { setPreview(value); setRegistration(null); setUsageReviews({}); setUsageReviewDrafts({}); setActiveUsageCasePath(value.rows.find((row) => row.role_kind === 'SIMULATION_CASE')?.relative_path ?? ''); setStep(3) })}>등록 내용 확인</button></footer>
      </article>}
      {step === 3 && <article className="folder-environment-card"><h2>등록·결과 확인</h2>{preview && !registration && <><p>신규 {preview.summary.new} · 기존 {preview.summary.existing} · 확인 필요 {preview.unresolved_count}</p><div className="folder-preview-table">{preview.rows.map((row) => <div className={`folder-preview-row folder-preview-row--preview ${row.status.toLowerCase()}`} key={row.id}><span>{row.relative_path}</span><b>{roleLabel(row.role_kind)}</b><span>{row.name}</span><em>{previewStatusLabel(row.status)} <DeviationBadge node={row} /></em>{row.message && <small>{row.message}</small>}</div>)}</div>{environment === 'USAGE' && activeUsageCase ? <><div className="folder-source-review-cases" aria-label="Case 선택">{usageCases.map((item, index) => <button type="button" disabled={Boolean(busy)} key={item.id} className={activeUsageCase.relative_path === item.relative_path ? 'active' : ''} onClick={() => setActiveUsageCasePath(item.relative_path)}>Case {index + 1}{usageReviews[item.relative_path] ? ' · 검수됨' : ''}</button>)}</div><button type="button" className="ghost-button" disabled={Boolean(busy)} onClick={() => void runUsageReview()}>{activeReview ? '다시 검수' : '파일·값 검수 열기'}</button>{activeReview ? <UsageSourceReviewPanel review={activeReview} draft={activeDraft} disabled={Boolean(busy)} onChange={(next) => setUsageReviewDrafts((current) => ({ ...current, [activeUsageCase.relative_path]: next }))} /> : <p className="folder-source-review-note">각 Case에서 파일·값 검수를 실행해야 등록할 수 있습니다.</p>}</> : null}{requestBlock ? <div role="alert"><p>{preview.message || REQUEST_BLOCK_MESSAGES[requestBlock]}</p>{preview.request_paths?.length ? <ul aria-label="조사 범위의 의뢰 폴더">{preview.request_paths.map((item) => <li key={item}><code>{item}</code></li>)}</ul> : null}</div> : !preview.can_apply && <p role="alert">{preview.message || '구조 확인으로 돌아가 미분류·충돌을 해결하세요.'}</p>}<footer className="folder-environment-footer"><button type="button" className="ghost-button" disabled={Boolean(busy)} onClick={() => setStep(2)}>구조 수정</button><button type="button" className="primary-button" disabled={Boolean(busy) || !preview.can_apply || Boolean(requestBlock) || (environment === 'USAGE' && !reviewCanPublish)} onClick={() => void execute('등록하고 결과 읽는 중…', () => service.register({ preview_id: preview.id, idempotency_key: `folder-${preview.id}`, capture: true }), showRegistration)}>등록하고 결과 읽기</button></footer></>}{jobPanel}</article>}
    </>}
  </section>
}
