import { useEffect, useState, type ComponentType, type CSSProperties } from 'react'
import { Plus, Save, Trash2, Upload } from 'lucide-react'
import { Responsive, WidthProvider, type Layout } from 'react-grid-layout'
import { reportVariables } from './reportLayoutUtils'
import type { Overview, ReportContentItem, ReportElementDefinition, ReportElementType, ReportLayoutDefinition, ReportSlideDefinition, ReportSlideKind, ReportTemplateAsset } from '../../types'

const ResponsiveGridLayout = WidthProvider(Responsive) as unknown as ComponentType<any>

type ReportLayoutEditorProps = {
  layout: ReportLayoutDefinition
  overview: Overview
  contents: ReportContentItem[]
  templates: ReportTemplateAsset[]
  isSystem: boolean
  onChange: (layout: ReportLayoutDefinition) => void
  onTemplateUpload: (file: File) => void
  onTemplateDelete: (id: string) => void
  onSave: () => void
  onSaveAs: () => void
  onDelete: () => void
}

export function ReportLayoutEditor({ layout, overview, contents, templates, isSystem, onChange, onTemplateUpload, onTemplateDelete, onSave, onSaveAs, onDelete }: ReportLayoutEditorProps) {
  const slides = layout.slides ?? []
  const variables = reportVariables(overview)
  const [activeSlideId, setActiveSlideId] = useState(slides[0]?.id ?? '')
  const [selectedElementId, setSelectedElementId] = useState<string | null>(null)
  const [editingTextElementId, setEditingTextElementId] = useState<string | null>(null)
  const [templateSlide, setTemplateSlide] = useState(1)
  const activeSlide = slides.find((item) => item.id === activeSlideId) ?? slides[0]
  const selectedElement = activeSlide?.elements.find((item) => item.id === selectedElementId)
  const activeTemplate = templates.find((item) => item.id === layout.templateAssetId)
  const selectedVariables = new Map(layout.variablePlacements.map((item) => [item.variableKey, item]))
  const master = layout.slideMaster ?? { backgroundColor: 'F8FBFD', design: 'frame' as const, accentColor: layout.accentColor }
  const usesMaster = activeSlide?.style?.useMaster !== false
  const activeSlideStyle = usesMaster ? master : {
    backgroundColor: activeSlide?.style?.backgroundColor ?? master.backgroundColor,
    design: activeSlide?.style?.design ?? master.design,
    accentColor: activeSlide?.style?.accentColor ?? master.accentColor,
  }
  const assignedVariable = variables.find((item) => item.key === selectedElement?.binding?.variableKey)

  useEffect(() => {
    if (!slides.some((item) => item.id === activeSlideId)) setActiveSlideId(slides[0]?.id ?? '')
  }, [layout.id, layout.version, slides.length, activeSlideId])

  useEffect(() => {
    const selectOrEditDirectText = (event: MouseEvent) => {
      const target = event.target instanceof Element ? event.target.closest('.report-slide-widget.direct-text') : null
      const canvas = target?.closest('.report-slide-layout')
      if (!target || !canvas || !activeSlide) return
      const index = Array.from(canvas.querySelectorAll('.report-slide-widget')).indexOf(target)
      const elementId = activeSlide.elements[index]?.id
      if (!elementId) return
      if (selectedElementId === elementId) {
        event.preventDefault()
        setEditingTextElementId(elementId)
      }
      setSelectedElementId(elementId)
    }
    document.addEventListener('mousedown', selectOrEditDirectText, true)
    return () => document.removeEventListener('mousedown', selectOrEditDirectText, true)
  }, [activeSlide, selectedElementId])

  const updateSlide = (slideId: string, updater: (slide: ReportSlideDefinition) => ReportSlideDefinition) => {
    onChange({ ...layout, slides: slides.map((slide) => slide.id === slideId ? updater(slide) : slide) })
  }
  const updateElement = (patch: Partial<ReportElementDefinition>) => {
    if (!activeSlide || !selectedElement) return
    updateElementById(selectedElement.id, patch)
  }
  const updateElementById = (elementId: string, patch: Partial<ReportElementDefinition>) => {
    if (!activeSlide) return
    updateSlide(activeSlide.id, (slide) => ({ ...slide, elements: slide.elements.map((item) => item.id === elementId ? { ...item, ...patch, style: { ...item.style, ...patch.style }, binding: patch.binding ?? item.binding } : item) }))
  }
  const addElement = (type: ReportElementType, variableKey?: string, position?: { x: number; y: number }) => {
    if (!activeSlide) return
    const size: Record<ReportElementType, [number, number]> = { title: [20, 2], text: [10, 5], verdict: [5, 2], 'scalar-card': [7, 4], chart: [16, 9], table: [13, 8], image: [16, 10] }
    const [w, h] = size[type]
    const element: ReportElementDefinition = {
      id: `report-element-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`, type,
      label: variableKey ? variables.find((item) => item.key === variableKey)?.name ?? variableKey : ({ title: '제목', text: '텍스트', verdict: '종합 판정', 'scalar-card': '결과 카드', chart: '차트', table: '결과표', image: '이미지' } as Record<ReportElementType, string>)[type],
      x: Math.min(32 - w, Math.max(0, position?.x ?? 1)), y: Math.min(18 - h, Math.max(0, position?.y ?? 4)), w, h, z: activeSlide.elements.length + 1,
      text: type === 'title' ? '제목을 입력하세요' : type === 'text' ? '텍스트를 입력하세요' : undefined,
      binding: variableKey ? { source: 'variable', variableKey, key: variableKey } : type === 'verdict' ? { source: 'field', key: 'verdict' } : { source: 'static' },
      rules: { visibleWhenData: true },
    }
    updateSlide(activeSlide.id, (slide) => ({ ...slide, elements: [...slide.elements, element] }))
    setSelectedElementId(element.id)
  }
  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    if (!activeSlide) return
    try {
      const payload = JSON.parse(event.dataTransfer.getData('application/json')) as { type?: ReportElementType; variableKey?: string }
      const rect = event.currentTarget.getBoundingClientRect()
      addElement(payload.type ?? (payload.variableKey && overview.time_series.some((item) => item.variable_key === payload.variableKey) ? 'chart' : 'scalar-card'), payload.variableKey, { x: Math.floor((event.clientX - rect.left) / rect.width * 32), y: Math.floor((event.clientY - rect.top) / rect.height * 18) })
    } catch { /* unsupported external drop */ }
  }
  const updateCanvasLayout = (items: Layout[]) => {
    if (!activeSlide) return
    updateSlide(activeSlide.id, (slide) => ({ ...slide, elements: slide.elements.map((element) => {
      const item = items.find((entry) => entry.i === element.id)
      return item ? { ...element, x: item.x, y: item.y, w: item.w, h: item.h } : element
    }) }))
  }
  const duplicateSlide = () => {
    if (!activeSlide) return
    const id = `slide-${Date.now()}`
    const copy = { ...activeSlide, id, name: `${activeSlide.name} 복사본`, kind: 'custom' as ReportSlideKind, repeat: 'none' as const, elements: activeSlide.elements.map((item) => ({ ...item, id: `${item.id}-${Date.now()}` })) }
    const index = slides.indexOf(activeSlide)
    onChange({ ...layout, slides: [...slides.slice(0, index + 1), copy, ...slides.slice(index + 1)] })
    setActiveSlideId(id)
  }
  const addSlide = () => {
    const id = `slide-custom-${Date.now()}`
    const slide: ReportSlideDefinition = { id, name: '사용자 슬라이드', kind: 'custom', repeat: 'none', elements: [] }
    onChange({ ...layout, slides: [...slides, slide] }); setActiveSlideId(id); setSelectedElementId(null)
  }
  const contentElements = (content: ReportContentItem): ReportElementDefinition[] => {
    const bodyType: ReportElementType = content.defaultPresentation === 'card' ? 'scalar-card' : content.defaultPresentation
    const suffix = `${Date.now()}-${Math.random().toString(16).slice(2, 6)}`
    return [
      { id: `content-title-${suffix}`, type: 'title', label: content.title, x: 1, y: 1, w: 30, h: 2, z: 1, binding: { source: 'content', contentId: content.contentId, key: 'title' }, rules: { visibleWhenData: true } },
      { id: `content-body-${suffix}`, type: bodyType, label: content.title, x: 1, y: 4, w: 30, h: 12, z: 2, binding: { source: 'content', contentId: content.contentId, key: 'body' }, rules: { visibleWhenData: true } },
    ]
  }
  const contentSlideId = (contentId: string) => slides.find((slide) => slide.elements.some((element) => element.binding?.contentId === contentId))?.id ?? ''
  const toggleContent = (content: ReportContentItem) => {
    const existingSlideId = contentSlideId(content.contentId)
    if (existingSlideId) {
      const nextSlides = slides.map((slide) => ({ ...slide, elements: slide.elements.filter((element) => element.binding?.contentId !== content.contentId) }))
        .filter((slide) => slide.kind === 'cover' || slide.elements.length > 0)
      onChange({ ...layout, contentMode: 'manual', slides: nextSlides })
      return
    }
    const id = `slide-content-${Date.now()}`
    onChange({ ...layout, contentMode: 'manual', slides: [...slides, { id, name: content.title, kind: 'custom', repeat: 'none', elements: contentElements(content) }] })
    setActiveSlideId(id)
  }
  const moveContentToSlide = (content: ReportContentItem, slideId: string) => {
    const elements = contentElements(content)
    onChange({ ...layout, contentMode: 'manual', slides: slides.map((slide) => ({
      ...slide,
      elements: [...slide.elements.filter((element) => element.binding?.contentId !== content.contentId), ...(slide.id === slideId ? elements : [])],
    })).filter((slide) => slide.kind === 'cover' || slide.id === slideId || slide.elements.length > 0) })
    setActiveSlideId(slideId)
  }
  const deleteSlide = (slideId: string) => {
    const next = slides.filter((slide) => slide.id !== slideId)
    onChange({ ...layout, contentMode: 'manual', slides: next })
    setActiveSlideId(next[0]?.id ?? '')
    setSelectedElementId(null)
  }
  const moveSlide = (index: number, direction: -1 | 1) => {
    const target = index + direction
    if (target < 0 || target >= slides.length) return
    const next = [...slides]; [next[index], next[target]] = [next[target], next[index]]; onChange({ ...layout, slides: next })
  }
  const toggleVariable = (key: string) => {
    const exists = selectedVariables.has(key)
    onChange({ ...layout, variablePlacements: exists ? layout.variablePlacements.filter((item) => item.variableKey !== key) : [...layout.variablePlacements, { variableKey: key, presentation: 'both', order: layout.variablePlacements.length }] })
  }
  const fieldOptions = [
    ['field:report_title', '보고서 제목'], ['field:project_name', '프로젝트명'], ['field:author_stage', '작성자 · 개발단계'], ['field:report_date', '작성날짜'], ['field:verdict', '종합 판정'], ['field:reliability_name', '시뮬레이션 종류'], ['field:review_purpose', '검토 목적'], ['field:review_conditions', '검토 사양 조건'], ['field:review_result', '검토 결과'], ['field:review_conclusion', '검토 결론'],
  ]
  const previewText = (element: ReportElementDefinition) => {
    if (element.binding?.source === 'field') return fieldOptions.find(([value]) => value === `field:${element.binding?.key}`)?.[1] ?? element.label
    if (element.binding?.source === 'variable') return variables.find((item) => item.key === element.binding?.variableKey)?.preview ?? element.label
    if (element.binding?.source === 'static') return element.text ?? element.label
    if (element.binding?.source === 'content') return contents.find((item) => item.contentId === element.binding?.contentId)?.title ?? element.label
    return element.label
  }

  return <section className="report-layout-editor visual-report-editor">
    <div className="report-layout-fields">
      <label><span>레이아웃 이름</span><input value={layout.name} onChange={(event) => onChange({ ...layout, name: event.target.value })} /></label>
      <label><span>출력 원본</span><select value={layout.templateSource ?? 'native'} onChange={(event) => onChange({ ...layout, templateSource: event.target.value as 'native' | 'pptx_upload' })}><option value="native">시각적 레이아웃</option><option value="pptx_upload">업로드 PPTX</option></select></label>
      <label><span>강조색</span><input type="color" value={`#${layout.accentColor}`} onChange={(event) => onChange({ ...layout, accentColor: event.target.value.slice(1).toUpperCase() })} /></label>
      <label className="wide"><span>설명</span><input value={layout.description} onChange={(event) => onChange({ ...layout, description: event.target.value })} /></label>
    </div>
    {layout.templateSource !== 'pptx_upload' && <div className="report-master-toolbar">
      <strong>슬라이드 마스터</strong><small>새 슬라이드와 마스터 사용 슬라이드에 공통 적용됩니다.</small>
      <label><span>마스터 디자인</span><select aria-label="슬라이드 마스터 디자인" value={master.design} onChange={(event) => onChange({ ...layout, slideMaster: { ...master, design: event.target.value as typeof master.design } })}><option value="plain">배경만</option><option value="frame">프레임</option><option value="header-band">상단 띠</option><option value="split">분할 패널</option></select></label>
      <label><span>배경색</span><input aria-label="슬라이드 마스터 배경색" type="color" value={`#${master.backgroundColor}`} onChange={(event) => onChange({ ...layout, slideMaster: { ...master, backgroundColor: event.target.value.slice(1).toUpperCase() } })} /></label>
      <label><span>디자인 색상</span><input aria-label="슬라이드 마스터 디자인 색상" type="color" value={`#${master.accentColor}`} onChange={(event) => onChange({ ...layout, slideMaster: { ...master, accentColor: event.target.value.slice(1).toUpperCase() } })} /></label>
      <button onClick={() => onChange({ ...layout, slides: slides.map((slide) => ({ ...slide, style: { ...slide.style, useMaster: true } })) })}>전체 슬라이드에 적용</button>
    </div>}

    {layout.templateSource === 'pptx_upload' ? <div className="pptx-template-editor">
      <aside className="report-slide-list"><header><strong>업로드 템플릿</strong><label><Upload /> PPTX 추가<input type="file" accept=".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation" onChange={(event) => { const file = event.target.files?.[0]; if (file) onTemplateUpload(file); event.currentTarget.value = '' }} /></label></header>{templates.map((template) => <button key={template.id} className={template.id === layout.templateAssetId ? 'active' : ''} onClick={() => { onChange({ ...layout, templateAssetId: template.id, templateBindings: {} }); setTemplateSlide(1) }}><span>{template.name}</span><small>{template.slide_count}장 · 태그 {template.definition.placeholders.length}개</small></button>)}</aside>
      <div className="pptx-preview-column"><div className="pptx-slide-tabs">{Array.from({ length: activeTemplate?.slide_count ?? 0 }, (_, index) => <button key={index} className={templateSlide === index + 1 ? 'active' : ''} onClick={() => setTemplateSlide(index + 1)}>{index + 1}</button>)}</div><div className="pptx-placeholder-canvas">{activeTemplate?.definition.placeholders.filter((item) => item.slideIndex === templateSlide).map((placeholder) => <div key={placeholder.id} className={`pptx-placeholder ${placeholder.kind}`} style={{ left: `${placeholder.x * 100}%`, top: `${placeholder.y * 100}%`, width: `${placeholder.w * 100}%`, height: `${placeholder.h * 100}%` }}><b>{placeholder.kind.toUpperCase()}</b><span>{placeholder.token}</span></div>)}{activeTemplate && !activeTemplate.definition.placeholders.some((item) => item.slideIndex === templateSlide) && <p>이 슬라이드에는 인식된 태그가 없습니다.</p>}</div></div>
      <aside className="report-properties"><header><strong>태그 변수 연결</strong><small>PowerPoint의 도형 이름 또는 &#123;&#123;태그&#125;&#125;를 연결합니다.</small></header>{activeTemplate?.definition.placeholders.map((placeholder) => <label key={placeholder.id}><span>{placeholder.slideIndex}쪽 · {placeholder.shapeName}</span><code>{placeholder.token}</code><select value={layout.templateBindings?.[placeholder.id] ?? placeholder.token} onChange={(event) => onChange({ ...layout, templateBindings: { ...layout.templateBindings, [placeholder.id]: event.target.value } })}><option value={placeholder.token}>태그 기본 연결 · {placeholder.token}</option>{fieldOptions.filter(([value]) => value !== placeholder.token).map(([value, label]) => <option key={value} value={value}>{label}</option>)}{variables.map((variable) => <option key={variable.key} value={`variable:${variable.key}`}>{variable.name}</option>)}</select></label>)}{activeTemplate && <button className="danger" onClick={() => onTemplateDelete(activeTemplate.id)}><Trash2 /> 템플릿 삭제</button>}</aside>
    </div> : <>
      <div className="report-designer-toolbar"><span>32 × 18 그리드 · 16:9</span><button onClick={addSlide}><Plus /> 빈 슬라이드</button><button onClick={duplicateSlide} disabled={!activeSlide}><Plus /> 슬라이드 복제</button><label className="media-toggle"><input type="checkbox" checked={layout.includeMedia} onChange={(event) => onChange({ ...layout, includeMedia: event.target.checked })} /> 미디어 포함</label></div>
      {activeSlide && <div className="report-slide-style-toolbar"><label><input aria-label="현재 슬라이드에 마스터 사용" type="checkbox" checked={usesMaster} onChange={(event) => updateSlide(activeSlide.id, (slide) => ({ ...slide, style: { ...slide.style, useMaster: event.target.checked } }))} /> 현재 슬라이드에 마스터 사용</label>{!usesMaster && <><label><span>디자인</span><select aria-label="현재 슬라이드 디자인" value={activeSlideStyle.design} onChange={(event) => updateSlide(activeSlide.id, (slide) => ({ ...slide, style: { ...slide.style, useMaster: false, design: event.target.value as typeof master.design } }))}><option value="plain">배경만</option><option value="frame">프레임</option><option value="header-band">상단 띠</option><option value="split">분할 패널</option></select></label><label><span>배경색</span><input aria-label="현재 슬라이드 배경색" type="color" value={`#${activeSlideStyle.backgroundColor}`} onChange={(event) => updateSlide(activeSlide.id, (slide) => ({ ...slide, style: { ...slide.style, useMaster: false, backgroundColor: event.target.value.slice(1).toUpperCase() } }))} /></label><label><span>디자인 색상</span><input aria-label="현재 슬라이드 디자인 색상" type="color" value={`#${activeSlideStyle.accentColor}`} onChange={(event) => updateSlide(activeSlide.id, (slide) => ({ ...slide, style: { ...slide.style, useMaster: false, accentColor: event.target.value.slice(1).toUpperCase() } }))} /></label></>}</div>}
      {contents.length > 0 && <section className="report-content-palette"><header><strong>보고서 콘텐츠</strong><small>포함 여부와 배치 슬라이드를 선택합니다.</small></header>{contents.map((content) => { const assignedSlideId = contentSlideId(content.contentId); return <div key={content.contentId}><label><input type="checkbox" checked={Boolean(assignedSlideId)} onChange={() => toggleContent(content)} /><span>{content.title}</span><b>{content.defaultPresentation}</b></label><select aria-label={`${content.title} 배치 슬라이드`} value={assignedSlideId} disabled={!assignedSlideId} onChange={(event) => moveContentToSlide(content, event.target.value)}>{slides.filter((slide) => slide.kind !== 'cover').map((slide, index) => <option key={slide.id} value={slide.id}>{index + 1} · {slide.name}</option>)}</select></div> })}</section>}
      <div className="report-designer-grid">
        <aside className="report-slide-list"><header><strong>슬라이드</strong><small>위아래로 출력 순서를 바꿉니다.</small></header>{slides.map((slide, index) => <div key={slide.id} className={slide.id === activeSlide?.id ? 'active' : ''} onClick={() => { setActiveSlideId(slide.id); setSelectedElementId(null) }}><span>{String(index + 1).padStart(2, '0')}</span><button>{slide.name}<small>{slide.kind}{slide.repeat !== 'none' ? ' · 반복' : ''}</small></button><nav><button onClick={(event) => { event.stopPropagation(); moveSlide(index, -1) }} disabled={index === 0}>↑</button><button onClick={(event) => { event.stopPropagation(); moveSlide(index, 1) }} disabled={index === slides.length - 1}>↓</button>{slide.kind !== 'cover' && <button aria-label={`${slide.name} 슬라이드 삭제`} onClick={(event) => { event.stopPropagation(); deleteSlide(slide.id) }}><Trash2 /></button>}</nav></div>)}</aside>
        <div className="report-canvas-column"><div className="report-widget-palette">{(['title', 'text', 'verdict', 'scalar-card', 'chart', 'table', 'image'] as ReportElementType[]).map((type) => <button key={type} draggable onDragStart={(event) => event.dataTransfer.setData('application/json', JSON.stringify({ type }))} onClick={() => addElement(type)}>{({ title: '제목', text: '텍스트 상자', verdict: '판정', 'scalar-card': '결과 카드', chart: '차트', table: '표', image: '이미지' } as Record<ReportElementType, string>)[type]}</button>)}</div>{activeSlide && <div className={`report-slide-canvas design-${activeSlideStyle.design}`} style={{ '--slide-background': `#${activeSlideStyle.backgroundColor}`, '--slide-accent': `#${activeSlideStyle.accentColor}` } as CSSProperties} onDragOver={(event) => event.preventDefault()} onDrop={handleDrop}><div className="report-slide-design"/><ResponsiveGridLayout className="report-slide-layout" layouts={{ lg: activeSlide.elements.map((item) => ({ i: item.id, x: item.x, y: item.y, w: item.w, h: item.h })) }} breakpoints={{ lg: 0 }} cols={{ lg: 32 }} rowHeight={20} margin={[0, 0]} containerPadding={[0, 0]} maxRows={18} compactType={null} preventCollision isDraggable isResizable onLayoutChange={updateCanvasLayout}>{activeSlide.elements.map((element) => { const directText = ['title', 'text'].includes(element.type) && element.binding?.source === 'static'; return <div key={element.id} className={`report-slide-widget ${element.type} ${directText ? 'direct-text' : ''} ${selectedElementId === element.id ? 'selected' : ''}`} style={{ color: element.style?.color, backgroundColor: element.style?.fill, textAlign: element.style?.align, fontSize: element.style?.fontSize ? `${element.style.fontSize}px` : undefined }} onMouseDown={() => setSelectedElementId(element.id)} onDoubleClick={(event) => { if (!directText) return; event.stopPropagation(); setSelectedElementId(element.id); setEditingTextElementId(element.id) }}>{!directText && <b>{element.label}</b>}{editingTextElementId === element.id ? <textarea aria-label="슬라이드 텍스트 직접 편집" autoFocus value={element.text ?? element.label} onChange={(event) => updateElementById(element.id, { text: event.target.value })} onMouseDown={(event) => event.stopPropagation()} onBlur={() => setEditingTextElementId(null)} /> : <span className={directText ? 'direct-text-content' : ''}>{previewText(element)}</span>}{element.binding?.variableKey && <code>{element.binding.variableKey}</code>}</div> })}</ResponsiveGridLayout></div>}</div>
        <aside className="report-properties"><header><strong>속성 · 변수</strong><small>변수를 끌어 캔버스에 놓거나 샘플값을 미리 볼 수 있습니다.</small></header>{selectedElement ? <div className="report-element-properties"><label><span>표시 이름</span><input value={selectedElement.label} onChange={(event) => updateElement({ label: event.target.value })} /></label>{['title', 'text'].includes(selectedElement.type) && selectedElement.binding?.source === 'static' && <label><span>텍스트 내용</span><textarea aria-label="텍스트 상자 내용" value={selectedElement.text ?? selectedElement.label} onChange={(event) => updateElement({ text: event.target.value })} /></label>}<label><span>위젯 형식</span><select value={selectedElement.type} onChange={(event) => updateElement({ type: event.target.value as ReportElementType })}>{(['title', 'text', 'verdict', 'scalar-card', 'chart', 'table', 'image'] as ReportElementType[]).map((type) => <option key={type}>{type}</option>)}</select></label><label><span>데이터 연결</span><select value={selectedElement.binding?.source === 'variable' ? `variable:${selectedElement.binding.variableKey}` : selectedElement.binding?.source === 'field' ? `field:${selectedElement.binding.key}` : 'static:'} onChange={(event) => { const [source, key] = event.target.value.split(':'); updateElement({ binding: source === 'variable' ? { source: 'variable', key, variableKey: key } : source === 'field' ? { source: 'field', key } : { source: 'static' } }) }}><option value="static:">고정 텍스트</option>{fieldOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}{variables.map((variable) => <option key={variable.key} value={`variable:${variable.key}`}>{variable.name} · {variable.kind === 'series' ? '시계열' : '정량'} · {variable.unit}</option>)}</select></label>{assignedVariable && <div className="report-binding-preview"><span><b>{assignedVariable.name}</b><em>{assignedVariable.kind === 'series' ? '시계열' : '정량'} · {assignedVariable.unit}</em></span><code>{assignedVariable.key}</code><strong>{assignedVariable.preview}</strong></div>}<div className="report-property-row"><label><span>글자 크기</span><input type="number" min="7" max="40" value={selectedElement.style?.fontSize ?? 10} onChange={(event) => updateElement({ style: { fontSize: Number(event.target.value) } })} /></label><label><span>정렬</span><select value={selectedElement.style?.align ?? 'left'} onChange={(event) => updateElement({ style: { align: event.target.value as 'left' | 'center' | 'right' } })}><option value="left">왼쪽</option><option value="center">가운데</option><option value="right">오른쪽</option></select></label></div><div className="report-property-row"><label><span>글자색</span><input type="color" value={selectedElement.style?.color ?? '#172033'} onChange={(event) => updateElement({ style: { color: event.target.value } })} /></label><label><span>채우기</span><input type="color" value={selectedElement.style?.fill ?? '#FFFFFF'} onChange={(event) => updateElement({ style: { fill: event.target.value } })} /></label></div><button className="danger" onClick={() => { updateSlide(activeSlide.id, (slide) => ({ ...slide, elements: slide.elements.filter((item) => item.id !== selectedElement.id) })); setSelectedElementId(null) }}><Trash2 /> 위젯 삭제</button></div> : <p className="report-empty-properties">캔버스의 위젯을 선택하면 데이터와 스타일을 편집할 수 있습니다.</p>}<div className="report-variable-palette"><strong>변수 카탈로그 · 현재 값 미리보기</strong>{variables.map((variable) => { const placement = selectedVariables.get(variable.key); return <div key={variable.key} draggable onDragStart={(event) => event.dataTransfer.setData('application/json', JSON.stringify({ variableKey: variable.key }))}><label><input type="checkbox" checked={Boolean(placement)} onChange={() => toggleVariable(variable.key)} /><span>{variable.name}</span><b>{variable.kind === 'series' ? '시계열' : '정량'} · {variable.unit}</b></label><code>{variable.key}</code><small>{variable.preview}</small>{placement && <select value={placement.presentation} onChange={(event) => onChange({ ...layout, variablePlacements: layout.variablePlacements.map((item) => item.variableKey === variable.key ? { ...item, presentation: event.target.value as 'chart' | 'table' | 'both' } : item) })}><option value="both">차트+표</option><option value="chart">차트</option><option value="table">표</option></select>}</div>})}</div></aside>
      </div>
    </>}
    <footer><button onClick={onSave}><Save /> 현재 레이아웃 새 버전 저장</button><button onClick={onSaveAs}><Plus /> 다른 이름으로 저장</button><button className="danger" disabled={isSystem} title={isSystem ? '기본 레이아웃은 삭제할 수 없습니다.' : ''} onClick={onDelete}><Trash2 /> 삭제</button></footer>
  </section>
}
