# PostgreSQL PC 간 이관 가이드

이 절차는 PostgreSQL로 운영 중인 원본 PC의 Analysis Canvas 데이터와 관리형 파일을, PostgreSQL과 동일한 프로그램 코드가 설치된 새 Windows PC로 옮길 때 사용한다.

이관 번들에는 PostgreSQL custom-format dump, `backend/assets` 파일, Alembic revision, 테이블별 행 수, 파일 크기와 SHA-256이 포함된다. `.env`, DB 비밀번호, owner 비밀번호, 관리자 URL, `AUTH_SECRET_KEY`는 포함하지 않는다. 대상 PC에서는 owner/app 비밀번호를 새로 생성한다.

## 1. 원본 PC에서 내보내기

일관된 DB와 파일을 묶기 위해 먼저 프로그램을 종료한다.

```bat
stop.bat
export-postgresql-transfer.bat
```

기본 출력 위치는 `transfer-bundles\analysis-canvas-transfer-<UTC 시각>`이다. 다른 위치를 지정할 수도 있다.

```bat
export-postgresql-transfer.bat "D:\analysis-transfer"
```

생성된 `analysis-canvas-transfer-...` 폴더 전체를 대상 PC로 복사한다. 폴더 안의 파일을 개별적으로 수정하거나 이름을 바꾸면 검증에 실패한다.

## 2. 대상 PC 준비

1. 동일한 버전의 프로그램 코드를 복사한다.
2. PostgreSQL을 설치하고 서비스를 시작한다.
3. `setup-windows.bat`으로 Python/프런트엔드 의존성을 설치한다.
4. 프로그램 루트에 `.env`를 만들고 대상 PC의 PostgreSQL 관리자 연결만 입력한다.

```env
POSTGRES_ADMIN_URL=postgresql://postgres:관리자비밀번호@127.0.0.1:5432/postgres
```

대상의 `simulation_dashboard`, `simdashboard_owner`, `simdashboard_app`은 존재하지 않아야 한다. 이관 도구는 기존 DB나 역할을 덮어쓰지 않는다.

## 3. 변경 없는 사전검증

먼저 `--validate-only`로 번들 checksum, Alembic 호환성, dump 구조와 assets 경로를 검사한다.

```bat
import-postgresql-transfer.bat "D:\받은폴더\analysis-canvas-transfer-..." --validate-only
```

이 모드에서는 DB, 역할, 파일 또는 `.env`를 변경하지 않는다.

## 4. 실제 가져오기

검증이 통과하면 같은 번들을 실제로 가져온다.

```bat
import-postgresql-transfer.bat "D:\받은폴더\analysis-canvas-transfer-..."
```

도구는 다음 순서로 처리한다.

1. 번들의 SHA-256과 안전한 상대 경로를 재검증한다.
2. 새 owner/app 비밀번호를 생성한다.
3. `simulation_dashboard` DB와 두 역할을 만든다.
4. owner 계정으로 dump를 단일 트랜잭션에 복구한다.
5. Alembic을 현재 코드의 head까지 적용한다.
6. 앱 최소권한과 감사 로그 append-only 권한을 적용한다.
7. revision과 테이블별 행 수를 원본 manifest와 비교한다.
8. 기존 `backend/assets`를 `backend/backups`에 보관하고 번들 assets로 전환한다.
9. 서비스 `.env`에는 app URL만, `.postgres-owner.env`에는 owner URL만 저장한다.

가져오기가 실패하면 생성된 대상 DB/역할을 자동 삭제하지 않는다. 오류 증거와 부분 상태를 보존한 뒤 원인을 확인해야 한다.

## 5. 시작과 확인

```bat
start-postgresql.bat
```

브라우저 또는 PowerShell에서 확인한다.

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

정상 응답:

```json
{"status":"ok","database_backend":"postgresql"}
```

