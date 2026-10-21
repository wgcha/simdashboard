# 버전 버튼과 버전 게시판

상단 바의 `v0.3.0.1` 버튼과 그 버튼으로 여는 `/workspace/releases`(버전 게시판)는 이 대시보드가 **자기 버전 기록을 스스로 관리**하는 기능이다(사용자 요청 2026-10-21, PhysicsAI 플랫폼의 같은 기능을 옮김). 기록은 배포 작업이 쓰고, 화면은 읽기만 한다.

## 1. 배포마다 버전이 바뀐다 (사용자 결정 2026-10-21)

서버에 올리는 모든 배포는 버전 번호가 달라야 한다. 묶어서 올릴 수 있으면 릴리스(`x.y.z`)로 묶고, 잘게 나눠 올릴 때는 **네 번째 자리를 붙인다(`x.y.z.n`, n은 1부터)**.

| 종류 | 형식 | 예 | 언제 |
| --- | --- | --- | --- |
| 묶음 릴리스 | `x.y.z` | `0.3.0` → `0.4.0`, `0.3.1` | 기능을 모아 한 번에 낼 때 |
| 작은 배포 | `x.y.z.n` | `0.3.0.1`, `0.3.0.2` | 같은 릴리스 위에 고침·작은 기능을 바로 올릴 때 |

`x.y.z.0`은 쓰지 않는다(`x.y.z`와 같으므로 파서가 제목으로 받지 않는다). 순서는 숫자로 비교한다(`0.3.0 < 0.3.0.1 < 0.3.0.2 < 0.3.0.10 < 0.3.1`; 백엔드 `version_tuple`, 프런트 `compareVersion`).

버전 값은 세 곳에 있고 아래 규칙으로 항상 맞춘다.

| 위치 | 값 | 역할 |
| --- | --- | --- |
| `backend/app/version.py` `__version__` | `x.y.z[.n]` 전체 | 기준 값. FastAPI 앱 버전(`openapi.json`의 `info.version`)과 API 응답이 이것을 쓴다. |
| `RELEASES.md` 맨 위 항목 | `__version__`과 같은 문자열 | 사용자에게 보이는 릴리스 기록. |
| `frontend/package.json` `version` | **앞 세 자리 `x.y.z`만** | 웹 패키지 버전. npm semver는 네 자리를 받지 않으므로 네 번째 자리는 넣지 않는다(`0.3.0.1`이면 `0.3.0`). 작은 배포 때는 이 값이 그대로다. |

`backend/tests/test_releases.py::test_version_values_agree_with_top_release_entry`가 세 값이 이 관계인지, 항목 순서가 최신 우선인지, 항목마다 글머리표가 있는지 본다.

### 올리는 방법

1. `backend/app/version.py`의 `__version__`을 올린다(작은 배포: `0.3.0.1` → `0.3.0.2`, 첫 작은 배포: `0.3.0` → `0.3.0.1`; 묶음: `0.3.x`/`0.4.0`).
2. `RELEASES.md` 맨 위에 `## <같은 버전> — YYYY-MM-DD`와 사용자가 체감하는 변화를 한국어 한 줄씩 추가한다.
3. 묶음 릴리스로 앞 세 자리가 바뀌면 `frontend/package.json`의 `version`도 같은 `x.y.z`로 바꾼다(작은 배포는 건드리지 않는다).
4. `frontend`에서 `npm run generate:api`(`info.version` 갱신), `backend`에서 `python scripts/check_openapi_contract.py --expected ../frontend/openapi.json`로 확인한다.

### 버전을 안 올린 배포 막기

`.github/workflows/version-bump.yml`(`claude/scx-drive`·`main` 푸시와 `main` 대상 PR)이 `backend/scripts/check_version_bump.py --base <이전 커밋>`을 실행한다. 기준 커밋과 HEAD 사이에 **문서·로그·시험·워크플로 밖의 파일**이 바뀌었는데 `__version__`이 기준 커밋보다 커지지 않았으면 실패한다. 면제: `docs/`, `log/`, `backend/tests/`, `frontend/e2e/`, `local_runner/tests/`, `.github/`, 모든 `*.md`(`RELEASES.md` 포함), 이름에 `self-test`·`.test.`·`.spec.`이 든 파일, `.gitignore`. 즉 문서만 고친 푸시는 버전을 올리지 않아도 된다. 기준 커밋을 알 수 없으면(첫 푸시·강제 푸시) 건너뛰고 알린다. 같은 규칙이 `tests/test_releases.py`의 `test_version_bump_guard_*`로 시험된다. 로컬에서는 `python backend/scripts/check_version_bump.py --base origin/main`.

## 2. `RELEASES.md`

- 저장소 루트. 폐쇄망 번들에서는 `release/<id>/RELEASES.md`(= `backend`와 같은 층)로 들어간다.
- 맨 위에 규칙 머리말, 그 아래 `## <버전> — YYYY-MM-DD`(버전은 `x.y.z` 또는 `x.y.z.n`) 제목과 `- ` 글머리표. **새 항목이 맨 위.** 글머리표는 사용자가 체감하는 변화를 한국어 한 줄로 쓰고, 긴 문장은 들여쓴 다음 줄로 이어 쓴다.
- 배포(서버에 올리는 작업)를 만들 때 고친다. 웹에서 고치는 화면은 없다.
- 파서는 `backend/app/services/releases.py` `parse_releases`. 서버는 틀린 제목을 건너뛰고(엄격 모드는 `ValueError`, 시험이 이것으로 저장소 파일을 검사), 파일이 없으면 빈 목록을 돌려준다.

## 3. 빌드 정보(커밋·날짜)

| 배포 방식 | 얻는 곳 | 화면 표시 |
| --- | --- | --- |
| 소스 배포(`deploy.bat`/`update.bat`, Git 체크아웃) | 서버가 시작 후 처음 요청 때 `git log -1 --format=%H%n%cI`를 한 번 실행해 프로세스에 보관(읽기 전용, `safe.directory` 지정, 5초 제한). Git이 없거나 실패하면 비워 둔다. | `커밋 날짜 … · <커밋 12자리>` |
| Windows 폐쇄망 번들 | `scripts/windows/build-offline-bundle.ps1`이 `release/<id>/build-info.json`(`commit`, `built_at`(UTC), `dirty`)과 `RELEASES.md`를 넣는다. 둘 다 `bundle-manifest.json` 해시 목록에 들어간다. GitHub 워크플로는 그대로(체크아웃에서 커밋을 읽는다). | `빌드 … · <커밋 12자리>` |
| Rocky 8 번들·소스 배포(`deploy/rocky8`) | `build-release.sh`가 `payload/RELEASES.md`와 `payload/build-info.json`(Windows와 같은 모양: 전체 `commit`, UTC `built_at`, `dirty`)을 만들고 `SHA256SUMS`에 포함한다. `install.sh`가 둘을 릴리스 루트(`/opt/simdashboard/releases/<id>/`, `backend`와 같은 층 = 백엔드가 읽는 곳)에 복사한다. `install-offline.sh`는 둘이 없으면 번들 불완전으로 멈추고, CI의 `verify-offline-bundle.sh`는 내용 모양까지 본다. 번들 없이 체크아웃에서 바로 설치하면 `RELEASES.md`를 복사하고 `build-info.json`은 Git 커밋으로 만든다(Git이 없으면 빌드 정보만 빈다). | `빌드 … · <커밋 12자리>` |

`build-info.json`이 있으면 Git보다 우선한다. 소스 배포의 날짜는 빌드 시각이 아니라 체크아웃된 커밋의 날짜이므로 화면에 "커밋 날짜"로 표시한다.

## 4. API

`GET /api/releases` — 로그인한 모든 계정(승인 대기·중지 계정은 공통 미들웨어가 막음), 읽기 전용.

```json
{ "version": { "current": "0.3.0.1", "released_at": "2026-10-21" },
  "build": { "commit": "0c09b07abcde", "built_at": "2026-10-21T00:43:38Z", "source": "git" },
  "releases": [ { "version": "0.3.0.1", "date": "2026-10-21", "notes": ["…"] } ] }
```

`current`는 실행 중 프로세스의 `__version__`, `released_at`은 `RELEASES.md`에서 같은 버전 항목의 날짜(없으면 null). 스키마는 `schemas/api.py`의 `ReleasesResponse`.

## 5. 화면

- 상단 바(`AppTopbar`)의 알림 벨 왼쪽에 `v0.3.0` 버튼(`features/releases/VersionBadge.tsx`). 누르면 `/workspace/releases`. 툴팁에 빌드 정보.
- `/workspace/releases`(`ReleaseBoard`): 서버 버전·빌드 정보와 릴리스 목록(최신 우선, 현재 버전 표시). 사이드바 메뉴가 아니고 메뉴 정책 대상도 아니다(알림과 같이 모든 활성 계정, `MENU_POLICY_FREE_PAGES`).
- **새 버전 알림**: 상단 바가 서버 버전을 60초마다·창 포커스 때 다시 읽는다. 이 페이지를 연 뒤 처음 읽은 **버전**(`x.y.z[.n]`, `deploymentKey`)과 달라지면 버튼 옆에 `새 버전이 배포되었습니다 [새로고침]`을 띄운다(게시판에도 같은 안내). 커밋·빌드 날짜는 비교하지 않고 표시용으로만 쓴다: 배포마다 버전이 바뀌는 것이 규칙이므로 같은 버전의 재배포는 새 배포로 보지 않는다. 처음 읽은 값은 화면 이동에도 유지되도록 모듈 상태(`releasesStore.ts`)에 둔다.

## 6. 시험

- 백엔드 `tests/test_releases.py`: 파서(형식·네 자리·`x.y.z.0` 거부·엄격 모드·BOM·긴 줄 이음), 버전 순서(`0.3.0 < 0.3.0.1`), 값 일치(`package.json`은 앞 세 자리), 버전 올림 검사(규칙·임시 Git 이력), 빌드 정보(스탬프·Git·없음), 응답 모양, 엔드포인트(401, 일반 계정 200).
- 프런트 `npm run test:api`(`scripts/shared-api-self-test.mjs`): 응답 정규화, 빌드 문구, 새 버전 알림 판정(버전만 비교, 네 번째 자리, 커밋 변경은 알림 없음), `compareVersion`. `npm run test:routing`: `/workspace/releases` 경로.
- Rocky 8: `bash deploy/rocky8/validate-templates.sh`(문법과 번들·설치 계약 문자열).
- 시험하지 못한 것: 브라우저(e2e), PowerShell 번들 빌드(Windows 러너 전용), Rocky 8.6 컨테이너 번들 빌드·`verify-offline-bundle.sh`(CI 전용).
