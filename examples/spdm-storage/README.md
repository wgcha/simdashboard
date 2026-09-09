# SPDM 저장 폴더 예제

관리자 저장소 설정을 이 `examples/spdm-storage` 폴더의 절대 경로로 지정하고 `저장 폴더 새로고침`을 실행한다. 실제 업무 저장소와 분리된 시험용 프로젝트가 발견된다.

```text
Project_0001_85QN80H_pv1/
  WR_0001_SimType2/
    CAE/Assy_SetCase1CushionCase1/Drop/
      sample_parallel/INDIVIDUAL/1_Face_Drop_Scene01_Face1_1st/
        results/summary.csv
```

`summary.csv`는 동작 검증용 가상 값이다. 실제 해석 결과나 승인 판단의 근거가 아니다. Windows에서는 복사가 끝난 파일을 직접 찾아 등록한다. 다른 플랫폼의 완료 파일 계약은 [SPDM 운영 문서](../../docs/spdm-storage-workflow.md)를 확인한다.

같은 파일로 다시 새로고침하면 기존 Run을 재사용해야 한다. 숫자를 수정한 후 새로고침하면 새 Run이 생기고 이전 Run은 남아 있어야 한다. 실제 업무에서는 저장소를 Git 소스 밖의 별도 폴더로 지정한다.
