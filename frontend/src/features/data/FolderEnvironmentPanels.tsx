import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { folderEnvironmentApi, type FolderEnvironment, type FolderEnvironmentProfile, type FolderEnvironmentProfileRule, type FolderEnvironmentRegistration, type UsageSourceProfile } from '../../shared/api/folderEnvironment'
import { folderDiscoveryApi, type FolderDiscoverySavedRule } from '../../shared/api/folderDiscovery'

export const environmentRoleLabels: Record<string, string> = { PROJECT: '프로젝트', REQUEST: '의뢰', SIMULATION_CASE: '해석 Case', EVALUATION: '평가 항목', LOAD_CASE: '하중경우', EXECUTION_RUN: 'Run Case', RUN_OPTION: 'Run Option', INPUT: '입력 폴더', RESULTS: '결과 폴더', SCENE: 'Scene', CONTAINER: '일반 중간 폴더', EXCLUDE: '이번 등록에서 제외' }
export const roleLabel = (value?: string | null) => value ? environmentRoleLabels[value] ?? value : '확인 필요'
export const environmentRoles = (environment: FolderEnvironment) => environment === 'USAGE'
  ? ['PROJECT', 'REQUEST', 'SIMULATION_CASE', 'EVALUATION', 'INPUT', 'RESULTS', 'CONTAINER']
  : ['PROJECT', 'REQUEST', 'SIMULATION_CASE', 'LOAD_CASE', 'EXECUTION_RUN', 'RUN_OPTION', 'SCENE', 'INPUT', 'RESULTS', 'CONTAINER']
const states: Record<string, string> = { REGISTERED: '연결 완료', CAPTURING: '결과 읽는 중', COMPLETED: '결과 읽기 완료', FAILED: '결과 읽기 실패', PENDING: '결과 읽기 대기', RUNNING: '결과 읽는 중', PARTIAL: '일부 결과 확인 필요' }

export function FolderRegistrationResults({ value, busy, onRefresh, onRetry }: { value: FolderEnvironmentRegistration; busy: boolean; onRefresh: () => void; onRetry: () => void }) {
  return <div className="folder-registration-results" aria-label="등록 결과">
    <div className="folder-environment-footer"><strong>{states[value.status] ?? value.status}</strong><span>{new Date(value.created_at).toLocaleString('ko-KR')}</span><button type="button" className="ghost-button" disabled={busy} onClick={onRefresh}>상태 새로고침</button>
      {value.capture_jobs.some((job) => ['FAILED', 'PENDING', 'RUNNING'].includes(job.status)) && <button type="button" className="primary-button" disabled={busy} onClick={onRetry}>미완료 결과 다시 읽기</button>}</div>
    {value.capture_jobs.map((job, index) => {
      const query = new URLSearchParams({ project: job.project_id || value.project_id, request: job.request_id || value.request_id, view: 'case_results', result_environment: value.environment, case: job.case_id, capture: job.capture_id || '' })
      return <div className="folder-job" key={job.id}><span>Case {index + 1} · {states[job.status] ?? job.status}</span>{job.error_code && <span role="alert">{job.error_code}</span>}{job.status === 'COMPLETED' && job.capture_id && <Link className="primary-button" to={`/workspace/requests?${query}`}>결과 보기{value.capture_jobs.length > 1 ? ` ${index + 1}` : ''}</Link>}</div>
    })}
    {value.usage_source_reviews && <details className="folder-history-review"><summary>저장된 파일·값 검수 {Object.keys(value.usage_source_reviews).length}건</summary>{Object.entries(value.usage_source_reviews).map(([casePath, review]) => <div key={casePath}><strong>{casePath}</strong><span> · 확인 필요 {review.blocking_count ?? 0} · 자료 없음 {review.missing_count ?? 0}</span>{review.entries?.flatMap((entry) => entry.metrics.map((metric) => <p key={`${entry.evaluation}:${entry.direction}:${metric.key}`}>{entry.evaluation} / {entry.direction} · {metric.key}: {metric.value == null ? '—' : String(metric.value)} ({metric.status})</p>))}</div>)}</details>}
  </div>
}

export function FolderProfileEditor({ environment, profiles, seedRules, seedUsageSources, busy, onSaved, onNotice }: { environment: FolderEnvironment; profiles: FolderEnvironmentProfile[]; seedRules: FolderEnvironmentProfileRule[]; seedUsageSources?: UsageSourceProfile; busy: boolean; onSaved: (profile: FolderEnvironmentProfile) => void; onNotice: (message: string) => void }) {
  const [editing, setEditing] = useState('')
  const [name, setName] = useState('')
  const [rules, setRules] = useState<FolderEnvironmentProfileRule[]>([])
  const [usageSources, setUsageSources] = useState<UsageSourceProfile | undefined>(seedUsageSources)
  const [saving, setSaving] = useState(false)
  const [legacy, setLegacy] = useState<FolderDiscoverySavedRule[]>([])
  const [legacyPath, setLegacyPath] = useState<string | null>(null)
  useEffect(() => { setEditing(''); setName(''); setRules([]); setUsageSources(seedUsageSources) }, [environment, seedUsageSources])
  useEffect(() => { let active = true; folderDiscoveryApi.savedRules().then((value) => { if (active) setLegacy(value.items) }).catch((error) => { if (active) onNotice(String(error)) }); return () => { active = false } }, [])
  const selected = profiles.find((profile) => profile.id === editing)
  const patch = (index: number, value: Partial<FolderEnvironmentProfileRule>) => setRules((current) => current.map((rule, i) => i === index ? { ...rule, ...value } : rule))
  const save = async (copy = false) => {
    setSaving(true)
    try {
      // Usage-source mappings are stored alongside folder rules. Editing a profile's
      // folder patterns must not silently discard the reusable JSON-key mappings.
      const body = { environment, name, rules: { rules, usage_sources: usageSources ?? selected?.rules.usage_sources } }
      const saved = copy && legacyPath !== null ? await folderEnvironmentApi.profileFromLegacy({ environment, name, relative_path: legacyPath })
        : selected ? await folderEnvironmentApi.updateProfile(selected.id, { ...body, expected_revision: selected.revision }) : await folderEnvironmentApi.createProfile(body)
      onSaved(saved); setEditing(saved.id); setName(saved.name); setRules(saved.rules.rules ?? []); onNotice(saved.message || `규칙 v${saved.revision}을 저장했습니다. 적용 전 폴더를 다시 조사하세요.`)
    } catch (error) { onNotice(error instanceof Error ? error.message : '규칙 저장에 실패했습니다.') } finally { setSaving(false) }
  }
  return <article className="folder-environment-card">
    <h2>저장된 규칙</h2><div className="folder-environment-form"><label>편집할 규칙<select value={editing} disabled={saving || busy} onChange={(event) => { const item = profiles.find((p) => p.id === event.target.value); setEditing(item?.id ?? ''); setName(item?.name ?? ''); setRules(item?.rules.rules ?? []); setUsageSources(item?.rules.usage_sources ?? seedUsageSources) }}><option value="">새 규칙</option>{profiles.map((p) => <option value={p.id} key={p.id}>{p.name} · v{p.revision}</option>)}</select></label><label>규칙 이름<input value={name} onChange={(event) => setName(event.target.value)} disabled={saving || busy} /></label></div>
    <div className="folder-environment-footer"><span>기본 환경 인식 위에 아래 규칙을 적용합니다. 여러 역할이 겹치면 확인 대상으로 남깁니다.</span><button type="button" className="ghost-button" disabled={saving || busy || !seedRules.length} onClick={() => { setEditing(''); setRules(seedRules); setName('확인한 폴더 규칙') }}>확인한 구조에서 가져오기</button><button type="button" className="ghost-button" disabled={saving || busy} onClick={() => setRules([...rules, { role_kind: 'SIMULATION_CASE', parent_role: 'REQUEST', pattern: '', match_mode: 'glob' }])}>규칙 추가</button></div>
    <div className="folder-rule-list">{rules.map((rule, index) => <div className="folder-rule-editor" key={index}>
      <label>상위 역할<select value={rule.parent_role ?? ''} disabled={saving} onChange={(event) => patch(index, { parent_role: event.target.value || undefined })}><option value="">모든 상위</option><option value="ROOT">조사 시작</option>{environmentRoles(environment).map((role) => <option key={role} value={role}>{roleLabel(role)}</option>)}</select></label>
      <label>이름 규칙<input aria-label={`이름 규칙 ${index + 1}`} value={rule.pattern} disabled={saving} placeholder="예: Run*" onChange={(event) => patch(index, { pattern: event.target.value })} /></label>
      <label>일치 방식<select value={rule.match_mode} disabled={saving} onChange={(event) => patch(index, { match_mode: event.target.value as 'glob' | 'contains' })}><option value="glob">패턴 (*, ?)</option><option value="contains">포함 단어</option></select></label>
      <label>역할<select value={rule.role_kind} disabled={saving} onChange={(event) => patch(index, { role_kind: event.target.value })}>{environmentRoles(environment).map((role) => <option key={role} value={role}>{roleLabel(role)}</option>)}</select></label>
      <details><summary>깊이</summary><input aria-label={`깊이 ${index + 1}`} type="number" min="0" max="64" value={rule.depth ?? ''} onChange={(event) => patch(index, { depth: event.target.value === '' ? undefined : Number(event.target.value) })} /></details>
      <button type="button" className="ghost-button" disabled={saving} onClick={() => setRules(rules.filter((_, i) => i !== index))}>삭제</button>
    </div>)}</div>
    <div className="folder-environment-footer"><small>빈 깊이는 부모 역할을 기준으로 찾습니다. 규칙 저장만으로 업무와 결과는 변경되지 않습니다.</small><button type="button" className="primary-button" disabled={saving || busy || !name.trim()} onClick={() => void save()}>{saving ? '저장 중…' : selected ? '새 개정 저장' : '규칙 저장'}</button>{selected && <button type="button" className="ghost-button" disabled={busy} onClick={() => { setEditing(''); setName(`${name} 복사`) }}>복사하여 편집</button>}</div>
    <details><summary>기존 깊이 규칙에서 복사</summary><div className="folder-environment-form"><label>기존 규칙 위치<select value={legacyPath ?? '__none__'} onChange={(event) => setLegacyPath(event.target.value === '__none__' ? null : event.target.value)}><option value="__none__">선택</option>{legacy.map((item) => <option key={item.relative_path} value={item.relative_path}>{item.relative_path || '저장소 전체'} · v{item.revision}</option>)}</select></label><button type="button" className="ghost-button" disabled={saving || legacyPath === null || !name.trim()} onClick={() => void save(true)}>새 환경 규칙으로 복사</button></div><p>기존 규칙을 보존합니다. 하중경우와 코드 추출은 자동 변환하지 않으므로 구조를 다시 확인하세요.</p></details>
  </article>
}
