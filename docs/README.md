# 개발 문서 지도

작업별 기준 문서와 필요한 후속 읽기를 표시합니다. 먼저 관련 문서 한두 개를 읽고, 조건부 링크가 해당하는 경우에만 더 확인하세요. 선택 가능한 전체 목록은 [상세 카탈로그](catalog.md)에 있습니다.

## 작업별 시작점

| 작업 | 먼저 읽기 | 조건부로 읽기 |
|---|---|---|
| 결과 등록·검수·Case 가시화 | [현재 결과 등록 계약](features/result-registration.md) | DB·경로가 바뀌면 [배포 정책](windows-deployment-policy.md), 과거 검증 근거는 [#32 완료 기록](archive/2026/result-registration-review-plan.md) |
| UI 크기·밀도·본문 폭 | [UI 개선 기준·데스크톱 적용 정책](dashboard/ui-density-improvement-plan.md) | 구현 경계는 [P0 계약](dashboard/ui-density-p0-contract.md), 완료 근거는 [P1~P5 기록](archive/README.md#2026-문서) |
| 배포·업데이트 | [Windows 배포 정책](windows-deployment-policy.md) | [설치·업데이트 시나리오](windows-deployment-scenarios.md), 폐쇄망이면 [패키지 설치](windows-server-offline-installation.md) |
| 대시보드 조회·비교 | [대시보드 기능 안내](dashboard/README.md) | 등록 방식 변경은 [결과 등록 계약](features/result-registration.md) |
| SPDM 폴더·기존 수집 API | [SPDM 연동 계약](spdm-storage-workflow.md) | 경로·확장자는 [저장 계약](storage-folder-and-file-contract.md) |
| 계정·프로젝트 권한 | [권한 구현 가이드](access-control-and-menu-policy-implementation-guide.md) | 동작 요구는 [권한 명세](access-control-functional-specification.md) |
| DB·migration·저장 경로 | [DB 통합 가이드](backend-sql-integration-guide.md), [저장 경로 계약](storage-folder-and-file-contract.md) | 현재 구성은 [아키텍처](current-architecture.md), 배포 영향은 [배포 정책](windows-deployment-policy.md) |
| 미완료 개발 계획 | [현재 계획 목록](plans/README.md) | 계획 문서가 요구할 때만 관련 기능·정책 문서를 확인 |
| 문서 정리·갱신 | [문서 관리 정책](policies/documentation.md) | 공통 프로젝트 규칙은 [`../AGENTS.md`](../AGENTS.md) |

## 현재 공통 기준

- 개발·보안·배포 계약: [`../AGENTS.md`](../AGENTS.md)
- 기존 기준 문서와 ADR은 원래 경로를 유지합니다. [상세 카탈로그](catalog.md)에서 영역별 경로를 찾으세요.
- [보관 문서](archive/README.md)는 과거 결정과 완료 기록을 모읍니다. 기본 작업 탐색에는 사용하지 않습니다.
