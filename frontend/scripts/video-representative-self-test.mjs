import assert from 'node:assert/strict'
import { attachPlaybackSync } from '../src/features/results/playbackSync.ts'
import { changedAtText, formatBytes, isUnplayable, keywordProblem, parseKeywords, sceneMatchKey, unionScenes, unplayableText } from '../src/features/results/videoRepresentative.ts'

// --- keyword rule parsing --------------------------------------------------------------------------------
assert.deepEqual(parseKeywords('Max_Stress, Mid\nmid ;  Iso  View '), ['Max_Stress', 'Mid', 'Iso View'])
assert.deepEqual(parseKeywords(' , ,'), [])
assert.equal(keywordProblem('a, b'), '')
assert.match(keywordProblem(Array.from({ length: 21 }, (_, i) => `k${i}`).join(',')), /최대 20개/)
assert.match(keywordProblem('a/b'), /경로 문자/)
assert.match(keywordProblem('x'.repeat(65)), /64자/)

// --- labels ----------------------------------------------------------------------------------------------
assert.equal(formatBytes(5.2 * 1024 * 1024), '5.2 MB')
assert.equal(formatBytes(2048), '2.0 KB')
assert.equal(formatBytes(null), '')
assert.equal(unplayableText({ asset_id: null, unplayable_reason: 'FORMAT', format: 'avi', size_bytes: 5.2 * 1024 * 1024 }), '재생 불가 · avi · 5.2 MB · 원본 드라이브에 있음')
assert.equal(unplayableText({ asset_id: null, unplayable_reason: 'FORMAT', file_name: 'x.avi' }), '재생 불가 · avi · 원본 드라이브에 있음')
assert.equal(unplayableText({ asset_id: 'a', unplayable_reason: 'CODEC', codec: 'mp4v' }), '재생 불가 · 코덱 · mp4v')
assert.equal(unplayableText({ asset_id: null, unplayable_reason: 'TOO_LARGE', format: 'mp4', size_bytes: 40 * 1024 * 1024 }), '재생 불가 · 용량 초과 · mp4 · 40.0 MB · 원본 드라이브에 있음')
assert.equal(isUnplayable({ asset_id: null }), true)
assert.equal(isUnplayable({ asset_id: 'a', playable: false }), true)
assert.equal(isUnplayable({ asset_id: 'a', playable: true, status: 'READY' }), false)
assert.equal(isUnplayable({ asset_id: 'a' }), false)
assert.match(changedAtText('2026-10-29T01:02:03'), /^\d{4}-\d\d-\d\d \d\d:\d\d$/)
assert.equal(changedAtText(null), '')

// --- Scene union across Cases ---------------------------------------------------------------------------
assert.equal(sceneMatchKey(' 2_Face '), '2_face')
const scenes = unionScenes([[{ label: '2_Face', scene_sequence_number: 2 }, { label: 'Zed' }], [{ label: '2_FACE', scene_sequence_number: 2 }, { label: '1_Edge', scene_sequence_number: 1 }]])
assert.deepEqual(scenes.map((scene) => scene.label), ['1_Edge', '2_Face', 'Zed'])

// --- 동시 재생 맞춤 -----------------------------------------------------------------------------------------
class FakeVideo extends EventTarget {
  paused = true
  ended = false
  currentTime = 0
  playbackRate = 1
  duration = 10
  play() { if (!this.paused) return Promise.resolve(); this.paused = false; this.dispatchEvent(new Event('play')); return Promise.resolve() }
  pause() { if (this.paused) return; this.paused = true; this.dispatchEvent(new Event('pause')) }
  seek(time) { this.currentTime = time; this.dispatchEvent(new Event('seeked')) }
}
const [a, b, c] = [new FakeVideo(), new FakeVideo(), new FakeVideo()]
c.duration = 4
const detach = attachPlaybackSync([a, b, c])
a.currentTime = 2
await a.play()
assert.deepEqual([b.paused, c.paused], [false, false], 'play is mirrored')
assert.equal(b.currentTime, 2)
assert.equal(c.currentTime, 2)
b.seek(7)
assert.equal(a.currentTime, 7)
assert.equal(c.currentTime, 4, 'a shorter video stops at its end')
b.playbackRate = 2
b.dispatchEvent(new Event('ratechange'))
assert.equal(a.playbackRate, 2)
c.ended = true
c.pause()
assert.deepEqual([a.paused, b.paused], [false, false], 'a video that ended does not stop the others')
c.ended = false
a.pause()
assert.deepEqual([b.paused, c.paused], [true, true], 'pause is mirrored')
detach()
a.seek(1)
assert.equal(b.currentTime, 7, 'detached videos are independent')

// enabling while one video plays aligns the rest
const [d, e] = [new FakeVideo(), new FakeVideo()]
d.currentTime = 5
await d.play()
const detachLate = attachPlaybackSync([d, e])
assert.equal(e.paused, false)
assert.equal(e.currentTime, 5)
detachLate()
console.log('video representative self-test passed')
