import { useEffect, useRef, useState } from 'react'
import { Gauge, LoaderCircle, RefreshCw, Table2 } from 'lucide-react'
import { semanticContextApi, semanticMappingApi, type ContextLoadCase, type ContextProject, type ContextRequest, type PreviewWidget, type SemanticCatalog } from '../../shared/api/semanticMapping'
import { SemanticWidgetGrid } from '../../shared/components/semanticResults'
import { SearchableSelect } from '../../shared/components/selectionLabels'

type StatusMessage = { kind: 'success' | 'error' | 'info'; text: string }
const definitionVersion = (definition: { active_version?: number | null }) => definition.active_version
const errorText = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback

export function ResultsTab({ catalog, onMessage, canImportResults = true, canImportForProject }: { catalog: SemanticCatalog; onMessage: (message: StatusMessage) => void; canImportResults?: boolean; canImportForProject?: (projectId: string) => boolean }) {
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
  const [refreshing, setRefreshing] = useState(false)
  const key = JSON.stringify([projectId, requestId, loadCaseId, runId, templateId])
  const currentKey = useRef(key)
  currentKey.current = key
  const querySequence = useRef(0)
  const results = response.key === key ? response.widgets : []
  const busy = busyKey === key
  const canRefresh = canImportForProject ? canImportForProject(projectId) : canImportResults
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
  const refreshAndLoad = async () => {
    if (!loadCaseId) { onMessage({ kind: 'error', text: '결과를 조회할 하중 경우를 선택하세요.' }); return }
    const sequence = ++querySequence.current
    const requestKey = key
    const isCurrent = () => sequence === querySequence.current && requestKey === currentKey.current
    setRefreshing(true); setResponse({ key: requestKey, widgets: [] })
    try {
      const refreshed = await semanticMappingApi.refreshLoadCaseResults(loadCaseId)
      if (!isCurrent()) return
      if (!refreshed.display_run_id) { onMessage({ kind: 'info', text: refreshed.partial ? '일부 파일 처리가 보류되었습니다. 저장된 결과를 확인하세요.' : '처리할 새 결과 파일이 없습니다.' }); return }
      const data = await semanticMappingApi.results({ load_case_id: loadCaseId, run_id: refreshed.display_run_id })
      if (!isCurrent()) return
      setRunId(refreshed.display_run_id)
      setTemplateId('')
      setResponse({ key: JSON.stringify([projectId, requestId, loadCaseId, refreshed.display_run_id, '']), widgets: data.widgets })
      onMessage({ kind: 'success', text: `결과 파일을 확인하고 Run ${refreshed.display_run_id}의 위젯 ${data.widgets.length}개를 표시했습니다.` })
    } catch (reason) {
      if (isCurrent()) onMessage({ kind: 'error', text: errorText(reason, '결과 파일을 확인하지 못했습니다.') })
    } finally { if (sequence === querySequence.current) setRefreshing(false) }
  }
  return <div className="semantic-flow"><div className="semantic-card"><div className="semantic-card-heading"><div><span>STEP 06 · RESULTS</span><h2>저장된 결과 위젯</h2></div><Table2 /></div><div className="result-query-fields"><label>프로젝트<SearchableSelect ariaLabel="프로젝트" items={projects} kind="project" value={projectId} onChange={setProjectId} placeholder="프로젝트 선택" /></label><label>의뢰<SearchableSelect ariaLabel="의뢰" items={requests} kind="request" disabled={!projectId} value={requestId} onChange={setRequestId} placeholder="의뢰 선택" /></label><label>하중 경우<SearchableSelect ariaLabel="하중 경우" items={loadCases} kind="load_case" disabled={!requestId} value={loadCaseId} onChange={setLoadCaseId} placeholder="하중 경우 선택" /></label><label>Run ID (선택)<input value={runId} onChange={(event) => setRunId(event.target.value)} placeholder="자동 최신" /></label><label>템플릿<select aria-label="템플릿" value={templateId} onChange={(event) => setTemplateId(event.target.value)}><option value="">기본 템플릿</option>{catalog.templates.filter((template) => template.active_version).map((template) => <option key={template.id} value={template.id}>{template.name ?? template.id} · v{definitionVersion(template)}</option>)}</select></label><button className="primary-button" onClick={() => void refreshAndLoad()} disabled={!canRefresh || busy || refreshing}>{refreshing ? <LoaderCircle className="spin" /> : <RefreshCw />} 파일 확인·결과 조회</button><button className="ghost-button" onClick={() => void load()} disabled={busy || refreshing}>저장 결과만 조회</button></div>{results.length ? <div data-testid="result-widget-display"><SemanticWidgetGrid widgets={results} /></div> : <div className="empty-preview"><Gauge /><p>프로젝트·의뢰·하중 경우를 선택하면 저장된 결과 위젯을 표시합니다.</p></div>}</div></div>
}
