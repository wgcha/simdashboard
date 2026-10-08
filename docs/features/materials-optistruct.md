# 사용환경 소재·물성 (OptiStruct 입력)

- 상태: 구현·격리 검증 (2026-10-08, 브랜치 `claude/scx-drive`), 독립 검수 지적(H1·M1–M3·L1–L7) 반영 2026-10-09
- 요구 출처: GitHub #48(OptiStruct 파서, HyperMesh 내보내기 예시), 사용자 결정 2026-10-08 (a)(b)
- 관련: 유통환경 Radioss 덱은 [소재·물성 계획](../plans/materials-dashboard-implementation.md), 화면 흐름은 [Case 결과 흐름](../plans/case-results-workflow-redesign.md), 드라이브 읽기는 [SCX 드라이브](scx-drive.md) §8.3

## 1. 환경 유지 (결정 a)

- Case 결과의 **소재·물성 탭은 현재 결과 환경을 그대로 쓴다.** 사용환경에서 탭을 열면 사용환경 소재를, 유통환경이면 Radioss 덱을 보여 준다. 탭을 열거나 닫아도 `result_environment`는 바뀌지 않는다. 소재 탭 안에서 환경 전환 버튼을 누르면 탭은 그대로이고 그 환경의 경로·카탈로그로 바뀐다.
- 이전 동작(소재 탭을 열면 유통환경으로 강제 전환, 닫을 때 `result_environment=DISTRIBUTION` 기록)은 제거했다. 예전 `environment=` 주소는 값을 `result_environment`로 옮기고 지운다.
- API: `GET /api/materials/catalog|deck`의 `environment`는 `DISTRIBUTION`(기본) 또는 `USAGE`. `deck`에 `retry=true`(사용환경 분석 실패 후 다시 분석)가 추가됐다.

## 2. 덱 위치 규칙 (사용환경)

| 순서 | 위치 | 파일 |
|---|---|---|
| 1 | 선택한 Scene 폴더 **바로 아래** | 확장자 `.fem`(대소문자 무시) |
| 2 | 그 Scene의 Case 폴더 **바로 아래** (1에 없을 때) | 같음 |

- 하위 폴더는 보지 않는다(진척 계약 P3의 "Scene 바로 아래 `.fem`"과 같은 기준). 내용으로 고르지 않는다.
- 한 폴더에 여러 개면 이름순 첫 파일을 쓰고 `MATERIALS_MULTIPLE_SOLVER_INPUTS` 경고를 응답에 남긴다.
- 폴더 소유권·의뢰 경계 검사는 유통환경과 같다(다른 의뢰·환경에 연결된 폴더는 읽지 않는다). Scene 목록은 의뢰의 사용환경 깊이 스키마(Case → Scene)에서 온다.
- `INCLUDE`는 **포함한 파일의 폴더 기준 상대 경로**만 따른다(`\`는 `/`로 바꾼다). 의뢰 폴더 밖, 드라이브 문자·절대 경로, 없는 파일은 읽지 않고 파서 알림으로 남긴다. 없는 파일도 의존성(`missing`)으로 기록해 나중에 그 파일이 생기면 저장된 결과를 무효로 한다. 순환·깊이(5)·파일 수(500)·크기·시간 한도 초과는 분석 실패다.

## 3. 분석 방식 (결정 b)

- 입력은 500–1000 MB 한 파일이다. **요청은 분석하지 않는다.** `materials_deck_cache`(root_key, rel_path, solver)를 보고, 저장된 지문·분석기 버전·INCLUDE 파일 지문이 모두 같으면 그 결과를 돌려준다(`analysis.cached=true`).
- 다르면 백그라운드 작업을 하나 넣고 `analysis.status=QUEUED|RUNNING`, 진행 바이트를 돌려준다. 화면은 1.5초마다 다시 묻고 "분석 대기 중 / 분석 중 · n%"를 보여 준다. 작업은 프로세스당 1개씩 순서대로 돈다.
- 지문: 로컬은 `local:<크기>:<수정 시각 ns>`, 드라이브는 `drive:<version_token>`. 분석기 버전(`PARSER_VERSION`)이 바뀌어도 다시 분석한다.
- 실패(`FAILED`)도 같은 지문과 그때까지 읽은 INCLUDE 의존성으로 저장한다. 같은 파일을 다시 열면 원인을 보여 주고 **[다시 분석]**(`retry=true`)으로 다시 돈다. INCLUDE 파일이 바뀌어도 저장된 실패는 무효가 된다. 다시 분석은 **파일마다 1분에 한 번**(프로세스 기준)이며, 그 안의 재요청은 저장된 실패와 `analysis.retry_after_seconds`를 돌려준다.
- **일시 오류는 저장하지 않는다**: 드라이브·스테이징·잠긴 파일 등 `SpdmStorageError`(파일 고유 오류 `SPDM_FILE_TOO_LARGE`·`SPDM_PATH_INVALID` 제외)와 `OSError`는 메모리에만 1분 두고 `FAILED`+`analysis.transient=true`로 보여 준다. 1분 뒤 다음 조회가 다시 넣고, [다시 분석]으로 바로 다시 돌릴 수도 있다.
- 분석 중 파일이 바뀌면 저장하지 않고 다음 조회가 새 지문으로 다시 넣는다. 대기 중에 파일이 바뀐 작업은 읽기(드라이브는 내려받기) 전에 지문을 비교해 멈춘다.
- 저장은 `INSERT … ON CONFLICT (root_key, rel_path, solver) DO UPDATE` 한 문장(PostgreSQL·DuckDB 공통)이다. 결과 JSON은 `allow_nan=False`로 만들며, 그래도 직렬화할 수 없으면 `MATERIALS_PARSE_FAILED`로 저장한다.
- 폴링 부담: 사용환경 `deck` 조회는 (root, 의뢰, scene_id, relative_path)별로 찾은 입력 파일을 **20초** 기억해, 1.5초 폴링마다 카탈로그 전체(후보 폴더·소유권)를 다시 훑지 않는다. 권한 검사는 매 요청 그대로이고, 폴더 소유권·후보 파일 변경은 최대 20초 늦게 반영된다. `retry=true`와 파일이 사라진 경우는 항상 새로 찾는다.
- 한도: `SIMDASH_OPTISTRUCT_MAX_BYTES`(기본 2 GiB, 입력+INCLUDE 합계, 초과 시 413 `MATERIALS_FILE_SIZE_LIMIT`), `SIMDASH_OPTISTRUCT_MAX_SECONDS`(기본 1800초), 표 곡선 점 200만 개.
- 파서 자원 한도(모두 413, 분석 실패): 한 줄 64 KiB(`MATERIALS_LINE_TOO_LONG`, 파일은 `readline(64 KiB+1)`로만 읽어 긴 줄을 통째로 메모리에 올리지 않음), 보존 카드 하나 25만 줄·32 MiB(`MATERIALS_CARD_TOO_LARGE`), 보존 카드 전체 512 MiB(`MATERIALS_KEPT_SIZE_LIMIT`), HyperMesh 컴포넌트 50만 개(`MATERIALS_COMPONENT_LIMIT`), 표 사용 연결 1만 개(`MATERIALS_FUNCTION_USE_LIMIT`).
- 시간 한도는 65,536줄 또는 8 MiB마다, 조립 단계에서는 카드·연결 256개마다 확인한다. 드라이브 내려받기 자체에는 `MAX_SECONDS`를 넘기지 못한다(`drive_reads.content()`에 기한 인자가 없음) — 내려받기 직전·직후에 확인하고, 내려받기 중에는 드라이브 게이트웨이 자체 시간 제한(`DRIVE_TIMEOUT`)을 따른다.
- 작업은 읽는 동안 DB 연결을 쥐지 않는다. 끝나면 짧은 연결 하나로 행을 쓴다. 드라이브 모드는 작업 자신의 읽기 세션으로 한 번 내려받아 서버 보관소에 두고, 같은 버전 재분석은 보관소 sha256을 다시 쓴다(§8.3).
- 측정(합성 200 MB, 메시 400만 줄, 이 개발 VM): 약 100 MB/s(2.0–2.2초). 1 GB 입력은 약 10초로 예상한다. 실제 서버·실제 덱에서는 측정하지 않았다.

## 4. 파서 (`backend/app/parsers/optistruct_deck_parser.py`)

- 바이트 줄 단위로 읽고, `GRID`·`CQUAD`·`CTRIA`·`CHEXA`·`CTETRA`·`CPENTA`·`CBUSH`·`RBE`·`SPC`·`FORCE`… 로 시작하는 줄은 한 번의 `startswith`로 건너뛴다(그 연속 줄도 함께). `ENDDATA` 뒤는 읽지 않는다.
- 필드 형식: 작은 고정폭(8자), 큰 고정폭(`KEY*`, 16자, `*` 연속 줄), 자유 형식(쉼표). 연속 줄은 `+`·`*`·`,`·빈 첫 필드로 시작한다. 실수 약식 `3.3-9`, `1.+5`, `1.5D3`을 읽는다. `NaN`·`Inf`·범위 초과(`1.+999`)는 수로 읽지 않고(원문 문자열로 남거나 빈 값) 카드마다 `OPTISTRUCT_FIELD_INVALID` 알림을 남긴다. 30자리 넘는 정수 문자열은 문자열로 둔다. 층 두께 합이 넘치면 두께는 비운다.
- 읽는 카드: 속성 `PSHELL` `PSOLID` `PBEAM` `PBEAML` `PBAR` `PBARL` `PBUSH` `PCOMP` `PCOMPG` `PELAS` 외 일반 속성(필드 그대로), 재료 `MAT1` `MAT2` `MAT8` `MAT9` `MAT10`, 확장 `MATS1` `MATT1`(재료 필드에 `MATS1.TID` 형식으로 붙임), 표 `TABLES1` `TABLED1` `TABLEM1` `TABLEST` `TABLEMD`, `INCLUDE`.
- 이름: `$HMNAME COMP <id>"<이름>" <PID> "<속성 이름>" <유형>`, `$HMNAME PROP/MAT/CURVES`, `$* Material: <id> name: <이름>`(Component·Property도). 주석은 앞 1,024자만 정규식에 넣는다(긴 공백 꼬리에서 2차 시간 방지).
- 표 사용 연결: `MATS1`·`MATT1`이 가리키는 표와, `TABLEST`의 온도별 표를 반복 탐색(재귀 없음)으로 따라간다. 한 (소유자, 역할)에서 같은 표는 한 번만 연결하므로 자기 참조·순환·서로 겹치는 `TABLEST`도 선형이다(자기·되돌아오는 참조는 `OPTISTRUCT_TABLE_REFERENCE_CYCLE` 알림). 중첩 표의 역할은 `<역할> · 온도 <T>`(가장 안쪽 온도)로 깊이에 따라 길어지지 않는다.
- 화면 형태 대응(Radioss와 같은 응답 모양, `deck.solver="OPTISTRUCT"`): HyperMesh 컴포넌트 = Part 행(PID → 속성 → 재료 ID: PSHELL `MID1`, PSOLID·PBEAM·PBAR `MID`, PCOMP/PCOMPG 첫 층 `MID`), 두께 = PSHELL `T` 또는 PCOMP 층 두께 합(`LAM=SYM`이면 2배), 표 = 함수 곡선(`card` 필드에 카드 이름). `$HMNAME COMP`가 없으면 속성마다 한 행이다. RBE 등 속성 없는 컴포넌트도 행으로 남긴다.
- 단위: OptiStruct 입력에는 단위 카드가 없다. 모든 밀도가 1e-12~1e-7이면 mm·t·s로 **추정**해 g/cm³ 환산을 보여 주고 `OPTISTRUCT_UNITS_INFERRED` 알림을 남긴다. 그 밖에는 환산하지 않는다.
- 화면에서 OptiStruct 전용 표시는 최소로 했다: 상세 머리 `COMP <id>`, 곡선 머리 `<카드> <id>`, `원본 RHO`, 목록 위 입력 파일·크기·분석 시간 한 줄.

## 5. 저장 (`materials_deck_cache`, migration `0039_materials_deck_cache`)

- 열: `id, root_key, rel_path, solver(OPTISTRUCT), parser_version, fingerprint, size_bytes, status(READY|FAILED), deck_json, dependencies_json, blob_sha256, error_code, error_message, parse_seconds, created_at, updated_at`, 고유 `(root_key, rel_path, solver)`.
- 추가만 하는 migration(기존 표·행 불변). PostgreSQL 앱 역할에 SELECT/INSERT/UPDATE/DELETE 부여, 개발용 DuckDB는 `ensure_materials_deck_cache_schema`, 앱 기동 필수 표·열 검사에 포함.
- 파생 자료다(원본은 SPDM 파일). 프로젝트·의뢰 id가 없어 프로젝트 정리 대상이 아니며 언제 지워도 다음 조회가 다시 분석한다. 배포 패키지에 포함하지 않는다.

## 6. 남은 일·확인할 점

- 예시 덱의 `TABLEMD`(`MATS1`이 참조)는 한 줄에 값 3개(1열 응력, 2열 소성 변형률, 3열 0)로 보고 x=2열, y=1열로 그렸다. 3열 의미(변형률 속도·온도)와 축 정의는 사용자 확인이 필요하다. 3열 값이 여러 개면 알림을 남긴다.
- `TABLES1 1`(재료 20의 `MATS1 … PLASTIC`) 값은 응력 126,607~240,783으로 같은 재료 `E=66,210`(MPa로 보임)과 단위가 달라 보인다(kPa?). 원본 값 그대로 표시한다.
- 위치 규칙의 확장자는 `.fem`만이다. `.bdf`·`.nas`·`.dat`·`.parm`을 대표 입력으로 인정할지 정하지 않았다(INCLUDE 대상은 확장자 제한 없음).
- 처리하지 않는 카드(목록에 개수만 남김): `PCOMPP`/`PLY`/`STACK`(층 기반 복합재), `MAT3`·`MATHE`·`MATVE`·`MATF` 등, `MATT8/9` 표 연결, 여러 줄 따옴표 `INCLUDE`. 요소 개수(컴포넌트별)는 세지 않는다.
- 실제 1 GB 덱·Windows Server 2022·실제 드라이브에서 측정·검증하지 않았다.
