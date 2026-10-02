import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Download, LoaderCircle, Settings2 } from 'lucide-react'
import { api } from '../../../api'
import { reportApi } from '../../../shared/api/reportLayouts'
import type { ReportLayout, ReportLayoutDefinition, ReportTemplateAsset } from '../../../types'
import { buildCaseReportContents, buildCaseReportData, buildCaseReportHtml, buildCaseReportPptx, caseReportFileName, caseReportOverview, loadCaseReportSources, prepareCaseReportLayout, type CaseReportData, type CaseReportFormat, type CaseReportScope } from './caseReport'

const ReportLayoutEditor = lazy(() => import('../../../shared/reports/ReportLayoutEditor').then(({ ReportLayoutEditor: Editor }) => ({ default: Editor })))

type Props = { scope: CaseReportScope; onClose: () => void }

function errorText(reason: unknown, fallback: string) { return reason instanceof Error && reason.message ? reason.message : fallback }

function downloadBlob(blob: Blob, fileName: string) {
  const href = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = href; anchor.download = fileName; anchor.rel = 'noopener'
  document.body.appendChild(anchor); anchor.click(); anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(href), 60_000)
}

/** Report dialog for the Case results selection fixed at open time. */
export function CaseReportDialog({ scope, onClose }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const generation = useRef(0)
  const [data, setData] = useState<CaseReportData | null>(null)
  const [labels, setLabels] = useState({ project: scope.labels.project, request: scope.labels.request, loadCase: scope.labels.loadCase })
  const [loadError, setLoadError] = useState('')
  const [formats, setFormats] = useState<Record<CaseReportFormat, boolean>>({ pptx: true, html: false })
  const [includeVideos, setIncludeVideos] = useState(false)
  const [layouts, setLayouts] = useState<ReportLayout[]>([])
  const [layout, setLayout] = useState<ReportLayoutDefinition | null>(null)
  const [templates, setTemplates] = useState<ReportTemplateAsset[]>([])
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [skipped, setSkipped] = useState<string[]>([])
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(() => { const dialog = dialogRef.current; if (dialog && !dialog.open) dialog.showModal() }, [])
  useEffect(() => () => controllerRef.current?.abort(), [])

  // Everything below reads the scope captured when the dialog opened; later
  // selection changes on the page do not reach this dialog.
  useEffect(() => {
    const controller = new AbortController()
    const source = scope.source
    const named = Promise.all([
      api.projects().then((items) => items.find((item) => item.id === source.projectId)?.name).catch(() => undefined),
      api.requests(source.projectId).then((items) => items.find((item) => item.id === source.requestId)?.title).catch(() => undefined),
    ])
    Promise.all([loadCaseReportSources(source, controller.signal), named, reportApi.layouts().catch(() => []), api.reportTemplates().catch(() => []), import('../../../reportExport')]).then(([sources, [project, request], storedLayouts, storedTemplates, reportModule]) => {
      if (controller.signal.aborted) return
      const nextLabels = { ...scope.labels, project: project || scope.labels.project, request: request || scope.labels.request }
      const recipe = buildCaseReportData({ scope: { source, labels: nextLabels }, distribution: sources.distribution, videos: sources.videos, generatedAt: new Date() })
      const selected = storedLayouts.find((item) => item.id === 'report-layout-standard')?.definition ?? storedLayouts[0]?.definition ?? reportModule.DEFAULT_REPORT_LAYOUT
      setLabels({ project: nextLabels.project, request: nextLabels.request, loadCase: nextLabels.loadCase })
      setLayouts(storedLayouts); setTemplates(storedTemplates); setData(recipe)
      setLayout(prepareCaseReportLayout(reportModule.normalizeReportLayout(selected), source, buildCaseReportContents(recipe), true))
    }).catch((reason) => { if (!controller.signal.aborted) setLoadError(errorText(reason, '보고서 자료를 불러오지 못했습니다.')) })
    return () => controller.abort()
  }, [scope])

  const contents = useMemo(() => data ? buildCaseReportContents(data) : [], [data])
  const overview = useMemo(() => data ? caseReportOverview(data, labels) : null, [data, labels])
  const chosen = (Object.keys(formats) as CaseReportFormat[]).filter((format) => formats[format])
  const close = () => { controllerRef.current?.abort(); dialogRef.current?.close(); onClose() }

  const selectLayout = async (layoutId: string) => {
    const stored = layouts.find((item) => item.id === layoutId)
    if (!stored || !data) return
    const reportModule = await import('../../../reportExport')
    setLayout(prepareCaseReportLayout(reportModule.normalizeReportLayout(stored.definition), data.source, contents))
  }
  const saveLayout = async (asNew: boolean) => {
    if (!layout) return
    setError('')
    try {
      const payload = { name: layout.name, description: layout.description, definition: layout, updated_by: '보고서 편집자' }
      const saved = asNew ? await reportApi.createLayout(payload) : await reportApi.updateLayout(layout.id, payload)
      setLayouts(await reportApi.layouts()); setLayout(saved.definition); setNotice(`${saved.name} v${saved.version} 저장 완료`)
    } catch (reason) { setError(errorText(reason, '보고서 레이아웃을 저장하지 못했습니다.')) }
  }
  const deleteLayout = async () => {
    if (!layout || !data || layouts.find((item) => item.id === layout.id)?.is_system) return
    try {
      await reportApi.deleteLayout(layout.id); const next = await reportApi.layouts(); setLayouts(next)
      if (next[0]) await selectLayoutFrom(next[0].definition)
    } catch (reason) { setError(errorText(reason, '보고서 레이아웃을 삭제하지 못했습니다.')) }
  }
  const selectLayoutFrom = async (definition: ReportLayoutDefinition) => {
    if (!data) return
    const reportModule = await import('../../../reportExport')
    setLayout(prepareCaseReportLayout(reportModule.normalizeReportLayout(definition), data.source, contents))
  }
  const uploadTemplate = async (file: File) => {
    try { const created = await api.uploadReportTemplate(file.name.replace(/\.pptx$/i, ''), file); setTemplates((items) => [created, ...items]); setNotice(`${created.name} 템플릿을 등록했습니다.`) } catch (reason) { setError(errorText(reason, 'PPTX 템플릿을 업로드하지 못했습니다.')) }
  }
  const deleteTemplate = async (templateId: string) => {
    try { await api.deleteReportTemplate(templateId); setTemplates((items) => items.filter((item) => item.id !== templateId)) } catch (reason) { setError(errorText(reason, 'PPTX 템플릿을 삭제하지 못했습니다.')) }
  }

  const download = async () => {
    if (!data || !layout || !chosen.length) return
    const run = ++generation.current
    controllerRef.current?.abort()
    const controller = new AbortController(); controllerRef.current = controller
    setBusy(true); setError(''); setNotice(''); setSkipped([])
    try {
      const files: Array<{ blob: Blob; name: string }> = []
      let skippedVideos: string[] = []
      if (formats.pptx) files.push({ blob: await buildCaseReportPptx(data, { layout, labels, signal: controller.signal }), name: caseReportFileName(data.caseLabel, data.generatedAt, 'pptx') })
      if (formats.html) {
        const html = await buildCaseReportHtml(data, { includeVideos, signal: controller.signal })
        skippedVideos = html.skippedVideos
        files.push({ blob: html.blob, name: caseReportFileName(data.caseLabel, data.generatedAt, 'html') })
      }
      if (controller.signal.aborted || run !== generation.current) return
      for (const file of files) downloadBlob(file.blob, file.name)
      setSkipped(skippedVideos)
      setNotice(`${files.map((file) => file.name).join(', ')} 다운로드`)
    } catch (reason) {
      if (!controller.signal.aborted) setError(errorText(reason, '보고서를 만들지 못했습니다.'))
    } finally { if (run === generation.current) setBusy(false) }
  }

  const isSystem = Boolean(layout && layouts.find((item) => item.id === layout.id)?.is_system)
  const source = scope.source
  return <dialog ref={dialogRef} className="case-report__dialog" aria-labelledby="case-report-title" data-testid="case-report-dialog" data-case-id={source.caseId} data-capture-id={source.captureId} onCancel={(event) => { event.preventDefault(); if (!busy) close() }}>
    <header className="case-report__heading">
      <div><h3 id="case-report-title">Case 결과 보고서</h3><p title={scope.labels.caseLabel}>{scope.labels.caseLabel}</p></div>
      <button type="button" className="case-report__close" aria-label="닫기" disabled={busy} onClick={close}>×</button>
    </header>
    <div className="case-report__body">
      <dl className="case-report__scope" aria-label="보고서 범위">
        {[['Case', scope.labels.caseLabel], ['하중경우', scope.labels.loadCase], ['Run Case', scope.labels.run], ['Run Option', scope.labels.option], ['Component · 기준', [scope.labels.component, scope.labels.basis].filter(Boolean).join(' · ')]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd title={value || '없음'}>{value || '없음'}</dd></div>)}
      </dl>
      <fieldset className="case-report__formats">
        <legend>형식</legend>
        <label><input type="checkbox" checked={formats.pptx} disabled={busy} onChange={(event) => setFormats((current) => ({ ...current, pptx: event.target.checked }))} />PPTX</label>
        <label><input type="checkbox" checked={formats.html} disabled={busy} onChange={(event) => setFormats((current) => ({ ...current, html: event.target.checked }))} />HTML</label>
        <label className="case-report__videos" title="영상당 20MB, 전체 200MB까지 포함합니다. 넘는 영상은 파일 이름으로 대신합니다."><input type="checkbox" checked={includeVideos} disabled={busy || !formats.html} onChange={(event) => setIncludeVideos(event.target.checked)} />영상 포함</label>
        {!chosen.length ? <span className="case-report__hint" role="status">형식을 하나 이상 선택하세요.</span> : null}
      </fieldset>
      {formats.pptx && layout ? <div className="case-report__layout">
        <label><span>PPTX 레이아웃</span><select value={layout.id} disabled={busy} onChange={(event) => void selectLayout(event.target.value)}>{layouts.some((item) => item.id === layout.id) ? null : <option value={layout.id}>{layout.name}</option>}{layouts.map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.version}</option>)}</select></label>
        <button type="button" className="case-report__secondary" aria-expanded={editing} disabled={busy} onClick={() => setEditing((current) => !current)}><Settings2 size={15} aria-hidden="true" />{editing ? '편집 닫기' : '레이아웃 편집'}</button>
      </div> : null}
      {formats.pptx && editing && layout && overview ? <div className="case-report__editor" data-testid="case-report-layout-editor"><Suspense fallback={<p className="case-report__hint">편집 화면을 준비하고 있습니다.</p>}><ReportLayoutEditor layout={layout} overview={overview} contents={contents} templates={templates} isSystem={isSystem} onChange={setLayout} onTemplateUpload={(file) => void uploadTemplate(file)} onTemplateDelete={(id) => void deleteTemplate(id)} onSave={() => void saveLayout(false)} onSaveAs={() => void saveLayout(true)} onDelete={() => void deleteLayout()} /></Suspense></div> : null}
      {data ? <p className="case-report__counts" data-testid="case-report-counts">Scene {data.sceneTable.rows.length}개 · 이미지 {data.images.length}개 · 영상 {data.videos.length}개</p> : !loadError ? <p className="case-report__hint" role="status"><LoaderCircle size={14} className="case-report__spinner" aria-hidden="true" />보고서 자료를 불러오는 중입니다.</p> : null}
      {loadError ? <p className="case-report__message case-report__message--error" role="alert"><AlertTriangle size={14} aria-hidden="true" />{loadError}</p> : null}
      {error ? <p className="case-report__message case-report__message--error" role="alert"><AlertTriangle size={14} aria-hidden="true" />{error}</p> : null}
      {notice ? <p className="case-report__message" role="status">{notice}</p> : null}
      {skipped.length ? <div className="case-report__skipped" role="status" data-testid="case-report-skipped"><strong>용량 제한 등으로 HTML에 넣지 못한 영상 {skipped.length}개</strong><ul>{skipped.map((item) => <li key={item} title={item}>{item}</li>)}</ul></div> : null}
    </div>
    <footer className="case-report__actions">
      <button type="button" className="case-report__secondary" disabled={busy} onClick={close}>닫기</button>
      <button type="button" className="case-report__primary" disabled={busy || !data || !layout || !chosen.length} onClick={() => void download()}>{busy ? <LoaderCircle size={15} className="case-report__spinner" aria-hidden="true" /> : <Download size={15} aria-hidden="true" />}다운로드</button>
    </footer>
  </dialog>
}
