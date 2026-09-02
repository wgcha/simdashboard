# 개발 문서 지도

- 기준일: 2026-08-25
- 목적: 현재 구현, 운영 절차, 기능 계약, 과거 계획 문서를 구분한다.

문서 제목에 `plan`, `roadmap`, `spec`이 포함되어 있어도 현재 구현 완료를 뜻하지 않는다. 코드와 문서가 다르면 아래 우선순위를 적용하고, 같은 변경에서 기준 문서를 갱신한다.

## 문서 우선순위

1. 실행 가능한 코드, migration, 환경변수 검증과 자동 테스트
2. 승인된 ADR
3. 이 문서에서 `현재 기준`으로 분류한 문서
4. 상태가 `구현 기준` 또는 `완료 기록`인 기능 문서
5. 계획·로드맵·초기 목표 기록

## 처음 읽을 문서

| 목적 | 문서 |
|---|---|
| 설치와 실행 | [`../README.md`](../README.md) |
| 실제 프로그램 구조 | [`current-architecture.md`](current-architecture.md) |
| 개발 변경 절차 | [`development-workflow.md`](development-workflow.md) |
| 프로그램 정리·개발 우선순위 | [`program-consolidation-and-development-plan.md`](program-consolidation-and-development-plan.md) |
| DB 결과 폴더·확장자 계약 | [`storage-folder-and-file-contract.md`](storage-folder-and-file-contract.md) |
| 완결형 예제 프로젝트 | [`coherent-demo-examples.md`](coherent-demo-examples.md) |
| WSL 개발환경 | [`wsl-development-setup.md`](wsl-development-setup.md) |
| PostgreSQL 전환·운영 | [`backend-sql-integration-guide.md`](backend-sql-integration-guide.md) |
| Rocky Linux 배포 | [`rocky8-deployment-runbook.md`](rocky8-deployment-runbook.md) |

## 현재 기준 문서

| 영역 | 문서 | 성격 |
|---|---|---|
| 구조 | [`current-architecture.md`](current-architecture.md) | 현재 코드 기준 |
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
| 업무 유형·결과 snapshot·마스터 Refresh | [`work-type-request-results-and-master-refresh.md`](work-type-request-results-and-master-refresh.md) |
| 업무 정의 결과 위젯 | [`work-definition-result-widget-ui-design.md`](work-definition-result-widget-ui-design.md) |
| 의뢰 접수·SPDM | [`work-type-request-intake-spdm-plan.md`](work-type-request-intake-spdm-plan.md) |
| 배치 실행·판정 | [`batch-execution-and-verdict-controls-spec.md`](batch-execution-and-verdict-controls-spec.md) |
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
- OpenAPI 생성 경로가 `frontend/src/shared/api/generated/openapi.ts`인가?
- DuckDB는 로컬 adapter, PostgreSQL은 운영 source of truth로 구분했는가?
- 계획과 구현 완료를 구분했는가?
- 예제 파일을 실제 parser가 읽는 자동 테스트가 있는가?
- 변경한 Markdown의 상대 링크가 존재하는가?
