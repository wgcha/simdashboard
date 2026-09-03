# Rocky Linux 8 deployment runbook

실행 명령과 상세 설정은 [`deploy/rocky8/README.md`](../deploy/rocky8/README.md)를
따른다. 이 문서는 운영 변경 순서와 release gate를 정의한다.

이 문서는 [`adr/0004-canonical-production-deployment-target.md`](adr/0004-canonical-production-deployment-target.md)에서
확정한 유일한 canonical 운영 경로다. Windows 실행·PostgreSQL 이관 절차는
이 runbook의 운영 release 인증 범위가 아니다.

## 배포 전 승인 항목

1. 대상은 Rocky Linux 8.6 이상(8.x)이며 Python 3.12.13, PostgreSQL 18.x 정책을 만족한다. Rocky 9 및 Rocky 이외 OS는 지원 대상이 아니며 installer가 거부한다.
2. PostgreSQL admin/owner/app 역할을 분리하고 app 역할에는 DB/schema CREATE를 주지 않는다.
3. 서비스 DNS, TLS 인증서와 키, 방화벽, SELinux 변경이 승인되어 있다.
4. 운영 인증은 `password` 또는 `oidc`이고 secure cookie와 HTTPS를 사용한다.
5. DB backup과 restore 시험이 완료되어 있다.
6. 인터넷 차단망이면 같은 Rocky minor/CPU 아키텍처에서 `--with-wheels` 번들을 만들었다.
7. 마스터 결과 Refresh를 사용하면 `SIMDASH_IMPORT_ROOT`, service user 읽기 권한, 공유 mount와 SELinux 정책이 승인되어 있다. installer는 이 값을 service EnvironmentFile과 systemd read-only path에 전달하고, `RequiresMountsFor`로 mount 준비 뒤 서비스를 시작하며 설치 시 service user의 재귀 읽기·traverse 권한을 검사한다.

## Release 절차

소스가 사내 서버에 있고 승인된 package mirror/proxy를 사용할 수 있으면 단일 명령
경로를 우선한다. `deploy/rocky8/install.env.example`을
`deploy/rocky8/install.local.env`로 복사하고 실제 값을 입력한 뒤 mode `0600`으로
설정한다. 이 로컬 설정 파일은 Git에서 제외되며 실행 중 root 전용 파일로 복사된다.

```bash
./deploy/rocky8/deploy-from-source.sh --config deploy/rocky8/install.local.env
```

단일 명령은 clean Git 확인 → 고정 Node/Python runtime 준비 → wheel 포함 release
빌드 → checksum 검증 → installer `--check` → 설치·migration → systemd/nginx와
health 확인을 순서대로 강제한다. Rocky 8.6 AppStream에 Python 3.12가 없어도
검증된 전용 Python 3.12.13 runtime을 사용한다. 실제 비밀번호는 root/사용자 전용
`0600` 설정 파일에 둘 수 있지만 Git, 명령행 인자와 로그에는 기록하지 않는다.
Node cache는 사용자 전용 `SIMDASH_NODE_RUNTIME_CACHE`, Python/uv cache는 root 전용
`SIMDASH_PYTHON_RUNTIME_CACHE`로 분리한다. Proxy/CA 값은 Python helper에 환경변수명만
보존해 전달한다. curl은 `CURL_CA_BUNDLE`, uv는 `--system-certs`와 OS trust store
(필요 시 `SSL_CERT_FILE`)를 사용한다.

빌드 장비와 운영 서버가 분리되거나 완전 폐쇄망이면 아래 수동 release 절차를
사용한다.

1. 테스트가 끝난 commit에서 `deploy/rocky8/build-release.sh`를 실행한다.
2. artifact와 `SHA256SUMS`를 변경관리 경로로 대상 서버에 전달한다.
3. root 소유 mode `0600` 설정 파일을 작성한다. owner/admin credential은 서비스
   EnvironmentFile에 복사하지 않는다.
4. `install.sh --check`를 실행해 OS, 설정, 인증서와 artifact를 읽기 전용 검증한다.
5. 배포 직전 custom-format PostgreSQL backup을 만들고 manifest를 검증한다.
6. `install.sh`를 실행한다. installer는 migration → 권한 강화 → app preflight →
   systemd/nginx 검증 → symlink 전환 → health check 순서를 강제한다.
7. public HTTPS URL, 로그인, 역할별 권한, 결과 등록/조회, 감사 이벤트를 smoke test한다.

`SEED_MODE=reference`는 명시적인 최초 예제 설치에서만 사용한다. `reference`와
`demo`는 현재 동일한 결정적 fixture의 별칭이며 운영 업무 데이터가 아니다.

## Release gate

- `/api/health`: `status=ok`, `database_backend=postgresql`
- DB server major: 18
- app connection role: `simdashboard_app`(또는 승인된 `SIM_DASH_APP_ROLE`)
- Alembic revision: code의 단일 head
- app role: DB/schema CREATE 거부, 업무 테이블 CRUD, audit append-only
- worker 수 × request/media pool budget: PostgreSQL usable connections 이내
- nginx config와 systemd unit 검증 통과
- `SIMDASH_IMPORT_ROOT`가 release 외부에 있고 service user가 재귀적으로 읽을 수 있으며 systemd에는 read-only path로 설정됨
- SELinux enforcing 상태와 HTTPS firewalld 정책에서 접근 성공

## 장애와 rollback

서비스 health check 실패 시 installer는 이전 `current` symlink가 있으면 application
code를 복원하고 서비스를 재시작한다. 이는 DB migration을 되돌리지 않는다. 이전
release가 현재 Alembic revision과 호환되지 않으면 서비스를 중지하고 수정 release를
배포한다. DB downgrade 또는 restore는 owner credential과 검증된 backup을 사용하는
별도의 승인 작업이다.
