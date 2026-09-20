# UI 개선 P4 화면별 확산

- 시작: 2026-09-20, 사용자 다음 단계 진행 승인.
- 상태: **P4 전체 구현·검수 완료**, 사용자 확인 대기. 2026-09-20 `p4해줘` 승인으로 남은 화면군까지 완료했다.
- 순서: 폴더 연결 → 의뢰/등록 → 비교/모델링 → 도움말/설정. 최신 사용자 승인은 남은 P4 전체이며, 통합 결과를 제시한 후 P5 진행을 확인한다. P5 html/rem 전환은 미착수다.
- 선행 검수: P3의 미완료 Sol 검수를 재시도하여 승인받았다. 이전 모델 용량 오류를 검수 통과로 간주한 것이 아니다.
- 영구 적용 대상: 2026-09-20 사용자 후속 지시에 따라 앞으로 모바일 전용 개선·검수는 제외한다. [실행 계획의 데스크톱 전용 정책](ui-density-improvement-plan.md)을 우선한다.

## P4-1 범위와 보존 계약

FolderEnvironmentWorkspace와 FolderDiscoveryWorkspace에 기존 `data-ui-density="v1"` 계약을 적용한다. 제목·본문·보조 문구, 저장 규칙 편집과 등록 이력, 조사 폴더 선택 창의 글자와 컨트롤 크기를 같은 토큰으로 맞춘다. 하위 구성요소도 scope 영향을 확인한다.

폴더 조사·규칙·등록·권한·API 및 사용자 설정을 변경하지 않는다. 실제 사용자 저장소·DB·실행 서비스를 검수 대상으로 사용하지 않는다. 신규 의존성·배포 변경은 없다. 기존 미커밋 P1~P3 및 AGENTS.md 변경을 보존한다.

## 검증 기록

임시 환경은 `http://127.0.0.1:15184`/backend18103과 `%TEMP%/sim-workbench-ui-p4-20260920` 합성 DB다. 브라우저의 e2e-admin은 검수 계정이며 비밀번호 저장을 선택하지 않았다.

- 최종 데스크톱 E2E 2개 통과(1366×768, 1920×1080; 각각 14/18pt·light/dark). 제목/본문/컨트롤 배율, 최소 높이, 페이지 가로 넘침, 규칙 선택, 폴더 조사·구조 확인, native 선택 창 상속 및 Escape 닫기를 검증했다. 증거는 위 TEMP 경로의 screenshots 폴더에 보관한다.
- 기존 폴더 선택→구조 확인→등록→결과 링크 흐름도 통과했다. 모바일 제외 지시 도착 전 기존 PC/모바일 검사가 이미 종료됐으며, 이후 신규 P4 테스트를 PC/와이드 전용으로 바꿨다. 모바일 전용 보조 버튼 표시 우회는 최종 변경에서 제거했다.
- TypeScript, architecture225 sources/5 imports, CSS checker self-test, `/home/` production build 통과. 기존 큰 chunk 경고는 남아 있다. 새 important/가드레일 상한 증가는 없다.
- IAB에서 desktop14/18pt를 육안 확인했다. URL/제목과 실제 콘텐츠 정상, framework overlay 없음, 마지막 console error/warn 없음, 등록 규칙 탭 전환과 입력 조작 확인. 미설정 합성 DB에서는 SPDM_ROOT_UNSET 안내가 표시되므로 실제 조사·선택 상태는 synthetic API fixture E2E로 검증했다.
- Sol이 발견한 환경 label·일반 버튼·card 제목·위젯 보조 문구·차트·dialog 역할 누락을 수정했다. 최종 코드 승인과 이후 데스크톱 재검사를 통과했고 Astra가 diff·검사·화면 증거를 통합 검수했다.
- KPI 숫자 자체의 기존 위젯 크기와 차트/SVG 영역은 보존하며 주변 단위·판정·축 문구는 토큰으로 처리한다. TSX 변경은 scope 표시뿐이고 상태·API 로직은 그대로다.
- 실제 사용자 DB/저장소/서비스, 전체 앱 회귀, Server2022 실기, commit/push/배포는 수행하지 않았다. 위 내용은 P4-1 종료 당시 기록이며, 이후 승인된 나머지 화면은 아래에 기록한다.

## P4 나머지 화면 완료 — 2026-09-20

- Terra: 의뢰 접수·집중 의뢰 개요·Workflow 대기/보드·의뢰 헤더와 결과 요약·데이터 등록·저장소. Luna: 비교 workspace/근거 표/차트/조건·모델링 템플릿과 dialog·도움말·내 PC 설정과 내장 비밀번호 카드. 제목/본문/보조 문구와 컨트롤에 scoped 역할 토큰을 적용했다. 상태·API·인증·저장 로직은 변경하지 않았다.
- Sol 독립 1차 검수에서 발견한 하위 badge/이력/preview/비교 빈 상태/설정 카드의 작은 고정 글씨를 보완했다. Astra가 통합 검수했고 Sol 최종 승인, 차단 이슈 없음.
- styles.css의 잔여 전역 font-size important 6개만 v1 root/자손에서 제외했다. 혼합 규칙의 색상 important는 그대로 유지했다. baseline fingerprint 6개와 잠금 hash만 정확히 교체하고 important 상한61을 늘리지 않았다.
- Workflow 보드 글씨는 저장된 `--workflow-font-size`를 유지한다. rowHeight84, 저장 좌표, 차트/SVG 기하, 기존 textarea 크기는 유지한다. html root/rem 전환·관리자/기타 route·별도 canvas/portal 전체 전환은 P4 완료 범위에 포함하지 않는다. P5에서 미이전 경계를 조사한 후 전역 강제 규칙 제거 여부를 결정해야 한다.

### 최종 검증

- 격리 환경: `%TEMP%/sim-workbench-ui-p4-rest-20260920/p4-rest.duckdb`, backend18103, frontend15184. 합성 계정/데이터만 사용하고 로컬 PC helper 접속은 E2E에서 차단했다.
- `ui-density-p4-surfaces.spec.ts` 6개 통과: 1366×768/1920×1080, 14/18pt, light/dark에서 화면 역할·제어·가로 넘침·접수/등록 문맥·모델링 dialog·비교 필터/근거, 실행→등록→뒤로→새로고침→Case 결과의 선택 유지. 하위 badge/preview21px, 등록 section30px, 비교 caption21px도 검증했다.
- 기존 `ui-density-typography.spec.ts`를 데스크톱1366/1920 및 이전 완료된 Help 기대값으로 갱신해 2개 통과. 11/14/18pt 역할, 저장 복원 및 html root 불변 확인.
- 기존 comparison-evidence의 문제 필터/검토 저장/차트 검사와 modeling-template-library의 생성/CSV 버전·업로드·다운로드 검사 각1개 통과. 이 검사의 모바일 viewport는 데스크톱으로 전환했다. 최종 관련 검사 총10개 통과이며 전체 앱 회귀를 의미하지 않는다.
- 기존 unified-request-workspace 첫 검사는 `결과 검토` exact 버튼 검색에서 실패했다. HEAD의 미변경 RequestWorkspaceShellHeader에도 이미 `Case 결과`/`기존 Run 대시보드`가 있어 낡은 기대값으로 분류했다. 해당 검사를 통과로 기록하지 않으며, 현행 journey의 문맥 복원은 위 새 P4 검사로 검증했다.
- TypeScript, `/home/` production build, architecture228 sources/5 imports, CSS architecture self-test, architecture checker self-test, diff 공백 검사 통과. 기존 큰 chunk 경고만 남는다.
- IAB Help18pt/light 육안 확인, URL/제목 정상, console error/warn 없음. 등록1366/dark 등 저장 screenshot에서 제목·컨트롤·줄바꿈과 겹침을 확인했다. 증거는 위 TEMP의 screenshots에 있다.
- 실제 사용자 DB/설정/서비스, commit/push/배포, 모바일 검수는 수행하지 않았다. 임시 검수 서버·브라우저는 완료 후 종료한다. 다음 P5는 사용자 확인 전 미착수다.
