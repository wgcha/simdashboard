# Working Case 최종확정 (Final 지정)

상태: 6단계 구현·검수 보강(2026-10-03). 격리된 임시 SPDM root·합성 데이터로 검증했다. 실제 Windows Server 2022·공유폴더 검증은 하지 않았다. 계획 근거는 [Case 결과 흐름 재설계](../plans/case-results-workflow-redesign.md) §3.6, 경로 계약은 [DEPTH_V1 스키마](../contracts/depth-schema.md) D11·D12·D14다. PDF 관련 구현은 없다(2026-10-02 23:58 결정).

## 흐름

1. Case 결과 헤더의 **Final 지정** → 서버가 미리보기 계획을 만든다(`POST /preview`).
2. 창에 Case, 기준(최신 결과 · Scene별 결과 버전), Final/CAE 파일 수·목록, 만들 보고서(Final/Report 경로)를 보여준다.
3. 사용자가 PPTX·HTML 중 하나 이상을 고른다. HTML은 `영상 포함`(기본 꺼짐)을 고를 수 있다.
4. **Final 지정 확정** → 브라우저가 5단계 빌더(`buildCaseReportPptx`·`buildCaseReportHtml`)로 보고서를 만들고, 형식마다 한 번씩 올린 뒤(`PUT /{operation_id}/reports/{format}`) 확정한다(`POST /confirm`).
5. 완료 화면에 CAE 폴더와 보고서 경로, HTML에 넣지 못한 영상을 보여준다. 실패하면 창에 오류를 보이고 **다시 시도**는 같은 확정 ID와 같은 보고서 바이트로 다시 보낸다.

보고서 범위는 화면 선택과 무관하게 **Case 전체**다(2026-10-03 후속).

- 유통환경: Case의 모든 하중경우 › Run Case › Run Option을 경로 바와 같은 후보 규칙(최신 결과 `latest:<Case>`에 속하거나 아직 수집되지 않은 항목)으로 모아 Run Option마다 구역 하나를 만든다. 각 구역은 그 Run Option의 Scene으로 요약·Scene 비교·이미지·영상을 담는다. Component는 화면에서 고른 이름과 같은 것, 없으면 값이 있는 첫 후보, Basis는 화면 값이 후보에 있으면 그 값, 아니면 첫 후보다. 표시 엣지·라인은 화면 설정을 따른다. 결과가 없는 Run Option은 `결과 없음` 구역으로 표시하고 확정을 막지 않는다. 결과 읽기 오류는 보고서 만들기를 실패시킨다(다시 시도).
- 사용환경: 사용환경 Case의 다섯 평가 종합과 이미지·영상([Case 결과 보고서](case-report.md)의 사용환경 레시피, Reference 없음).
- 화면에서 Run Case·Run Option을 고르지 않아도 확정할 수 있다. 결과(수집본)가 없는 Case만 보고서를 만들 수 없어 확정이 막힌다.
- 헤더의 `보고서` 버튼은 지금처럼 현재 선택 범위로 만든다.
- 보고서를 만들 때 이미지는 한 번 읽어 두 형식이 공유하고, 넣지 못한 이미지·영상은 창에 표시한다.

## 저장 위치 (DEPTH_V1 D11·D12)

| 위치 | 내용 |
|---|---|
| `Final/CAE/<Case>/<확정 ID>/<Working Case 기준 상대 경로>` | 기준 Scene의 모든 입력·결과: `.rad`·`.inc`와 include 참조, 결과 CSV/JSON, 이미지, 영상, Scene 폴더의 PDF/PPT/PPTX/XLSX 문서 |
| `Final/Report/<Case>/<확정 ID>/` | 앱이 만든 보고서만, 폴더 바로 아래: `<Case>_report.pptx`, `<Case>_report.html` |
| `Final/.finalizations/<확정 ID>/` | 서명된 `plan.json`, 보고서 임시 보관 `reports/`와 `reports.json`, `complete.json`, 의뢰 잠금 `.request.lock` |

- 확정 ID는 32자리 소문자 hex(`uuid4().hex`, `_OPERATION_ID`)로 DEPTH_V1 `FINAL_VERSION` 형식과 같다.
- 보고서 파일 이름은 서버가 Case 이름으로 정한다(Windows 금지·제어 문자 `_`, 공백 `_`, 80자, 예약 이름 앞 `_`). 클라이언트가 보낸 이름은 쓰지 않는다.
- CAD는 복사하지 않는다. 앱의 SPDM 쓰기는 의뢰의 `Final/` 아래뿐이다. Working과 기존 Final 파일은 옮기거나 덮어쓰거나 지우지 않는다(D14).

## 기준: 최신 결과

- `capture_id`로 화면과 같은 `latest:<dashboard_case_id>`를 받는다. Scene별 기준 수집본은 화면이 쓰는 `dashboard_capture.merge_latest_payload`를 같은 수집본 순서(`created_at, id`, `get_latest_capture`와 동일)로 직접 호출해 정한다(규칙의 단일 원천, 2026-10-03 보강). 병합된 Scene은 결과·미디어 경로(없으면 같은 수집본 안의 Scene 폴더 이름)로 현재 확정 Scene 경로에 대응시킨다. 사용환경은 가장 최근 수집본 하나다(화면·Final 보고서·Final 기준 모두 `latest:<Case>`).
- 화면이 보여 주는 최신 수집본이 현재 Folder Schema와 맞지 않으면(위치 목록 없음 `CAPTURE_SCHEMA_MISSING`, 대상·계층 불일치 `CAPTURE_SCHEMA_INCOMPATIBLE`) 그 Scene은 **제외**하고 더 오래된 수집본으로 대신하지 않는다. 수집본이 없는 현재 확정 Scene은 `NO_CAPTURE`로 제외한다. 제외 목록은 계획·미리보기 응답 `excluded_scenes`(`scene_path`, `source_capture_id`, `reason`)에 있고 Final 지정 창에 표시한다. 정보용이라 확정 시 비교하지 않는다(Scene 기준은 `scene_sources`로 비교).
- 계획에 Scene별 `source_capture_id`·`source_capture_fingerprint`(`scene_sources`)와 최신 결과 지문(수집본 ID 목록 해시)을 서명해 기록한다.
- 결과 파일은 해당 수집본의 해시·크기와 현재 파일이 같아야 한다(`FINALIZATION_SOURCE_STALE`). 입력 덱·Scene 문서는 미리보기 시점 해시를 고정한다. 미리보기 뒤 새 수집본이 생기면 `FINALIZATION_CAPTURE_CHANGED`로 막고 새 미리보기를 요구한다.
- 구체적인 수집본 ID도 계속 받는다(이전 호환, `basis: CAPTURE`). 현재 Folder Schema와 호환되지 않는 Scene은 제외한다.

## API

| 메서드·경로 | 권한 | 내용 |
|---|---|---|
| `POST /api/dashboard/finalizations/preview` | 의뢰 `RESULT_IMPORT` | 본문 `project_id, request_id, environment, case_id, capture_id`. 응답: 서명 계획(`excluded_scenes` 포함) + `plan_sha256`, `can_confirm`, `output_paths`, `report_files`, `report_paths`, `report_limits` |
| `PUT /api/dashboard/finalizations/{operation_id}/reports/{pptx\|html}` | 의뢰 `RESULT_IMPORT` | 질의 `project_id, request_id, environment, case_id, capture_id`, 본문은 파일 바이트(raw). 응답 `file_name, size, sha256, report_path, status: STAGED`. 동시 업로드·검사는 프로세스당 2건, 넘으면 기다리지 않고 429 `FINALIZATION_REPORT_BUSY` |
| `POST /api/dashboard/finalizations/confirm` | 의뢰 `RESULT_IMPORT` | 본문 미리보기 필드 + `operation_id`, `report_formats: ["pptx"\|"html"]`(하나 이상). 응답: 완료 기록(`reports[]`에 형식·이름·크기·SHA-256·경로) |
| `GET /api/dashboard/finalizations/status` | `PROJECT_DATA_VIEW` | 의뢰 최근·선택 Case 최근 완료, 재시도 가능한 계획, 확인 필요 기록 수 |

보고서는 multipart 대신 확정 ID에 묶인 별도 raw 업로드 단계로 받는다. 새 실행 의존성(python-multipart)을 피하고 요청마다 파일 하나로 크기 상한을 적용하기 위해서다. 본문을 받기 전에 권한, 그다음 확정 계획(서명된 `plan.json`이 이 범위의 것인지, `complete.json`이 없는지)을 먼저 확인한다. 없는 확정 ID는 404, 범위 불일치 403, 서명 불일치 409, 완료된 확정은 409다.

저장소 쓰기·권한 오류(`OSError`)는 503 `FINALIZATION_WRITE_FAILED`로 알리고 임시 파일은 지운다. 같은 요청으로 다시 시도할 수 있다.

## 보고서 검사와 상한

| 형식 | 상한 | 검사 |
|---|---|---|
| PPTX | 64 MiB | zip 시작 바이트. `zipfile`이 목록을 읽기 전에 EOCD·ZIP64 EOCD를 직접 읽어 항목 10,000개·중앙 목록 4 MiB 초과를 거부(`FINALIZATION_REPORT_PPTX_TOO_MANY_ENTRIES`). `[Content_Types].xml`·`ppt/presentation.xml` 존재. `[Content_Types].xml`과 모든 `.rels`(각 1 MiB, 합계 16 MiB)는 BOM·XML 선언으로 UTF-8/UTF-16을 판별해 표준 라이브러리 파서로 읽고 DTD·ENTITY와 다른 인코딩은 거부(새 의존성 없음, defusedxml 미사용). 매크로 거부(`FINALIZATION_REPORT_PPTX_MACRO`: `vbaProject`·`vbaData` 항목, `macroEnabled`·`vbaProject` 형식, vbaProject 관계). 앱이 만들지 않는 실행 가능 내용 거부(`FINALIZATION_REPORT_PPTX_ACTIVE_CONTENT`): `ppt/activeX/`, `ppt/embeddings/`의 `.xlsx` 외 항목, ActiveX·OLE 형식, `oleObject`·`control`·`activeXControl*`·`attachedTemplate` 등 관계, `TargetMode="External"` 관계 전부, `ppt/embeddings/*.xlsx`가 아닌 `package` 관계. pptxgenjs 차트의 내장 통합문서(`ppt/embeddings/*.xlsx`, 16 MiB)는 허용하되 그 안의 매크로·ActiveX·내장 항목을 다시 거부. 절대 경로·`..`·`\`·`:`·중복 항목·암호화 항목 거부, 압축 해제 합계 512 MiB와 1 MiB 넘는 항목의 압축률 200배 상한 |
| HTML | 320 MiB | UTF-8 엄격 디코딩, BOM·공백 뒤 `<!doctype html`(대소문자 무관)로 시작, NUL 거부. 저장만 하고 앱이 페이지로 제공하지 않는다 |

- 빈 파일은 `FINALIZATION_REPORT_EMPTY`, 상한 초과는 413 `FINALIZATION_REPORT_TOO_LARGE`(선언 길이·실제 수신 모두 검사).
- 요청 크기: 앱(uvicorn)에는 본문 상한이 없고 라우트가 형식별 상한으로 끊는다. 인트라넷 Caddy 템플릿(`deploy/windows/Caddyfile.intranet.template`)의 `request_body max_size 512MB`는 요청 하나당이라 HTML 320 MiB도 통과한다. 이 값을 줄이면 HTML 영상 포함 보고서 업로드가 실패한다.
- CAE 복사 상한은 기존과 같다: 파일당 32 MiB, 전체 256 MiB·500개.

## 멱등·재시도·불변

- 보고서 업로드는 `.finalizations/<ID>/reports/`에 보관하고 서명된 `reports.json`에 형식별 크기·SHA-256과 업로드 이력 해시를 기록한다. 완료 전에는 같은 형식을 다시 올려 교체할 수 있다.
- 확정 순서: 보고서 필수 검사(`FINALIZATION_REPORT_REQUIRED`, 아무것도 복사하지 않음) → 계획·원본 재검증 → 고른 형식의 보관본 해시 확인(`FINALIZATION_REPORT_NOT_STAGED`/`_STAGE_INVALID`) → CAE 복사 → Report 게시 → 전체 해시 검증 → `complete.json`. 보고서가 실패하면 완료 기록이 생기지 않는다(CAE만 복사된 상태는 완료가 아니다).
- 같은 확정 ID 재시도는 이미 같은 해시로 있는 파일을 건너뛴다. Report에 같은 확정의 이전 업로드(이력 해시와 일치)가 남아 있으면 새 보고서로 바꾼다. 그 밖의 기존 파일은 덮어쓰지 않고 `FINALIZATION_DESTINATION_CONFLICT`로 멈춘다.
- 이전 시도가 이미 게시한 형식은 재시도의 `report_formats`에 포함해야 한다. 빠뜨리면 아무것도 복사하기 전에 409 `FINALIZATION_REPORT_FORMATS_MISMATCH`로 막는다(기록되지 않은 보고서가 남는 것을 방지). 이 확정의 Report 폴더에 이 확정이 만들지 않은 항목이 있으면 409 `FINALIZATION_REPORTS_UNEXPECTED_FILE`로 막고, `complete.json`을 쓰기 직전에도 폴더 내용이 기록할 보고서와 정확히 같은지 다시 확인한다. 자기 것임을 증명할 수 없는 파일은 지우지 않는다.
- 완료 후에는 `.finalizations/<ID>/reports/`의 보관본 중 서명된 이력 해시와 같은 파일만 지운다(`reports.json`은 남김, 실패해도 완료에는 영향 없음). 확정하지 않고 버린 미리보기의 보관본은 그대로 남는다(자동 정리 없음, 관리자가 `.finalizations/<ID>/`를 확인해 정리).
- 완료 후에는 모두 불변이다. 같은 확정 요청은 서명된 완료 기록을 그대로 돌려주고, 보고서 업로드는 409 `FINALIZATION_ALREADY_COMPLETED`다.
- 확정 전에 실패한 계획은 상태 조회에 재시도 가능으로 남는다. 화면은 같은 창에서 **다시 시도**하거나 새로 지정한다.

## 이력과 서명

- 계획(`plan_signature`)·보고서 기록(`reports_signature`)·완료(`complete_signature`)는 `AUTH_SECRET_KEY`로 용도를 분리한 HMAC으로 서명한다. 키가 없으면 새 확정을 막고, 키 교체·변조·파일 손상으로 검증할 수 없는 기록은 파일을 보존한 채 확인 필요로 센다. 서명 없는 기록을 신뢰하지 않는다.
- 계획 `schema_version` 2가 현재 형식이다. 1(2026-10-03 이전: 결과·Scene 문서를 `Final/Report/<Case>/<ID>/` 미러에 둔 형식)의 완료 기록도 상태 조회에서 그대로 검증·표시한다. 완료되지 않은 1 형식 계획은 확정할 수 없고 재시도 목록에 나오지 않는다(새로 지정).
- 이력 JSON은 4 MiB까지 읽는다. 상태 조회는 메타데이터 64 MiB·항목 1,000개를 넘으면 명시적 오류를 낸다.
- 상태 조회의 출력 검증(2026-10-03 보강): 모든 완료 기록은 서명·범위·출력 파일의 존재와 기록된 크기를 확인한다. SHA-256 재계산은 응답에 나오는 기록(의뢰 최근, 선택 Case 최근)에만 하고, 실패하면 확인 필요로 세고 그다음 최근 기록을 같은 방식으로 확인한다. 해시 검증 누계 상한은 1 GiB(보고서 포함)다. 장단점: 큰 HTML 보고서가 쌓여도 상태 조회가 영구 실패하지 않고, 표시되는 기록은 손상되면 정상으로 보이지 않는다. 대신 표시되지 않는 과거 기록의 같은 크기 내용 변조는 그 기록이 표시될 때까지 드러나지 않는다(크기 변경·누락은 바로 확인 필요로 센다).
- 새 DB migration·실행 의존성은 없다. Final은 일반 Case 조회·결과 등록·수집 대상이 아니다.

## 검증

- 백엔드 `backend/tests/test_case_finalization_reports.py`: CAE 미러(결과·이미지·영상·Scene 문서·덱), Report에는 서버 이름의 업로드 보고서만, 최신 결과 기준의 Scene별 수집본, 새 수집본 뒤 확정 차단, 보고서 누락·미업로드 시 미완료, PPTX(비 zip·매크로 항목·macroEnabled·경로 이탈·압축률·압축 해제 합계·필수 항목 누락)·HTML(doctype 없음·비 UTF-8·빈 파일)·크기 초과 거부, 완료 전 보고서 교체·완료 후 불변·남의 파일 비덮어쓰기, 1 형식 완료 기록의 상태 검증, Final 밖 쓰기 없음·Working 불변. 기존 `test_environment_folder_flow_api.py`의 서명·변조·재시도·상태 제한 시나리오는 새 계약으로 갱신했다.
- 보강(2026-10-03 검수 반영) 같은 파일: 부분 게시 뒤 형식을 줄인 재시도 거부와 완료 후 보관본 정리, Report 폴더의 남의 항목 차단·보존, 실제 pptxgenjs 4.0.1 파일(텍스트·도형·표·이미지·차트 내장 xlsx, 고정 fixture `backend/tests/fixtures/case_finalization/`와 node가 있으면 테스트 중 생성) 통과, UTF-16 형식 목록·DTD·ActiveX·OLE·외부 관계·attachedTemplate·control·vbaProject 관계·xlsx 아닌 package·매크로 든 내장 xlsx·`:` 항목 거부, EOCD·ZIP64 EOCD 항목·목록 크기 상한(`zipfile` 생성 전), 본문 전 확정 확인(없는 ID·다른 기준·변조 계획·완료됨)과 429, 쓰기 권한 오류 503과 재시도, 상태 조회의 표시 기록만 해시 검증·예산, 최신 수집본에 위치 목록이 없을 때 이전 수집본으로 대체하지 않음(`excluded_scenes`), 사용환경 최신 기준 = 화면 `get_latest_capture`.
- e2e `frontend/e2e/case-finalization.spec.ts`(제외 Scene 표시, 사용환경 보고서가 `latest:<Case>`로만 읽음, 화면 선택 없이 Run Option 3개(결과 없음 1개)를 담은 유통 Final 보고서, 사용환경 Final 보고서 업로드·확정, 형식 선택·형식별 업로드 본문·확정 본문·완료 경로·건너뛴 영상, 업로드 오류와 같은 바이트 재시도), `folder-working-final.spec.ts`(미리보기 기준·CAE 목록·화면 선택 없이 Case 전체 보고서로 확정 가능·1 형식 이력 배지).
