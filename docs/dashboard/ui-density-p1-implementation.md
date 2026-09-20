# UI 개선 P1 — 토큰과 작업 본문 폭

- 날짜: 2026-09-19
- 상태: **P1 구현·Sol 독립 검수·Astra 최종 검수 완료, 사용자 확인 대기**. P2 미착수, 사용자 확인 후 진행.
- 기준: [실행 계획](ui-density-improvement-plan.md), [P0 계약](ui-density-p0-contract.md), [Windows 배포 정책](../windows-deployment-policy.md).
- 역할: Astra 설계·통합·최종 검수, Luna 폭·패딩, Terra 토큰·CSS 검사, Sol 독립 검수.

## 적용 내용

`tokens.css`를 기존 스타일보다 먼저 로드한다. 간격·모서리·글자 역할·컨트롤 크기 토큰을 정의하되 P1에서 실제 소비하는 값은 shell 좌우 여백이다. 글자에 비례하는 토큰은 `--ui-font-size`가 주입되는 `.app-shell`에서 정의한다.

작업 wrapper의 중복 폭 제한과 assigned-only의 1120/1920px 제한을 해제했다. workflow, chassis, comparison, data, folder discovery, semantic mapping, modeling template의 작업 영역이 가용 폭을 사용한다. 주요 grid/flex wrapper에는 `min-width:0`을 유지·보완했다. 다크 및 반응형 재정의도 같은 여백 토큰을 사용한다.

| viewport | header·topbar·작업 실행 본문 좌우 여백 | 확인 |
|---|---|---|
| 1366px | 약 27.32px | 기존 32/40px 혼재와 시작선 차이 해소 |
| 2560px | 40px | 기존 32/56px 혼재와 본문 cap 해소 |
| 390px | 14px | 다크 assigned-only의 28px 재정의도 수정 |

도움말 1450px, VOC 읽기 영역, 예제 갤러리, dialog·cell·preview·media·report의 고유 폭은 유지한다. 기본 14pt, 11~18pt 설정·저장 키, 테마, grid rowHeight 84/70/74/20 및 보고서 maxRows 18, 페이지 크기, API·DB·업무 로직은 변경하지 않는다.

## CSS 회귀 방지

PostCSS AST로 기존 font-size important, wrapper cap, breakpoint, 기능 CSS의 전역 selector를 기록한다. 새 위반을 막고 `styles.css` 줄·선언 수 증가를 제한한다. 선언 fingerprint와 baseline 고정 해시를 함께 검사하므로 JSON 기준만 높이는 변경은 실패한다.

후속 `data-ui-density="v1"` 이전 영역의 컨트롤 높이와 radius 토큰 사용도 검사한다. 따옴표 변형·쉼표 selector·변수 폭 cap·무스코프 selector·임의 pill radius를 검사하는 fixture를 포함한다. 이는 정적 가드레일이며 모든 CSS 의미나 렌더 충돌을 증명하는 검사는 아니다.

## 검증과 제한

- IAB 브라우저에서 합성 의뢰로 작업 실행, light/dark, 1366/2560/390px, 18pt 확대·상한 버튼, 데이터 화면 탭 전환을 확인했다. 작업 화면 페이지 전체 가로 넘침과 확인한 브라우저 console 오류가 없었다. 의미 매핑은 2560px에서 max-width none, 좌우 40px를 확인했다.
- 영구 회귀 spec: `frontend/e2e/ui-density-width.spec.ts`. 3 viewport에서 두 테마의 시작선·cap, 선택 의뢰, 18pt 새로고침 유지, 도움말 cap을 확인한다. 최초 정상 실행 3 passed(45.3s), 최종 보완 후 재검사 **3 passed(46.3s), exit 0**.
- architecture check(216 sources/5 reviewed cross-feature imports), architecture checker self-test, CSS checker self-test 및 기존 workspace preferences self-test 통과.
- TypeScript `tsc -b` 통과. `/home/` production build 통과(`--configLoader runner`, TEMP 산출물). 기존 500kB 초과 chunk 경고는 남아 있다.
- 실제 Caddy 실행파일과 새 production 산출물을 사용하는 `offline-web-self-test.py` 통과: HTML, 2개 entry asset, API·media proxy, redirect, SPA route. 임시 mock API와 loopback 포트만 사용했다.
- LAN proxy self-test는 이 환경의 LAN 주소 연결 시간 초과로 실패했다. 이를 통과로 기록하지 않는다.
- 초기 표준 E2E runner는 Windows `.vite/deps` EPERM과 child cleanup 오류로 실행하지 못했다. 중단 후 재개 첫 시도도 임시 서버가 종료되어 CONNECTION_REFUSED였다. 최종 재검사는 원본 Vite 설정을 로드하고 TEMP 캐시만 별도 지정한 임시 서버를 사용한다.

## 의존성과 배포 범위

검사기의 PostCSS 8.5.22를 정확한 버전의 devDependency로 명시하고 pnpm lock을 갱신했다. 이미 사용하던 전이 의존성 버전이며 Windows에서 검사기 실행을 확인했다. 서버 런타임·wheel·DB migration은 추가되지 않는다.

외부 패키징 경로는 기존 `scripts/windows/build-offline-bundle.ps1`의 pnpm 설치·웹 빌드·dist 수집을 유지한다. `pnpm install --offline --frozen-lockfile` 시 다운로드 0/reuse 171은 관찰했으나 registry metadata EACCES 경고와 exit code 기록 누락이 있어 완전한 오프라인 설치 성공으로 판정하지 않는다. 보안 설정을 끄는 우회는 하지 않았다. 배포 진입점·오프라인 대상의 Node/Git 불필요 계약은 변경하지 않았다.

실제 사용자 DB·설정·서비스를 테스트하지 않았다. Server 2022 폐쇄망 설치·업데이트·재부팅, 전체 배포 CI, 사내 실자료 검증은 수행하지 않았다. 커밋·push·배포는 하지 않았다.

## 증거 위치

- 합성 DB·빌드·스크린샷: `%TEMP%/sim-workbench-ui-p1-20260919/`
- 화면: `screenshots/p1-execution-{1366,2560,390}-{light,dark}.png`
- 이전 화면 기준: [P0 화면 기록](ui-density-visual-baseline.md)

TEMP 증거는 로컬 검수 산출물이며 저장소에 사용자 데이터나 DB를 포함하지 않는다.

Sol이 발견한 cascade·min-width·CSS 검사 우회를 수정하고 selector별 독립 fixture까지 재검증했다. 추가 차단 사항 없음. Astra는 최종 화면·변경 범위·검사 결과·문서 링크와 공백 검사를 확인했다. 이 승인은 P1 로컬 구현에 한정하며 미수행 배포 검증을 대체하지 않는다.
