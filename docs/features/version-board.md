# 버전 버튼과 버전 게시판

상단 바의 `v0.3.0` 버튼과 그 버튼으로 여는 `/workspace/releases`(버전 게시판)는 이 대시보드가 **자기 버전 기록을 스스로 관리**하는 기능이다(사용자 요청 2026-10-21, PhysicsAI 플랫폼의 같은 기능을 옮김). 기록은 배포 작업이 쓰고, 화면은 읽기만 한다.

## 1. 버전 값은 세 곳, 항상 같다

| 위치 | 역할 |
| --- | --- |
| `backend/app/version.py` `__version__` | 기준 값. FastAPI 앱 버전(`openapi.json`의 `info.version`)과 API 응답이 이것을 쓴다. |
| `frontend/package.json` `version` | 웹 패키지 버전. |
| `RELEASES.md` 맨 위 항목 | 사용자에게 보이는 릴리스 기록. |

새 버전을 올릴 때 세 곳을 함께 고친다. 하나라도 다르면 `backend/tests/test_releases.py::test_version_values_agree_with_top_release_entry`가 실패한다(같은 시험이 항목 순서가 최신 우선인지, 항목마다 글머리표가 있는지도 본다). 버전 올릴 때 `frontend/openapi.json`은 `info.version` 때문에 다시 만든다(`npm run generate:api`).

## 2. `RELEASES.md`

- 저장소 루트. 폐쇄망 번들에서는 `release/<id>/RELEASES.md`(= `backend`와 같은 층)로 들어간다.
- 맨 위에 규칙 머리말, 그 아래 `## <버전> — YYYY-MM-DD` 제목과 `- ` 글머리표. **새 항목이 맨 위.** 글머리표는 사용자가 체감하는 변화를 한국어 한 줄로 쓰고, 긴 문장은 들여쓴 다음 줄로 이어 쓴다.
- 배포(서버에 올리는 작업)를 만들 때만 고친다. 웹에서 고치는 화면은 없다.
- 파서는 `backend/app/services/releases.py` `parse_releases`. 서버는 틀린 제목을 건너뛰고(엄격 모드는 `ValueError`, 시험이 이것으로 저장소 파일을 검사), 파일이 없으면 빈 목록을 돌려준다.

## 3. 빌드 정보(커밋·날짜)

| 배포 방식 | 얻는 곳 | 화면 표시 |
| --- | --- | --- |
| 소스 배포(`deploy.bat`/`update.bat`, Git 체크아웃) | 서버가 시작 후 처음 요청 때 `git log -1 --format=%H%n%cI`를 한 번 실행해 프로세스에 보관(읽기 전용, `safe.directory` 지정, 5초 제한). Git이 없거나 실패하면 비워 둔다. | `커밋 날짜 … · <커밋 12자리>` |
| Windows 폐쇄망 번들 | `scripts/windows/build-offline-bundle.ps1`이 `release/<id>/build-info.json`(`commit`, `built_at`(UTC), `dirty`)과 `RELEASES.md`를 넣는다. 둘 다 `bundle-manifest.json` 해시 목록에 들어간다. GitHub 워크플로는 그대로(체크아웃에서 커밋을 읽는다). | `빌드 … · <커밋 12자리>` |

`build-info.json`이 있으면 Git보다 우선한다. 소스 배포의 날짜는 빌드 시각이 아니라 체크아웃된 커밋의 날짜이므로 화면에 "커밋 날짜"로 표시한다. Rocky 8 패키지(`deploy/rocky8`)는 이 파일들을 싣지 않아 그쪽에서는 목록이 비고 빌드 정보가 없다(버전 숫자는 표시).

## 4. API

`GET /api/releases` — 로그인한 모든 계정(승인 대기·중지 계정은 공통 미들웨어가 막음), 읽기 전용.

```json
{ "version": { "current": "0.3.0", "released_at": "2026-10-21" },
  "build": { "commit": "0c09b07abcde", "built_at": "2026-10-21T00:43:38Z", "source": "git" },
  "releases": [ { "version": "0.3.0", "date": "2026-10-21", "notes": ["…"] } ] }
```

`current`는 실행 중 프로세스의 `__version__`, `released_at`은 `RELEASES.md`에서 같은 버전 항목의 날짜(없으면 null). 스키마는 `schemas/api.py`의 `ReleasesResponse`.

## 5. 화면

- 상단 바(`AppTopbar`)의 알림 벨 왼쪽에 `v0.3.0` 버튼(`features/releases/VersionBadge.tsx`). 누르면 `/workspace/releases`. 툴팁에 빌드 정보.
- `/workspace/releases`(`ReleaseBoard`): 서버 버전·빌드 정보와 릴리스 목록(최신 우선, 현재 버전 표시). 사이드바 메뉴가 아니고 메뉴 정책 대상도 아니다(알림과 같이 모든 활성 계정, `MENU_POLICY_FREE_PAGES`).
- **새 버전 알림**: 상단 바가 서버 버전을 60초마다·창 포커스 때 다시 읽는다. 이 페이지를 연 뒤 처음 읽은 배포 키(`버전+커밋`)와 달라지면 버튼 옆에 `새 버전이 배포되었습니다 [새로고침]`을 띄운다(게시판에도 같은 안내). 같은 버전 번호로 코드만 바뀐 재배포(커밋 변경)도 감지한다. 처음 읽은 값은 화면 이동에도 유지되도록 모듈 상태(`releasesStore.ts`)에 둔다.

## 6. 시험

- 백엔드 `tests/test_releases.py`: 파서(형식·엄격 모드·BOM·긴 줄 이음), 세 값 일치, 빌드 정보(스탬프·Git·없음), 응답 모양, 엔드포인트(401, 일반 계정 200).
- 프런트 `npm run test:api`(`scripts/shared-api-self-test.mjs`): 응답 정규화, 빌드 문구, 새 버전 알림 판정. `npm run test:routing`: `/workspace/releases` 경로.
- 시험하지 못한 것: 브라우저(e2e)와 PowerShell 번들 빌드(Windows 러너 전용).
