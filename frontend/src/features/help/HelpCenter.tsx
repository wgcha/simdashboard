import { AlertTriangle } from 'lucide-react'
type HelpDestination = 'dashboard' | 'data' | 'examples' | 'schemas'

type HelpCenterProps = {
  onNavigate: (page: HelpDestination) => void
}

const scenarios = [
  { title: '새 해석 결과를 등록하고 확인하기', steps: ['해석 데이터에서 프로젝트·의뢰·하중 경우를 선택합니다.', 'CSV 또는 폴더 스키마 기반 결과를 검증 후 적재합니다.', '해석 상세에서 판정, 시간 이력, 위치별 결과를 확인합니다.'], action: 'data' as const, label: '해석 데이터 열기' },
  { title: '폴더 결과를 변수와 대시보드에 연결하기', steps: ['폴더 스키마에서 파일 패턴과 variable_key 매핑을 정의합니다.', '변수 카탈로그에서 같은 variable_key의 표시 이름·단위·기준·허용 위젯을 선언합니다.', '대시보드 편집에서 변수를 위젯에 바인딩합니다. 값은 결과 테이블에서 조회됩니다.'], action: 'schemas' as const, label: '폴더 스키마 열기' },
  { title: '편집 가능한 PPT 보고서 만들기', steps: ['해석 상세의 평가 탭 옆 보고서 내보내기를 누릅니다.', '레이아웃 편집·관리에서 슬라이드를 선택하고 위젯을 이동·크기 조절하거나 변수를 끌어 놓습니다.', '회사 PPTX를 사용하려면 도형 이름 또는 {{variable:key}} 태그를 지정해 업로드하고 변수를 연결합니다.', '레이아웃과 버전을 선택한 뒤 PPTX를 생성합니다.'], action: 'dashboard' as const, label: '해석 상세 열기' },
  { title: '대시보드 레이아웃을 추가·복구하기', steps: ['해석 상세에서 대시보드 편집을 시작합니다.', '위젯을 추가하고 변수·집계·크기·위치를 설정합니다.', '레이아웃을 저장하거나 복제하고, 필요하면 정상 버전을 복구합니다.'], action: 'dashboard' as const, label: '대시보드 편집 열기' },
  { title: '이전 Run과 비교하고 검토 의견 남기기', steps: ['해석 상세에서 Run 비교·검토 탭을 엽니다.', '기준 Run과 대상 Run을 선택해 회귀·개선 및 공통 시계열을 확인합니다.', '데이터 신뢰도에서 출처·카탈로그·단위·검증 경고를 확인합니다.', '변수와 시점을 선택해 북마크·검토 의견을 저장하고 상태를 관리합니다.'], action: 'dashboard' as const, label: 'Run 비교 열기' },
  { title: '예제로 전체 기능 빠르게 둘러보기', steps: ['예제 갤러리에서 확인할 기능이나 상태를 고릅니다.', '카드의 기대 결과와 데이터 구성을 먼저 읽습니다.', '예제 열기로 이동해 확인 목록을 따라 기능을 직접 사용합니다.'], action: 'examples' as const, label: '예제 갤러리 열기' },
] satisfies ReadonlyArray<{ title: string; steps: readonly string[]; action: HelpDestination; label: string }>

export function HelpCenter({ onNavigate }: HelpCenterProps) {
  return <section className="help-center"><header><span>SCENARIO GUIDE</span><h1>VD simulation workbench 사용 도움말</h1><p>하려는 작업을 기준으로 필요한 화면과 데이터 흐름을 안내합니다.</p></header><div className="help-flow"><strong>핵심 데이터 흐름</strong><div><span>폴더 스키마</span><b>→</b><span>결과 테이블</span><b>→</b><span>변수 카탈로그</span><b>→</b><span>대시보드·PPT</span></div><p>변수 카탈로그는 값을 저장하지 않습니다. 결과 테이블의 <code>variable_key</code>를 해석하고 표시하는 계약입니다.</p></div><div className="help-scenarios">{scenarios.map((scenario, index) => <article key={scenario.title}><span>0{index + 1}</span><h2>{scenario.title}</h2><ol>{scenario.steps.map((step) => <li key={step}>{step}</li>)}</ol><button onClick={() => onNavigate(scenario.action)}>{scenario.label}</button></article>)}</div><aside><AlertTriangle /><div><strong>외부 배포 전 확인</strong><p>PostgreSQL 어댑터·Alembic·데이터 검증·로그인·역할 권한·감사·백업 도구가 준비되어 있습니다. 외부 공개 시에는 HTTPS, <code>AUTH_MODE=password</code>, 별도 DB 역할과 복구 시험을 반드시 적용하세요.</p></div></aside></section>
}
