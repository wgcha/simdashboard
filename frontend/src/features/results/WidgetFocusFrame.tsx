import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { GripVertical, Maximize2, Minimize2, Settings2, X } from 'lucide-react'
import './WidgetFocusFrame.css'

type Props = {
  children: ReactNode
  className: string
  customFontSize?: number
  editMode: boolean
  onConfigure: () => void
  onRemove: () => void
  title: string
  type: string
  widgetId: string
}

export function WidgetFocusFrame({ children, className, customFontSize, editMode, onConfigure, onRemove, title, type, widgetId }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const expandButtonRef = useRef<HTMLButtonElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const wasModalRef = useRef(false)
  const [isFocused, setIsFocused] = useState(false)
  const titleId = `widget-focus-title-${widgetId}`
  const style = Number.isFinite(customFontSize) ? { '--widget-font-size': `${customFontSize! * 1.2}px` } as CSSProperties : undefined

  useEffect(() => {
    const dialog = dialogRef.current
    if (dialog && !dialog.open) dialog.show()
    return () => { if (dialog?.open) dialog.close() }
  }, [])
  useEffect(() => {
    if (editMode && wasModalRef.current) dialogRef.current?.close()
  }, [editMode])

  const restoreInline = () => {
    const dialog = dialogRef.current
    if (!dialog || dialog.open) return
    dialog.show()
    expandButtonRef.current?.focus()
  }
  const collapse = () => dialogRef.current?.close()
  const expand = () => {
    const dialog = dialogRef.current
    if (!dialog || editMode) return
    dialog.close()
    dialog.showModal()
    wasModalRef.current = true
    setIsFocused(true)
    requestAnimationFrame(() => closeButtonRef.current?.focus())
  }

  return <dialog ref={dialogRef} role={isFocused ? 'dialog' : 'article'} className={className} data-custom-font={Number.isFinite(customFontSize) ? 'true' : undefined} style={style} aria-modal={isFocused || undefined} aria-labelledby={titleId} onCancel={(event) => { if (wasModalRef.current) { event.preventDefault(); collapse() } }} onClose={() => {
    if (dialogRef.current?.open) return
    if (!wasModalRef.current) return
    wasModalRef.current = false
    setIsFocused(false)
    requestAnimationFrame(restoreInline)
  }}>
    <header>
      <div className={editMode ? 'widget-drag-handle' : undefined} aria-label={editMode ? `${title} 이동 손잡이` : undefined}>
        {editMode && <GripVertical />}
        <div>{editMode && <span className="widget-kicker">{type.replace('_', ' ')}</span>}<h3 id={titleId}>{title}</h3></div>
      </div>
      {editMode ? <div className="widget-edit-actions"><button aria-label={`${title} 설정`} onClick={onConfigure}><Settings2 /></button><button aria-label={`${title} 삭제`} onClick={onRemove}><X /></button></div> : isFocused ? <button ref={closeButtonRef} className="widget-focus-button" type="button" aria-label={`${title} 원래 크기로`} onClick={collapse}><Minimize2 /><span>원래 크기</span></button> : <button ref={expandButtonRef} className="widget-focus-button" type="button" aria-label={`${title} 확대 보기`} onClick={expand}><Maximize2 /><span>확대 보기</span></button>}
    </header>
    <div className="widget-body">{children}</div>
  </dialog>
}
