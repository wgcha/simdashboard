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

## 2. 대상 PC 준비와 통합 설치

1. GitHub에서 동일한 버전의 저장소 전체를 다운로드한다.
2. VS Code에서 저장소 최상위 폴더를 연다.
3. PostgreSQL을 설치하고 서비스를 시작한다.
4. VS Code 터미널에서 `setup.bat`을 실행한다.
5. 회사 프록시를 입력한 뒤 `Transfer` 모드와 받은 번들 폴더를 선택한다.
6. 설치가 완료되면 `start-postgresql.bat`을 실행한다.

관리자 호스트·포트·사용자·비밀번호는 설치 중 안전하게 입력받으며 관리자 URL을 `.env`에 저장하지 않는다. 통합 설치는 번들을 먼저 검증하고, 기존 DB가 있으면 검증된 백업을 만든 뒤 스테이징 DB에서 복원과 행 수 검증을 마치고 교체한다. 기존 전용 역할이 다른 DB에서도 사용 중이면 자동 교체를 중단한다.

비대화형 실행은 다음과 같이 관리자 URL을 프로세스 환경으로만 전달한다. `-BackupDir`을 생략하면 프로젝트의 기본 백업 위치를 사용한다.

```powershell
$env:POSTGRES_ADMIN_URL = 'postgresql://postgres:관리자비밀번호@127.0.0.1:5432/postgres'
.\setup.bat -NonInteractive -Mode Transfer `
  -Bundle 'D:\받은폴더\analysis-canvas-transfer-...' `
  -BackupDir 'D:\analysis-backups' `
  -ProxyUrl 'http://proxy.company.local:8080'
Remove-Item Env:POSTGRES_ADMIN_URL
```

### 기존 데이터가 있는 경우

현재 통합 설치의 기본 정책은 기존 DB를 검증된 백업으로 보존한 뒤, 이관 DB를 스테이징에서 검증하여 통째로 교체하는 방식이다. 기존 데이터를 그대로 사용하려면 `Keep` 모드를 선택한다. 두 DB의 레코드를 합치는 병합 방식은 ID, 참조 무결성, 감사 이력과 assets 파일명 충돌 정책을 먼저 정해야 하므로 자동 실행하지 않는다. 병합이 필요하면 백업을 보존한 상태에서 별도 기능으로 검토·구현한다.

## 3. 개별 명령으로 변경 없는 사전검증

먼저 `--validate-only`로 번들 checksum, Alembic 호환성, dump 구조와 assets 경로를 검사한다.

```bat
import-postgresql-transfer.bat "D:\받은폴더\analysis-canvas-transfer-..." --validate-only
```

이 모드에서는 DB, 역할, 파일 또는 `.env`를 변경하지 않는다.

## 4. 개별 명령으로 실제 가져오기

검증이 통과하면 같은 번들을 실제로 가져온다.

```bat
import-postgresql-transfer.bat "D:\받은폴더\analysis-canvas-transfer-..."
```

통합 설치 또는 가져오기 도구는 다음 순서로 처리한다.

1. 번들의 SHA-256과 안전한 상대 경로를 재검증한다.
2. 기존 DB가 있으면 custom-format dump와 assets archive를 만들고 크기와 SHA-256을 검증한다. 검증 실패 시 교체를 시작하지 않는다.
3. 신규 대상이면 owner/app 비밀번호를 새로 생성한다. 검증된 기존 전용 역할이 있으면 현재 자격정보를 유지하고 고유 이름의 스테이징 DB만 준비한다.
4. owner 계정으로 dump를 단일 트랜잭션에 복구하고 Alembic을 현재 코드의 head까지 적용한다.
5. 앱 최소권한, revision, 테이블별 행 수와 assets를 검증한다.
6. 기존 DB를 타임스탬프 백업명으로 바꾸고 스테이징 DB를 `simulation_dashboard`로 전환한다.
7. assets와 환경설정 전환까지 완료한 뒤 서비스 `.env`에는 app URL만, `.postgres-owner.env`에는 owner URL만 저장한다.

교체 중 실패하면 기존 DB 이름과 파일을 복구하고 서비스 시작을 차단한다. 백업과 진단 정보는 원인 확인을 위해 보존한다.

## 5. 실패 시 복구

1. 오류가 발생하면 `start-postgresql.bat`을 실행하지 않는다.
2. 설치 출력에 표시된 백업 폴더의 manifest, database dump, assets archive와 진단 정보를 보존한다.
3. 관리자에게 오류 메시지와 백업 폴더 전체를 전달한다. 수동 복구 전에는 기존 DB, 스테이징 DB 또는 타임스탬프 DB를 삭제하거나 이름을 바꾸지 않는다.
4. `.setup-recovery-required.json`이 있으면 `setup.bat`과 서비스 시작이 모두 차단된다. 관리자가 파일에 기록된 DB 이름·OID와 백업을 확인하고 원래 DB 또는 승격 DB 상태를 복구한 뒤에만 마커를 제거한다.
5. 복구 마커가 없고 백업 단계에서만 실패했다면 프록시·권한·디스크 공간 등 원인을 해결한 뒤 같은 `setup.bat` 명령을 다시 실행한다.

## 6. 시작과 확인

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
