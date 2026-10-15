# 소스 배포 → GitHub 빌드 설치 패키지로 전환 (Windows)

- 기준일: 2026-10-15 (사용자 결정: 서버에서 빌드하지 않고 GitHub에서 빌드한 패키지를 내려받아 설치)
- 대상: 지금 `deploy.bat`·`update.bat`(소스 배포)로 운영 중인 Windows 서버
- 관련: [폐쇄망 패키지 설치](windows-server-offline-installation.md), [Windows 배포 정책](windows-deployment-policy.md), [SCX 드라이브](features/scx-drive.md)

> **미검증 주의**: 오프라인 설치기는 GitHub의 Windows Server 2022 러너에서 패키지 빌드·검증까지만 확인했다. 실제 서버에서 신규 설치·기존 DB 업데이트·재부팅 수락 시험은 아직 하지 않았다. 첫 전환은 업무 시간 밖에, 되돌리기 절차(§7)를 준비하고 진행한다.

## 1. 무엇이 달라지나

| | 소스 배포 (지금) | 설치 패키지 (전환 후) |
|---|---|---|
| 업데이트 | 서버에서 `update.bat` (git·uv·pnpm·Node가 서버에서 동작) | GitHub에서 받은 ZIP을 풀고 `install.cmd` |
| 서버에 필요한 것 | Git, 인터넷, 서버에서 실행 가능한 uv·Node | 없음 (Python·패키지·화면·Caddy·WinSW 포함) |
| 실행 | `start.bat` 프로세스 | Windows 서비스 2개(API, 웹), 재부팅 시 자동 시작 |
| 접속 주소 | `http://서버:8080/` | `http://서버:8080/home/` (`/`로 들어오면 자동 이동) |
| 설정 파일 | `<소스 폴더>\.env` | `C:\ProgramData\SimulationWorkbench\state\.env` |
| 관리형 첨부 | `<소스 폴더>\backend\assets` | `state\assets` |
| 드라이브 워커 폴더 | `<소스 폴더>\backend\data\scx-worker` | `state\scx-worker` |
| 어댑터 wheel | `<소스 폴더>\external-wheels` | `state\external-wheels` |

DB(PostgreSQL 18)는 그대로 쓴다. 설치기는 기존 DB를 **백업·검증한 뒤 대기 중인 migration만** 적용한다.

## 2. 패키지 내려받기

1. GitHub 저장소 → **Actions → Windows offline bundle** → 브랜치 `claude/scx-drive`의 가장 최근 성공 실행.
2. 아래 **Artifacts**의 `simworkbench-windows-offline-<날짜>-<커밋>`을 받는다(약 260 MB, 14일 보관).
3. 받은 파일을 풀면 `…zip`, `…zip.sha256`, `…exe`, `…exe.sha256`이 있다. 서버로 옮긴 뒤 해시를 확인한다.
   ```powershell
   Get-FileHash .\simworkbench-windows-offline-*.zip -Algorithm SHA256
   Get-Content .\simworkbench-windows-offline-*.zip.sha256
   ```
   두 값이 같아야 한다.

`claude/scx-drive`에 커밋이 올라갈 때마다 자동으로 새 패키지가 만들어진다(약 5분).

## 3. 전환 준비 (처음 한 번)

관리자 PowerShell에서 진행한다. 예시의 소스 폴더는 `E:\VD_SIMULATION_WEB\simdashboard`이다.

1. **소스 배포 중지**: 소스 폴더에서 `stop.bat`.
2. **설정 이전**: 새 상태 폴더에 소스 배포의 설정을 그대로 복사한다.
   ```powershell
   $src = 'E:\VD_SIMULATION_WEB\simdashboard'
   $state = 'C:\ProgramData\SimulationWorkbench\state'
   New-Item -ItemType Directory -Force $state, "$state\external-wheels", "$state\assets" | Out-Null
   Copy-Item "$src\.env" "$state\.env"
   if (Test-Path "$src\.postgres-owner.env") { Copy-Item "$src\.postgres-owner.env" "$state\.postgres-owner.env" }
   Copy-Item "$src\external-wheels\*.whl" "$state\external-wheels\"
   robocopy "$src\backend\assets" "$state\assets" /E
   ```
   - `.env`의 `AUTH_SECRET_KEY`·`SIMDASH_SECRET_ENC_KEY`는 **반드시 그대로** 가져간다. 바뀌면 모두 다시 로그인해야 하고, 저장된 드라이브 토큰을 다시 등록해야 한다.
   - `.env`에 `ANALYSIS_DB_BACKEND=postgresql`, `AUTH_MODE=password`가 있어야 한다(설치기가 확인).
   - `DATABASE_URL`(앱 계정)과 `.postgres-owner.env`의 `POSTGRES_OWNER_URL`(소유자 계정)이 기존 DB(예: 포트 5434)를 가리키는지 확인한다.
3. **소스 배포 전용 값 정리** (`state\.env`에서 지우거나 비운다. 설치기가 새 위치로 다시 쓴다):
   `WINDOWS_WEB_PORT`, `POSTGRES_BIN`, `SIMDASH_ASSETS_ROOT`, `SIMDASH_SCX_WORK_DIR`.
   드라이브 다운로드 캐시는 새 위치(`state\scx-worker`)에서 다시 채워진다. 업로드 대기열에 남은 항목이 있으면 전환 전에 대기열을 비운다(드라이브 관리 → 업로드 대기열).
4. **설치 설정 파일** `C:\deployment\install.json` (DB 연결 정보는 넣지 않는다. `state\.env`에서 읽는다):
   ```json
   {
     "postgresMode": "existing",
     "webPort": 8080,
     "apiPort": 18080,
     "serverName": "_",
     "tlsMode": "http",
     "serviceAccount": "NT AUTHORITY\\LOCAL SERVICE",
     "installRoot": "C:\\ProgramData\\SimulationWorkbench"
   }
   ```
5. **서비스 계정 권한**: 서비스는 `serviceAccount`로 실행된다. 이 계정이 다음을 읽을 수 있어야 한다.
   - SPDM 폴더(`SIMDASH_SPDM_ROOT`), 드라이브 워커 런타임(`C:\SimDashboard\scx-runtime`), `.env`가 가리키는 CA 번들 파일.
   - SPDM 폴더가 **네트워크 공유**이면 `LOCAL SERVICE`는 접근할 수 없다. 공유 권한이 있는 도메인 계정을 `serviceAccount`로 지정한다.

## 4. 설치

```powershell
Expand-Archive .\simworkbench-windows-offline-<…>.zip -DestinationPath C:\Install
cd C:\Install\simworkbench-windows-offline-<…>
.\install.ps1 -Check                                  # 패키지·OS 확인만 (변경 없음)
.\install.ps1 -Config C:\deployment\install.json      # 실제 설치
```
- `install.cmd`를 더블클릭해도 된다(질문에 답하는 방식). 같은 `installRoot`에 설정이 저장되어 다음부터는 질문하지 않는다.
- 설치기는 기존 DB를 백업·검증하고, 대기 중인 migration만 적용하고, 어댑터 wheel을 설치(`import scx_drive_adapter` 확인)한 뒤 서비스를 시작한다.
- 실패하면 메시지와 `state\recovery-required.json`을 확인한다. DB를 지우거나 초기화해서 우회하지 않는다.

## 5. 확인

| 확인 | 기대 |
|---|---|
| `http://서버:8080/home/` | 로그인 화면, 기존 계정으로 로그인 |
| 의뢰·결과 조회 | 기존 데이터 그대로 |
| 드라이브 관리 → 연결 시험 | 성공 (저장된 토큰 그대로) |
| Windows 재부팅 후 | 서비스 자동 시작(`SimulationWorkbenchApi`, `SimulationWorkbenchProxy`), 같은 주소로 접속 |

- 다른 PC에서 접속이 안 되면 방화벽을 본다. 설치기가 만드는 규칙(`SimulationWorkbenchWeb`)은 **같은 서브넷(LocalSubnet)만** 허용한다. 사용자 PC가 다른 대역이면 기존 소스 배포 때 쓰던 8080 허용 규칙을 그대로 두거나, 그 규칙의 원격 주소 범위를 사내 대역으로 넓힌다.
- 서비스 로그는 `state\logs`에 있다.

## 6. 이후 업데이트

새 Artifacts를 받아 해시 확인 → 압축 풀기 → `install.cmd`(또는 `install.ps1 -Config …`). 저장된 설정을 그대로 쓴다. 서버에서 `git`·`update.bat`은 더 쓰지 않는다. 어댑터 wheel을 바꿀 때는 `state\external-wheels`의 wheel만 교체하고 같은 방법으로 다시 설치한다.

## 7. 되돌리기 (전환이 실패했을 때)

1. 새 서비스 중지: `Get-Service SimulationWorkbench* | Stop-Service`.
2. 소스 폴더에서 `start.bat`으로 기존 배포를 다시 실행한다.
3. 같은 커밋 기준으로 만든 패키지라면 DB 구조가 같아 그대로 동작한다. 설치기가 migration을 적용했는데 소스 배포가 그보다 오래된 버전이면, 소스 배포도 `update.bat`으로 같은 커밋까지 올린다. 설치기의 DB 백업은 `state\backups`에 남아 있다.
