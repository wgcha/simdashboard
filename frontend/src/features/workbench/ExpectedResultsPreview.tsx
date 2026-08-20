import { LayoutDashboard, LoaderCircle } from 'lucide-react'

import type { ResultProfile } from './types'

export function ExpectedResultsPreview({ profile, loading }: { profile: ResultProfile | null; loading: boolean }) {
  if (loading) return <section className="expected-results-preview" aria-live="polite"><header><div><span>EXPECTED RESULTS</span><h3>예상 결과 구성</h3></div><LoaderCircle className="spin" /></header><p>선택한 작업 유형의 결과 구성을 확인하고 있습니다.</p></section>

  if (!profile) return <section className="expected-results-preview" data-testid="expected-results-preview"><header><div><span>EXPECTED RESULTS</span><h3>결과 구성 미지정</h3></div><strong>UNCONFIGURED</strong></header><div className="expected-results-empty">이 작업 유형에는 결과 레이아웃이 연결되지 않았습니다. 의뢰는 정상 접수되며, 결과 화면에서는 중립적인 빈 상태가 표시됩니다.</div></section>

  const selected = new Set(profile.included_widget_ids)
  const pages = profile.template.page_definitions.map((page) => ({
    ...page,
    widgets: page.widgets.filter((widget) => selected.size === 0 || selected.has(widget.id)),
  })).filter((page) => page.widgets.length > 0)

  return <section className="expected-results-preview" data-testid="expected-results-preview">
    <header><div><span>EXPECTED RESULTS · TEMPLATE v{profile.template_version}</span><h3>{profile.template.display_name}</h3><p>의뢰 생성 시 현재 구성과 위젯 배치가 immutable snapshot으로 고정됩니다.</p></div><LayoutDashboard /></header>
    <div className="expected-results-pages">{pages.map((page) => <article className="expected-results-page" key={page.id}><header><strong>{page.name}</strong><span>{page.widgets.length} WIDGETS</span></header><div className="expected-results-widgets">{page.widgets.map((widget) => <span key={widget.id}>{widget.title}</span>)}</div></article>)}</div>
  </section>
}
