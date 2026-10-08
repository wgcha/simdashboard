import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, FolderTree, LoaderCircle, Plus, X } from 'lucide-react'
import { Button } from '../../shared/components/Button'
import { Select } from '../../shared/components/Select'
import { ApiError } from '../../shared/api/errors'
import { resultDropApi, type DropEnvironment, type StructureEnvironment, type StructureOverview, type StructureResult } from '../../shared/api/resultDrop'
import { DRIVE_READ_ONLY_NOTICE, type DriveUploadBatch } from '../../shared/api/drive'
import { DriveBatchProgress } from '../../shared/components/DriveBatchProgress'
import './ResultStructureDialog.css'

const ENV_LABELS: Record<DropEnvironment, string> = { USAGE: '사용환경', DISTRIBUTION: '유통환경' }
const NEW = '__new__'
type Row = { id: number; name: string; source?: 'REGISTERED' | 'FOLDER' }
type EnvForm = { enabled: boolean; target: string; rows: Row[]; confirmOtherWr: boolean }
type Outcome = { result?: StructureResult; error?: string; problems?: Array<{ name: string; message: string }>; warnings?: string[]; otherWr?: boolean }

let rowId = 0
const row = (name = '', source?: Row['source']): Row => ({ id: ++rowId, name, source })

function detailOf(reason: unknown): Record<string, unknown> {
  return reason instanceof ApiError && reason.detail && typeof reason.detail === 'object' ? reason.detail as Record<string, unknown> : {}
}
function messageOf(reason: unknown, fallback: string) {
  const detail = detailOf(reason)
  if (typeof detail.message === 'string') return detail.message
  return reason instanceof Error ? reason.message.replace(/^[A-Z][A-Z0-9_]{2,}:\s*/, '') || fallback : fallback
}
function formFor(entry: StructureEnvironment): EnvForm {
  const target = entry.folder?.relative_path ?? (entry.proposal ? NEW : '')
  const rows = entry.case_suggestions.map((item) => row(item.name, item.source))
  return { enabled: Boolean(entry.folder) && entry.status !== 'SELECTED', target, rows: rows.length ? rows : [row()], confirmOtherWr: false }
}
const LINK_TEXT: Record<string, string> = {
  LINKED: '이 의뢰에 연결되어 있습니다. Case 결과에 반영합니다.',
  PENDING: '1분 안에 이 의뢰에 연결하고 Case 결과에 반영합니다.',
  NONE: '환경 키워드가 없는 폴더라 대시보드에 자동으로 연결하지 않습니다.',
}

/** "폴더 구조 만들기": per environment, the (empty) request folder → Working → Case folders of the selected request. */
export function ResultStructureDialog({ projectId, requestId, readOnly, onClose, onCreated }: {
  projectId: string; requestId: string; readOnly: boolean; onClose: () => void; onCreated: () => void
}) {
  const [overview, setOverview] = useState<StructureOverview | null>(null)
  const [forms, setForms] = useState<Partial<Record<DropEnvironment, EnvForm>>>({})
  const [loadError, setLoadError] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [outcomes, setOutcomes] = useState<Partial<Record<DropEnvironment, Outcome>>>({})

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoadError('')
    try {
      const value = await resultDropApi.structure({ project_id: projectId, request_id: requestId }, signal)
      setOverview(value)
      setForms(Object.fromEntries(value.environments.map((entry) => [entry.environment, formFor(entry)])))
    } catch (reason) {
      if (!signal?.aborted) setLoadError(messageOf(reason, '의뢰 폴더를 확인하지 못했습니다.'))
    }
  }, [projectId, requestId])
  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [load])
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape' && !busy) onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onClose])

  const update = (environment: DropEnvironment, change: (form: EnvForm) => EnvForm) => {
    setForms((current) => current[environment] ? { ...current, [environment]: change(current[environment] as EnvForm) } : current)
    setConfirm(false)
    setOutcomes((current) => ({ ...current, [environment]: undefined }))
  }
  // Choosing another existing folder re-reads its Working and Case folders (prefill follows the folder).
  const chooseTarget = async (environment: DropEnvironment, target: string) => {
    update(environment, (form) => ({ ...form, target, confirmOtherWr: false }))
    if (!target || target === NEW) return
    try {
      const value = await resultDropApi.structure({ project_id: projectId, request_id: requestId, environment, request_relative_path: target })
      const entry = value.environments.find((item) => item.environment === environment)
      if (!entry) return
      setOverview((current) => current ? { ...current, environments: current.environments.map((item) => item.environment === environment ? entry : item) } : value)
      update(environment, (form) => ({ ...form, target, rows: formFor(entry).rows }))
    } catch (reason) {
      setOutcomes((current) => ({ ...current, [environment]: { error: messageOf(reason, '폴더를 확인하지 못했습니다.') } }))
    }
  }

  const selected = useMemo(() => (overview?.environments ?? []).filter((entry) => forms[entry.environment]?.enabled && forms[entry.environment]?.target), [forms, overview])
  const create = async () => {
    if (!overview || !selected.length) return
    setBusy(true)
    let created = false
    let warned = false
    const next: Partial<Record<DropEnvironment, Outcome>> = {}
    for (const entry of selected) {
      const form = forms[entry.environment] as EnvForm
      const names = form.rows.map((item) => item.name.trim()).filter(Boolean)
      try {
        const result = await resultDropApi.createStructure({
          project_id: projectId, request_id: requestId, environment: entry.environment, case_names: names, confirm,
          confirm_other_wr: form.confirmOtherWr,
          request_relative_path: form.target === NEW ? null : form.target,
          new_request_folder: form.target === NEW && entry.proposal ? { parent_relative_path: entry.proposal.parent_relative_path, name: entry.proposal.name } : null,
        })
        next[entry.environment] = { result }
        created = created || result.created.length > 0 || Boolean(result.queued?.length)
      } catch (reason) {
        const detail = detailOf(reason)
        if (detail.code === 'RESULT_STRUCTURE_OTHER_WR') {
          next[entry.environment] = { error: messageOf(reason, '의뢰번호가 다른 폴더입니다.'), otherWr: true }
        } else if (detail.code === 'RESULT_STRUCTURE_NAME_WARNING' && Array.isArray(detail.warnings)) {
          next[entry.environment] = { warnings: detail.warnings as string[] }
          warned = true
        } else {
          next[entry.environment] = { error: messageOf(reason, '폴더를 만들지 못했습니다.'), problems: Array.isArray(detail.problems) ? detail.problems as Outcome['problems'] : undefined }
        }
      }
    }
    setOutcomes(next)
    setConfirm(warned)
    setBusy(false)
    if (created) onCreated()
  }
  const batchFinished = useCallback((_batch: DriveUploadBatch) => onCreated(), [onCreated])

  return <div className="result-structure-backdrop" role="presentation" onMouseDown={() => { if (!busy) onClose() }}>
    <section className="result-structure" role="dialog" aria-modal="true" aria-labelledby="result-structure-title" data-testid="result-structure-dialog" onMouseDown={(event) => event.stopPropagation()}>
      <header className="result-structure__head">
        <FolderTree aria-hidden="true" />
        <div><h2 id="result-structure-title">폴더 구조 만들기</h2>
          <p>환경별 의뢰 폴더 아래에 <code>Working</code>과 Case 폴더를 빈 폴더로 만듭니다. 그 아래(Scene·하중경우 등)는 해석자가 만듭니다. 이미 있는 폴더는 그대로 둡니다.</p></div>
        <Button size="sm" variant="ghost" onClick={onClose} disabled={busy} aria-label="닫기"><X aria-hidden="true" /></Button>
      </header>
      {readOnly ? <p className="result-structure__readonly" role="note" data-testid="result-structure-read-only">{DRIVE_READ_ONLY_NOTICE}</p> : null}
      {loadError ? <p className="result-structure__error" role="alert"><AlertTriangle aria-hidden="true" />{loadError}</p> : null}
      {!overview && !loadError ? <p className="result-structure__muted" role="status"><LoaderCircle className="result-structure__spin" aria-hidden="true" /> 의뢰 폴더를 확인하는 중</p> : null}
      {overview && !overview.project_folders.length ? <p className="result-structure__error" role="alert"><AlertTriangle aria-hidden="true" />이 프로젝트의 SPDM 프로젝트 폴더를 찾지 못했습니다. 관리자에게 폴더 연결을 요청하세요.</p> : null}
      {overview ? <div className="result-structure__envs">
        {overview.environments.map((entry) => <EnvironmentSection key={entry.environment} entry={entry} overview={overview} form={forms[entry.environment]} outcome={outcomes[entry.environment]} busy={busy}
          onToggle={(enabled) => update(entry.environment, (form) => ({ ...form, enabled }))}
          onTarget={(target) => void chooseTarget(entry.environment, target)}
          onRows={(rows) => update(entry.environment, (form) => ({ ...form, rows }))}
          onConfirmOtherWr={(value) => update(entry.environment, (form) => ({ ...form, confirmOtherWr: value }))}
          onBatchFinished={batchFinished} />)}
      </div> : null}
      <footer className="result-structure__foot">
        <span className="result-structure__muted">{selected.length ? `${selected.map((entry) => ENV_LABELS[entry.environment]).join('·')}에 만듭니다.` : '만들 환경을 고르세요.'}</span>
        <Button variant="ghost" onClick={onClose} disabled={busy}>닫기</Button>
        <Button variant="primary" onClick={() => void create()} disabled={busy || readOnly || !selected.length} title={readOnly ? DRIVE_READ_ONLY_NOTICE : undefined}>
          {busy ? <LoaderCircle className="result-structure__spin" aria-hidden="true" /> : null}{confirm ? '그래도 만들기' : '만들기'}</Button>
      </footer>
    </section>
  </div>
}

function EnvironmentSection({ entry, overview, form, outcome, busy, onToggle, onTarget, onRows, onConfirmOtherWr, onBatchFinished }: {
  entry: StructureEnvironment; overview: StructureOverview; form?: EnvForm; outcome?: Outcome; busy: boolean
  onToggle: (enabled: boolean) => void; onTarget: (target: string) => void; onRows: (rows: Row[]) => void
  onConfirmOtherWr: (value: boolean) => void; onBatchFinished: (batch: DriveUploadBatch) => void
}) {
  if (!form) return null
  const label = ENV_LABELS[entry.environment]
  const options = overview.candidates.filter((item) => item.owner !== 'OTHER' && !item.wr_other_request && (!item.linked_environment || item.linked_environment === entry.environment)
    && item.keyword_code !== 'ENV_KEYWORD_BOTH' && (item.environment === null || item.environment === entry.environment))
  const isNew = form.target === NEW
  const folder = !isNew && entry.folder?.relative_path === form.target ? entry.folder : null
  const existing = new Set((folder?.existing_cases ?? []).map((name) => name.toLowerCase()))
  const chosen = options.find((item) => item.relative_path === form.target)
  const noKeyword = chosen ? chosen.environment === null : false
  // A folder not linked to this request whose WR key differs from the request's needs an explicit confirmation.
  const otherWr = Boolean(chosen && !chosen.wr_match && !folder?.linked) || Boolean(outcome?.otherWr)
  const display = isNew ? entry.proposal?.display_path : (folder?.display_path ?? chosen?.display_path)
  const working = folder?.working_name ?? 'Working'
  const problemOf = new Map((outcome?.problems ?? []).map((item) => [item.name, item.message]))
  const statusText = entry.status === 'LINKED' ? '이 의뢰에 연결된 폴더' : entry.status === 'FOUND' ? '같은 의뢰번호의 폴더를 찾았습니다'
    : entry.status === 'AMBIGUOUS' ? `같은 의뢰번호의 폴더가 ${entry.choices.length}개입니다. 고르세요` : entry.status === 'SELECTED' ? '선택한 폴더' : '이 환경의 의뢰 폴더가 없습니다'
  return <section className={`result-structure__env${form.enabled ? ' is-on' : ''}`} data-testid={`result-structure-${entry.environment}`} aria-label={label}>
    <header>
      <label className="result-structure__toggle"><input type="checkbox" checked={form.enabled} disabled={busy || !form.target} onChange={(event) => onToggle(event.target.checked)} aria-label={`${label} 폴더 만들기`} /><b>{label}</b></label>
      <span className="result-structure__muted">{statusText}</span>
    </header>
    <label className="result-structure__field"><span>의뢰 폴더</span>
      <Select controlSize="sm" aria-label={`${label} 의뢰 폴더`} value={form.target} disabled={busy} onChange={(event) => onTarget(event.target.value)}>
        <option value="">고르세요</option>
        {options.map((item) => <option key={item.relative_path} value={item.relative_path}>{item.name}{item.environment === null ? ' (환경 키워드 없음)' : ''}{item.wr_match ? '' : ' · 다른 의뢰번호'}</option>)}
        {entry.folder && !options.some((item) => item.relative_path === entry.folder?.relative_path) ? <option value={entry.folder.relative_path}>{entry.folder.name}</option> : null}
        {entry.proposal ? <option value={NEW}>새로 만들기: {entry.proposal.name}</option> : null}
      </Select>
    </label>
    {!entry.proposal && entry.status === 'MISSING' ? <p className="result-structure__muted">의뢰번호로 새 의뢰 폴더 이름을 정하지 못했습니다. SPDM에서 의뢰 폴더를 만든 뒤 다시 여세요.</p> : null}
    {isNew && entry.proposal ? <p className="result-structure__new" data-testid={`result-structure-new-${entry.environment}`}>새 의뢰 폴더 <code>{entry.proposal.name}</code>을(를) 프로젝트 폴더 <code>{entry.proposal.parent_relative_path}</code> 아래에 만듭니다. 이름을 확인하세요.</p> : null}
    {noKeyword ? <p className="result-structure__warning" role="note"><AlertTriangle aria-hidden="true" />이 폴더 이름에는 '{entry.keyword}'가 없어 대시보드가 자동으로 읽지 못합니다. 이름은 바꾸지 않습니다.</p> : null}
    {otherWr ? <div className="result-structure__warning" role="note" data-testid={`result-structure-other-wr-${entry.environment}`}>
      <AlertTriangle aria-hidden="true" />
      <span>이 폴더의 의뢰번호({chosen?.wr_key ?? '없음'})가 이 의뢰({overview.wr_key ?? '의뢰번호 없음'})와 다릅니다. 이 의뢰의 폴더가 맞을 때만 만드세요. 만들면 이 의뢰에 연결됩니다.</span>
      <label><input type="checkbox" checked={form.confirmOtherWr} disabled={busy} onChange={(event) => onConfirmOtherWr(event.target.checked)} aria-label={`${label} 의뢰번호가 다른 폴더 확인`} /> 확인했습니다</label>
    </div> : null}
    {display ? <code className="result-structure__path" title={display}>{display}</code> : null}
    <div className="result-structure__tree" aria-label={`${label} 만들 폴더`}>
      <p><b>{working}</b> <small>{folder?.working_exists ? '이미 있음' : '새로 만듦'}</small></p>
      <ul>
        {form.rows.map((item, index) => {
          const exists = existing.has(item.name.trim().toLowerCase())
          const problem = problemOf.get(item.name.trim())
          return <li key={item.id} className={problem ? 'is-error' : undefined}>
            <input aria-label={`${label} Case ${index + 1}`} value={item.name} maxLength={255} placeholder="Case 폴더 이름" disabled={busy}
              onChange={(event) => onRows(form.rows.map((other) => other.id === item.id ? { ...other, name: event.target.value } : other))} />
            {exists ? <small className="result-structure__tag">이미 있음</small> : item.source === 'REGISTERED' ? <small className="result-structure__tag">등록된 Case</small> : null}
            <Button size="sm" variant="ghost" aria-label={`${label} Case ${index + 1} 빼기`} disabled={busy} onClick={() => onRows(form.rows.length > 1 ? form.rows.filter((other) => other.id !== item.id) : [row()])}><X aria-hidden="true" /></Button>
            {problem ? <span className="result-structure__problem" role="alert">{problem}</span> : null}
          </li>
        })}
      </ul>
      <Button size="sm" variant="ghost" disabled={busy || form.rows.length >= overview.max_cases} onClick={() => onRows([...form.rows, row()])}><Plus aria-hidden="true" />Case 추가</Button>
    </div>
    {outcome?.error ? <p className="result-structure__error" role="alert"><AlertTriangle aria-hidden="true" />{outcome.error}</p> : null}
    {outcome?.warnings?.map((text) => <p key={text} className="result-structure__warning" role="status"><AlertTriangle aria-hidden="true" />{text}</p>)}
    {outcome?.result ? <Result result={outcome.result} onBatchFinished={onBatchFinished} /> : null}
  </section>
}

function Result({ result, onBatchFinished }: { result: StructureResult; onBatchFinished: (batch: DriveUploadBatch) => void }) {
  const made = result.queued ?? result.created
  return <div className="result-structure__done" data-testid={`result-structure-done-${result.environment}`} role="status">
    <p><CheckCircle2 aria-hidden="true" /><strong>{result.drive ? `드라이브에 폴더 ${made.length}개를 만드는 중` : made.length ? `폴더 ${made.length}개를 만들었습니다` : '모두 이미 있습니다'}</strong>
      {result.existing.length ? ` · 이미 있음 ${result.existing.length}개` : ''}</p>
    {made.length || result.existing.length ? <ul>
      {made.map((item) => <li key={item.relative_path}><code>{item.name}</code> <small>{result.drive ? '대기열' : '만듦'}</small></li>)}
      {result.existing.map((item) => <li key={item.relative_path}><code>{item.name}</code> <small>이미 있음</small></li>)}
    </ul> : null}
    {result.drive ? <DriveBatchProgress batchId={result.drive.batch_id} initial={result.drive} onFinished={onBatchFinished} /> : null}
    <p className="result-structure__muted">{result.link.status === 'REVIEW' ? `대시보드 연결 확인 필요: ${result.link.message ?? result.link.code ?? ''}` : LINK_TEXT[result.link.status] ?? ''}</p>
  </div>
}
