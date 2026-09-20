# UI 밀도 개선 P0 — 적용 계약과 기준선

- 작성일: 2026-09-19
- 기준 코드: `54131b333bfd594ae0b448954daf0e4b0b489cb7`. 착수 시 앱 변경은 없고 이전 계획 문서의 미커밋 변경만 존재했다.
- 사용자 진행 방식: 각 단계 결과를 제시하고 **사용자 확인 후 다음 단계로 진행**한다.
- 관련: [전체 계획](ui-density-improvement-plan.md), [CSS 기준선](ui-density-css-baseline.md), [화면 기준선](ui-density-visual-baseline.md).
- 이 단계는 조사·계약 확정이다. 앱 CSS/TSX, API, DB, 사용자 설정을 변경하지 않는다.
- 상태: **P0 완료·사용자 확인 후 P1 진행**. P0의 Sol 독립 검수와 Astra 최종 검수 완료. 후속 구현 상태는 [P1 기록](ui-density-p1-implementation.md)을 따른다.
- 적용 대상 변경(2026-09-20): 향후 모바일 전용 개선·검수는 제외한다. [실행 계획의 데스크톱 전용 정책](ui-density-improvement-plan.md)을 우선하며, 이 문서의 모바일 측정은 과거 기록이다.

## 보존 계약

| 영역 | 확정 사항 |
|---|---|
| 글자 설정 | 기본 14pt, 11~18pt. 본문 기준 14.67/18.67/24 CSS px. 작은 글자/큰 글자 버튼의 양끝 disabled 유지 |
| 저장 | `simdashboard.workspace.font-size-pt.v1`, `vd-workbench-font-size-pt` migration 유지. localStorage 접근 실패의 기본값 fallback 유지 |
| 테마 | 기존 dark/light 선택과 자체 호스팅 Pretendard 유지 |
| 배치 | Workflow rowHeight 84px, GenericResultLayoutWorkspace 70px, ResultsWorkspaceGrid 74px, 보고서 20px/maxRows 18. 글자 배율 자동 연동 금지, x/y/w/h 보존 |
| 데이터 문맥 | 프로젝트·의뢰·Case·환경·Run Option·capture 선택/URL·권한·불변 수집 이력 유지 |
| 배포 | frontend 정적 빌드, deploy.bat/update.bat, Windows 폐쇄망 계약 유지. 신규 runtime 의존성을 전제하지 않음 |

## P1 토큰 계약

파일은 `frontend/src/tokens.css`, 로드는 main.tsx의 styles.css보다 앞이다. P1에서는 토큰 선언과 폭 정책만 적용하고 글자·컨트롤 치수 소비는 P2 이후에 한다.

| 토큰 | 초기 계약 |
|---|---|
| `--ui-font-size` | 기존 shell inline pt 값 그대로. P5 전까지 html font-size 변경 없음 |
| `--text-body`, `--text-caption` | 사용자 pt 값의 1 / 0.875배 |
| `--text-subheading`, `--text-section`, `--text-title` | 사용자 pt 값의 1.125 / 1.25 / 1.5배 |
| `--control-h-sm`, `--control-h-md`, `--control-h-lg` | 14pt에서 최소 높이 32/38/44px, `calc(var(--ui-font-size) * 12 / 7)` 등의 동일 단위 비례 계산 |
| `--row-h`, `--row-h-detail` | 14pt에서 최소 높이 40/52px, 글자 확대 비례. 내용에 따라 증가 가능 |
| `--space-1` ~ `--space-6` | 4/8/12/16/24/32px. P5의 rem 전환 전에 전역 치환하지 않음 |
| `--radius-sm`, `--radius-md`, `--radius-lg` | 4/8/12px |
| `--shell-pad-x` | 데스크톱 clamp(24px, 2vw, 40px), 620px 이하 14px |

정적 토큰은 :root에 둘 수 있다. `--ui-font-size`를 참조하는 파생 토큰은 그 값이 주입되는 **.app-shell과 명시적 이전 scope에서 정의**한다. :root에 파생 토큰을 두고 자식의 inline 변수가 역으로 반영된다고 가정하지 않는다. portal이 추가되면 해당 root에 동일 값과 scope를 전달한다.

기존 breakpoint는 P1에서 통합하지 않는다. shell의 620/621, 900, wide 1440 기준을 유지하고, 기능 CSS의 다른 지점은 기준선에 등록해 신규 증가만 통제한다. 중단점 전체 통합은 별도 검토 없이 수행하지 않는다.

## 폭 정책과 예외

- 작업 화면: 결과 개요, Case 결과, Workflow, 의뢰·비교 작업공간, 데이터 조사·매핑, 모델링 템플릿의 최상위 작업 wrapper. 가용 폭을 사용하고 min-width:0으로 자식 overflow를 제어한다.
- assigned-only의 hero/오류/카드/작업 목록은 개별 1120px cap을 갖고 있어 같은 폭 규칙으로 묶는다. 폼 필드 자체의 읽기 폭과 혼동하지 않는다.
- 도움말: shell은 넓게 쓸 수 있으나 글 본문은 읽기 폭 유지. VOC도 글 상세/작성 폼 폭은 유지하고 게시판 전체 cap은 P4에서 별도로 판단한다.
- 폼·dialog: 기존 제한을 우선 유지한다. min(고정 상한, viewport 여백)을 쓰는 폭은 작업 wrapper cap 제거 대상이 아니다.
- 보고서 canvas, 차트/영상/이미지 비율, tooltip·preview·drawer는 P1 cap 제거 대상에서 제외한다.
- cell max-width와 말줄임은 column 정책이다. 전체 wrapper 폭과 구분해 보존한다.
- 패딩은 기능 wrapper마다 한 번만 적용한다. header와 본문이 이미 공통 부모에 있으면 자식에 중복 패딩을 넣지 않는다.

## P2 프리미티브 API

- Button: native button props와 ref, `size: sm|md|lg`, 기존 시각 의도에 대응하는 variant. 기본 type=button, 폼 submit은 명시한다. 자동으로 새 로딩 상태/데이터 로직을 추가하지 않는다.
- Input/Select: native props·ref·id·name·aria 속성 보존, native size 속성과 충돌하지 않도록 `controlSize: sm|md|lg`를 사용한다. label은 호출 화면 책임이다.
- Table: TableScroll, Table, TableHead, TableBody, TableRow, TableHeaderCell, TableCell. native 의미·scope·ref 보존. 정렬·페이지·선택은 기능 책임이며 표가 아닌 기존 목록을 강제로 table로 바꾸지 않는다.
- 기존 디자인을 유지하는 얇은 CSS/React 계층으로 구현한다. 새로운 디자인 시스템 패키지 도입은 필요하지 않다.

공개 타입 계약은 다음과 같다. React 18의 forwardRef로 각 native element ref를 전달한다. className/style/children과 native 이벤트를 중간에서 삭제하지 않는다.

```ts
type ControlSize = 'sm' | 'md' | 'lg'
type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
type ButtonProps = React.ComponentPropsWithoutRef<'button'> & {
  size?: ControlSize // default: md
  variant?: ButtonVariant // default: secondary
}
type InputProps = React.ComponentPropsWithoutRef<'input'> & {
  controlSize?: ControlSize // default: md; native size 유지
}
type SelectProps = React.ComponentPropsWithoutRef<'select'> & {
  controlSize?: ControlSize // default: md; native size/multiple 유지
}
// Button/Input/Select: HTMLButtonElement/HTMLInputElement/HTMLSelectElement refs
// TableScroll: ComponentPropsWithoutRef<'div'>, HTMLDivElement ref
// Table: ComponentPropsWithoutRef<'table'>, HTMLTableElement ref
// TableHead/TableBody: 'thead'/'tbody' props, HTMLTableSectionElement refs
// TableRow: 'tr' props, HTMLTableRowElement ref
// TableHeaderCell/TableCell: 'th'/'td' props, HTMLTableCellElement refs
```

TableScroll은 overflow wrapper이며 자동 role/tabIndex를 강제하지 않는다. 기능 화면이 가로 스크롤 영역의 이름·키보드 접근을 제공한다. 기본 Table은 `--row-h`를 쓰고 두 줄 목록은 호출 화면의 클래스로 `--row-h-detail`을 적용한다. semantic table props와 데이터 상태가 섞이지 않게 한다.

## P3 이전 scope와 최종 root 정책

- 이전 scope는 `data-ui-density="v1"`로 표시한다. 시범 페이지 root 및 필요한 overlay root에서만 사용한다. html/body에는 P3에서 붙이지 않는다.
- 기존 blanket selector에서 scope 자신과 그 후손을 제외하는 방식으로 이전한다. selector 목록·specificity·지원 브라우저는 fixture와 computed style로 확인한다. `!important`를 더 추가하는 우회는 금지한다.
- 한 페이지에서 혼합 적용이 필요하면 해당 영역을 별도 scope로 나눈다. custom widget font와 nav 축소 규칙은 무조건 지우지 않고 별도 우선순위를 확인한다.
- P5에서 공통 어댑터가 documentElement를 관리할 때만 rem을 전환한다. 실제 입력 정규화·이전 root 값 복원·로그인/로그아웃 cleanup이 완료 조건이다.

## 작업 소유권과 사용자 확인

| 단계 | Terra | Luna | 통합·검수 |
|---|---|---|---|
| P0 | CSS 인벤토리 | 화면 기준선 | Astra 계약, Sol 독립 검수 |
| P1 | tokens/main import/구조 검사 | 폭·패딩 CSS | 공용 CSS는 Luna 단독 편집, Astra 통합 |
| P2 | shared primitives | 기능별 대시보드 적용 | primitive 계약 변경은 먼저 협의 |
| P3~P5 | 설정 어댑터·검사 | 기능별 스타일 이전 | 전역 CSS 편집 소유자는 작업 시작 전 단일 지정 |

각 단계마다 실제 변경, 통과 검사, 전후 화면, 기존 결함/새 결함, 미검증 범위를 보고한다. 사용자 확인 전 다음 단계 코드를 적용하지 않는다. 현재 발견한 기존 결함은 증거를 남기고 관련 단계에서 수정하며, 기준선을 정상이라고 위장하거나 검사를 느슨하게 하지 않는다.

## P0 검사 기록

- `node --experimental-strip-types scripts/workspace-preferences-self-test.mjs`: 통과. 기존 migration·저장·잘못된 값·storage 실패 경로 확인.
- `node scripts/check-architecture.mjs`: 통과. 215 sources, 검토된 cross-feature import 5개.
- CSS 선언 수치와 실제 화면 검증 결과는 각각 위 기준선 문서에 기록한다. 코드만 읽은 사실과 실제 브라우저로 본 사실을 구분한다.
- 화면 기준선 11개 PNG/JSON을 수집했다. 1366×768/14pt, 핵심 화면 390×844/18pt, 대표 wrapper 2560×1440/14pt에서 비교 기준을 확보했다. 모든 화면의 root 가로 넘침은 없었다.
- Playwright 시나리오는 통과했으나 Windows runner의 기존 taskkill AggregateError가 발생했다. 성공적인 무오류 runner 종료로 기록하지 않는다. 종료 후 테스트 포트 15173/18000에 LISTEN 프로세스가 없고 임시 spec 2개가 제거됐음을 확인했다.
- 앱 source 변경 없음, 문서 링크·공백 검사 통과. 라이트/다크·11pt 전체 조합, 실제 구성된 Run 위젯 배치, 실자료·운영 배포는 이번 기준선 검증 범위 밖이다.
