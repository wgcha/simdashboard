import { explicitCustomAnalysisPage, preservesResultLayoutOnLoadCaseChange, resultLayoutPollDelay, resultWidgetMessage, resultWidgetState, shouldPollResultLayout } from '../src/features/results/resultLayoutRuntime.ts'
import { normalizeResultDataContracts, resultProfileValidation, workflowDataContracts } from "../src/features/workbench/resultProfileContracts.ts"
import { canCommitResultLayoutOpen, canOpenResultLayout, createRequestContextIntentGate, createUnifiedContextIntentGate, createWorkflowAnalysisOpener, detailedAnalysisRoute, isPendingResultAnalysis } from '../src/features/results/resultLayoutRouting.ts'

if (!preservesResultLayoutOnLoadCaseChange('request-result-layout') || preservesResultLayoutOnLoadCaseChange('dashboard-drop-default')) throw new Error('switching load cases must preserve only the snapshot workspace')

const widget = { id: 'summary', type: 'summary', title: '요약', x: 0, y: 0, w: 4, h: 2, settings: {} }

if (resultWidgetState(widget, ['LOAD_CASE']) !== 'WAITING') throw new Error('result layouts without data must wait rather than infer Open Cell')
if (resultWidgetState(widget, ['result_run'], { available_data_contracts: ['RESULT_RUN'], load_cases: [{}], latest_result_run: null, scalars: [], error: null }) !== 'READY') throw new Error('lowercase profile contracts must match canonical runtime bindings')
const lowercaseProfile = { required_data_contracts: ['result_run'], included_widget_ids: ['summary'], template: { page_definitions: [{ widgets: [{ ...widget, settings: { data_contracts: ['result_run'] } }] }] } }
const lowercaseTask = { output_artifact_types: ['analysis_run_reference'] }
if (resultProfileValidation(lowercaseProfile, [lowercaseTask]) !== '' || workflowDataContracts([lowercaseTask])[0] !== 'LOAD_CASE' || normalizeResultDataContracts([' result_run '])[0] !== 'RESULT_RUN') throw new Error('lowercase profile and task contracts must normalize before validation')
if (resultWidgetState({ ...widget, settings: { result_state: 'PARTIAL' } }, []) !== 'PARTIAL') throw new Error('declared partial widget state was not preserved')
if (resultWidgetState({ ...widget, settings: { result_state: 'READY' } }, []) !== 'READY') throw new Error('declared ready widget state was not preserved')
if (resultWidgetState({ ...widget, settings: { not_applicable: true } }, []) !== 'NOT_APPLICABLE') throw new Error('not-applicable widget state was not preserved')
if (resultWidgetMessage('FAILED').length === 0) throw new Error('every widget state requires an operator-facing message')
const liveBindings = { available_data_contracts: [], load_cases: [], latest_result_run: null, scalars: [], error: null, widget_states: { summary: 'FAILED' }, widget_data_contracts: { summary: ['RESULT_RUN'] } }
if (resultWidgetState({ ...widget, settings: { result_state: 'READY' } }, [], liveBindings) !== 'FAILED') throw new Error('live widget state must win over static widget settings')
if (resultWidgetState(widget, [], { ...liveBindings, widget_states: {}, widget_errors: { summary: 'conversion failed' } }) !== 'ERROR') throw new Error('widget-specific errors must be surfaced')
if (resultWidgetState(widget, [], { ...liveBindings, widget_states: {}, widget_errors: {} }) !== 'WAITING') throw new Error('widget-specific contracts must gate readiness')
if (resultLayoutPollDelay(0) !== 5_000 || resultLayoutPollDelay(9) !== 60_000 || resultLayoutPollDelay(0, true) !== 60_000) throw new Error('polling must back off with a bounded hidden-tab delay')
const customPage = explicitCustomAnalysisPage([{ id: 'system', page: { analysis_key: 'open_cell', is_system: true } }, { id: 'custom', page: { analysis_key: 'custom', is_system: false } }])
if (customPage?.id !== 'custom') throw new Error('unconfigured fallback may restore only an explicit custom page')

const events = []
const workflow = { request: { id: 'request-layout', project_id: 'project-layout' } }
let workflowIntent = 0
const open = createWorkflowAnalysisOpener({
  selectRequestContext: async (item, preferred) => { events.push(`context:${item.request.project_id}:${item.request.id}:${preferred ?? 'default'}`) },
  loadLayout: async () => ({ snapshot: { pages: [{}] } }), beginIntent: () => ++workflowIntent, isCurrentIntent: (intent) => intent === workflowIntent,
  setActiveDashboardId: (id) => { events.push(`dashboard:${id}`) },
  setActiveView: (view) => { events.push(`view:${view}`) },
  setError: (message) => { throw new Error(message) },
})
await open(workflow)
if (events.join(',') !== 'context:project-layout:request-layout:default,dashboard:request-result-layout,view:custom') throw new Error('configured snapshots must commit clicked request context before opening the snapshot')

const legacyEvents = []
await createWorkflowAnalysisOpener({
  selectRequestContext: async (item, preferred) => { legacyEvents.push([item.request.project_id, item.request.id, preferred]) }, loadLayout: async () => ({ snapshot: null, compatibility: { route_kind: 'DOMAIN' } }), beginIntent: () => ++workflowIntent, isCurrentIntent: (intent) => intent === workflowIntent,
  setActiveDashboardId: () => {}, setActiveView: () => {}, setError: (message) => { throw new Error(message) },
})(workflow)
if (legacyEvents[0]?.join(':') !== 'project-layout:request-layout:open_cell') throw new Error('legacy domain results must retain their dedicated Open Cell route')

if (detailedAnalysisRoute({ snapshot: null }) !== 'UNCONFIGURED') throw new Error('snapshotless requests must not infer a domain route')
if (!shouldPollResultLayout({ snapshot: { required_data_contracts: ['LOAD_CASE'], pages: [{ widgets: [widget] }] } })) throw new Error('waiting widgets must enable polling')
if (shouldPollResultLayout({ snapshot: { required_data_contracts: [], pages: [{ widgets: [{ ...widget, settings: { result_state: 'READY' } }] }] } })) throw new Error('ready-only layouts must stop polling')

if (!isPendingResultAnalysis(false, 'pending-open-cell')) throw new Error('unconfigured requests must stay in the neutral detailed-analysis workspace even when a load case exists')
if (isPendingResultAnalysis(false, 'dashboard-drop-default')) throw new Error('domain dashboards must not be hidden behind the neutral detailed-analysis workspace')

const requestContextGate = createRequestContextIntentGate()
const staleRequestLoad = requestContextGate.start()
const detailIntent = requestContextGate.start()
if (requestContextGate.isCurrent(staleRequestLoad)) throw new Error('a stale request context load must not override a newer detail-analysis intent')
if (!requestContextGate.isCurrent(detailIntent)) throw new Error('the latest request context intent must remain active')
if (canOpenResultLayout('request-layout', true)) throw new Error('detail analysis must stay disabled while the selected request context is loading')
if (!canOpenResultLayout('request-layout', false)) throw new Error('detail analysis must become available after the selected request context is ready')

async function expectNewerContextEntryWins(label, loadsMonitoringContext) {
  const events = []
  const gate = createUnifiedContextIntentGate()
  let selectedRequest = 'request-A'
  let resolveDelayedLayout
  const delayedOpen = createWorkflowAnalysisOpener({
    selectRequestContext: async () => { events.push('context:A') },
    loadLayout: () => new Promise((resolve) => { resolveDelayedLayout = resolve }),
    beginIntent: gate.beginContextEntry, isCurrentIntent: gate.isCurrentContextEntry,
    setActiveDashboardId: () => { events.push('dashboard:A') },
    setActiveView: () => { events.push('view:A') },
    setError: (message) => { throw new Error(message) },
  })
  const delayedOpenPromise = delayedOpen(workflow)
  if (!resolveDelayedLayout) throw new Error(`workflow layout request did not start before ${label}`)
  const latestEntry = gate.beginContextEntry()
  const latestMonitoring = loadsMonitoringContext ? gate.beginMonitoringContext() : undefined
  selectedRequest = `request-${label}`
  resolveDelayedLayout({ snapshot: { pages: [{}] } })
  await delayedOpenPromise
  if (selectedRequest !== `request-${label}` || events.length !== 0 || !gate.isCurrentContextEntry(latestEntry)) throw new Error(`a delayed workflow response overrode the newer ${label} context`)
  if (latestMonitoring !== undefined && !gate.isCurrentMonitoringContext(latestMonitoring)) throw new Error(`${label} monitoring context was invalidated by stale workflow A`)
}

await expectNewerContextEntryWins('portfolio', true)
await expectNewerContextEntryWins('import', false)
await expectNewerContextEntryWins('example', true)
await expectNewerContextEntryWins('intake-navigation', false)

const detailNavigationGate = createUnifiedContextIntentGate()
const detailContextIntent = detailNavigationGate.beginContextEntry()
let detailLocalIntent = 1
let detailCallbackCount = 0
let resolveDelayedDetail
const delayedDetail = new Promise((resolve) => { resolveDelayedDetail = resolve }).then(() => {
  if (canCommitResultLayoutOpen(1, detailLocalIntent, detailContextIntent, detailNavigationGate.isCurrentContextEntry)) detailCallbackCount += 1
})
const navigationIntent = detailNavigationGate.beginContextEntry()
if (!resolveDelayedDetail) throw new Error('detail-tab layout request did not start before navigation')
resolveDelayedDetail()
await delayedDetail
if (detailCallbackCount !== 0 || !detailNavigationGate.isCurrentContextEntry(navigationIntent)) throw new Error('a delayed detail-tab A response must not override navigation B')
detailLocalIntent += 1
if (canCommitResultLayoutOpen(1, detailLocalIntent, navigationIntent, detailNavigationGate.isCurrentContextEntry)) throw new Error('an unmounted or request-changed detail tab must not commit locally stale work')

console.log('Result layout runtime self-test passed.')
