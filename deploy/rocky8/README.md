# Rocky Linux 8 설치·배포

이 디렉터리는 Rocky Linux 8용 배포 번들 생성기와 root 설치기를 제공한다.
대상 구조는 `nginx → loopback FastAPI → PostgreSQL 18`이며 Docker와 운영
Node.js 서버를 사용하지 않는다.

## PostgreSQL만 설치되어 있으면 되는가?

아니다. 대상 서버는 최소한 다음 조건을 충족해야 한다.

- Rocky Linux 8.10, `systemd`, root 또는 sudo 권한
- 실행 중이며 접속 가능한 PostgreSQL 18.x
- DNS 이름과 발급된 TLS 인증서/개인키
- PostgreSQL 관리자(최초 DB 구성 시), owner(Alembic), app(평상시 서비스) 접속 정보
- 온라인 설치라면 승인된 DNF/PyPI 저장소, 오프라인이면 사전에 만든 wheel 포함 번들
- password 인증이면 최초 관리자 계정 정보, OIDC면 IdP/client/directory 설정

`install.sh`가 Python 3.12, nginx, curl, SELinux/firewalld 도구를 DNF로 설치할
수 있으므로, 위 저장소에 접근 가능하고 설정 파일·인증서·DB 접속 정보가 준비된
Rocky 8 서버라면 PostgreSQL 외 OS 패키지를 미리 수동 설치할 필요는 없다.
프로젝트 정책은 Python `3.12.13`을 고정하므로 Rocky 저장소의 patch가 다르면
승인된 내부 RPM/runtime을 공급하거나 정책 예외를 명시해야 한다.

## 1. 배포 번들 만들기

온라인 대상 서버용 번들:

```bash
./deploy/rocky8/build-release.sh
```

인터넷이 차단된 대상 서버용 번들은 대상과 같은 Rocky 8/CPU 아키텍처에서 만든다.
이 방식은 Python wheel을 번들에 포함하며, Node.js는 빌드 장비에만 필요하다.
Rocky OS RPM, 조직 CA와 DNF 저장소까지 번들에 포함하는 것은 아니므로 완전한 폐쇄망에서는 승인된 내부 RPM mirror 또는 사전 설치된 OS 패키지가 별도로 필요하다.

```bash
./deploy/rocky8/build-release.sh --with-wheels
```

생성물은 `dist/simdashboard-rocky8-<release-id>.tar.gz`와 전송 검증용
`.tar.gz.sha256`이다. Git 작업 트리가
수정된 상태에서는 기본적으로 생성을 거부한다. 검증 목적의 로컬 변경을 일부러
포함할 때만 `--allow-dirty`를 사용한다.

## 2. 대상 서버 설정 파일 준비

번들을 대상 Rocky 서버로 복사하고 압축을 푼 뒤 설정 예제를 별도 root 전용
파일로 복사한다. 번들 안의 예제 파일을 직접 수정하면 checksum 검증에 실패한다.

압축 해제 전에는 함께 전달된 sidecar checksum도 확인한다.

```bash
sha256sum --check simdashboard-rocky8-<release-id>.tar.gz.sha256
tar -xzf simdashboard-rocky8-<release-id>.tar.gz
cd simdashboard-rocky8-<release-id>
sudo install -o root -g root -m 0600 install.env.example /root/simdashboard-install.env
sudo vi /root/simdashboard-install.env
```

중요한 선택값은 다음과 같다.

- 기존 DB/역할이 준비된 서버: `BOOTSTRAP_DATABASE=0`
- PostgreSQL만 있고 앱 DB/역할이 없는 서버: `BOOTSTRAP_DATABASE=1`과 admin/owner/app 비밀 설정
- 새 빈 운영 DB: `SEED_MODE=empty`
- 명시적인 예제/기준 데이터 설치: `SEED_MODE=reference`
- 사내 비밀번호 인증: `AUTH_MODE=password`, 필요 시 최초 관리자 설정
- 사내 SSO: `AUTH_MODE=oidc`와 OIDC/directory 설정
- Master Result Refresh: `SIMDASH_IMPORT_ROOT`를 기본값으로 사용하거나 승인된
  읽기 전용 NAS/SMB/NFS mount로 바꾼다.

`SIMDASH_IMPORT_ROOT`는 release 폴더 밖의 유일한 import root다. 기본값
`/var/lib/simdashboard/import`은 설치 중 `root:simdashboard`, mode `0750`으로
생성된다. systemd는 이 경로를 read-only로 mount하고, 환경값은
`/etc/simdashboard/simdashboard.env`에만 전달한다. 외부 공유 경로를 지정할 때
installer는 공유의 ownership/mode를 바꾸지 않는다. 대신 service user가 모든
하위 디렉터리를 traverse하고 모든 파일을 읽을 수 있는지 검사한다. 공유 mount는
설치 전 완료되어 있어야 하며 부팅 후에도 service 시작보다 먼저 준비되어야 한다.
비기본 경로가 없으면 installer는 빈 로컬 디렉터리를 만들지 않고 실패한다.
생성되는 systemd unit은 `RequiresMountsFor=SIMDASH_IMPORT_ROOT`를 사용해 fstab
기반 mount가 준비된 뒤 API를 시작한다.

`install.sh --check`는 비기본 import root가 실제 non-symlink directory인지
fail-closed로 확인한다. 이미 service user가 있으면 동일한 재귀 읽기/traverse
검사도 수행하며, 새 서버처럼 account가 아직 없으면 실제 설치가 account 생성 직후
그 검사를 수행한다.

SELinux enforcing 환경에서 기본 local root는 `var_lib_t`로 복원한다. 외부
NFS/SMB mount는 조직의 mount context와 SELinux 정책을 유지하므로, 운영자는
해당 mount에서 `simdashboard` service user의 읽기 권한을 별도로 승인·검증해야
한다. nginx는 import root를 정적 제공하지 않는다.

`POSTGRES_OWNER_URL`, `POSTGRES_ADMIN_URL`, 역할 비밀번호는 설치 단계에서만
사용되고 systemd 서비스 환경파일에는 기록되지 않는다. 평상시 서비스에는
최소 권한 `DATABASE_URL`만 기록된다.

## 3. 점검 후 설치

`--check`는 OS, 설정, 인증서, payload, checksum만 확인하고 시스템을 변경하지 않는다.

```bash
sudo ./install.sh --config /root/simdashboard-install.env --check
sudo ./install.sh --config /root/simdashboard-install.env
```

설치기는 다음 순서로 동작한다.

1. Rocky 8, root 전용 설정 파일, TLS/DB/auth 입력값을 fail-closed로 검사한다.
2. 필요한 OS 패키지와 서비스 계정을 준비한다.
3. `/opt/simdashboard/releases/<release-id>`에 immutable release와 전용 venv를 만든다.
4. 필요 시 PostgreSQL 역할/DB를 만들고 owner 역할로 Alembic과 권한 강화를 수행한다.
5. PostgreSQL 18, app 역할, 최신 revision, DDL 거부, pool budget을 검사한다.
6. root 전용 EnvironmentFile, 읽기 전용 import root, systemd, nginx, SELinux,
   firewalld를 설정한다.
7. `current` symlink를 원자적으로 전환하고 API health check가 성공한 경우에만 완료한다.

상태 확인:

```bash
sudo systemctl status simdashboard nginx
sudo journalctl -u simdashboard -n 100 --no-pager
sudo simdashboard-healthcheck
```

## 업데이트와 rollback

새 bundle을 풀고 동일한 설정으로 `install.sh`를 다시 실행하면 새 release를 만든 뒤
`current`를 전환한다. 설치 직후 health check 실패 시 이전 application symlink는
가능한 경우 자동 복원된다. Alembic migration은 자동 downgrade하지 않는다. 이전
코드가 현재 DB revision과 호환되는지 확인한 뒤에만 수동으로 application release를
되돌린다. DB downgrade/restore는 검증된 백업과 별도 승인 절차로 수행한다.

설치 완료 후 staging 설정 파일에 admin/owner/초기 사용자 비밀번호가 남아 있다면
조직의 비밀관리 정책에 따라 안전하게 회수한다. 서비스 설정은
`/etc/simdashboard/simdashboard.env`, 가변 보고서 템플릿은
`/var/lib/simdashboard/report-templates`, Master Result import source는
`SIMDASH_IMPORT_ROOT`에 보관된다.
