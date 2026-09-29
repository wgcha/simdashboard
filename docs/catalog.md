# 상세 개발 문서 카탈로그

이 카탈로그는 선택 탐색용입니다. 먼저 읽을 문서와 작업별 경로는 [문서 지도](README.md)를 확인하세요.

기존 목록의 발견 가능성을 보존한 색인이며, 모든 문서의 현재 유효성을 재인증한 목록은 아닙니다. 과거 Rocky 운영 기준은 [Windows 배포 정책](windows-deployment-policy.md)과 [ADR 0005](adr/0005-windows-server-offline-deployment.md)가 대체합니다. 계획·검증 기록은 현재 계약과 구분해 읽으세요.

## 현재 기준 문서

PC 개인 실행 환경 지원과 향후 중앙 배치 방향은 [실행 환경 운영 정책](execution-environment-policy.md)을 따른다(2026-09-28). 과거 PC도우미 설치·개인 설정 제공 계획보다 우선한다.

| 영역 | 문서 | 성격 |
|---|---|---|
| 런타임 | [`windows-development-setup.md`](windows-development-setup.md) | native Windows 설치·실행·DB 선택 |
| PostgreSQL | [`windows-postgresql-quickstart.md`](windows-postgresql-quickstart.md) | Windows Git 업데이트·최초 설정·실행·DuckDB 이관 |
| 검증 기록 | [`windows-migration-validation-2026-09-07.md`](windows-migration-validation-2026-09-07.md) | WSL→Windows 보존·실행·DB 조사 |
| 구조 | [`current-architecture.md`](current-architecture.md) | 현재 코드 기준 |
| GUI | [`request-centric-workspace-ux.md`](request-centric-workspace-ux.md) | 의뢰 중심 화면, 문맥 유지, 과거 GUI 설계 대체 범위 |
| GUI 후속 개선 | [`unified-request-workspace-plan.md`](unified-request-workspace-plan.md) | 네 탭 공통 화면, 중복 메뉴 제거, 와이드 화면 검증 |
| 개발 | [`development-workflow.md`](development-workflow.md) | 명령·변경·검증 절차 |
| 통합 계획 | [`program-consolidation-and-development-plan.md`](program-consolidation-and-development-plan.md) | P0~P2 정리·개발 backlog |
| 저장 계약 | [`storage-folder-and-file-contract.md`](storage-folder-and-file-contract.md) | DB·결과 폴더·확장자·예제 |
| 예제 카탈로그 | [`coherent-demo-examples.md`](coherent-demo-examples.md) | 프로젝트·의뢰·하중 경우·결과 상태 |
| 런타임 | [`wsl-development-setup.md`](wsl-development-setup.md) | WSL 설치와 테스트 |
| PostgreSQL | [`backend-sql-integration-guide.md`](backend-sql-integration-guide.md) | DB profile·migration·이전 |
| 운영 | [`deploy/rocky8/README.md`](../deploy/rocky8/README.md) | Rocky 설치기 |
| 운영 | [`rocky8-deployment-runbook.md`](rocky8-deployment-runbook.md) | 배포·rollback 순서 |
| 운영 | [`deployment-security-backup-guide.md`](deployment-security-backup-guide.md) | 보안·백업 기준 |
| 이관 | [`postgresql-pc-transfer-guide.md`](postgresql-pc-transfer-guide.md) | PC 간 PostgreSQL 이관 |

## ADR

| 문서 | 결정 |
|---|---|
| [`adr/0001-runtime-version-policy.md`](adr/0001-runtime-version-policy.md) | WSL/Rocky 런타임 버전과 DB 역할 |
| [`adr/0002-persistence-bootstrap-and-api-contract.md`](adr/0002-persistence-bootstrap-and-api-contract.md) | PostgreSQL migration과 OpenAPI 경계 |
| [`adr/0003-future-report-and-knowledge-protocol.md`](adr/0003-future-report-and-knowledge-protocol.md) | 보고서 구성과 미래 지식 연동 경계 |
| [`adr/0004-canonical-production-deployment-target.md`](adr/0004-canonical-production-deployment-target.md) | Rocky 8 운영 target과 Windows compatibility profile |

## 기능 계약과 구현 기록

| 기능 | 문서 |
|---|---|
| 개인 회원가입·비밀번호 변경·과거 도우미 설치 구현 | [`personal-onboarding-implementation.md`](personal-onboarding-implementation.md) |
| 개인 계정 첫 화면·기존 사내 설치 전환 검토와 개발 계획 | [`personal-account-entry-rollout-plan.md`](personal-account-entry-rollout-plan.md) |
| 개인 계정 최초 관리자·업데이트·검증 기록 | [`personal-account-rollout-runbook.md`](personal-account-rollout-runbook.md) |
| 계정·프로젝트 권한 백업·재설치·이관 | [`account-backup-and-recovery.md`](account-backup-and-recovery.md) |
| Windows 사내 HTTPS 템플릿 | [`windows-caddy-intranet.md`](windows-caddy-intranet.md) |
| 업무 유형·결과 snapshot·마스터 Refresh | [`work-type-request-results-and-master-refresh.md`](work-type-request-results-and-master-refresh.md) |
| 업무 정의 결과 위젯 | [`work-definition-result-widget-ui-design.md`](work-definition-result-widget-ui-design.md) |
| 의뢰 접수·SPDM | [`work-type-request-intake-spdm-plan.md`](work-type-request-intake-spdm-plan.md) |
| 배치 실행·판정 | [`batch-execution-and-verdict-controls-spec.md`](batch-execution-and-verdict-controls-spec.md) |
| 로컬 프로그램 검색·실행 | [`local-program-execution-plan.md`](local-program-execution-plan.md) |
| 계정별 PC 연결·중앙 실행 이력 | [`managed-local-execution-plan.md`](managed-local-execution-plan.md) |
| 계정별 PC 운영 검증 | [`managed-local-execution-qa.md`](managed-local-execution-qa.md) |
| 내 PC 설정·웹 시작 흐름의 과거 구현 기록(현재 비노출) | [`personal-pc-settings-plan.md`](personal-pc-settings-plan.md) |
| 내 PC 설정 검증 | [`personal-pc-settings-qa.md`](personal-pc-settings-qa.md) |
| 로컬 프로그램 실행 검증 | [`local-program-execution-qa.md`](local-program-execution-qa.md) |
| 통합 workbench | [`integrated-simulation-workbench-plan.md`](integrated-simulation-workbench-plan.md) |
| 결과 import | [`result-import-pipeline-spec.md`](result-import-pipeline-spec.md) |
| 결과 형식·미디어 | [`result-data-types-and-media-spec.md`](result-data-types-and-media-spec.md) |
| DB 미디어 저장 | [`postgresql-binary-media-storage-implementation-plan.md`](postgresql-binary-media-storage-implementation-plan.md) |
| 사용자 분석 페이지 | [`custom-analysis-pages-plan.md`](custom-analysis-pages-plan.md) |
| URL·모듈화 | [`frontend-url-routing-and-modularization-plan.md`](frontend-url-routing-and-modularization-plan.md) |
| Run 비교·신뢰도·검토 | [`priority-1-run-comparison-trust-review-spec.md`](priority-1-run-comparison-trust-review-spec.md) |
| 영상 비교 | [`drop_video_dashboard_spec.md`](drop_video_dashboard_spec.md) |
| PPTX 보고서 | [`ppt-report-export-spec.md`](ppt-report-export-spec.md) |
| 도움말·대시보드 확장 | [`help-and-dashboard-extension-spec.md`](help-and-dashboard-extension-spec.md) |
| 기능 예제 | [`feature-example-gallery-spec.md`](feature-example-gallery-spec.md) |
| 접근 제어 기능 | [`access-control-functional-specification.md`](access-control-functional-specification.md) |
| 접근 제어 구현 | [`access-control-and-menu-policy-implementation-guide.md`](access-control-and-menu-policy-implementation-guide.md) |
| 접근 제어 이력 | [`access-control-change-log.md`](access-control-change-log.md) |

기능 문서를 수정할 때는 문서 상단에 `상태`, `기준일`, 실제 구현 파일, 미구현 범위를 명시한다. 장래 설계와 현재 완료 내용을 같은 시제로 쓰지 않는다.

## 계획·배경 기록

다음 문서는 의사결정 배경과 과거 수용 조건을 보존한다. 현재 명령이나 경로의 기준으로 직접 사용하지 않는다.

- [`architecture-refactoring-roadmap-v2.md`](architecture-refactoring-roadmap-v2.md)
- [`luna-architecture-refactoring-spec.md`](luna-architecture-refactoring-spec.md)
- [`stabilization-and-portability-spec.md`](stabilization-and-portability-spec.md)
- [`CAE_Dashboard_WSL_Ubuntu_to_Rocky8_Development_Guide.md`](CAE_Dashboard_WSL_Ubuntu_to_Rocky8_Development_Guide.md)
- [`access-control-and-menu-policy-plan.md`](access-control-and-menu-policy-plan.md)
- [`../GOAL.md`](../GOAL.md): 초기 MVP 목표 기록
- [`../assumptions.md`](../assumptions.md): 초기 구현 가정 기록

## GitHub Issues 연결 규칙

GitHub Issues는 요구사항과 논의의 출처이고, 저장소 문서는 확정된 계약과 실행 절차의 원본이다.

- 계획 항목에는 이슈 번호, 결정, 담당 모듈, 완료조건을 기록한다.
- 이슈에서 확정된 DB 폴더·확장자·proxy 결정은 기준 문서에도 반영한다.
- private issue에 접근할 수 없는 자동화 환경을 위해 필요한 계약은 이슈에만 남기지 않는다.
- 이슈와 코드가 다르면 임의로 추측하지 않고 적용 범위와 잔여 구현을 분리해 표시한다.

현재 기준 문서는 [#13](https://github.com/wgcha/simdashboard/issues/13)의 SPDM
discovery folder, [#14](https://github.com/wgcha/simdashboard/issues/14)의
source/solver/result/report extension inventory, [#15](https://github.com/wgcha/simdashboard/issues/15)의 Linux corporate proxy·CA 요구사항을
[`program-consolidation-and-development-plan.md`](program-consolidation-and-development-plan.md)에 추적한다. 이슈의 upstream 요구사항과 현재 executable 계약의 차이는 각 기준 문서에 명시한다.

## 문서 변경 체크리스트

- 파일·디렉터리 경로가 실제로 존재하는가?
- 명령이 현재 `package.json`, `pytest.ini`, script 인자와 일치하는가?
- 환경변수가 `.env.example` 또는 배포 설정 예제에 있는가?
- native Windows 명령은 [`windows-development-setup.md`](windows-development-setup.md)와 실제 PowerShell script의 profile 선택이 일치하는가?
- OpenAPI 생성 경로가 `frontend/src/shared/api/generated/openapi.ts`인가?
- DuckDB는 로컬 adapter, PostgreSQL은 운영 source of truth로 구분했는가?
- 계획과 구현 완료를 구분했는가?
- 예제 파일을 실제 parser가 읽는 자동 테스트가 있는가?
- 변경한 Markdown의 상대 링크가 존재하는가?
