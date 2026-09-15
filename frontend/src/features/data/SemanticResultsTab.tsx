import { useEffect, useRef, useState } from 'react'
import { Gauge, LoaderCircle, RefreshCw, Table2 } from 'lucide-react'
import { semanticContextApi, semanticMappingApi, type ContextLoadCase, type ContextProject, type ContextRequest, type PreviewWidget, type SemanticCatalog } from '../../shared/api/semanticMapping'
import { SemanticWidgetGrid } from '../../shared/components/semanticResults'

type StatusMessage = { kind: 'success' | 'error' | 'info'; text: string }
const definitionVersion = (definition: { active_version?: number | null }) => definition.active_version
const errorText = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback

export function ResultsTab({ catalog, onMessage }: { catalog: SemanticCatalog; onMessage: (message: StatusMessage) => void }) {
  const [projectId, setProjectId] = useState('')
  const [requestId, setRequestId] = useState('')
  const [loadCaseId, setLoadCaseId] = useState('')
  const [runId, setRunId] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [projects, setProjects] = useState<ContextProject[]>([])
  const [requests, setRequests] = useState<ContextRequest[]>([])
  const [loadCases, setLoadCases] = useState<ContextLoadCase[]>([])
  const [response, setResponse] = useState<{ key: string; widgets: PreviewWidget[] }>({ key: '', widgets: [] })
  const [busyKey, setBusyKey] = useState('')
  const key = JSON.stringify([projectId, requestId, loadCaseId, runId, templateId])
  const currentKey = useRef(key)
  currentKey.current = key
  const querySequence = useRef(0)
  const results = response.key === key ? response.widgets : []
  const busy = busyKey === key
  useEffect(() => () => { querySequence.current += 1 }, [])
  useEffect(() => {
    let cancelled = false
    semanticContextApi.projects().then((items) => { if (!cancelled) setProjects(items) }).catch((reason) => { if (!cancelled) onMessage({ kind: 'error', text: errorText(reason, '프로젝트 목록을 불러오지 못했습니다.') }) })
    return () => { cancelled = true }
  }, [])
  useEffect(() => {
    let cancelled = false
    setRequests([]); setRequestId(''); setLoadCases([]); setLoadCaseId(''); setRunId('')
    if (projectId) semanticContextApi.requests(projectId).then((items) => { if (!cancelled) { setRequests(items); setRequestId(items[0]?.id ?? '') } }).catch((reason) => { if (!cancelled) onMessage({ kind: 'error', text: errorText(reason, '의뢰 목록을 불러오지 못했습니다.') }) })
    return () => { cancelled = true }
  }, [projectId])
  useEffect(() => {
    let cancelled = false
    setLoadCases([]); setLoadCaseId(''); setRunId('')
    if (requestId) semanticContextApi.loadCases(requestId).then((items) => { if (!cancelled) { setLoadCases(items); setLoadCaseId(items[0]?.id ?? '') } }).catch((reason) => { if (!cancelled) onMessage({ kind: 'error', text: errorText(reason, '하중 경우 목록을 불러오지 못했습니다.') }) })
    return () => { cancelled = true }
  }, [requestId])
  const load = async () => {
    if (!loadCaseId || !loadCases.some((item) => item.id === loadCaseId)) { onMessage({ kind: 'error', text: '결과를 조회할 하중 경우를 선택하세요.' }); return }
    const sequence = ++querySequence.current
    const isCurrent = () => sequence === querySequence.current && key === currentKey.current
    setBusyKey(key); setResponse({ key, widgets: [] })
    try {
      const data = await semanticMappingApi.results({ load_case_id: loadCaseId, run_id: runId || undefined, template_id: templateId || undefined })
      if (isCurrent()) { setResponse({ key, widgets: data.widgets }); onMessage({ kind: 'success', text: `저장 결과 ${data.widgets.length}개 위젯을 불러왔습니다${data.run_id ? ` · run ${data.run_id}` : ''}.` }) }
    } catch (reason) {
      if (isCurrent()) onMessage({ kind: 'error', text: errorText(reason, '저장 결과를 불러오지 못했습니다.') })
    } finally { if (sequence === querySequence.current) setBusyKey('') }
  }
  return <div className="semantic-flow"><div className="semantic-card"><div className="semantic-card-heading"><div><span>STEP 06 · RESULTS</span><h2>저장된 결과 위젯</h2></div><Table2 /></div><div className="result-query-fields"><label>프로젝트<select aria-label="프로젝트" value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">프로젝트 선택</option>{projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>의뢰<select aria-label="의뢰" value={requestId} disabled={!projectId} onChange={(event) => setRequestId(event.target.value)}><option value="">의뢰 선택</option>{requests.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label><label>하중 경우<select aria-label="하중 경우" value={loadCaseId} disabled={!requestId} onChange={(event) => setLoadCaseId(event.target.value)}><option value="">하중 경우 선택</option>{loadCases.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>Run ID (선택)<input value={runId} onChange={(event) => setRunId(event.target.value)} placeholder="자동 최신" /></label><label>템플릿<select aria-label="템플릿" value={templateId} onChange={(event) => setTemplateId(event.target.value)}><option value="">기본 템플릿</option>{catalog.templates.filter((template) => template.active_version).map((template) => <option key={template.id} value={template.id}>{template.name ?? template.id} · v{definitionVersion(template)}</option>)}</select></label><button className="primary-button" onClick={() => void load()} disabled={busy}>{busy ? <LoaderCircle className="spin" /> : <RefreshCw />} 결과 조회</button></div>{results.length ? <SemanticWidgetGrid widgets={results} /> : <div className="empty-preview"><Gauge /><p>프로젝트·의뢰·하중 경우를 선택하면 저장된 결과 위젯을 표시합니다.</p></div>}</div></div>
}
