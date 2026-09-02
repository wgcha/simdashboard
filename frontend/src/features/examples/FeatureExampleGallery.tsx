import { useEffect, useState } from 'react'
import { AlertTriangle, LoaderCircle, Play, Search } from 'lucide-react'
import { api } from '../../api'
import type { FeatureExample } from '../../types'

type FeatureExampleGalleryProps = {
  onOpen: (example: FeatureExample) => Promise<void>
}

export function FeatureExampleGallery({ onOpen }: FeatureExampleGalleryProps) {
  const [items, setItems] = useState<FeatureExample[]>([])
  const [category, setCategory] = useState('전체')
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')

  useEffect(() => { api.featureExamples().then(setItems).catch((reason) => setError(reason instanceof Error ? reason.message : '예제 목록을 불러오지 못했습니다.')) }, [])

  const categories = ['전체', ...Array.from(new Set(items.map((item) => item.category)))]
  const filtered = items.filter((item) => (category === '전체' || item.category === category) && (!query || `${item.title} ${item.summary} ${item.features.join(' ')}`.toLowerCase().includes(query.toLowerCase())))
  const profileEntries = (profile: FeatureExample['data_profile']) => ([['Run', profile.runs], ['수치', profile.scalars], ['시계열', profile.series], ['곡선', profile.curves], ['미디어', profile.media], ['검토', profile.reviews]] as const).filter(([, value]) => value > 0)

  return <section className="example-gallery">
    <header><div><span>FEATURE SHOWCASE</span><h1>기능 예제 갤러리</h1><p>기존 데이터를 건드리지 않고, 보고 싶은 기능과 상태를 골라 바로 체험하세요.</p></div><aside><strong>{items.length || '-'}개</strong><small>독립 시나리오</small></aside></header>
    <div className="example-guide"><div><Play /><span><strong>추천 순서</strong> Run 비교 → 신뢰도 정상/경고 → 검토 → 다중 결과형 → PPT 편집</span></div><p>각 카드의 ‘확인할 것’은 그 예제에서 재현되는 기대 결과입니다. WARN·NO DATA·BLOCKED도 의도된 정상 예제입니다.</p></div>
    <div className="example-filters"><label><Search /><input aria-label="예제 검색" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="기능, 상태, 데이터형 검색" /></label><nav>{categories.map((item) => <button key={item} className={category === item ? 'active' : ''} onClick={() => setCategory(item)}>{item}</button>)}</nav></div>
    {error ? <div className="portfolio-state error"><AlertTriangle />{error}</div> : !items.length ? <div className="portfolio-state"><LoaderCircle className="spin" />예제를 준비하고 있습니다.</div> : <div className="example-grid">{filtered.map((item) => <article key={item.id} className={`example-card ${item.badge.toLowerCase().replaceAll(' ', '-')}`}>
      <header><span>{String(item.order).padStart(2, '0')} · {item.category}</span><b>{item.badge}</b></header><h2>{item.title}</h2><p>{item.summary}</p>
      <div className="example-tags">{item.features.map((feature) => <span key={feature}>{feature}</span>)}</div>
      {profileEntries(item.data_profile).length > 0 && <div className="example-profile">{profileEntries(item.data_profile).map(([label, value]) => <span key={label}><small>{label}</small><strong>{value}</strong></span>)}</div>}
      <section><strong>확인할 것</strong><ol>{item.checks.map((check) => <li key={check}>{check}</li>)}</ol></section>
      {item.action_hint && <small className="example-action-hint">{item.action_hint}</small>}
      <button onClick={() => void onOpen(item)}><Play /> 이 예제 열기</button>
    </article>)}</div>}
    {items.length > 0 && filtered.length === 0 && <div className="portfolio-empty"><Search /><h2>조건에 맞는 예제가 없습니다.</h2><button onClick={() => { setCategory('전체'); setQuery('') }}>필터 초기화</button></div>}
  </section>
}
