import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Download, LoaderCircle, Settings2 } from 'lucide-react'
import { reportApi } from '../../../shared/api/reportLayouts'
import type { ReportLayout, ReportLayoutDefinition } from '../../../types'
import { buildCaseReportContents, buildCaseReportHtml, buildCaseReportPptx, caseReportFileName, caseReportOverview, loadCaseReport, loadCaseReportImages, prepareCaseReportLayout, type CaseReportData, type CaseReportFormat, type CaseReportScope } from './caseReport'

const ReportLayoutEditor = lazy(() => import('../../../shared/reports/ReportLayoutEditor').then(({ ReportLayoutEditor: Editor }) => ({ default: Editor })))

type Props = { scope: CaseReportScope; onClose: () => void }

const CAPS_TEXT = '이미지 전체 300MB, 영상당 20MB·전체 200MB까지 넣습니다(원본 크기 기준). HTML은 base64로 약 1.33배 커집니다.'

function errorText(reason: unknown, fallback: string) { return reason instanceof Error && reason.message ? reason.message : fallback }

function downloadBlob(blob: Blob, fileName: string) {
  const href = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = href; anchor.download = fileName; anchor.rel = 'noopener'
  document.body.appendChild(anchor); anchor.click(); anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(href), 60_000)
}

function scopeItems(scope: CaseReportScope): Array<[string, string]> {
  if (scope.source.kind === 'case_usage') {
    const labels = scope.labels as Extract<CaseReportScope, { source: { kind: 'case_usage' } }>['labels']
    return [['Case', labels.caseLabel], ['환경', '사용환경'], ['Reference', labels.reference || '미선택']]
  }
  const labels = scope.labels as Extract<CaseReportScope, { source: { kind: 'case_results' } }>['labels']
  return [['Case', labels.caseLabel], ['하중경우', labels.loadCase], ['Run Case', labels.run], ['Run Option', labels.option], ['Component · 기준', [labels.component, labels.basis].filter(Boolean).join(' · ')]]
}

/** Report dialog for the Case results selection fixed at open time. */
export function CaseReportDialog({ scope, onClose }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const generation = useRef(0)
  const [data, setData] = useState<CaseReportData | null>(null)
  const [loadError, setLoadError] = useState('')
  const [formats, setFormats] = useState<Record<CaseReportFormat, boolean>>({ pptx: true, html: false })
  const [includeVideos, setIncludeVideos] = useState(false)
  const [layouts, setLayouts] = useState<ReportLayout[]>([])
  const [layout, setLayout] = useState<ReportLayoutDefinition | null>(null)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [skipped, setSkipped] = useState<{ videos: string[]; images: string[] }>({ videos: [], images: [] })
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(() => { const dialog = dialogRef.current; if (dialog && !dialog.open) dialog.showModal() }, [])
  useEffect(() => () => controllerRef.current?.abort(), [])

  // Everything below reads the scope captured when the dialog opened; later
  // selection changes on the page do not reach this dialog.
  useEffect(() => {
    const controller = new AbortController()
    Promise.all([loadCaseReport(scope, { signal: controller.signal }), reportApi.layouts().catch(() => []), import('../../../reportExport')]).then(([recipe, storedLayouts, reportModule]) => {
      if (controller.signal.aborted) return
      const selected = storedLayouts.find((item) => item.id === 'report-layout-standard')?.definition ?? storedLayouts[0]?.definition ?? reportModule.DEFAULT_REPORT_LAYOUT
      setLayouts(storedLayouts); setData(recipe)
      setLayout(prepareCaseReportLayout(reportModule.normalizeReportLayout(selected), recipe.source, buildCaseReportContents(recipe), true))
    }).catch((reason) => { if (!controller.signal.aborted) setLoadError(errorText(reason, '보고서 자료를 불러오지 못했습니다.')) })
    return () => controller.abort()
  }, [scope])

  const contents = useMemo(() => data ? buildCaseReportContents(data) : [], [data])
  const coverLabels = useMemo(() => {
    const value = (label: string) => data?.scopeRows.find((row) => row.label === label)?.value ?? ''
    return { project: value('프로젝트'), request: value('의뢰'), loadCase: value('하중경우') }
  }, [data])
  const overview = useMemo(() => data ? caseReportOverview(data, coverLabels) : null, [data, coverLabels])
  const chosen = (Object.keys(formats) as CaseReportFormat[]).filter((format) => formats[format])
  const close = () => { controllerRef.current?.abort(); dialogRef.current?.close(); onClose() }
  const cancelBuild = () => { if (!busy) return; generation.current += 1; controllerRef.current?.abort(); setBusy(false); setNotice('보고서 만들기를 취소했습니다.') }

  const selectLayout = async (layoutId: string) => {
    const stored = layouts.find((item) => item.id === layoutId)
    if (!stored || !data) return
    const reportModule = await import('../../../reportExport')
    setLayout(prepareCaseReportLayout(reportModule.normalizeReportLayout(stored.definition), data.source, contents))
  }
  // Only "save as new" from this dialog: the current (possibly system or
  // template-bound) layout is never versioned here, and the saved copy keeps
  // its PPTX template binding (overridden only when this report renders).
  const saveAsNew = async () => {
    if (!layout) return
    setError('')
    try {
      const payload = { name: layout.name, description: layout.description, definition: layout, updated_by: '보고서 편집자' }
      const saved = await reportApi.createLayout(payload)
      setLayouts(await reportApi.layouts()); setLayout(saved.definition); setNotice(`${saved.name} v${saved.version} 새 레이아웃으로 저장 완료`)
    } catch (reason) { setError(errorText(reason, '보고서 레이아웃을 저장하지 못했습니다.')) }
  }

  const download = async () => {
    if (!data || !layout || !chosen.length) return
    const run = ++generation.current
    controllerRef.current?.abort()
    const controller = new AbortController(); controllerRef.current = controller
    setBusy(true); setError(''); setNotice(''); setSkipped({ videos: [], images: [] })
    try {
      const files: Array<{ blob: Blob; name: string }> = []
      let skippedVideos: string[] = []
      // Images are read once and shared by both formats.
      const images = await loadCaseReportImages(data, { signal: controller.signal })
      if (formats.pptx) files.push({ blob: await buildCaseReportPptx(data, { layout, labels: coverLabels, images, signal: controller.signal }), name: caseReportFileName(data.caseLabel, data.generatedAt, 'pptx') })
      if (formats.html) {
        const html = await buildCaseReportHtml(data, { includeVideos, images, signal: controller.signal })
        skippedVideos = html.skippedVideos
        files.push({ blob: html.blob, name: caseReportFileName(data.caseLabel, data.generatedAt, 'html') })
      }
      if (controller.signal.aborted || run !== generation.current) return
      for (const file of files) downloadBlob(file.blob, file.name)
      setSkipped({ videos: skippedVideos, images: images.skipped })
      setNotice(`${files.map((file) => file.name).join(', ')} 다운로드`)
    } catch (reason) {
      if (!controller.signal.aborted && run === generation.current) setError(errorText(reason, '보고서를 만들지 못했습니다.'))
    } finally { if (run === generation.current) setBusy(false) }
  }

  const isSystem = Boolean(layout && layouts.find((item) => item.id === layout.id)?.is_system)
  const templateNote = layout?.templateSource === 'pptx_upload' ? '이 레이아웃에 연결된 업로드 PPTX 템플릿은 Case 결과 보고서에 적용되지 않습니다. 화면 레이아웃으로 만들며, 저장한 사본에는 템플릿 연결이 그대로 남습니다.' : undefined
  const source = scope.source
  return <dialog ref={dialogRef} className="case-report__dialog" aria-labelledby="case-report-title" data-testid="case-report-dialog" data-case-id={source.caseId} data-capture-id={source.captureId} data-environment={source.kind === 'case_usage' ? 'USAGE' : 'DISTRIBUTION'} onCancel={(event) => { event.preventDefault(); if (busy) cancelBuild(); else close() }}>
    <header className="case-report__heading">
      <div><h3 id="case-report-title">Case 결과 보고서</h3><p title={scope.labels.caseLabel}>{scope.labels.caseLabel}</p></div>
      <button type="button" className="case-report__close" aria-label="닫기" disabled={busy} onClick={close}>×</button>
    </header>
    <div className="case-report__body">
      <dl className="case-report__scope" aria-label="보고서 범위">
        {scopeItems(scope).map(([label, value]) => <div key={label}><dt>{label}</dt><dd title={value || '없음'}>{value || '없음'}</dd></div>)}
      </dl>
      <fieldset className="case-report__formats">
        <legend>형식</legend>
        <label><input type="checkbox" checked={formats.pptx} disabled={busy} onChange={(event) => setFormats((current) => ({ ...current, pptx: event.target.checked }))} />PPTX</label>
        <label><input type="checkbox" checked={formats.html} disabled={busy} onChange={(event) => setFormats((current) => ({ ...current, html: event.target.checked }))} />HTML</label>
        <label className="case-report__videos" title={CAPS_TEXT}><input type="checkbox" checked={includeVideos} disabled={busy || !formats.html} onChange={(event) => setIncludeVideos(event.target.checked)} />영상 포함</label>
        {!chosen.length ? <span className="case-report__hint" role="status">형식을 하나 이상 선택하세요.</span> : null}
        {formats.html ? <p className="case-report__caps">{CAPS_TEXT}</p> : null}
      </fieldset>
      {formats.pptx && layout ? <div className="case-report__layout">
        <label><span>PPTX 레이아웃</span><select value={layout.id} disabled={busy} onChange={(event) => void selectLayout(event.target.value)}>{layouts.some((item) => item.id === layout.id) ? null : <option value={layout.id}>{layout.name}</option>}{layouts.map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.version}</option>)}</select></label>
        <button type="button" className="case-report__secondary" aria-expanded={editing} disabled={busy} onClick={() => setEditing((current) => !current)}><Settings2 size={15} aria-hidden="true" />{editing ? '편집 닫기' : '레이아웃 편집'}</button>
      </div> : null}
      {formats.pptx && editing && layout && overview ? <div className="case-report__editor" data-testid="case-report-layout-editor"><Suspense fallback={<p className="case-report__hint">편집 화면을 준비하고 있습니다.</p>}><ReportLayoutEditor layout={layout} overview={overview} contents={contents} templates={[]} isSystem={isSystem} restricted={{ templateNote }} onChange={setLayout} onTemplateUpload={() => undefined} onTemplateDelete={() => undefined} onSave={() => void saveAsNew()} onSaveAs={() => void saveAsNew()} onDelete={() => undefined} /></Suspense></div> : null}
      {data ? <p className="case-report__counts" data-testid="case-report-counts">{data.sections.length > 1 ? `구성 ${data.sections.length}개 · ` : ''}{data.environment === 'USAGE' ? `평가 ${data.sections[0]?.summary.rows.length ?? 0}행` : `Scene ${data.sections.reduce((total, section) => total + (section.sceneTable?.rows.length ?? 0), 0)}개`} · 이미지 {data.sections.reduce((total, section) => total + section.images.length, 0)}개 · 영상 {data.sections.reduce((total, section) => total + section.videos.length, 0)}개</p> : !loadError ? <p className="case-report__hint" role="status"><LoaderCircle size={14} className="case-report__spinner" aria-hidden="true" />보고서 자료를 불러오는 중입니다.</p> : null}
      {loadError ? <p className="case-report__message case-report__message--error" role="alert"><AlertTriangle size={14} aria-hidden="true" />{loadError}</p> : null}
      {error ? <p className="case-report__message case-report__message--error" role="alert"><AlertTriangle size={14} aria-hidden="true" />{error}</p> : null}
      {notice ? <p className="case-report__message" role="status">{notice}</p> : null}
      {busy ? <p className="case-report__hint" role="status" data-testid="case-report-building"><LoaderCircle size={14} className="case-report__spinner" aria-hidden="true" />보고서를 만드는 중입니다. 취소하거나 Esc를 누르면 멈춥니다.</p> : null}
      {skipped.images.length ? <div className="case-report__skipped" role="status" data-testid="case-report-skipped-images"><strong>용량 제한 등으로 넣지 못한 이미지 {skipped.images.length}개</strong><ul>{skipped.images.map((item, index) => <li key={`${index}:${item}`} title={item}>{item}</li>)}</ul></div> : null}
      {skipped.videos.length ? <div className="case-report__skipped" role="status" data-testid="case-report-skipped"><strong>용량 제한 등으로 HTML에 넣지 못한 영상 {skipped.videos.length}개</strong><ul>{skipped.videos.map((item, index) => <li key={`${index}:${item}`} title={item}>{item}</li>)}</ul></div> : null}
    </div>
    <footer className="case-report__actions">
      {busy ? <button type="button" className="case-report__secondary" onClick={cancelBuild}>취소</button> : <button type="button" className="case-report__secondary" onClick={close}>닫기</button>}
      <button type="button" className="case-report__primary" disabled={busy || !data || !layout || !chosen.length} onClick={() => void download()}>{busy ? <LoaderCircle size={15} className="case-report__spinner" aria-hidden="true" /> : <Download size={15} aria-hidden="true" />}다운로드</button>
    </footer>
  </dialog>
}
