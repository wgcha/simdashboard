# Working Case 최종확정 (Final 지정)

상태: 6단계 구현·검수 보강(2026-10-03), **W2 Final 복사 개편 구현(2026-10-06, 독립·보안 검수 대기)**: 조각 복사·형식 필터와 CAE 상한 제거·남은 공간 확인·임시 폴더 공개·작업 스레드·진행률·이어하기. 격리된 임시 SPDM root·합성 데이터로 검증했다. 실제 Windows Server 2022·공유폴더(SMB 잠금·백신·이름 바꾸기) 검증은 하지 않았다(W9). 계획 근거는 [Case 결과 흐름 재설계](../plans/case-results-workflow-redesign.md) §3.6, 경로 계약은 [DEPTH_V1 스키마](../contracts/depth-schema.md) D11·D12·D14다. PDF 관련 구현은 없다(2026-10-02 23:58 결정).

## 흐름

1. Case 결과 헤더의 **Final 지정** → 서버가 미리보기 계획을 만든다(`POST /preview`).
2. 창에 Case, 기준(최신 결과 · Scene별 결과 버전), Final/CAE 파일 수·목록, 만들 보고서(Final/Report 경로)를 보여준다.
3. 사용자가 PPTX·HTML 중 하나 이상을 고른다. HTML은 `영상 포함`(기본 꺼짐)을 고를 수 있다.
4. **Final 지정 확정** → 브라우저가 5단계 빌더(`buildCaseReportPptx`·`buildCaseReportHtml`)로 보고서를 만들고, 형식마다 한 번씩 올린 뒤(`PUT /{operation_id}/reports/{format}`) 확정한다(`POST /confirm`). 확정은 검사를 마치고 **복사 작업을 등록한 뒤 바로 응답**한다(`state: QUEUED/RUNNING`).
5. 창이 진행률(막대, 파일 n/m, 용량, 현재 파일, 단계 복사·검증·공개)을 1초마다 갱신한다(`GET /{operation_id}/job`). 창을 닫아도 서버에서 계속되고, Case 결과 헤더 배지가 `Final 복사 중 n%`(검증 단계는 `Final 검증 중 n%`)를 보인다. 페이지를 다시 열어도 상태 조회의 `active_operations`로 이어서 보인다.
6. 완료되면 창(열려 있으면)에 CAE 폴더와 보고서 경로, HTML에 넣지 못한 영상을 보여주고 헤더에 `Final 지정 완료`를 알린다. 실패하면 사유를 보이고 **재시도**는 같은 Final ID로 다시 등록한다(창에서는 같은 보고서 바이트를 다시 올린 뒤, 헤더에서는 이미 올린 보고서로). 진행률(%)은 복사 0~60, 검증 60~95, 공개 95~100으로 나눈다.

보고서 범위는 화면 선택과 무관하게 **Case 전체**다(2026-10-03 후속).

- 유통환경: Case의 모든 하중경우 › Run Case › Run Option을 경로 바와 같은 후보 규칙(최신 결과 `latest:<Case>`에 속하거나 아직 수집되지 않은 항목)으로 모아 Run Option마다 구역 하나를 만든다. 각 구역은 그 Run Option의 Scene으로 요약·Scene 비교·이미지·영상을 담는다. Component는 화면에서 고른 이름과 같은 것, 없으면 값이 있는 첫 후보, Basis는 화면 값이 후보에 있으면 그 값, 아니면 첫 후보다. 표시 엣지·라인은 화면 설정을 따른다. 결과가 없는 Run Option은 `결과 없음` 구역으로 표시하고 확정을 막지 않는다. 결과 읽기 오류는 보고서 만들기를 실패시킨다(다시 시도).
- 사용환경: 사용환경 Case의 다섯 평가 종합과 이미지·영상([Case 결과 보고서](case-report.md)의 사용환경 레시피, Reference 없음).
- 화면에서 Run Case·Run Option을 고르지 않아도 확정할 수 있다. 결과(수집본)가 없는 Case만 보고서를 만들 수 없어 확정이 막힌다.
- 헤더의 `보고서` 버튼은 지금처럼 현재 선택 범위로 만든다.
- 보고서를 만들 때 이미지는 한 번 읽어 두 형식이 공유하고, 넣지 못한 이미지·영상은 창에 표시한다.

## 저장 위치 (DEPTH_V1 D11·D12)

| 위치 | 내용 |
|---|---|
| `Final/CAE/<Case>/<확정 ID>/<Working Case 기준 상대 경로>` | 기준 Scene 폴더 아래 **모든 파일**(W2: 형식 필터 없음)과 덱 include 참조. 제외: Office 잠금·임시 `~$*`, `*.tmp`, 이름이 `.`·`$`·`~`로 시작하는 파일·폴더, Windows 숨김·시스템 속성 항목, 기존과 같이 `CAD`·`Final`·`Validation`·`Library` 폴더와 스키마 제외(EXCLUDE) 경로. 대시보드 표시 형식과 무관하다 |
| `Final/Report/<Case>/<확정 ID>/` | 앱이 만든 보고서만, 폴더 바로 아래: `<Case>_report.pptx`, `<Case>_report.html` |
| `Final/.finalizations/<확정 ID>/` | 서명된 `plan.json`, 보고서 임시 보관 `reports/`와 `reports.json`, 서명된 작업 `job.json`·진행 `progress.json`, 복사 완료 기록 `copied.jsonl`(줄마다 서명), 작업 잠금 `.job.lock`, 공개 전 임시 폴더 `staging/{CAE,Report,partial}`, `complete.json`, 해시 확인 기록 `verified.json`(서명). 의뢰 잠금은 `Final/.finalizations/.request.lock` |

- 확정 ID는 32자리 소문자 hex(`uuid4().hex`, `_OPERATION_ID`)로 DEPTH_V1 `FINAL_VERSION` 형식과 같다.
- 보고서 파일 이름은 서버가 Case 이름으로 정한다(Windows 금지·제어 문자 `_`, 공백 `_`, 80자, 예약 이름 앞 `_`). 클라이언트가 보낸 이름은 쓰지 않는다.
- CAD는 복사하지 않는다. 앱의 SPDM 쓰기는 의뢰의 `Final/` 아래뿐이다. Working과 기존 Final 파일은 옮기거나 덮어쓰거나 지우지 않는다(D14). 앱이 옮기는 것은 이 확정이 만든 `staging/CAE`·`staging/Report` 폴더뿐이다(공개).

## 복사·공개 (W2)

1. **확정(동기, 빠름):** 보고서 필수·업로드 해시·계획·원본(목록과 크기·수정 시각) 재검증, 대상 폴더 충돌, **남은 공간**을 확인하고 서명된 `job.json`과 `progress.json`(QUEUED)을 쓴 뒤 응답한다. 남은 공간은 Final 폴더 드라이브의 `shutil.disk_usage` 여유 공간이 `남은 복사량 + max(5 %, 1 GiB)` 이상이어야 하며 부족하면 507 `FINALIZATION_DISK_SPACE`로 아무것도 쓰지 않는다(작업 시작 때 다시 확인).
2. **작업 스레드:** 백엔드 프로세스 안의 데몬 스레드(`case_finalization_jobs`, 새 서비스·의존성 없음)가 실행한다. 프로세스당 동시 2개, 같은 의뢰의 작업은 한 번에 하나(대기열에서 순서대로). 같은 확정은 작업 잠금(`.job.lock`, 기다리지 않음)으로 한 작업자만 실행하고, `complete.json`은 기존 의뢰 잠금 안에서 쓴다. 의뢰 잠금을 복사 내내 잡지 않는 이유: 수 GB 복사 동안 같은 의뢰의 미리보기·보고서 업로드가 멈추지 않게 하기 위해서다.
3. **조각 복사:** 저장소 계층 `LocalFsProvider.copy_stream`이 8 MiB 조각으로 읽고 쓰며 SHA-256을 함께 계산한다(메모리는 파일 크기와 무관). 읽기는 기존 안정 읽기와 같은 안전 장치를 쓴다: 링크·reparse 원본 거부(POSIX `O_NOFOLLOW`, Windows 핸들 속성), 원본·대상 상위 폴더 고정, 복사 전·중(크기 증가)·후 원본의 식별자·크기·수정 시각 비교(`SPDM_FILE_CHANGED` → `FINALIZATION_SOURCE_STALE`), 대상은 새 파일로만 만들고 fsync, 실패하면 지운다. 파일은 `staging/partial/<임의 이름>`에 쓴 뒤 확인을 마치면 `staging/CAE/<경로>`로 옮긴다.
4. **검증:** 모든 임시 파일을 다시 읽어(조각 단위) 기대 해시와 비교한다. 기대 해시는 최신 결과 수집본의 결과 파일이면 수집 해시, 그 밖의 파일이면 복사하며 계산한 원본 해시다. 다르면 그 파일만 원본에서 한 번 더 복사하고, 그래도 다르면 실패한다. (`VERIFY_STAGED_CONTENT`로 끌 수 있는 추가 읽기 1회이며 실제 HPC 드라이브에서 W9에 측정한다.) 임시 폴더 쓰기(파일 옮기기·교체·삭제)는 매번 임시 폴더 상위 경로를 고정(Windows: 이름 바꾸기·junction 교체 불가 핸들)한 상태에서 경로에 바로가기·reparse point가 없음을 다시 확인한 뒤 한다(`FINALIZATION_STAGING_UNSAFE`). 폴더를 만들기 전에도 확인한다. 저장소 계층의 FINAL 구역 `replace`·`remove`·`append_bytes`(`copied.jsonl`)도 같은 고정·확인을 하며, `append_bytes`는 연 핸들과 경로의 `lstat` 항목이 같은 일반 파일인지(링크·reparse 아님, 같은 식별자) 확인해 Windows에서도 링크를 따라가지 않는다(LEGACY 구역 호출은 기존 동작 유지). 임시 폴더는 한 단계씩, 고정·재확인한 상위 아래에서만 만든다.
5. **공개:** 이름 바꾸기 직전에 `staging/CAE`·`staging/Report` 하위 전체를 `lstat`으로 다시 훑어, 정확히 계획한 파일과 그 상위 폴더만 있는지 확인한다(바로가기·reparse point·계획에 없는 파일이나 빈 폴더가 있으면 `FINALIZATION_STAGING_UNEXPECTED`). 같은 볼륨 안에서 `staging/CAE` → `Final/CAE/<Case>/<확정 ID>`, `staging/Report` → `Final/Report/<Case>/<확정 ID>` 순서로 폴더째 이름을 바꾼다(`LocalFsProvider.rename_no_replace`: 대상이 있으면 절대 바꾸지 않음 — Windows `MoveFileEx` 기본 동작, Linux `renameat2(RENAME_NOREPLACE)`, 지원하지 않는 파일 시스템은 존재 확인 후 이름 바꾸기). `<Case>` 상위 폴더는 필요하면 만든다. 공유 위반·잠금(`PermissionError`)은 0.2·0.5·1·2·4·8초 간격으로 다시 시도하고, 그래도 안 되면 `FINALIZATION_PUBLISH_BUSY`로 실패한다. 공개 전에는 `Final/CAE/<Case>/<확정 ID>`와 `Final/Report/...`가 보이지 않는다.
6. **완료:** 두 폴더가 모두 공개된 뒤 공개된 폴더 내용이 정확히 계획과 같은지(위와 같은 `lstat` 검사) 보고, **공개된 CAE 파일과 보고서를 모두 다시 읽어 해시를 확인**한 뒤(확인 전후 파일 크기·수정 시각·식별자가 같아야 함) `complete.json`을 쓴다. 따라서 `complete.json`의 해시는 공개된 파일과 일치한다. 이어서 확인 당시 출력 파일 목록의 크기·수정 시각·식별자를 서명한 `verified.json`을 쓴다. **읽기 횟수:** 원본 읽기(복사) 1회 + 공개 전 임시 파일 해시 1회 + 공개 후 해시 1회 = CAE 데이터 3회 읽기(쓰기 1회). 공개 후 해시는 `complete.json`의 근거라 항상 한다. 공개 전 해시(`VERIFY_STAGED_CONTENT`, 기본 켜짐)는 손상된 임시 복사본을 공개 전에 원본에서 다시 복사하게 해 주는 것이 목적이다(끄면 그런 손상은 공개 후 확인에서 `FINALIZATION_OUTPUT_VERIFY_FAILED`로만 드러나고, 이미 공개된 폴더는 관리자가 정리해야 한다). 대용량 HPC 드라이브에서는 W9 측정 뒤 끌지 정한다. 완료 기록만이 완료의 근거다. 그다음 이 확정의 보고서 업로드 보관본과 빈 `staging` 폴더를 지운다(앱이 만든 것만, 실패해도 완료에는 영향 없음).

### 진행 기록과 이어하기

- `progress.json`은 `state`(QUEUED·RUNNING·FAILED·COMPLETE), `phase`(COPYING·VERIFYING·PUBLISHING), `files_done/files_total`, `bytes_done/bytes_total`, `current_file`, `error{code,message}`, 공개한 폴더 `published`, 시도 횟수를 HMAC으로 서명해 약 1초마다 쓴다. 서명이 맞지 않는 진행 기록은 무시하며(처음부터 다시 판단), 어떤 진행 기록도 완료로 취급하지 않는다.
- `copied.jsonl`은 임시 폴더로 복사를 마친 파일마다 경로·크기·SHA-256 한 줄을 서명해 덧붙인다. 이어할 때 줄 단위로 읽으며(한 번에 메모리에 올리지 않음) 1 GiB(파일 수백만 개 수준)를 넘으면 `FINALIZATION_METADATA_LIMIT`로 분명히 실패한다(기록을 조용히 버리지 않음). 메모리에는 파일당 기록 하나와 계획(`plan.json`, 최대 64 MiB)이 올라간다. 이어할 때 서명이 맞고 크기가 같고 임시 파일이 있는 파일은 건너뛴다(공개 전 검증에서 다시 해시 확인). 끊긴 마지막 줄·위조 줄은 무시한다. `staging/partial`의 반쯤 쓴 파일은 이어하기 시작 때 지우고 그 파일은 처음부터 다시 복사한다.
- **서버 시작:** 앱 시작 때 데몬 스레드 하나가 DB에서 Case 결과가 있는 의뢰 목록을 한 번 읽고(`dashboard_cases`), 각 의뢰의 `Final/.finalizations`만 나열해(DB 연결을 잡지 않음) `job.json`이 있고 완료되지 않았으며 상태가 QUEUED·RUNNING(중단됨)인 작업을 찾은 뒤, 조회 때와 같은 DB 범위 확인을 통과한 작업만 같은 Final ID로 다시 등록한다. 시작은 기다리지 않는다. 환경 변수 `SIMDASH_FINALIZATION_RESUME_SCAN=0`으로 끌 수 있다.
- **상태 조회:** `GET /status`와 `GET /{id}/job`이 QUEUED·RUNNING인데 이 프로세스에서 실행 중이 아닌 작업을 보면, 작업의 프로젝트·의뢰·Case가 DB에 같은 범위·같은 SPDM root로 남아 있을 때만 다시 등록한다(이 조회는 `PROJECT_DATA_VIEW`로 열림). FAILED 작업은 자동으로 재시도하지 않는다(사용자 **재시도**).
- 서명된 계획·작업 기록을 확인할 수 없거나 작업을 시작할 수 없으면 `FAILED`(`FINALIZATION_JOB_INVALID` 등)를 기록해 조회가 같은 작업을 계속 다시 등록하지 않게 한다. `complete.json`이 있어도 서명·계획·출력 존재를 확인하지 못하면 완료로 보지 않고 `FINALIZATION_COMPLETE_UNVERIFIED`로 남기며 보고서 업로드 보관본을 지우지 않는다.
- 알려진 한계(대상 플랫폼 아님): POSIX(Linux 개발 환경)에서는 디렉터리를 핸들로 고정할 수 없어 "확인 직후·쓰기 직전" 사이에 상위 폴더를 바꿔치기하는 경쟁은 막지 못한다(확인은 매번 다시 함). Windows Server는 고정 핸들로 막는다.
- 이름 바꾸기와 그 진행 기록 사이에 중단되면 대상 폴더는 있고 `published`에는 없다. 이어할 때 그 폴더가 정확히 이 확정의 파일(`lstat` 검사로 바로가기·계획에 없는 폴더 없음, 목록 일치, 모든 해시 일치)일 때만 공개된 것으로 받아들이고, 아니면 `FINALIZATION_DESTINATION_CONFLICT`로 멈춘다(지우지 않음).
- 한 폴더만 공개된 뒤 실패하면(예: Report 이름 바꾸기 잠금) 완료 기록은 없고, 같은 Final ID 재시도가 나머지를 마저 공개한다. 이미 공개한 CAE는 다시 복사하지 않는다.
- 버려진 임시 폴더(확정하지 않았거나 실패한 채 다시 시도하지 않은 작업의 `.finalizations/<ID>/staging`)는 자동으로 지우지 않는다. 관리자가 `.finalizations/<ID>/`의 `progress.json`을 확인하고 정리한다(SPDM은 `.finalizations`를 가져가지 않는다).

## 현재 Final과 재지정 (W3, 2026-10-06)

- **의뢰·환경당 현재 Final은 하나**다. 현재 Final = 그 환경에서 완료된 Final 중 `confirmed_at`이 가장 늦은 것. `confirmed_at`은 의뢰 잠금 안에서 `complete.json`을 쓰기 직전에 정하며, 의뢰마다 마지막 값(`designations.json`)보다 반드시 커지게 한다(시계가 뒤로 가도 1 µs 뒤). 따라서 **완료를 마지막으로 커밋한 Final이 현재 Final**이다. 두 지정이 동시에 진행되면 둘 다 복사·완료되고(다른 Final ID, 같은 의뢰는 작업 대기열에서 순서대로), 나중에 완료된 쪽이 현재가 된다. 더 오래된 완료가 더 새 지정을 덮지 않는다(`_designate`가 기록된 `confirmed_at`보다 늦을 때만 바꿈).
- 같은 Case를 다시 지정해도 새 Final이 현재가 된다. 이전 Final은 이력에 **이전 Final**로 남고 폴더·파일은 옮기거나 지우지 않는다(D14). 이전 형식(1·2·3) 완료 기록도 이력에 표시된다.
- 앱이 믿는 포인터: `Final/.finalizations/designations.json`(서명, 환경별 현재 Final ID·Case·`confirmed_at`·`complete.json` 해시·이전 Final ID, 의뢰 전체의 마지막 `confirmed_at`). 없거나 서명이 맞지 않으면(W3 이전 의뢰) 상태 조회는 완료 기록에서 가장 늦은 것을 현재로 보이고, 다음 완료가 이전 Final ID를 서명된 완료 기록에서 찾아 적는다.
- **요약 파일(SPDM용, 임시안):** `Final/current.json`. 이름은 저장소 계층 상수 `FINAL_SUMMARY_FILE` 하나로 정하고(`case_finalization.SUMMARY_FILE`), FINAL 쓰기 구역은 정확히 이 파일과 그 임시 파일 `.current.json.<32 hex>.tmp`만 `Final` 바로 아래에 허용한다. SPDM 협의 후 위치·형식을 바꾸려면 이 상수와 이 절을 함께 바꾼다.
- 쓰는 때: 새 Final의 `complete.json`을 쓴 같은 의뢰 잠금 구역에서 포인터와 함께 쓴다. 같은 폴더의 임시 파일에 쓰고 fsync한 뒤 이름 바꾸기로 교체한다(원자적, 상위 폴더 고정·링크 확인). 실패해도 Final은 완료 상태로 남고 상태 조회가 **요약 파일 갱신 필요**(`summary.state` MISSING/STALE)를 알린다. 화면의 **요약 파일 갱신**(`POST /summary/repair`, `RESULT_IMPORT`)이 현재 Final로 다시 쓴다. 상태 조회(GET)는 요약 파일·포인터를 쓰지 않는다.
- 형식(`schema_version` 1):

```json
{
 "format": "simdashboard-final-summary", "schema_version": 1, "generated_at": "…Z",
 "environments": {
  "DISTRIBUTION": {
   "final_id": "<32 hex>", "environment": "DISTRIBUTION",
   "case_label": "<Case>", "case_relative_path": "<SPDM root 기준 Working Case 경로>",
   "designated_by": "<사용자 ID>", "designated_at": "…Z",
   "cae_path": "CAE/<Case>/<Final ID>", "report_path": "Report/<Case>/<Final ID>",
   "reports": [{"format": "pptx", "path": "Report/<Case>/<Final ID>/<Case>_report.pptx", "size": 0, "sha256": "…"}],
   "files_count": 0, "total_bytes": 0,
   "files": [{"path": "<cae_path 기준 상대 경로>", "size": 0, "sha256": "…"}],
   "complete_record": ".finalizations/<Final ID>/complete.json", "complete_sha256": "<complete.json 바이트의 SHA-256>",
   "previous_final_id": "<32 hex>|null"
  }
 }
}
```

  - 경로는 모두 이 파일이 있는 `Final` 폴더 기준이다. 환경마다 항목 하나이며 다른 환경 항목은 유지한다.
  - CAE 파일이 20,000개(`SUMMARY_MAX_LISTED_FILES`)를 넘으면 `files: null`, `files_in: complete_record`로 서명된 `complete.json`(같은 `files` 목록)을 가리킨다.
  - SPDM은 앱의 HMAC 키를 모르므로 서명 필드는 넣지 않고, 추적용으로 `complete.json`의 SHA-256을 적는다. `summary.state`는 요약 파일의 현재 환경 항목이 현재 Final ID와 그 `complete.json` 해시를 가리키면 OK, 다르면 STALE, 없거나 읽을 수 없으면 MISSING, 완료된 Final이 없으면 NONE.
- 상태 조회 추가 필드: `current_final`(operation_id, case_id, case_label, case_path, designated_by, designated_at, schema_version, output_paths, verified), `final_history`(최신순 최대 50, `role: CURRENT|PREVIOUS`), `summary`(state, path, final_id).
- 화면: Case 결과 헤더에 `현재 Final · <Case> · <지정자> · <시각>`, 요약 파일 갱신 필요 표시와 **요약 파일 갱신** 버튼. 이 Case의 마지막 Final이 현재가 아니면 배지가 **이전 Final**. Final 지정 창은 현재 Final이 다른 Case이면 "현재 Final(Case X)을 이전 Final로 바꾸고 이 Case를 Final로 지정합니다" 확인란을 체크해야 확정할 수 있고, 같은 Case면 안내만 보인다. 창에 Final 이력(현재/이전)을 보인다.
- 검수 반영(2026-10-06, c836593 이후):
  - **현재 Final 검증:** 상태 조회의 `current_final.verification`은 SHA256·STAT_SINCE_COMPLETION·SIZE·FAILED·MISSING이고 `verified`는 FAILED·MISSING일 때 false다. 이때 `summary.state`는 OK가 아니라 `CURRENT_UNVERIFIED`(해시 불일치)·`CURRENT_MISSING`이며 헤더에 경고를 크게 보인다.
  - **갱신(repair)은 해시 확인 필수:** 현재 Final의 모든 출력 파일을 예산 없이 다시 해시한다(완료 때의 `verified.json`은 믿지 않음). 해시는 **의뢰 잠금 밖**에서 하고 전후 출력 stat 서명(크기·수정 시각·파일 식별자)이 같아야 한다. 그다음 의뢰 잠금 안에서 서명 포인터가 그대로인지(바뀌었으면 409 `FINALIZATION_REPAIR_RETRY`)와 stat 서명이 그대로인지(바뀌었으면 409 `FINALIZATION_CURRENT_UNVERIFIED`) 다시 보고, **요약 파일을 먼저, 서명 포인터를 나중에** 쓴다. 요약 파일 쓰기가 실패하면 포인터는 움직이지 않는다(포인터 쓰기만 실패하면 요약이 앞서 STALE로 보이며 다시 갱신하면 맞춰진다). 해시가 다르면 409 `FINALIZATION_CURRENT_UNVERIFIED`. 큰 Final은 요청이 오래 걸릴 수 있으나 다른 완료를 막지 않는다(W9 측정). 완료 때(`_designate`)는 새 Final이 규칙상 현재이므로 포인터를 먼저 쓰고 요약 파일 실패는 MISSING/STALE로 보인다.
  - **override는 전역 관리자만:** `override: true`는 전역 관리자가 아니면 403 `GLOBAL_ADMIN_REQUIRED`. 상태 응답 `can_override_summary`가 true일 때만 화면이 CONFLICT·CURRENT_MISSING에서 **강제 갱신(관리자)** 버튼(확인 창)을 보인다. 갱신은 성공(`CASE_FINALIZATION_SUMMARY_REPAIRED`)과 실패(`CASE_FINALIZATION_SUMMARY_REPAIR_FAILED`, 오류 코드·HTTP 상태) 모두 `override` 값과 함께 감사 기록한다(실패 기록은 별도 연결로 남김).
  - **현재 Final이 사라진 경우:** 서명된 포인터가 가리키는 Final의 기록·출력이 확인되지 않고 그보다 새 완료도 없으면 현재 Final은 "없음"(`missing: true`)으로 표시하고, 더 오래된 Final로 조용히 되돌리지 않는다. 갱신은 409 `FINALIZATION_CURRENT_MISSING`. 관리자가 확인 뒤 `override: true`로 요청하면(감사 기록 `override`) 남아 있는 가장 새 Final로 옮긴다.
  - **앱이 만들지 않은 `Final/current.json`:** 일반 파일인데 앱 형식이 아니거나(16 MiB 초과 포함) 바로가기·폴더이면 바꾸지 않는다(`FINALIZATION_SUMMARY_CONFLICT`, 완료는 유지, `summary.state` CONFLICT, 헤더 "요약 파일 충돌(관리자 확인)"). `override: true`는 앱 형식이 아닌 일반 파일만 바꿀 수 있고 바로가기·폴더는 절대 바꾸지 않는다.
  - 요약 파일은 16 MiB까지 읽고 (크기, 수정 시각, 식별자)로 캐시한다. 목록에 넣는 CAE 파일 상한은 10,000개(넘으면 `complete.json`을 가리킴). 다른 환경 항목은 기존 요약에서 복사하지 않고 그 환경의 서명된 포인터와 완료 기록에서 다시 만든다(없으면 뺌).
  - 포인터가 없으면 다음 완료 시각의 하한은 서명된 `complete.json`들의 가장 늦은 `confirmed_at`이다.
  - 다음 요약 쓰기 때 1시간 넘은 `.current.json.<32 hex>.tmp`(정확히 이 이름)만 지운다.
- 알려진 한계·개인정보: `designated_by`는 사용자 ID 그대로이며(이름 아님) SPDM이 읽는 파일에 들어간다. 표시 이름 추가·ID 제외는 SPDM 협의 때 정한다.

## 기준: 최신 결과

- `capture_id`로 화면과 같은 `latest:<dashboard_case_id>`를 받는다. Scene별 기준 수집본은 화면이 쓰는 `dashboard_capture.merge_latest_payload`를 같은 수집본 순서(`created_at, id`, `get_latest_capture`와 동일)로 직접 호출해 정한다(규칙의 단일 원천, 2026-10-03 보강). 병합된 Scene은 결과·미디어 경로(없으면 같은 수집본 안의 Scene 폴더 이름)로 현재 확정 Scene 경로에 대응시킨다. 사용환경은 가장 최근 수집본 하나다(화면·Final 보고서·Final 기준 모두 `latest:<Case>`).
- 화면이 보여 주는 최신 수집본이 현재 Folder Schema와 맞지 않으면(위치 목록 없음 `CAPTURE_SCHEMA_MISSING`, 대상·계층 불일치 `CAPTURE_SCHEMA_INCOMPATIBLE`) 그 Scene은 **제외**하고 더 오래된 수집본으로 대신하지 않는다. 수집본이 없는 현재 확정 Scene은 `NO_CAPTURE`로 제외한다. 제외 목록은 계획·미리보기 응답 `excluded_scenes`(`scene_path`, `source_capture_id`, `reason`)에 있고 Final 지정 창에 표시한다. 정보용이라 확정 시 비교하지 않는다(Scene 기준은 `scene_sources`로 비교).
- 계획에 Scene별 `source_capture_id`·`source_capture_fingerprint`(`scene_sources`)와 최신 결과 지문(수집본 ID 목록 해시)을 서명해 기록한다.
- **무엇을 언제 확인하는가(W2, 계획 3 형식):**
  - 미리보기·확정: 폴더 목록과 파일별 `lstat`(크기·수정 시각)만 한다. 내용은 읽지 않는다(덱 include 확인 제외). 최신 결과 수집본의 결과 파일은 수집 기록의 크기와 현재 크기가 달라야 `FINALIZATION_SOURCE_STALE`이고 해시는 수집 기록 값을 계획에 고정한다. 그 밖의 파일은 크기·수정 시각을 계획에 고정한다(`sha256: null`, `modified_ns`).
  - 복사 중: 결과 파일은 복사하며 계산한 해시가 수집 해시와 같아야 하고, 그 밖의 파일은 크기·수정 시각이 계획과 같아야 하며 복사 중에 바뀌지 않아야 한다. 다르면 `FINALIZATION_SOURCE_STALE`로 실패하고 아무것도 공개하지 않는다. 계산한 해시는 `complete.json`의 `files[].sha256`에 기록한다.
  - 공개 전: 임시 파일 전체를 다시 읽어 해시를 확인한다. 공개 후: 폴더 내용 정확성과 CAE·보고서 전체 해시(완료 기록의 근거).
- 확정 시 계획 이후 크기·수정 시각이 바뀐 Scene 파일이 있으면 `FINALIZATION_PLAN_CHANGED`(새 미리보기). 미리보기 뒤 새 수집본이 생기면 `FINALIZATION_CAPTURE_CHANGED`로 막고 새 미리보기를 요구한다.
- 덱 include 확인: Scene 파일은 모두 복사하므로 include는 범위 밖 참조 거부와, Scene 탐색이 건너뛰는 폴더(`Library` 등)의 덱을 더하는 데 쓴다. 파싱 한도(파일 64 MiB·합계 256 MiB·20초)를 넘는 덱은 그대로 복사하되 include를 따라가지 않고 `include_unchecked`에 적어 창에 표시한다.
- 구체적인 수집본 ID도 계속 받는다(이전 호환, `basis: CAPTURE`). 현재 Folder Schema와 호환되지 않는 Scene은 제외한다.

## API

| 메서드·경로 | 권한 | 내용 |
|---|---|---|
| `POST /api/dashboard/finalizations/preview` | 의뢰 `RESULT_IMPORT` | 본문 `project_id, request_id, environment, case_id, capture_id`. 응답: 서명 계획(`schema_version: 3`, `excluded_scenes`, `include_unchecked`, `counts.other_files`·`counts.total_bytes` 포함) + `plan_sha256`, `can_confirm`, `output_paths`, `report_files`, `report_paths`, `report_limits`, `disk{required_bytes, margin_bytes, free_bytes, sufficient}`(참고용) |
| `PUT /api/dashboard/finalizations/{operation_id}/reports/{pptx\|html}` | 의뢰 `RESULT_IMPORT` | 질의 `project_id, request_id, environment, case_id, capture_id`, 본문은 파일 바이트(raw). 응답 `file_name, size, sha256, report_path, status: STAGED`. 동시 업로드·검사는 프로세스당 2건, 넘으면 기다리지 않고 429 `FINALIZATION_REPORT_BUSY` |
| `POST /api/dashboard/finalizations/confirm` | 의뢰 `RESULT_IMPORT` | 본문 미리보기 필드 + `operation_id`, `report_formats: ["pptx"\|"html"]`(하나 이상). 응답(W2): 작업 보기 `{operation_id, state, phase, files_done, files_total, bytes_done, bytes_total, current_file, error, attempt, queued_at, started_at, updated_at, case_id, capture_id, reports[], output_paths, active, record}`. 새 작업은 `state: QUEUED/RUNNING`, 이미 완료된 확정은 `state: COMPLETE`와 `record`(서명된 완료 기록). 실행 중인 확정에 다시 보내면 현재 진행을 돌려준다 |
| `GET /api/dashboard/finalizations/{operation_id}/job` | `PROJECT_DATA_VIEW` | 질의 `project_id, request_id, environment, case_id`. 같은 작업 보기. `job.json`·`progress.json`(작은 파일)만 읽고, 완료되면 `record`. 없거나 범위가 다르면 404 `FINALIZATION_JOB_NOT_FOUND`. 중단된 작업은 이어서 등록 |
| `POST /api/dashboard/finalizations/summary/repair` | 의뢰 `RESULT_IMPORT` | 본문 `project_id, request_id, environment, case_id`. 현재 Final로 `Final/current.json`·포인터를 다시 쓰고 상태 조회 응답을 돌려준다. 완료된 Final이 없으면 409 `FINALIZATION_NO_CURRENT` (W3) |
| `GET /api/dashboard/finalizations/status` | `PROJECT_DATA_VIEW` | 의뢰 최근·선택 Case 최근 완료(`verification: SHA256\|SIZE`), 재시도 가능한 계획(각각 `job`), 선택 Case의 복사 작업 `active_operations`(QUEUED·RUNNING·FAILED, 최신순), 확인 필요 기록 수 |

보고서는 multipart 대신 확정 ID에 묶인 별도 raw 업로드 단계로 받는다. 새 실행 의존성(python-multipart)을 피하고 요청마다 파일 하나로 크기 상한을 적용하기 위해서다. 본문을 받기 전에 권한, 그다음 확정 계획(서명된 `plan.json`이 이 범위의 것인지, `complete.json`이 없는지)을 먼저 확인한다. 없는 확정 ID는 404, 범위 불일치 403, 서명 불일치 409, 완료된 확정은 409다.

저장소 쓰기·권한 오류(`OSError`)는 503 `FINALIZATION_WRITE_FAILED`로 알리고 임시 파일은 지운다. 같은 요청으로 다시 시도할 수 있다. 확정의 남은 공간 부족은 507 `FINALIZATION_DISK_SPACE`, 남은 공간을 확인할 수 없으면 503 `FINALIZATION_DISK_UNAVAILABLE`, 복사 중 보고서 업로드·다른 형식 확정은 409 `FINALIZATION_JOB_ACTIVE`. 작업 중 오류(원본 변경 `FINALIZATION_SOURCE_STALE`, 원본 사용 중 `FINALIZATION_SOURCE_BUSY`, 대상 충돌 `FINALIZATION_DESTINATION_CONFLICT`, 이름 바꾸기 잠금 `FINALIZATION_PUBLISH_BUSY`, 공간 부족 등)는 HTTP 응답이 아니라 작업 보기의 `state: FAILED`·`error`로 알린다.

## 보고서 검사와 상한

| 형식 | 상한 | 검사 |
|---|---|---|
| PPTX | 64 MiB | zip 시작 바이트. `zipfile`이 목록을 읽기 전에 EOCD·ZIP64 EOCD를 직접 읽어 항목 10,000개·중앙 목록 4 MiB 초과를 거부(`FINALIZATION_REPORT_PPTX_TOO_MANY_ENTRIES`). `[Content_Types].xml`·`ppt/presentation.xml` 존재. `[Content_Types].xml`과 모든 `.rels`(각 1 MiB, 합계 16 MiB)는 BOM·XML 선언으로 UTF-8/UTF-16을 판별해 표준 라이브러리 파서로 읽고 DTD·ENTITY와 다른 인코딩은 거부(새 의존성 없음, defusedxml 미사용). 매크로 거부(`FINALIZATION_REPORT_PPTX_MACRO`: `vbaProject`·`vbaData` 항목, `macroEnabled`·`vbaProject` 형식, vbaProject 관계). 앱이 만들지 않는 실행 가능 내용 거부(`FINALIZATION_REPORT_PPTX_ACTIVE_CONTENT`): `ppt/activeX/`, `ppt/embeddings/`의 `.xlsx` 외 항목, ActiveX·OLE 형식, `oleObject`·`control`·`activeXControl*`·`attachedTemplate` 등 관계, `TargetMode="External"` 관계 전부, `ppt/embeddings/*.xlsx`가 아닌 `package` 관계. pptxgenjs 차트의 내장 통합문서(`ppt/embeddings/*.xlsx`, 16 MiB)는 허용하되 그 안의 매크로·ActiveX·내장 항목을 다시 거부. 절대 경로·`..`·`\`·`:`·중복 항목·암호화 항목 거부, 압축 해제 합계 512 MiB와 1 MiB 넘는 항목의 압축률 200배 상한 |
| HTML | 320 MiB | UTF-8 엄격 디코딩, BOM·공백 뒤 `<!doctype html`(대소문자 무관)로 시작, NUL 거부. 저장만 하고 앱이 페이지로 제공하지 않는다 |

- 빈 파일은 `FINALIZATION_REPORT_EMPTY`, 상한 초과는 413 `FINALIZATION_REPORT_TOO_LARGE`(선언 길이·실제 수신 모두 검사).
- 요청 크기: 앱(uvicorn)에는 본문 상한이 없고 라우트가 형식별 상한으로 끊는다. 인트라넷 Caddy 템플릿(`deploy/windows/Caddyfile.intranet.template`)의 `request_body max_size 512MB`는 요청 하나당이라 HTML 320 MiB도 통과한다. 이 값을 줄이면 HTML 영상 포함 보고서 업로드가 실패한다.
- **CAE 복사 상한 없음(W2):** 파일 수·파일 크기·전체 크기 상한(이전 32 MiB·256 MiB·500개)을 없앴다. 대신 시작 전 남은 공간을 확인한다. 안전 상한으로 Scene 폴더 탐색 항목 500,000개·깊이 32, include 덱 5,000개, `plan.json`·`complete.json` 각 64 MiB(파일 약 20만 개 수준), `copied.jsonl` 1 GiB를 둔다(`FINALIZATION_SCAN_LIMIT`·`FINALIZATION_DEPTH_LIMIT`·`FINALIZATION_INCLUDE_LIMIT`·`FINALIZATION_METADATA_LIMIT`).
- 보고서 상한·검사(PPTX 64 MiB, HTML 320 MiB)는 그대로다.

## 멱등·재시도·불변

- 보고서 업로드는 `.finalizations/<ID>/reports/`에 보관하고 서명된 `reports.json`에 형식별 크기·SHA-256과 업로드 이력 해시를 기록한다. 완료 전에는 같은 형식을 다시 올려 교체할 수 있다.
- 확정 순서: 보고서 필수 검사(`FINALIZATION_REPORT_REQUIRED`, 아무것도 복사하지 않음) → 계획·원본 재검증 → 고른 형식의 보관본 해시 확인(`FINALIZATION_REPORT_NOT_STAGED`/`_STAGE_INVALID`) → 대상 폴더 충돌·남은 공간 확인 → 작업 등록(응답) → (작업) CAE·보고서를 임시 폴더로 복사 → 임시 파일 해시 검증 → CAE·Report 폴더 이름 바꾸기 → 공개 확인 → `complete.json`. 보고서가 실패하면 완료 기록이 생기지 않는다(CAE만 공개된 상태는 완료가 아니다).
- 같은 확정 ID 재시도는 복사 완료 기록과 해시가 맞는 임시 파일을 건너뛰고, 이미 공개한 폴더는 그대로 둔다. Report 폴더가 아직 공개되지 않았으면 보고서를 다시 올려 바꾸거나 형식을 바꿀 수 있다(임시 폴더의 이전 보고서는 이 확정이 만든 것이므로 교체). 공개되지 않은 대상 자리에 이미 폴더가 있으면(이 확정이 만들었음을 증명할 수 없으면) 덮어쓰지 않고 409 `FINALIZATION_DESTINATION_CONFLICT`로 멈춘다(확정 때와 이름 바꾸기 때 모두).
- Report 폴더가 공개된 뒤에는 보고서가 고정된다. 재시도의 `report_formats`·보고서 바이트가 공개한 것과 다르면 409 `FINALIZATION_REPORT_FORMATS_MISMATCH`. `complete.json`을 쓰기 직전에 Report 폴더 내용이 기록할 보고서와 정확히 같은지 다시 확인하고, 다른 항목이 생겼으면 409 `FINALIZATION_REPORTS_UNEXPECTED_FILE`(작업 실패)로 멈춘다. 자기 것임을 증명할 수 없는 파일은 지우지 않는다.
- 완료 후에는 `.finalizations/<ID>/reports/`의 보관본 중 서명된 이력 해시와 같은 파일만 지운다(`reports.json`은 남김, 실패해도 완료에는 영향 없음). 확정하지 않고 버린 미리보기의 보관본은 그대로 남는다(자동 정리 없음, 관리자가 `.finalizations/<ID>/`를 확인해 정리).
- 완료 후에는 모두 불변이다. 같은 확정 요청은 서명된 완료 기록을 그대로 돌려주고, 보고서 업로드는 409 `FINALIZATION_ALREADY_COMPLETED`다.
- 확정 전·복사 중 실패한 계획은 상태 조회에 재시도 가능으로 남는다(`job.state`). 화면은 창이나 헤더에서 **재시도**하거나 새로 지정한다.

## 이력과 서명

- 계획(`plan_signature`)·보고서 기록(`reports_signature`)·완료(`complete_signature`)는 `AUTH_SECRET_KEY`로 용도를 분리한 HMAC으로 서명한다. 키가 없으면 새 확정을 막고, 키 교체·변조·파일 손상으로 검증할 수 없는 기록은 파일을 보존한 채 확인 필요로 센다. 서명 없는 기록을 신뢰하지 않는다.
- 계획 `schema_version` 3이 현재 형식이다(W2: 모든 Scene 파일, 비수집 파일은 크기·수정 시각 고정, 완료 기록에 파일별 해시). 2(2026-10-03~W2 이전: 형식 필터·상한, 모든 해시가 계획에 있음)와 1(2026-10-03 이전: 결과·Scene 문서를 `Final/Report/<Case>/<ID>/` 미러에 둔 형식)의 완료 기록도 상태 조회에서 그대로 검증·표시하며 그 시절 상한(500개·파일 32 MiB·전체 256 MiB)으로 판정한다. 완료되지 않은 1·2 형식 계획은 확정할 수 없고 재시도 목록에 나오지 않는다(새로 지정). 기존 Final 폴더는 옮기거나 지우지 않는다(D14).
- `job.json`(도메인 `job`), `progress.json`(`progress`), `copied.jsonl` 줄(`copied`)도 같은 키로 용도를 분리해 서명한다. 완료 근거는 `complete.json`뿐이다.
- 이력 JSON은 4 MiB(보고서·작업·진행 기록), 계획·완료 기록은 64 MiB까지 읽는다. 상태 조회는 메타데이터 누계 256 MiB·항목 1,000개를 넘으면 명시적 오류를 낸다.
- 상태 조회의 출력 검증(2026-10-03 보강): 모든 완료 기록은 서명·범위·출력 파일의 존재와 기록된 크기를 확인한다. SHA-256 재계산은 응답에 나오는 기록(의뢰 최근, 선택 Case 최근)에만 하고, 실패하면 확인 필요로 세고 그다음 최근 기록을 같은 방식으로 확인한다. 해시 검증 누계 상한은 1 GiB(보고서 포함)다. W2(검수 반영): 계획 3 형식 기록은 완료 때 공개된 파일을 전부 해시 확인하고 `verified.json`(서명, 출력 파일별 크기·수정 시각·식별자 요약과 `complete.json` 해시)을 남긴다. 상태 조회는 예산 안에 드는 표시 기록을 **항상 다시 해시**한다(`verification: "SHA256"`, 재검수 N1). 남은 예산보다 큰 기록(수 GB CAE)만 다시 읽지 않는다: `verified.json`과 현재 파일의 크기·수정 시각·식별자가 같으면 `verification: "STAT_SINCE_COMPLETION"`(헤더 표시 **완료 후 변경 없음(크기·시각 확인)**), 기록이 없거나(1·2 형식) 다르면 존재·크기만 확인해 `verification: "SIZE"`(헤더 **크기만 확인**). 두 경우 모두 **내용이 바뀌지 않았다는 증명이 아니다**: 같은 크기로 고치고 수정 시각을 되돌리면 STAT_SINCE_COMPLETION으로도 드러나지 않는다. 내용 변조는 해시 예산 안에서 다시 해시될 때 확인 필요로 센다. 장단점: 큰 HTML 보고서가 쌓여도 상태 조회가 영구 실패하지 않고, 해시 예산 안의 표시 기록은 손상되면 정상으로 보이지 않는다. 대신 표시되지 않는 과거 기록의 같은 크기 내용 변조는 그 기록이 표시될 때까지 드러나지 않는다(크기 변경·누락은 바로 확인 필요로 센다).
- 새 DB migration·실행 의존성은 없다. Final은 일반 Case 조회·결과 등록·수집 대상이 아니다.

## 검증

- W3 `backend/tests/test_case_finalization_current.py`: A 지정 → 요약 파일이 A, B 지정 → B·이전 A·A 파일 그대로·이력 현재/이전, 같은 Case 재지정, 먼저 시작했어도 나중에 커밋된 완료가 현재, 오래된 완료가 새 포인터를 덮지 않음·완료 시각 단조 증가, 요약 파일 쓰기 실패 → Final 완료·MISSING → 갱신으로 OK, W3 이전 의뢰(요약·포인터 없음)는 가장 늦은 완료를 현재로 보이고 GET이 아무것도 쓰지 않음·다음 완료가 이전 Final을 찾음, STALE 감지, 요약 파일은 `Final/current.json`(과 그 임시 파일)에만 쓸 수 있음. e2e `case-finalization.spec.ts`: 다른 Case가 현재 Final일 때 헤더 표시·확인란·이력·요약 파일 갱신.

- W2(2026-10-06) `backend/tests/test_case_finalization_copy_jobs.py`: 형식 필터 없이 모든 Scene 파일 복사·임시/잠금/숨김 제외, 320 MiB(희소) 파일을 고정 메모리로 복사(tracemalloc 최대치가 작은 Case와 24 MiB 이내), 남은 공간 부족 507(작업 미등록), 공개 전 대상 폴더 비노출·진행률 형태·복사 중 업로드 409·재확정은 진행 반환, 복사 중 강제 중단(BaseException) 뒤 서버 시작 검색으로 같은 Final ID 이어하기(복사 완료 파일 건너뜀, 반쯤 쓴 파일 삭제·재복사, 해시 동일, Working 불변), 상태 조회로 이어하기, Report 이름 바꾸기 잠금 실패 → 완료 없음·CAE만 공개 → 재시도 시 재복사 없이 완료, 이름 바꾸기와 진행 기록 사이 중단 → 해시 검증 후 받아들임, 남의 대상 폴더 비덮어쓰기(확정 시·이름 바꾸기 시), 복사 중 원본 변경·같은 크기 결과 변경 → `FINALIZATION_SOURCE_STALE`, 손상된 임시 파일 재복사, 큰 기록 `verification: SIZE`, 2 형식 완료 기록 검증, 저장소 기본 기능(`copy_stream`·서버 측 복사 자리·`rename_no_replace`·`try_lock`). 독립 검수 공격 시나리오(같은 파일, `test_review_*` 12건): 임시 폴더 상위를 바로가기로 바꿔치기해도 root 밖에 쓰거나 지우지 않음, 폴더 생성 전 안전 확인, 임시 폴더·채택 대상의 바로가기와 계획에 없는 폴더 거부, 공개 전 검증 뒤 변조를 완료 전에 발견, `verified.json`은 예산 초과 때만 쓰고 `STAT_SINCE_COMPLETION`으로 표시·변경 시 SIZE/확인 필요, 재검수(`test_rereview_*`): 같은 크기 수정+수정 시각 복원도 예산 안에서는 확인 필요로 셈(검수자 테스트 이식), 링크된 `copied.jsonl`에 덧붙이지 않음, 시작 시 이어하기도 DB 범위 확인, 확인할 수 없는 `complete.json` 비완료, 무효 작업 FAILED 기록, `copied.jsonl` 상한 초과 명시적 실패, 범위가 사라진 작업 재등록 안 함. 기존 테스트는 작업 완료를 기다리는 방식으로 갱신했다. Windows 전용 고정 테스트(`test_case_finalization_pins_output_parent_during_temp_write`)는 새 복사 경로로 바꿨으나 Linux에서는 건너뛴다.
- e2e(W2): `case-finalization.spec.ts`에 진행률·창 닫기 후 헤더 `Final 복사 중 n%`·실패 사유·헤더 재시도, `folder-working-final.spec.ts`에 다시 연 화면의 진행(`Final 검증 중 77%`)과 완료 알림.

- 백엔드 `backend/tests/test_case_finalization_reports.py`: CAE 미러(결과·이미지·영상·Scene 문서·덱), Report에는 서버 이름의 업로드 보고서만, 최신 결과 기준의 Scene별 수집본, 새 수집본 뒤 확정 차단, 보고서 누락·미업로드 시 미완료, PPTX(비 zip·매크로 항목·macroEnabled·경로 이탈·압축률·압축 해제 합계·필수 항목 누락)·HTML(doctype 없음·비 UTF-8·빈 파일)·크기 초과 거부, 완료 전 보고서 교체·완료 후 불변·남의 파일 비덮어쓰기, 1 형식 완료 기록의 상태 검증, Final 밖 쓰기 없음·Working 불변. 기존 `test_environment_folder_flow_api.py`의 서명·변조·재시도·상태 제한 시나리오는 새 계약으로 갱신했다.
- 보강(2026-10-03 검수 반영) 같은 파일: 부분 게시 뒤 형식을 줄인 재시도 거부와 완료 후 보관본 정리, Report 폴더의 남의 항목 차단·보존, 실제 pptxgenjs 4.0.1 파일(텍스트·도형·표·이미지·차트 내장 xlsx, 고정 fixture `backend/tests/fixtures/case_finalization/`와 node가 있으면 테스트 중 생성) 통과, UTF-16 형식 목록·DTD·ActiveX·OLE·외부 관계·attachedTemplate·control·vbaProject 관계·xlsx 아닌 package·매크로 든 내장 xlsx·`:` 항목 거부, EOCD·ZIP64 EOCD 항목·목록 크기 상한(`zipfile` 생성 전), 본문 전 확정 확인(없는 ID·다른 기준·변조 계획·완료됨)과 429, 쓰기 권한 오류 503과 재시도, 상태 조회의 표시 기록만 해시 검증·예산, 최신 수집본에 위치 목록이 없을 때 이전 수집본으로 대체하지 않음(`excluded_scenes`), 사용환경 최신 기준 = 화면 `get_latest_capture`.
- e2e `frontend/e2e/case-finalization.spec.ts`(제외 Scene 표시, 사용환경 보고서가 `latest:<Case>`로만 읽음, 화면 선택 없이 Run Option 3개(결과 없음 1개)를 담은 유통 Final 보고서, 사용환경 Final 보고서 업로드·확정, 형식 선택·형식별 업로드 본문·확정 본문·완료 경로·건너뛴 영상, 업로드 오류와 같은 바이트 재시도), `folder-working-final.spec.ts`(미리보기 기준·CAE 목록·화면 선택 없이 Case 전체 보고서로 확정 가능·1 형식 이력 배지).
