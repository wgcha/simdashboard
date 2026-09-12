import { useEffect, useMemo, useRef } from 'react'
import { Check, Eye, X } from 'lucide-react'
import type { DashboardWidget } from '../../types'
import type { Layout } from 'react-grid-layout'
import './RecommendedLayoutPreview.css'

export type LayoutSuggestion = {
  widgets: DashboardWidget[]
  changedCount: number
  reason: string
  available: boolean
}

export function layoutFingerprint(widgets: DashboardWidget[], dashboardId: string, loadCaseId: string) {
  return JSON.stringify({ dashboardId, loadCaseId, widgets })
}

export function applyPositionOnly(current: DashboardWidget[], proposed: DashboardWidget[]): Layout[] {
  const proposedById = new Map(proposed.map((widget) => [widget.id, widget]))
  return current.map((widget) => {
    const next = proposedById.get(widget.id)
    return { i: widget.id, x: next?.x ?? widget.x, y: next?.y ?? widget.y, w: widget.w, h: widget.h }
  })
}

function Schematic({ label, testId, widgets }: { label: string; testId: string; widgets: DashboardWidget[] }) {
  return <section className="recommended-schematic" aria-label={label} data-testid={testId}>
    <h3>{label}</h3>
    <div className="recommended-schematic-grid">{widgets.map((widget) => <div key={widget.id} className="recommended-schematic-widget" data-widget-id={widget.id} data-x={widget.x} data-y={widget.y} data-w={widget.w} data-h={widget.h} style={{ gridColumn: `${Math.max(1, widget.x + 1)} / span ${Math.max(1, Math.min(12 - Math.max(0, widget.x), widget.w))}`, gridRow: `${Math.max(1, widget.y + 1)} / span ${Math.max(1, widget.h)}` }} title={`${widget.title} · ${widget.type}`}><strong>{widget.title}</strong></div>)}</div>
  </section>
}

type Props = { open: boolean; current: DashboardWidget[]; suggestion: LayoutSuggestion | null; stale: boolean; onApply: () => void; onClose: () => void }

export function RecommendedLayoutPreview({ open, current, suggestion, stale, onApply, onClose }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const triggerRef = useRef<HTMLElement | null>(null)
  const proposed = suggestion?.widgets ?? current
  const changed = suggestion?.changedCount ?? 0
  const unavailable = !suggestion?.available || !changed
  const closePreview = () => { onClose(); queueMicrotask(() => triggerRef.current?.focus()) }
  const summary = !suggestion ? '추천 배치를 계산하는 중입니다.' : !suggestion.available ? '현재 위젯 배치에서는 추천안을 만들 수 없습니다.' : !changed ? '현재 배치가 이미 권장 상태입니다.' : `${changed}개 위젯의 위치 변경을 제안합니다.`

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (open && !dialog.open) { triggerRef.current = document.activeElement as HTMLElement; dialog.showModal(); dialog.querySelector<HTMLElement>('[autofocus]')?.focus() }
    if (!open && dialog.open) dialog.close()
  }, [open])

  useEffect(() => {
    if (!open) return
    const dialog = dialogRef.current
    const restore = () => triggerRef.current?.focus()
    dialog?.addEventListener('close', restore)
    return () => dialog?.removeEventListener('close', restore)
  }, [open])

  const evidence = useMemo(() => suggestion?.reason || '위젯 유형과 현재 크기, 행 배치를 함께 비교했습니다.', [suggestion?.reason])
  return <dialog ref={dialogRef} data-testid="recommended-layout-preview" className="recommended-layout-dialog" aria-labelledby="recommended-layout-title" onCancel={(event) => { event.preventDefault(); closePreview() }}>
    <header><div><span className="recommended-layout-kicker"><Eye /> LAYOUT REVIEW</span><h2 id="recommended-layout-title">추천 배치 미리보기</h2></div><button type="button" aria-label="추천 배치 미리보기 닫기" onClick={closePreview}><X /></button></header>
    <div className="recommended-layout-scroll"><p className="recommended-layout-summary">{stale ? '대시보드가 변경되어 이 미리보기는 오래되었습니다. 새로 미리보기를 열어 주세요.' : summary}</p><div className="recommended-schematics"><Schematic label="현재 배치" testId="layout-preview-current" widgets={current} /><Schematic label="추천 배치" testId="layout-preview-proposed" widgets={proposed} /></div><div className="recommended-layout-explanation"><div><strong>근거</strong><p>{evidence}</p></div><div><strong>적용 범위</strong><p>위젯 크기와 설정은 유지됩니다. 적용 후 상단 레이아웃 저장을 눌러야 저장됩니다.</p></div></div></div>
    <footer><button type="button" className="recommended-secondary" onClick={closePreview}>닫기</button><button type="button" className="recommended-primary" onClick={onApply} disabled={stale || unavailable}><Check /> 편집안에 적용</button></footer>
  </dialog>
}
