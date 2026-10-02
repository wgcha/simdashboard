import { useEffect, useMemo, useRef, useState } from 'react'
import { folderEnvironmentApi, type DepthLevel, type DepthLowerRole, type DepthSampleLevel, type DepthSampleSegment, type DepthSchema, type DepthSchemaCheck, type DepthSchemaEnvironmentsInput, type DepthSchemaSamples, type DepthUpperRole, type FolderEnvironment } from '../../shared/api/folderEnvironment'
import { depthRowEditable, depthRows, deviationLabel, errorCode, lowerRoleOptions, setDepthRole, UPPER_MAX_LEVELS, UPPER_ROLES, validateLowerLevels, validateUpperLevels } from '../../shared/api/depthSchemaModel'
import { roleLabel } from './FolderEnvironmentPanels'

type Tab = DepthSampleSegment
const TABS: Array<[Tab, string]> = [['UPPER', '상위 구조'], ['USAGE', '사용환경'], ['DISTRIBUTION', '유통환경']]
const FINAL_STRUCTURE: Array<[string, string, string]> = [
  ['L1', 'Final', 'Final 보관 영역 (이름으로 판정, 선택)'],
  ['L2', 'CAE · Reports · CAD', 'CAE 해석 파일 · Reports 보고서 · CAD (하위는 내용물)'],
  ['L3', '<Case>', '해석 Case'],
  ['L4', '<finalization_id>', 'Final 버전 (32자리 hex)'],
  ['L5~', '…', 'Working의 L3 이후 구조를 그대로 미러'],
]

type Drafts = { upper: DepthLevel<DepthUpperRole>[]; USAGE: DepthLevel<DepthLowerRole>[]; DISTRIBUTION: DepthLevel<DepthLowerRole>[] }
const draftsFrom = (schema: DepthSchema): Drafts => ({ upper: schema.upper.levels, USAGE: schema.environments.USAGE.lower.levels, DISTRIBUTION: schema.environments.DISTRIBUTION.lower.levels })
const message = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback

function SampleCell({ sample }: { sample?: DepthSampleLevel }) {
  if (!sample?.samples.length) return <span className="depth-schema-muted">—</span>
  const shown = sample.samples.reduce((sum, item) => sum + item.count, 0)
  const rest = Math.max(0, sample.folder_count - shown)
  const text = sample.samples.map((item) => `${item.name} ×${item.count}`).join(', ')
  return <span className="depth-schema-samples" title={text}>{sample.samples.map((item) => <span key={item.name}>{item.name} <b>×{item.count}</b></span>)}{rest > 0 || sample.truncated ? <em>+{rest || '…'}</em> : null}</span>
}

/** Admin editor for the global DEPTH_V1 schema (contract §7). Exactly two actions: 확인 and 저장. */
export function DepthSchemaEditor({ onSaved }: { onSaved?: (schema: DepthSchema) => void }) {
  const [tab, setTab] = useState<Tab>('UPPER')
  const [schema, setSchema] = useState<DepthSchema | null>(null)
  const [drafts, setDrafts] = useState<Drafts | null>(null)
  const [samples, setSamples] = useState<Record<string, DepthSchemaSamples>>({})
  const [loadingSamples, setLoadingSamples] = useState(false)
  const [check, setCheck] = useState<DepthSchemaCheck | null>(null)
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])

  const load = () => folderEnvironmentApi.getDepthSchema().then((value) => { if (!alive.current) return; setSchema(value); setDrafts(draftsFrom(value)); setCheck(null) })
  useEffect(() => { load().catch((error) => { if (alive.current) setNotice(message(error, '깊이 스키마를 불러오지 못했습니다.')) }) }, [])

  const upperKey = JSON.stringify(drafts?.upper ?? [])
  const sampleKey = tab === 'UPPER' ? 'UPPER' : `${tab}:${upperKey}`
  useEffect(() => {
    if (!drafts || samples[sampleKey]) return
    if (tab !== 'UPPER' && validateUpperLevels(drafts.upper).length) return
    const controller = new AbortController()
    setLoadingSamples(true)
    folderEnvironmentApi.depthSchemaSamples(tab, tab === 'UPPER' ? undefined : { levels: drafts.upper }, controller.signal)
      .then((value) => setSamples((current) => ({ ...current, [sampleKey]: value })))
      .catch((error) => { if (!controller.signal.aborted) setNotice(message(error, '예시 폴더를 불러오지 못했습니다.')) })
      .finally(() => { if (!controller.signal.aborted) setLoadingSamples(false) })
    return () => { controller.abort(); setLoadingSamples(false) }
    // Samples follow the open tab and the upper draft only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sampleKey, Boolean(drafts)])

  const errors = useMemo(() => drafts ? {
    UPPER: validateUpperLevels(drafts.upper),
    USAGE: validateLowerLevels('USAGE', drafts.USAGE),
    DISTRIBUTION: validateLowerLevels('DISTRIBUTION', drafts.DISTRIBUTION),
  } : null, [drafts])
  const invalid = !errors || Object.values(errors).some((items) => items.length > 0)

  const environmentsInput = (): DepthSchemaEnvironmentsInput | null => schema && drafts ? {
    USAGE: { environment_keyword: '사용', lower: { levels: drafts.USAGE, below_last: 'CONTENT' }, usage_sources: schema.environments.USAGE.usage_sources ?? null },
    DISTRIBUTION: { environment_keyword: '유통', lower: { levels: drafts.DISTRIBUTION, below_last: 'CONTENT' }, usage_sources: schema.environments.DISTRIBUTION.usage_sources ?? null },
  } : null
  const run = async (label: string, operation: () => Promise<void>) => {
    setBusy(label); setNotice('')
    try { await operation() } finally { if (alive.current) setBusy('') }
  }
  const runCheck = () => run('확인 중…', async () => {
    const environments = environmentsInput()
    if (!drafts || !environments) return
    try { const value = await folderEnvironmentApi.depthSchemaCheck({ levels: drafts.upper }, environments); if (alive.current) setCheck(value) }
    catch (error) { setNotice(message(error, '확인하지 못했습니다.')) }
  })
  const save = () => run('저장 중…', async () => {
    const environments = environmentsInput()
    if (!schema || !drafts || !environments) return
    try {
      const saved = await folderEnvironmentApi.saveDepthSchema(schema.schema_set_id, { levels: drafts.upper }, environments)
      if (!alive.current) return
      setSchema(saved); setDrafts(draftsFrom(saved)); setCheck(null)
      setNotice('깊이 스키마를 저장했습니다. 이미 등록된 의뢰는 의뢰 화면의 재해석으로만 새 스키마가 적용됩니다.')
      onSaved?.(saved)
    } catch (error) {
      if (errorCode(error) === 'DEPTH_SCHEMA_CONFLICT') {
        await load().catch(() => undefined)
        setNotice('다른 관리자가 먼저 저장했습니다. 최신 스키마를 다시 불러왔으니 변경 내용을 다시 지정한 뒤 저장하세요.')
      } else setNotice(message(error, '저장하지 못했습니다.'))
    }
  })

  const current = samples[sampleKey]
  const levels = drafts ? (tab === 'UPPER' ? drafts.upper : drafts[tab]) : []
  const rows = depthRows<string>(levels, current?.levels ?? [])
  const options = (level: number): string[] => tab === 'UPPER' ? UPPER_ROLES : lowerRoleOptions(tab, level)
  const changeRole = (level: number, role: string) => {
    if (!drafts) return
    setCheck(null)
    if (tab === 'UPPER') setDrafts({ ...drafts, upper: setDepthRole(drafts.upper, level, role as DepthUpperRole | '') })
    else setDrafts({ ...drafts, [tab]: setDepthRole(drafts[tab], level, role as DepthLowerRole | '') })
  }
  const lowerEnv: FolderEnvironment | null = tab === 'UPPER' ? null : tab
  const requestLevel = drafts?.upper.length ?? 0
  const runOptions = current?.run_option_names ?? []
  const disabled = Boolean(busy) || !drafts

  return <article className="folder-environment-card depth-schema-editor">
    <div className="folder-environment-card-head"><div><h2>저장된 규칙</h2><p>Root로부터의 깊이마다 역할을 한 번 정합니다. 같은 깊이의 모든 폴더가 같은 역할을 갖습니다.</p></div>{schema && <span>{schema.schema_set_id}</span>}</div>
    <nav className="saved-work-tabs" aria-label="깊이 스키마 구간">{TABS.map(([key, title]) => <button type="button" key={key} className={tab === key ? 'active' : ''} aria-current={tab === key ? 'page' : undefined} disabled={Boolean(busy)} onClick={() => setTab(key)}>{title}{errors?.[key].length ? ' · 확인 필요' : ''}</button>)}</nav>
    {notice && <div className="folder-environment-notice info" role="status">{notice}</div>}
    {!drafts ? <p role="status">깊이 스키마를 불러오는 중…</p> : <>
      {lowerEnv && errors?.UPPER.length ? <p role="alert">상위 구조를 먼저 바르게 지정해야 예시 폴더를 볼 수 있습니다.</p> : null}
      <div className="depth-schema-table-wrap">
        <table className="depth-schema-table" aria-label={`${TABS.find(([key]) => key === tab)?.[1]} 깊이 표`}>
          <thead><tr><th scope="col">깊이</th><th scope="col">예시 폴더 (이름 ×개수)</th><th scope="col">폴더 수</th><th scope="col">역할</th></tr></thead>
          <tbody>
            {tab === 'UPPER'
              ? <tr className="depth-schema-fixed"><td>L0</td><td>Root (SPDM 저장소)</td><td>—</td><td>역할 없음</td></tr>
              : <tr className="depth-schema-fixed"><td>기준</td><td>의뢰 폴더 (상위 L{requestLevel}) · 의뢰명에 “{lowerEnv === 'USAGE' ? '사용' : '유통'}” 포함</td><td>{current ? `의뢰 ${current.requests_sampled}개 조사` : '—'}</td><td>의뢰</td></tr>}
            {rows.map((row) => {
              const locked = tab !== 'UPPER' && row.level === 1
              const editable = depthRowEditable(levels.length, row.level, locked)
              const canClear = row.inSchema && row.level === levels.length && !locked
              const isRunOption = lowerEnv === 'DISTRIBUTION' && row.role === 'RUN_OPTION'
              return <tr key={row.level} className={row.inSchema ? '' : 'depth-schema-content'}>
                <td>L{row.level}</td>
                <td>{locked ? <span>Working <small className="depth-schema-muted">(이름 고정)</small></span> : isRunOption
                  ? <div className="depth-schema-runoptions" aria-label="Run Option 이름 전체 목록">{runOptions.length ? <table><thead><tr><th scope="col">이름</th><th scope="col">폴더 수</th><th scope="col">의뢰 수</th></tr></thead><tbody>{runOptions.map((item) => <tr key={item.name}><td>{item.name}</td><td>{item.count}</td><td>{item.request_count}</td></tr>)}</tbody></table> : <span className="depth-schema-muted">{loadingSamples ? '불러오는 중…' : '발견된 Run Option 폴더가 없습니다.'}</span>}{current?.run_option_names_truncated ? <small>목록이 500개에서 잘렸습니다.</small> : null}</div>
                  : <SampleCell sample={row.sample} />}</td>
                <td>{row.sample?.folder_count ?? '—'}</td>
                <td>{editable
                  ? <select aria-label={`L${row.level} 역할`} value={row.role} disabled={disabled} onChange={(event) => changeRole(row.level, event.target.value)}>
                    {(canClear || !row.inSchema) && <option value="">{row.inSchema ? '깊이 삭제 (내용물)' : '내용물 (역할 없음)'}</option>}
                    {options(row.level).map((role) => <option key={role} value={role}>{roleLabel(role)}</option>)}
                  </select>
                  : <span className={row.inSchema ? '' : 'depth-schema-muted'}>{row.inSchema ? roleLabel(row.role) : '내용물'}</span>}</td>
              </tr>
            })}
            {tab === 'UPPER' && levels.length >= UPPER_MAX_LEVELS ? <tr className="depth-schema-content"><td colSpan={4}>상위 구조는 최대 {UPPER_MAX_LEVELS}단계입니다.</td></tr> : null}
          </tbody>
        </table>
      </div>
      {loadingSamples && <p className="depth-schema-muted" role="status">예시 폴더를 불러오는 중…</p>}
      {(errors?.[tab] ?? []).length > 0 && <ul className="depth-schema-errors" role="alert">{errors?.[tab].map((item) => <li key={item}>{item}</li>)}</ul>}
      {lowerEnv && <details className="depth-schema-final" open>
        <summary>Final 고정 구조 (읽기 전용)</summary>
        <table className="depth-schema-table"><thead><tr><th scope="col">깊이</th><th scope="col">폴더</th><th scope="col">역할</th></tr></thead>
          <tbody>{FINAL_STRUCTURE.map(([level, name, role]) => <tr key={level}><td>{level}</td><td>{name}</td><td>{role}</td></tr>)}</tbody></table>
        <small>.finalizations 폴더는 앱 메타 정보라 무시합니다. Final은 결과 판독 대상이 아닙니다.</small>
      </details>}
      {check && <section className="depth-schema-check" aria-label="확인 결과">
        <strong>{Object.values(check.by_code).reduce((sum, value) => sum + value, 0) ? '이탈 건수' : '이탈 없음'}</strong>
        <ul>{Object.entries(check.by_code).filter(([, count]) => count > 0).map(([code, count]) => <li key={code}><b>{count}</b> {deviationLabel(code)} <code>{code}</code></li>)}</ul>
        {check.examples.length > 0 && <details><summary>예시 {check.examples.length}건</summary><ul>{check.examples.map((item) => <li key={`${item.code}:${item.relative_path}`}><code>{item.relative_path}</code> · {deviationLabel(item.code)}</li>)}</ul></details>}
      </section>}
    </>}
    <footer className="folder-environment-footer"><span>저장해도 기존 등록 의뢰는 바뀌지 않습니다. 의뢰 화면의 재해석으로 적용하세요.</span>
      <button type="button" className="ghost-button" disabled={disabled || invalid} onClick={() => void runCheck()}>{busy === '확인 중…' ? busy : '확인'}</button>
      <button type="button" className="primary-button" disabled={disabled || invalid} onClick={() => void save()}>{busy === '저장 중…' ? busy : '저장'}</button>
    </footer>
  </article>
}
