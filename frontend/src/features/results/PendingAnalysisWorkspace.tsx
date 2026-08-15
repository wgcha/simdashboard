import { BarChart3, Clock3, Database, Gauge, Map, Table2 } from 'lucide-react'

type Props = {
  projectName: string
  requestTitle: string
  onOpenData: () => void
}

export function PendingAnalysisWorkspace({ projectName, requestTitle, onOpenData }: Props) {
  return <section className="canvas-area pending-analysis-workspace" data-testid="pending-analysis-workspace">
    <header className="pending-analysis-head"><div><small>OPEN CELL EVALUATION · RESULT PENDING</small><h2>Open Cell 평가 분석</h2><p>{projectName} · {requestTitle}</p></div><span className="pending-analysis-status">결과 대기 중</span></header>
    <div className="pending-analysis-grid">
      <article className="pending-analysis-widget"><header><div><Gauge /><div><small>OPEN CELL SUMMARY</small><h3>Open Cell 판정 요약</h3></div></div><strong>대기 중</strong></header><p>해석 결과가 연결되면 전체 판정과 최대 응력이 자동으로 표시됩니다.</p></article>
      <article className="pending-analysis-widget"><header><div><Map /><div><small>OPEN CELL MAP</small><h3>Open Cell 엣지 맵</h3></div></div><strong>—</strong></header><p>상·하·좌·우 엣지별 응력 위치와 기준 초과 여부를 기다리고 있습니다.</p></article>
      <article className="pending-analysis-widget"><header><div><BarChart3 /><div><small>EDGE BAR</small><h3>엣지별 최대 응력</h3></div></div><strong>— MPa</strong></header><p>결과 데이터가 등록되면 기준선과 함께 비교 그래프를 보여줍니다.</p></article>
      <article className="pending-analysis-widget"><header><div><Table2 /><div><small>RESULT TABLE</small><h3>상세 결과</h3></div></div><strong>0건</strong></header><p>측정 위치, 허용 기준, 판정 결과가 결과 등록 후 채워집니다.</p></article>
    </div>
    <div className="pending-analysis-setup"><div><strong>분석 입력이 아직 준비되지 않았습니다.</strong><span>하중 경우를 선택하거나 새로 만들면 이 기본 위젯이 실제 결과와 연결됩니다.</span></div><button type="button" onClick={onOpenData}><Database /> 하중 경우 설정</button></div>
  </section>
}
