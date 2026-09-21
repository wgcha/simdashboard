import { useMemo, useState } from 'react'
import type { UsageSourceReview, UsageSourceReviewEntry } from '../../shared/api/folderEnvironment'

export type UsageReviewSelection = { json: boolean; video: boolean; image: boolean; csv: boolean }
export type UsageReviewDraft = { selection: UsageReviewSelection; selectedSources: Record<string, string>; metricPaths: Record<string, string[]>; excludes: Record<string, string>; acknowledgedPartial: boolean; dirty: boolean }

export const defaultUsageReviewDraft = (): UsageReviewDraft => ({ selection: { json: true, video: true, image: true, csv: false }, selectedSources: {}, metricPaths: {}, excludes: {}, acknowledgedPartial: false, dirty: false })

type Props = { review: UsageSourceReview; draft: UsageReviewDraft; onChange: (next: UsageReviewDraft) => void; disabled?: boolean }
type Metric = UsageSourceReviewEntry['metrics'][number]

function metricId(entry: UsageSourceReviewEntry, metric: Metric) { return `${entry.evaluation}:${entry.direction || 'common'}:${metric.key}` }
function sourceId(entry: UsageSourceReviewEntry) { return `${entry.evaluation}:${entry.direction || 'common'}` }
function statusLabel(status: string) { return ['READY', 'CONFIRMED', 'OK'].includes(status) ? '확인' : ['MISSING_SOURCE', 'EXCLUDED'].includes(status) ? '자료 없음' : '검수 필요' }
function valueText(value: unknown) { return value == null ? '—' : typeof value === 'string' ? value : JSON.stringify(value) }
function basename(path: string) { return path.split('/').at(-1) ?? path }

function leafPaths(value: unknown, path: string[] = [], output: string[][] = []): string[][] {
  if (output.length >= 120 || path.length >= 8) return output
  if (value === null || typeof value !== 'object' || Array.isArray(value)) { if (path.length) output.push(path); return output }
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    leafPaths(child, [...path, key], output)
    if (output.length >= 120) break
  }
  return output
}

export function UsageSourceReviewPanel({ review, draft, onChange, disabled = false }: Props) {
  const [onlyIssues, setOnlyIssues] = useState(false)
  const entries = review.entries ?? []
  const rows = useMemo(() => entries.flatMap((entry) => entry.metrics.map((metric) => ({ entry, metric }))), [entries])
  const issues = rows.filter(({ metric }) => !['READY', 'CONFIRMED', 'OK'].includes(metric.status))
  const partial = issues.some(({ metric }) => ['MISSING_SOURCE', 'EXCLUDED'].includes(metric.status)) || Object.keys(draft.excludes).length > 0
  const change = (patch: Partial<UsageReviewDraft>) => onChange({ ...draft, ...patch, dirty: true, acknowledgedPartial: patch.acknowledgedPartial ?? false })
  const changeExclude = (id: string, reason: string) => {
    const excludes = { ...draft.excludes }
    if (reason.trim()) excludes[id] = reason
    else delete excludes[id]
    change({ excludes })
  }
  return <section className="folder-source-review" aria-label="파일·값 검수">
    <header><div><h3>파일·값 검수</h3><p>평가별 원본, JSON 키 경로와 값을 확인합니다. 변경 후에는 다시 검수하세요.</p></div><label><input type="checkbox" checked={onlyIssues} onChange={(event) => setOnlyIssues(event.target.checked)} /> 확인 필요만 보기</label></header>
    <fieldset disabled={disabled}><div className="folder-source-review-form">{(['json', 'video', 'image', 'csv'] as const).map((format) => <label key={format}><input type="checkbox" checked={draft.selection[format]} onChange={(event) => change({ selection: { ...draft.selection, [format]: event.target.checked } })} />{format === 'json' ? 'JSON 수치' : format === 'video' ? '영상' : format === 'image' ? '이미지' : 'CSV (고급)'}</label>)}<span>{draft.dirty ? '변경됨 · 다시 검수 필요' : '검수 결과 기준'}</span></div>
    <div className="folder-source-review-summary">선택 {review.entries?.filter((entry) => entry.source).length ?? 0}개 · 제외 {review.excluded_count ?? 0}개 · 확인 필요 {review.blocking_count ?? issues.length}개</div>
    <div className="folder-source-review-table"><div className="folder-source-review-row heading"><span>평가 / 방향</span><span>선택 파일</span><span>원문 키</span><span>값 · 타입</span><span>상태</span></div>
      {rows.filter(({ metric }) => !onlyIssues || !['READY', 'CONFIRMED', 'OK'].includes(metric.status)).map(({ entry, metric }) => {
        const id = metricId(entry, metric); const selectedPath = draft.metricPaths[id] ?? metric.path; const availablePaths = leafPaths(entry.values)
        const source = draft.selectedSources[sourceId(entry)] ?? entry.source ?? ''
        const sourceOptions = entry.candidates ?? []
        const canExclude = !['READY', 'CONFIRMED', 'OK', 'MISSING_SOURCE', 'EXCLUDED'].includes(metric.status)
        return <div className={`folder-source-review-row ${metric.status === 'READY' ? '' : 'needs-review'}`} key={id}><span><b>{entry.evaluation}</b><small>{entry.direction || 'common'} · {metric.key}</small></span><span title={source || undefined}>{sourceOptions.length > 1 ? <select aria-label={`${id} 원본 선택`} value={source} onChange={(event) => change({ selectedSources: { ...draft.selectedSources, [sourceId(entry)]: event.target.value } })}><option value="">선택 필요</option>{sourceOptions.map((candidate) => <option key={candidate} value={candidate}>{basename(candidate)}</option>)}</select> : source ? basename(source) : '자료 없음'}</span><span><select aria-label={`${id} 실제 키`} value={JSON.stringify(selectedPath)} onChange={(event) => change({ metricPaths: { ...draft.metricPaths, [id]: JSON.parse(event.target.value) as string[] } })}><option value={JSON.stringify(metric.path)}>{metric.path.join(' / ') || metric.key}</option>{availablePaths.filter((path) => JSON.stringify(path) !== JSON.stringify(metric.path)).map((path) => <option key={JSON.stringify(path)} value={JSON.stringify(path)}>{path.join(' / ')}</option>)}</select></span><span><b>{valueText(metric.value)}</b><small>{metric.value_type ?? 'type 미확인'} · 기대 {metric.expected_type ?? '값'}</small></span><span className={`folder-source-review-status ${metric.status === 'READY' ? '' : 'review'}`}>{statusLabel(metric.status)}{canExclude ? <input aria-label={`${id} 제외 사유`} placeholder="제외 사유 (필수)" value={draft.excludes[id] ?? ''} onChange={(event) => changeExclude(id, event.target.value)} /> : null}{metric.status === 'EXCLUDED' && draft.excludes[id] ? <button type="button" className="link-button" onClick={() => changeExclude(id, '')}>제외 취소</button> : null}{metric.exclude_reason ? <small>{metric.exclude_reason}</small> : null}</span></div>
      })}</div>
    {partial ? <label className="folder-source-review-partial"><input type="checkbox" checked={draft.acknowledgedPartial} onChange={(event) => change({ acknowledgedPartial: event.target.checked })} /> 일부 결과는 자료 없음 또는 제외됨을 확인했습니다.</label> : null}
    {review.excluded_files?.length ? <details className="folder-source-review-excluded"><summary>선택 제외 파일 {review.excluded_files.length}개</summary>{review.excluded_files.map((file) => <p key={file.relative_path} title={file.relative_path}>{basename(file.relative_path)} · {file.reason}</p>)}</details> : null}
    {review.message ? <p className="folder-source-review-message" role="status">{review.message}</p> : null}</fieldset>
  </section>
}
