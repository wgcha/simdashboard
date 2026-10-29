import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Link2, Pause, Play, RotateCcw, Star } from 'lucide-react'
import { simulationDashboardApi, type DashboardCatalog, type DashboardRunVideo, type DashboardVideoScene } from '../../shared/api/simulationDashboard'
import { Button } from '../../shared/components/Button'
import { Select } from '../../shared/components/Select'
import { resolveCompareMember, type CompareCandidate, type CompareMember, type ComparePath } from './caseCompare'
import { attachPlaybackSync } from './playbackSync'
import { isUnplayable, sceneMatchKey, sourceShort, unionScenes, unplayableText } from './videoRepresentative'
import './VideoRepresentative.css'

type Props = {
  catalog: DashboardCatalog
  /** The compared Cases (2–4), in column order. */
  cases: CompareCandidate[]
  /** The path bar selection every Case is matched by (null until complete). */
  path: ComparePath | null
  /** 동시 재생 맞춤 initially on. */
  defaultSyncPlayback?: boolean
}

type Member = { candidate: CompareCandidate; member: CompareMember | null; reason: string }
type SceneList = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ok'; scenes: DashboardVideoScene[] }
type Cell = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ok'; video: DashboardRunVideo | null }

const errorText = (reason: unknown) => reason instanceof Error && reason.message ? reason.message : '영상을 불러오지 못했습니다.'
const scopeOf = (member: CompareMember) => ({ capture_id: member.captureId, run_option_id: member.optionId || undefined, mode: member.optionId ? undefined : member.mode || undefined })

/** 「같은 Scene 대표 영상 나란히」: the representative video of one Scene in every compared Case, played together. */
export function CaseSceneVideoCompare({ catalog, cases, path, defaultSyncPlayback = true }: Props) {
  const members = useMemo<Member[]>(() => path ? cases.map((candidate) => {
    const resolved = resolveCompareMember(catalog, candidate, path)
    return 'member' in resolved ? { candidate, member: resolved.member, reason: '' } : { candidate, member: null, reason: resolved.reason }
  }) : [], [catalog, cases, path])
  const memberKey = members.map((item) => `${item.candidate.id}:${item.member?.captureId}:${item.member?.runId}:${item.member?.optionId}`).join('|')
  const [lists, setLists] = useState<Record<string, SceneList>>({})
  const [sceneKey, setSceneKey] = useState('')
  const [cells, setCells] = useState<Record<string, Cell>>({})
  const [sync, setSync] = useState(defaultSyncPlayback)
  const videoRefs = useRef(new Map<string, HTMLVideoElement>())

  useEffect(() => {
    const controller = new AbortController()
    setLists({})
    for (const item of members) {
      if (!item.member) continue
      const id = item.candidate.id
      simulationDashboardApi.videoScenes(item.member.runId, scopeOf(item.member), controller.signal)
        .then((result) => { if (!controller.signal.aborted) setLists((current) => ({ ...current, [id]: { status: 'ok', scenes: result.scenes ?? [] } })) })
        .catch((reason) => { if (!controller.signal.aborted) setLists((current) => ({ ...current, [id]: { status: 'error', message: errorText(reason) } })) })
    }
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [memberKey])

  const listsKey = members.map((item) => lists[item.candidate.id]?.status ?? '-').join(',')
  const sceneLists = members.map((item) => lists[item.candidate.id])
  const options = useMemo(() => unionScenes(sceneLists.map((list) => list?.status === 'ok' ? list.scenes.filter((scene) => scene.video_count + scene.unplayable_count > 0) : [])),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [lists, memberKey])
  const activeKey = options.some((option) => option.key === sceneKey) ? sceneKey : options[0]?.key ?? ''
  const labelIn = (id: string) => { const list = lists[id]; return list?.status === 'ok' ? list.scenes.find((scene) => sceneMatchKey(scene.label) === activeKey)?.label : undefined }
  const cellKey = (id: string) => `${id}|${activeKey}`

  useEffect(() => {
    if (!activeKey) return undefined
    const controller = new AbortController()
    setCells({})
    for (const item of members) {
      if (!item.member) continue
      const id = item.candidate.id
      const list = lists[id]
      if (!list || list.status === 'loading') continue
      if (list.status === 'error') { setCells((current) => ({ ...current, [cellKey(id)]: { status: 'error', message: list.message } })); continue }
      const label = labelIn(id)
      if (!label) { setCells((current) => ({ ...current, [cellKey(id)]: { status: 'ok', video: null } })); continue }
      simulationDashboardApi.videos(item.member.runId, { ...scopeOf(item.member), scene_label: label, representative_only: true, page_size: 1 }, controller.signal)
        .then((result) => { if (!controller.signal.aborted) setCells((current) => ({ ...current, [cellKey(id)]: { status: 'ok', video: result.videos?.[0] ?? null } })) })
        .catch((reason) => { if (!controller.signal.aborted) setCells((current) => ({ ...current, [cellKey(id)]: { status: 'error', message: errorText(reason) } })) })
    }
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeKey, memberKey, listsKey])

  const playable = members.filter((item) => { const cell = cells[cellKey(item.candidate.id)]; return cell?.status === 'ok' && cell.video && !isUnplayable(cell.video) })
  const playableKey = playable.map((item) => item.candidate.id).join('|')
  useEffect(() => {
    if (!sync) return undefined
    return attachPlaybackSync([...videoRefs.current.values()])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sync, playableKey, activeKey])

  if (!path || cases.length < 2) return null
  const all = () => [...videoRefs.current.values()]
  const playAll = (restart: boolean) => all().forEach((video) => { video.muted = true; if (restart) { try { video.currentTime = 0 } catch { /* no metadata yet */ } } void video.play().catch(() => undefined) })
  const loadingScenes = members.some((item) => item.member && (!lists[item.candidate.id] || lists[item.candidate.id].status === 'loading'))

  return <section className="rep-compare" aria-label="같은 Scene 대표 영상 나란히" data-testid="case-compare-videos">
    <header className="rep-compare__head">
      <h3>같은 Scene 대표 영상 나란히</h3>
      <label className="case-video-scene"><span>Scene</span>
        <Select controlSize="sm" value={activeKey} disabled={options.length === 0} aria-label="나란히 볼 Scene" onChange={(event) => setSceneKey(event.target.value)}>
          {options.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
        </Select>
      </label>
      <Button size="sm" onClick={() => playAll(false)} disabled={playable.length === 0}><Play aria-hidden="true" /> 전체 재생</Button>
      <Button size="sm" onClick={() => all().forEach((video) => video.pause())} disabled={playable.length === 0}><Pause aria-hidden="true" /> 전체 정지</Button>
      <Button size="sm" onClick={() => playAll(true)} disabled={playable.length === 0}><RotateCcw aria-hidden="true" /> 처음부터</Button>
      <button type="button" className={`case-video-sync${sync ? ' active' : ''}`} aria-pressed={sync} title="비교하는 영상의 재생·정지·위치를 함께 맞춥니다." onClick={() => setSync((value) => !value)}><Link2 aria-hidden="true" /> 동시 재생 맞춤 {sync ? '켜짐' : '꺼짐'}</button>
    </header>
    {options.length === 0 ? <p className="rep-panel__state" role="status">{loadingScenes ? '영상 목록을 불러오는 중입니다.' : '선택한 Case에 영상이 있는 Scene이 없습니다.'}</p>
      : <div className="rep-compare__grid" style={{ gridTemplateColumns: `repeat(${members.length}, minmax(0, 1fr))` }}>
        {members.map((item) => {
          const cell = cells[cellKey(item.candidate.id)]
          const video = cell?.status === 'ok' ? cell.video : null
          return <article key={item.candidate.id} className="rep-compare__cell" data-case-id={item.candidate.id}>
            <header><strong title={item.candidate.label}>{item.candidate.label}</strong>
              {video?.is_representative && <b className="rep-video-star" title="이 Case의 대표 영상"><Star aria-hidden="true" />대표 · {sourceShort(video.representative_source)}</b>}</header>
            <div className="drop-video-frame">
              {item.reason ? <p className="rep-compare__empty">{item.reason}</p>
                : !cell ? <p className="rep-compare__empty">불러오는 중입니다.</p>
                : cell.status === 'error' ? <p className="rep-compare__empty error"><AlertTriangle aria-hidden="true" />{cell.message}</p>
                : !video ? <p className="rep-compare__empty">이 Case에는 이 Scene의 영상이 없습니다.</p>
                : isUnplayable(video) ? <div className="rep-video-unplayable" role="note"><AlertTriangle aria-hidden="true" /><strong>{unplayableText(video)}</strong>{video.asset_id && <a href={simulationDashboardApi.assetUrl(video.asset_id)} download={video.file_name}>내려받기</a>}</div>
                : <video ref={(element) => { if (element) videoRefs.current.set(item.candidate.id, element); else videoRefs.current.delete(item.candidate.id) }} aria-label={`${item.candidate.label} ${video.file_name ?? video.title} 영상`} controls muted playsInline preload="metadata" src={simulationDashboardApi.assetUrl(video.asset_id!)} />}
            </div>
            <footer className="rep-video-foot"><span title={video?.relative_path}>{video?.file_name ?? ''}</span></footer>
          </article>
        })}
      </div>}
  </section>
}
