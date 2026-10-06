# 계약: SPDM 저장소 공급자 (StorageProvider) — 1단계 코드 정리

- 상태: 확정 (2026-10-04)
- 목적: SPDM 폴더 접근을 한 인터페이스로 모아, 이후 알테어원 드라이브(사이드카 HTTP 서비스) 공급자를 설정만으로 교체할 수 있게 한다.
- 1단계 범위: **동작 변화 없음.** 현재 로컬 폴더 접근을 `LocalFsProvider` 하나로 옮긴다. 원격 공급자, 설정 스위치, 사이드카 연결은 2단계 이후.

## 1. 결정

| # | 결정 |
|---|---|
| S1 | SPDM 루트 아래의 모든 목록·조회·읽기·쓰기는 `StorageProvider`를 거친다. 서비스 모듈에서 SPDM 경로에 대한 `os.scandir`/`os.walk`/`os.listdir`/`Path.iterdir`/`glob`/`open`/`stat`/쓰기 직접 호출을 없앤다 |
| S2 | 1단계 구현체는 `LocalFsProvider` 하나. 결과(목록 순서, 숨김·링크 처리, 오류, 지문, 한도)는 현재와 동일해야 한다 |
| S3 | 쓰기는 공급자에서 **명시한 쓰기 구역**만 허용하고 그 밖은 `NOT_ALLOWED_WRITE`. 구역은 두 종류다. ① `FINAL`: `*/Final/`, `*/Final/CAE/**`, `*/Final/Report/**`, `*/Final/.finalizations/**`(생성·이동·교체·정리용 삭제 포함). ② `LEGACY`(구 방식 기능, 동작 보존용, 신규 사용 금지): 구 SPDM 저장소 업로드(`spdm_storage`: `Project_*/WR_*/CAE/**`, `<P>/<WR>/보고서/**`, `*.simdashboard-complete.json`, 루트 생성), 결과 등록 경로 준비·복사(`result_registration_paths.prepare_folders`, `result_registration` 파일 복사, 실패 시 rmdir). 각 쓰기 호출은 구역을 인자로 명시하고, LEGACY 구역 쓰기는 호출 모듈을 허용 목록으로 제한한다. LEGACY 구역 폐지는 별도 결정. ③ `WORKING`(W8, 2026-10-06; 검수 L2): `<프로젝트>/<의뢰>/Working/**`(Working 위 2단계 이상, `Working` 한 번, `Final`·`.`·`..` 없음; 그 밖의 쓰기 함수는 거부) — 결과 등록 끌어서 올리기(`result_drop_upload`)만, 폴더 생성(`mkdir_pinned`)·임시 조각(`write_chunk`)·비덮어쓰기 공개(`rename_no_replace`)·자기 임시 파일 정리. [결과 등록](../features/result-registration.md) §5 |
| S4 | SPDM 루트 해석은 한 곳(`spdm_storage.storage_root`)에서만. 모듈별 `_root()` 사본을 제거하고 공급자를 얻는 함수 하나(`get_storage_provider(conn)`)를 둔다 |
| S5 | SPDM 루트 **밖**의 파일 접근(앱 데이터 폴더, 업로드 임시, 백업, 배포 스크립트, local_runner)은 범위 밖이다 |

## 2. 인터페이스 (`backend/app/services/storage/provider.py`)

```python
class StorageProvider(Protocol):
    def root_identity(self) -> str
    def list(self, rel_path: str) -> list[Entry]          # 1단계는 페이지 없음(전체), 정렬은 현재 코드와 동일
    def stat(self, rel_path: str, *, follow_links: bool = False) -> Entry | None
    def read_stable(self, rel_path: str, *, max_bytes: int) -> bytes | Iterator[bytes]   # 읽기 전후 size·mtime 불변 확인, 아니면 BUSY (현 read_stable_bytes)
    def open_read(self, rel_path: str) -> BinaryIO
    # 쓰기: zone은 "FINAL" | "LEGACY"
    def mkdirs(self, rel_path: str, *, zone: str) -> None
    def create_exclusive(self, rel_path: str, data: bytes | Iterable[bytes], *, zone: str, fsync: bool = True) -> Entry   # 'xb'
    def move_no_overwrite(self, src_rel: str, dst_rel: str, *, zone: str) -> None   # 현 link/rename 의미, 대상 존재 시 EXISTS
    def replace(self, src_rel: str, dst_rel: str, *, zone: str) -> None             # os.replace 의미(허용된 기존 사용처만)
    def remove(self, rel_path: str, *, zone: str) -> None                           # 파일 unlink / 빈 폴더 rmdir (정리용)
    def lock(self, rel_path: str, *, zone: str) -> ContextManager[None]            # 현 .request.lock (flock/msvcrt) 의미
    def changes(self, rel_path: str, since: str | None) -> None                     # 1단계는 항상 None(미지원)
```

- 디렉터리 고정(`_pin_directory_chain`), 링크·reparse 검사, 이름 충돌(대소문자) 검사는 공급자 내부로 옮기되 결과는 동일해야 한다.

@dataclass(frozen=True)
class Entry:
    name: str
    kind: Literal["file", "dir", "other"]
    size: int | None
    modified_ns: int | None
    item_id: str               # 로컬: st_dev:st_ino
    etag: str | None           # 로컬: None
    is_link: bool              # reparse point/symlink
```

- 경로: 루트 기준 상대경로, `/` 구분. `..`, 절대경로, 루트 밖으로 나가는 경로는 거부(현재 검사 재사용).
- `root_identity()`: 현재 `root_identity`/`root_key` 계산과 동일한 값을 반환해 DB 호환을 유지.
- 오류: `StorageError(code)` — `NOT_FOUND`, `FORBIDDEN`, `NOT_ALLOWED_WRITE`, `UNAVAILABLE`, `LIMIT`. 기존 호출부가 잡던 예외 의미는 유지(필요하면 어댑터에서 기존 예외로 변환).
- 한도(`MAX_ENTRIES`, 시간 예산, `_Lister` 등)는 호출부 로직이므로 그대로 두고, 공급자는 단순 목록만 제공.

## 3. 완료 기준 (Verifier)

1. `backend/app` 안에서 SPDM 루트 경로를 대상으로 하는 직접 파일시스템 호출이 공급자 모듈 밖에 없다(정적 검사 테스트 추가: 허용 모듈 목록 외 `scandir|walk|listdir|iterdir|glob|open(|.stat(|write_|mkdir|rename|replace|unlink` 사용 시 실패. SPDM 무관 사용처는 명시적 예외 목록)
2. 기존 전체 테스트 결과가 이전과 동일(DuckDB 전체, 알려진 9건 외 실패 없음, Postgres 주요 묶음 통과)
3. 쓰기 구역 밖 경로, 또는 LEGACY 허용 모듈 밖에서의 LEGACY 쓰기는 `NOT_ALLOWED_WRITE`(단위 테스트)
4. 실제형 트리에서 자동 탐색→등록→수집→진척→Final 지정→보고서 업로드 흐름의 산출(DB 행, 파일)이 리팩터 전후 동일(비교 스크립트)
5. 성능: 같은 트리에서 자동 탐색·동기화 1회 소요 시간이 전후 ±20% 이내
6. 배포 계약 영향 없음(새 의존성·migration 없음)

## 4. 2단계 정리 대상

1단계는 공개 함수 시그니처(`root: Path` 인자, 시험이 직접 호출하는 내부 함수)를 유지하려고 서비스 안에서 `LocalFsProvider(root)`를 직접 만든다. 2단계에서 `get_storage_provider(conn)`로 얻은 공급자를 인자로 넘기도록 정리한다(2026-10-04 기준 69곳, `backend/app/services/` 기준 줄 번호).

- `case_finalization.py` 29곳: 368, 388, 443, 481, 589, 596, 644, 660, 696, 759, 779, 1090, 1113, 1140, 1160, 1181, 1218, 1249, 1295, 1406, 1417, 1460, 1514, 1536, 1569, 1624, 1648, 1757, 1839
- `result_registration_paths.py` 12곳: 157, 456, 585, 692, 721, 777, 828, 867, 951, 1013, 1033, 1121
- `spdm_storage.py` 8곳: 97, 120, 220, 244, 623, 722, 753, 835
- `dashboard_capture.py` 4곳: 58, 63, 91, 195
- `folder_discovery_scan.py` 4곳: 28, 33, 42, 58
- `materials_catalog.py` 4곳: 168, 320, 532, 648 (168은 덱 파일 경로 기준 `LocalFsProvider(path.parent)` 읽기)
- `result_registration_locations.py` 3곳: 110, 119, 133
- `result_registration.py` 2곳: 243, 948
- `folder_auto_discovery.py` 1곳: 162 / `folder_request_progress.py` 1곳: 182 / `folder_schema_resolver.py` 1곳: 97

기타 1단계 구현 메모:

- `create_exclusive`는 추가 stat 없이 `None`을 반환한다(§2의 `-> Entry`와 다름).
- `move_no_overwrite`는 POSIX에서 hardlink만 만들고 임시 이름 정리는 호출부가 기존처럼(엄격 또는 best-effort) 수행한다. Windows는 rename이다.
- §2 밖의 로컬 보조 메서드: `is_link`, `assert_safe`, `case_collision`, `pin`, `walk`, `rglob`, `resolve`, `read_stable_digest`, `read_small_nofollow`, `list(stat="files")`.
- 쓰기 호출 모듈 제한: FINAL은 `case_finalization`, LEGACY는 `spdm_storage`·`result_registration_paths`·`result_registration`. FINAL 경로는 비어 있지 않은 의뢰 접두 아래 `Final` 자신과 `Final/(CAE|Report|.finalizations)/**`이며 `Working` 아래의 `Final`은 제외한다.
