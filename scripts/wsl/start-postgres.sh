#!/usr/bin/env bash
set -euo pipefail

for command in pg_lsclusters pg_isready pg_ctlcluster; do
  command -v "${command}" >/dev/null 2>&1 || {
    echo "필수 PostgreSQL 도구를 찾을 수 없습니다: ${command}. postgresql-common 및 PostgreSQL 18을 설치하세요." >&2
    exit 1
  }
done

cluster="$(pg_lsclusters --no-header | awk '$1 == "18" && $2 == "main" { print $1 " " $2 " " $3 " " $4; exit }')"
[[ -n "${cluster}" ]] || {
  echo "PostgreSQL 18 main cluster를 찾지 못했습니다. sudo pg_createcluster 18 main 후 다시 실행하세요." >&2
  exit 1
}
read -r version name port status <<<"${cluster}"
[[ "${port}" == "5432" ]] || {
  echo "PostgreSQL 18 main cluster가 예상 포트 5432가 아닌 ${port}를 사용합니다." >&2
  exit 1
}

if pg_isready --host=127.0.0.1 --port=5432 >/dev/null 2>&1; then
  pg_isready --host=127.0.0.1 --port=5432
  exit 0
fi

if [[ "${status}" == "down" ]]; then
  sudo pg_ctlcluster "${version}" "${name}" start
else
  echo "PostgreSQL 18 main 상태가 ${status}이지만 127.0.0.1:5432로 준비되지 않았습니다." >&2
  exit 1
fi

pg_isready --host=127.0.0.1 --port=5432
