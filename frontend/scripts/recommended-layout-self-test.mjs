import assert from 'node:assert/strict'
import { suggestLayout } from '../src/features/results/recommendedLayout.ts'
import { compact } from 'react-grid-layout/build/utils.js'

const widget = (id, type, x, y, w = 4, h = 2, settings = { variableId: id }) => ({ id, type, title: `${type} title`, x, y, w, h, settings })

function hasOverlap(first, second) {
  return first.x < second.x + second.w && first.x + first.w > second.x && first.y < second.y + second.h && first.y + first.h > second.y
}

function assertValidGeometry(widgets) {
  const ids = new Set()
  for (const item of widgets) {
    assert.equal(ids.has(item.id), false, `duplicate ${item.id}`)
    ids.add(item.id)
    assert.ok(item.x >= 0 && item.y >= 0 && item.x + item.w <= 12, `${item.id} is outside grid`)
  }
  for (let index = 0; index < widgets.length; index += 1) {
    for (let other = 0; other < index; other += 1) assert.equal(hasOverlap(widgets[index], widgets[other]), false, `${widgets[index].id} overlaps ${widgets[other].id}`)
  }
}

const settings = { variableId: 'stress', color: '#50d5ff' }
const source = [
  widget('video', 'video_grid', 0, 0, 12, 4, { videoColumns: 5, videoRows: 4, pageSize: 20 }),
  widget('chart-late', 'time_series', 0, 7, 6, 3),
  widget('kpi', 'kpi', 0, 13, 3, 2, settings),
  widget('map', 'open_cell_map', 0, 4, 5, 3),
  widget('note', 'note', 0, 15, 12, 1),
  widget('chart-early', 'scatter', 5, 4, 6, 3),
  widget('summary', 'summary', 3, 13, 5, 2),
]

const proposal = suggestLayout(source)
assert.equal(proposal.available, true)
assert.ok(proposal.changedCount > 0)
assert.deepEqual(proposal.widgets.map((item) => item.id), source.map((item) => item.id), 'array order is preserved')
assert.deepEqual(proposal.widgets.map(({ id, type, title, w, h, settings: itemSettings }) => ({ id, type, title, w, h, settings: itemSettings })), source.map(({ id, type, title, w, h, settings: itemSettings }) => ({ id, type, title, w, h, settings: itemSettings })), 'only coordinates may change')
assert.equal(proposal.widgets.find((item) => item.id === 'kpi').settings, settings, 'settings are retained by identity')
assert.deepEqual(proposal.widgets.find((item) => item.id === 'video').settings, { videoColumns: 5, videoRows: 4, pageSize: 20 }, '5×4 video-grid settings are retained')
assert.deepEqual(
  compact(proposal.widgets.map((item) => ({ i: item.id, x: item.x, y: item.y, w: item.w, h: item.h })), 'vertical', 12)
    .map((item) => ({ id: item.i, x: item.x, y: item.y })),
  proposal.widgets.map((item) => ({ id: item.id, x: item.x, y: item.y })),
  'the proposal already matches react-grid-layout vertical compaction',
)
assertValidGeometry(proposal.widgets)
assert.equal(proposal.widgets.find((item) => item.id === 'chart-early').y <= proposal.widgets.find((item) => item.id === 'chart-late').y, true, 'evidence preserves current visual order')
assert.ok(proposal.widgets.find((item) => item.id === 'video').y >= proposal.widgets.find((item) => item.id === 'note').y, 'video is last')

const repeated = suggestLayout(proposal.widgets)
assert.equal(repeated.changedCount, 0, 'the suggestion is idempotent')
assert.deepEqual(repeated.widgets, proposal.widgets, 'repeated suggestions are deterministic')

assert.equal(suggestLayout([]).changedCount, 0)
assert.equal(suggestLayout([widget('only', 'kpi', 0, 0)]).changedCount, 0)
assert.equal(suggestLayout([widget('a', 'kpi', 0, 0), widget('a', 'summary', 4, 0)]).available, false)
assert.equal(suggestLayout([widget('wide', 'summary', 8, 0, 5, 2)]).available, false)
assert.equal(suggestLayout([widget('one', 'kpi', 0, 0, 6, 2), widget('two', 'summary', 4, 1, 6, 2)]).available, false)
assert.equal(suggestLayout([widget('unsafe-y', 'summary', 0, Number.MAX_SAFE_INTEGER, 12, 2)]).available, false)

const alreadyGood = [
  widget('overview', 'chassis_summary', 0, 0, 12, 2),
  widget('diagram', 'chassis_diagram', 0, 2, 7, 5),
  widget('bar', 'chassis_bar', 7, 2, 5, 5),
  widget('table', 'chassis_table', 0, 7, 8, 4),
  widget('note-side-by-side', 'note', 8, 7, 4, 3),
]
const alreadyGoodProposal = suggestLayout(alreadyGood)
assert.equal(alreadyGoodProposal.available, true)
assert.equal(alreadyGoodProposal.changedCount, 0, 'an RGL-stable chassis layout is left untouched')
assert.deepEqual(alreadyGoodProposal.widgets, alreadyGood)

const noteBeforeSummary = suggestLayout([widget('note-first', 'note', 0, 0, 12, 2), widget('summary-last', 'summary', 0, 2, 12, 2)])
assert.equal(noteBeforeSummary.available, true)
assert.equal(noteBeforeSummary.widgets.find(w => w.type === 'summary').y, 0, 'summary precedes notes even without charts')

const large = Array.from({ length: 36 }, (_, index) => widget(`large-${index}`, index % 11 === 0 ? 'video' : index % 7 === 0 ? 'note' : index % 5 === 0 ? 'result_table' : index % 3 === 0 ? 'kpi' : 'time_series', (index % 3) * 4, Math.floor(index / 3) * 3, 4, index % 4 === 0 ? 3 : 2))
const largeProposal = suggestLayout(large)
assert.equal(typeof largeProposal.available, 'boolean')
if (largeProposal.available) {
  assertValidGeometry(largeProposal.widgets)
  assert.equal(suggestLayout(largeProposal.widgets).changedCount, 0)
} else {
  assert.match(largeProposal.reason, /유지할 수 없습니다/)
}

console.log('Recommended layout self-test passed.')
