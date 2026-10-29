import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, LoaderCircle, Star, X } from 'lucide-react'
import { simulationDashboardApi, type DashboardRunVideo, type DashboardVideoRule } from '../../shared/api/simulationDashboard'
import { Button } from '../../shared/components/Button'
import { changedAtText, formatBytes, isUnplayable, sourceLabel, unplayableText } from './videoRepresentative'
import './VideoRepresentative.css'

export type RepresentativeTarget = { sceneId: string; sceneKey: string; sceneLabel: string }
export type VideoRepresentativePanelProps = {
  target: RepresentativeTarget
  caseId: string
  captureId: string
  runId: string
  runOptionId?: string | null
  mode?: string | null
  rule?: DashboardVideoRule
  /** The viewer may save and reset (same permission as result import). */
  canChoose: boolean
  onClose: () => void
  /** A choice was saved or reset: the grid reloads. */
  onChanged: () => void
}

const MAX_PAGES = 5

/** 「대표 영상 바꾸기」: pick the representative among the videos of one Scene, with preview, save and reset. */
export function VideoRepresentativePanel({ target, caseId, captureId, runId, runOptionId, mode, rule, canChoose, onClose, onChanged }: VideoRepresentativePanelProps) {
  const [videos, setVideos] = useState<DashboardRunVideo[] | null>(null)
  const [loadError, setLoadError] = useState('')
  const [picked, setPicked] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null)
  const [reloadToken, setReloadToken] = useState(0)
  const panelRef = useRef<HTMLElement | null>(null)

  useEffect(() => { panelRef.current?.scrollIntoView?.({ block: 'nearest' }) }, [target.sceneId])
  useEffect(() => {
    const controller = new AbortController()
    setVideos(null); setLoadError('')
    ;(async () => {
      const all: DashboardRunVideo[] = []
      for (let page = 1; page <= MAX_PAGES; page += 1) {
        const result = await simulationDashboardApi.videos(runId, { capture_id: captureId, run_option_id: runOptionId ?? undefined, mode: runOptionId ? undefined : mode ?? undefined, scene_id: target.sceneId, page, page_size: 20 }, controller.signal)
        all.push(...(result.videos ?? []))
        if (!result.pagination.has_next) break
      }
      if (controller.signal.aborted) return
      setVideos(all)
      setPicked((current) => all.some((video) => video.relative_path === current && !isUnplayable(video)) ? current : all.find((video) => video.is_representative)?.relative_path ?? '')
    })().catch((reason) => { if (!controller.signal.aborted) setLoadError(reason instanceof Error ? reason.message : '영상 목록을 불러오지 못했습니다.') })
    return () => controller.abort()
  }, [captureId, runId, runOptionId, mode, target.sceneId, reloadToken])

  const current = videos?.find((video) => video.is_representative)
  const choice = current?.representative_source === 'USER' ? current.choice : null
  const selected = videos?.find((video) => video.relative_path === picked && video.asset_id)
  const keywords = rule?.keywords ?? []

  const run = useCallback(async (action: () => Promise<unknown>, done: string) => {
    setBusy(true); setNotice(null)
    try { await action(); setNotice({ text: done, error: false }); setReloadToken((value) => value + 1); onChanged() }
    catch (reason) { setNotice({ text: reason instanceof Error && reason.message ? reason.message : '저장하지 못했습니다.', error: true }) }
    finally { setBusy(false) }
  }, [onChanged])

  const save = () => { if (selected?.relative_path) void run(() => simulationDashboardApi.setVideoRepresentative({ case_id: caseId, scene_key: target.sceneKey, relative_path: selected.relative_path! }), '대표 영상을 저장했습니다.') }
  const reset = () => void run(() => simulationDashboardApi.resetVideoRepresentative(caseId, target.sceneKey), '기본 대표로 되돌렸습니다.')

  return <section ref={panelRef} className="rep-panel" aria-label={`대표 영상 바꾸기 · ${target.sceneLabel}`} data-testid="video-representative-panel">
    <header className="rep-panel__head">
      <div><strong>대표 영상 바꾸기 · {target.sceneLabel}</strong>
        <small>{videos ? `이 Scene의 영상 ${videos.length}개 · ` : ''}{keywords.length ? `자동 규칙: ${keywords.join(' → ')} → 없으면 파일 이름순 첫 번째` : '기본 대표는 파일 이름순 첫 번째'}</small></div>
      <Button size="sm" variant="ghost" onClick={onClose} aria-label="대표 영상 패널 닫기"><X aria-hidden="true" /></Button>
    </header>
    {loadError ? <p className="rep-panel__state error" role="alert"><AlertTriangle aria-hidden="true" />{loadError} <Button size="sm" onClick={() => setReloadToken((value) => value + 1)}>다시 시도</Button></p>
      : !videos ? <p className="rep-panel__state" role="status"><LoaderCircle className="spin" aria-hidden="true" />영상 목록을 불러오는 중입니다.</p>
      : videos.length === 0 ? <p className="rep-panel__state">이 Scene에는 영상이 없습니다.</p>
      : <div className="rep-panel__body">
        <ul className="rep-panel__list" aria-label="Scene 영상 목록">
          {videos.map((video) => {
            const blocked = isUnplayable(video)
            const isPicked = !blocked && video.relative_path === picked
            return <li key={video.video_id}>
              <button type="button" className={`rep-panel__item${isPicked ? ' is-picked' : ''}`} aria-pressed={isPicked} disabled={blocked} title={blocked ? unplayableText(video) : video.relative_path} onClick={() => setPicked(video.relative_path ?? '')}>
                <span className="rep-panel__name">{video.file_name ?? video.title}</span>
                {video.is_representative && <b className="rep-video-star"><Star aria-hidden="true" />대표</b>}
                {blocked ? <small className="rep-panel__blocked">{unplayableText(video)}</small> : <small>{formatBytes(video.size_bytes)}{video.codec ? ` · ${video.codec}` : ''}</small>}
              </button>
            </li>
          })}
        </ul>
        <div className="rep-panel__preview">
          <strong>선택한 영상 미리보기</strong>
          {selected?.asset_id ? <video key={selected.asset_id} aria-label={`${selected.file_name} 미리보기`} controls muted playsInline preload="metadata" src={simulationDashboardApi.assetUrl(selected.asset_id)} /> : <p className="rep-panel__state">미리볼 영상을 고르세요.</p>}
        </div>
      </div>}
    <footer className="rep-panel__foot">
      <Button size="sm" variant="primary" disabled={busy || !canChoose || !selected || selected.relative_path === current?.relative_path && current?.representative_source === 'USER'} onClick={save}>대표로 저장</Button>
      <Button size="sm" disabled={busy || !canChoose || current?.representative_source !== 'USER'} onClick={reset}>기본(첫 번째)으로 되돌리기</Button>
      <span className="rep-panel__meta" aria-live="polite">
        {!canChoose ? '대표 영상을 바꾸려면 결과 등록 권한이 필요합니다.'
          : choice ? `마지막 변경: ${choice.chosen_by_name || choice.chosen_by || '알 수 없음'}${choice.chosen_at ? ` · ${changedAtText(choice.chosen_at)}` : ''}`
          : current?.choice_missing ? '지정한 영상이 더 이상 없어 기본 대표를 표시합니다.'
          : current ? `현재 대표: ${sourceLabel(current.representative_source)}` : ''}
      </span>
      {notice && <span className={`rep-panel__notice${notice.error ? ' error' : ''}`} role={notice.error ? 'alert' : 'status'}>{notice.text}</span>}
    </footer>
  </section>
}
