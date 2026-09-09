# Windows 원클릭 업데이트 검증 기록

검증일: 2026-09-09. 대상은 `update.bat`, `update.ps1`, `scripts/windows/GitUpdate.psm1`이며 Windows PowerShell 5.1에서 검증한다.

## 사용자 요청과 구현

기존 ZIP 배포 폴더에서 여러 Git/배포 명령을 입력하던 절차를 `update.bat` 실행 하나로 통합했다. 기존 업데이트기가 없는 폴더에는 BAT 한 파일을 넣어 시작한다. 최초 Git 연결 때만 대상 폴더·브랜치·소스 백업 위치를 확인하고 Enter를 누른다. 이후 업데이트는 현재 브랜치의 upstream을 따른다.

Git 사전 확인과 다운로드를 앱 종료보다 먼저 수행한다. 소스 적용 후 기존 배포·DB migration·시작 도구를 재사용하며, 실패하면 다음 단계로 넘어가지 않는다. 기존 포트와 개인 설정·DB·로컬 실행 기록을 보존한다.

## 검증 도구

| 파일 | 검증 범위 |
|---|---|
| `scripts/windows/git-update-self-test.ps1` | 로컬 bare 저장소, FF/upstream, 수정·분기 거부, ZIP/unborn, 충돌 소스 백업, 한글 경로, 보호 경로, stale lock |
| `scripts/windows/update-entry-self-test.ps1` | 단계 순서, stdout과 종료 코드 분리, Git/stop/apply/deploy/migration/start 실패 중단, 포트, 중복 실행 |
| `scripts/windows/update-bootstrap-self-test.ps1` | BAT 한 파일의 다운로드, 경로 인자 전달, 실행 중 BAT 교체, 원격 driver 누락, 일반 옵션 전달 |
| `scripts/windows/update-integration-self-test.ps1` | 실제 Git·모듈·driver를 통합한 unborn 최초 연결, 다음 FF, 수정 파일 거부, BAT 한 파일과 Enter로 no-.git ZIP 폴더 전체 업데이트 |

최종 결과: 위 4개 스크립트를 각각 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File <스크립트>`로 실행하여 모두 종료 코드 0을 확인했다. 업데이트기 구문 검사와 `git diff --check`도 통과했다. 통합 테스트는 실제 추적 안내 문서를 포함하고, 광범위한 `*.log` ignore 없이 업데이트기 자체의 로그 제외를 검증한다.

모든 테스트는 임시 로컬 저장소와 무해한 stop/deploy/Python/start 대체 프로그램을 사용한다. 실제 원격 저장소에 fetch/push하거나 운영 앱을 종료하지 않는다. 사용자 설정·DB 파일은 테스트용 바이트이며 보존 여부를 해시로 비교한다. bootstrap·integration fixture는 검증용으로 출력된 임시 경로에 남긴다. 이 테스트들을 Windows 배포 CI에도 추가했다.

## 발견하여 수정한 사항

- PowerShell 5.1이 Git의 일반적인 실패 probe(stderr)를 즉시 예외로 바꾸던 문제를 종료 코드 기반으로 처리했다.
- 업데이트 중 만든 잠금 파일과 최초 다운로드용 driver 폴더가 정상 checkout을 dirty 상태로 만들지 않도록 제외했다.
- 자식 PowerShell의 일반 출력을 종료 코드와 분리했다.
- 잠금 파일이 남아도 활성 파일 핸들이 없으면 재시도하도록 했다.
- Git의 한글 파일명 출력과 PS5.1 소스 인코딩을 검증했다.
- 인증서/로그 폴더에 있는 실제 추적 안내 문서는 정확한 경로로 허용하고 개인 파일 보호는 유지했다. 기존 HEAD의 전체 추적 경로도 검사했다.

## 검증 한계와 배포 조건

사내 Git 인증·프록시, 실제 PostgreSQL owner 계정·운영 DB migration, 실제 의존성 다운로드와 서버 재시작은 이 테스트에서 수행하지 않는다. 기존 배포 도구의 검증 기록은 `windows-one-click-deployment.md`를 따른다.

원격 브랜치에 새 BAT·driver·모듈이 반영되어야 사내에서 최초 다운로드할 수 있다. 이 변경은 개발 작업 폴더에서 구현·검증하며 원격 게시 여부와 구분한다.
