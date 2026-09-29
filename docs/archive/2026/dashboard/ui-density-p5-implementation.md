# UI 개선 P5 — root 글자 설정과 전역 규칙 정리

- 시작: 2026-09-27 사용자 `응 p5 해줘` 승인. 상태: **P5 구현·Sol 독립 검수·Astra 최종 통합 검수 완료**.
- 목표: 인증 작업공간에서 html root에 사용자 글자 설정을 적용하고 rem 글자 역할을 사용한다. 로그아웃/unmount 시 이전 root 값을 복원하며, 전역 blanket font-size important를 제거한다.
- 역할: Astra 목표·주요 계약·통합 검수, Sol 세부 설계·조정, Luna 최고 추론으로 구현·국소 검증, 별도 Sol 독립 검수. 문서·로그는 Astra 담당.
- 선행: [P4 완료 기록](ui-density-p4-implementation.md), [전체 계획](../../../dashboard/ui-density-improvement-plan.md), [P0 보존 계약](../../../dashboard/ui-density-p0-contract.md).

## 설계 계약

- 11~18pt, 기본14pt, 소수 설정, v1/legacy 저장 키를 보존한다. 기존 shell의 `--ui-font-size` 호환성을 유지한다.
- root의 기존 inline 값과 priority를 보관·복원한다. 인증 전 화면, 로그인/로그아웃, 별도 shell, StrictMode와 해제 경계를 검증한다. 인증/권한 로직 자체는 변경하지 않는다.
- 텍스트 역할을 rem으로 전환한다. spacing/radius 및 저장된 grid/canvas 기하의 px 규격은 유지한다. Workflow 사용자 글씨, rowHeight84/70/74/20과 보고서 maxRows18, x/y/w/h 보존.
- 미이전 업무 route와 overlay를 조사하고 역할별 규칙을 보완한 후 전역 강제 글자 규칙을 제거한다. 단순 전역 px 치환이나 새로운 blanket important로 대체하지 않는다.
- desktop 전용. 실제 사용자 DB·설정·서비스를 테스트하지 않는다. 신규 의존성, 배포, commit/push는 이번 승인 범위에 포함하지 않는다.

## 검증 환경과 진행 기록

- `%TEMP%/sim-workbench-ui-p5-20260927`에 새 합성 DuckDB와 QA 계정을 생성했다. backend18103/frontend15184를 사용한다. 실제 로컬 PC helper 접속은 E2E에서 차단한다.

## 구현 결과

- App의 공통 `useWorkspaceDocumentFontSize`가 인증/설치/승인 대기 경계에 맞춰 root에 pt 값을 적용한다. cleanup은 이전 inline 값과 priority를 복원하며 중복 cleanup도 안전하게 처리한다. 별도 shell과 초기 설정 화면도 같은 경로를 따른다.
- `tokens.css`의 글자 역할은 :root의 1/.875/1.125/1.25/1.5rem이다. shell의 호환 pt 변수와 control/row 최소높이, spacing/radius의 기존 기하는 보존했다. portal API 사용은 현재 소스에서 발견되지 않았고 overlay는 shell 내부다.
- 다중 route, data, comparison, 전역 Recharts blanket과 일반 light-theme font 강제 선언을 제거했다. 실제 font-size important는 60개에서15개로 줄었고 기준선도 감축했다. 색상 important는 보존한다.
- 남은15개는 명시적인 메뉴/상단 shell과 사용자 custom widget/portfolio 글자 규칙이다. nav link/text는 사용자 값의 .86배, settings/logout은 .76배로 양 테마를 통일했다. grid/canvas 사용자 글자 예외는 전역 root 크기로 덮지 않는다.
- 미이전 관리자/변수/작업/예제/VOC/낙하 영상과 차트 문구는 명시적 역할 selector로 보완했다. 작업 유형 설명은 본문(1배), 변수 메타/VOC 글자 수는 caption(.875배)이다. 상단 breadcrumb의 light13px 예외도 제거했다.
- 인증·권한·API·DB·배포 계약 및 신규 의존성 변경 없음. Sol은 실제 보안 경계 변경이 없어 security-diff-scan 대상이 아니라고 판단했다. 보안 스캔을 수행한 것으로 기록하지 않는다.

## 검증 결과

- 최종 관련 E2E14개 통과: P5 3, P3 typography2, P4 surfaces6, folders2, bootstrap1. 최초 통합 실행은11개 통과/3개 실패였으며, 작업 설명의 body/caption 기대값과 레코드 로딩 대기를 수정한 후 관련4개를 재실행해 모두 통과했다. 실패를 숨기거나 최초 실행 전체 통과로 기록하지 않는다.
- P5는 registry의17 canonical route 모두를1366×768/18pt/dark 및1920×1080/11pt/light에서 확인했다. URL만 확인하지 않고 각 화면 고유 제목/testid와 API spinner 종료를 기다린다. root 배율, 메뉴/로그아웃/상단 경로 배율, 대표 본문/caption, 가로 넘침과 pageerror 없음 검증. 최초 임시 테스트의 이전 화면 race 및 폴더 제목 불일치는 테스트를 수정했다.
- 16.25pt 소수 설정, Help→VOC→Help→새로고침→로그아웃에서 기존17px!important/custom12px!important 복원을 확인했다. 단위 self-test로 invalid fallback, StrictMode setup/cleanup, 업데이트 및 중복 cleanup, 강제 CSS 회귀도 검증했다.
- P3는11/14/18pt 및 양 테마, P4는14/18pt 및 양 테마에서 역할/제어/dialog/문맥/비교 근거/폴더 선택기 흐름을 확인했다. bootstrap은 합성 빈 데이터+18pt와 테마 전환 확인.
- TypeScript, architecture, CSS architecture self-test, root/CSS self-test, architecture checker self-test, preferences self-test, API self-test, E2E runner self-test, API 생성 후 계약 diff 없음, `/home/` production build 통과. 기존 큰 chunk 경고는 유지된다.
- 환경 Node26.5는 과거 `--experimental-transform-types`를 지원하지 않아 API self-test의 첫 실행이 실패했다. 번들 Node24.19로 동일 검사를 통과했고 앱 코드는 변경하지 않았다. 저장소 지정 Node22.23.2 CI 자체를 로컬에서 실행한 것으로 기록하지 않는다.
- Browser(IAB) 사용: Help18pt/light의 root24px/title36px/breadcrumb24px/logout18.24px, URL/제목/본문/테마 조작 및 가로 넘침·framework overlay 없음 확인. 콘솔에는 개발 HMR 중 `A router only supports one blocker at a time` 경고1건이 남았지만 최종 reload에서는 새 경고/오류가 관측되지 않았다. 콘솔 전체가 처음부터 무경고였다고 기록하지 않는다.
- screenshot 증거: `%TEMP%/sim-workbench-ui-p5-20260927/screenshots`의 p5-access_admin, schemas, voc, help1366/1920 및 P3/P4 화면. 특히 실제 route 내용으로 screenshot인지 확인했고 VOC counter/빈 상태 가독성도 확인했다.
- 검증 범위는 정식 route 진입과 위 대표 기능이다. 모든 가능한 사용자 위젯 조합, 모든 권한/데이터 상태, 전체 backend/E2E suite 또는 Server2022 실기 검수를 의미하지 않는다. 모바일 검수·실제 사용자 서비스/DB/설정 변경·commit/push/배포 미실행.


- Sol 독립 검수 최종 승인: 남은 코드 차단점 없음. 설계·구현에 참여하지 않은 검수자가 diff/계약을 확인했으며 E2E/build는 Astra 실행 증거를 확인하고 중복 실행하지 않았다. Astra가 수정 해소·화면 증거·계약을 최종 통합 확인했다.
