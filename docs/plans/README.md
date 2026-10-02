# 현재 작업 계획

여기에는 구현이나 운영 검증이 실제로 남아 있는 계획만 둔다. 완료된 계획은 [보관 문서](../archive/README.md)에서 찾는다.

- [폴더 규칙·공통 조회·최종확정 구현 계획](folder-schema-working-final-implementation.md): 단계별 구현과 격리 검증 완료. 실제 Server 2022·SMB 운영 환경 검증 범위는 별도로 남아 있다.

- [Case 결과 중심 사용 흐름 재설계 계획](case-results-workflow-redesign.md): 자동 반영(30초), 공용 계층 트리, A안 화면, 등록 단순화, Case 보고서·서버 PDF 변환, Final 보고서 저장. 구현 미착수. 소재물성 계획의 2026-10-02 후속 UI 계획을 대체한다.
- [모델별 소재·물성 대시보드 단계별 구현 계획](materials-dashboard-implementation.md): 파서·조회·화면의 기존 확인 범위와 2026-10-02 후속 UI 계획을 관리한다. [계층 선택 UI 개선](materials-dashboard-implementation.md#모델-소재물성-계층-선택-ui-개선-계획)은 Refresh 의뢰 목록·폴더명 갱신·base URL 이동 문제의 우선 복구, Case→하중→Run→Option→Scene 선택, Case 결과 보고서 호환을 포함하며 구현 미착수다.
- [프로그램 통합 및 개발 계획](../program-consolidation-and-development-plan.md): 미완료 항목의 추적 자료다. 과거 Rocky 운영 기준이 섞여 있으므로 현재 배포 기준으로 사용하지 않는다. 배포는 [Windows 배포 정책](../windows-deployment-policy.md)과 [ADR 0005](../adr/0005-windows-server-offline-deployment.md)를 우선한다. 과거 미완료 항목을 현재 승인 작업으로 자동 해석하지 않는다.
