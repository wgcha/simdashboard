#!/usr/bin/env bash
# Run only in the disposable, pinned Rocky 8.6 build container used by CI.
set -Eeuo pipefail
source /etc/os-release
[[ "${ID:-}:${VERSION_ID:-}:$(uname -m)" == rocky:8.6:x86_64 ]] || {
  printf '%s\n' 'CI builder requires Rocky 8.6 x86_64.' >&2
  exit 1
}
vault='https://dl.rockylinux.org/vault/rocky/8.6'
repo_args=(--disablerepo='*' --releasever=8.6 --setopt=module_platform_id=platform:el8
  --setopt=install_weak_deps=False)
for entry in baseos:BaseOS appstream:AppStream extras:extras; do
  repo_name="rocky86-${entry%%:*}"
  repo_path="${entry#*:}"
  repo_args+=(--repofrompath="${repo_name},${vault}/${repo_path}/x86_64/os/"
    --setopt="${repo_name}.gpgcheck=1"
    --setopt="${repo_name}.gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-Rocky-8")
done
dnf -y "${repo_args[@]}" install git curl tar gzip findutils ca-certificates \
  dnf-plugins-core createrepo_c
git config --global --add safe.directory /src
bash deploy/rocky8/build-offline-release.sh --skip-frontend-build \
  --output /src/dist/simdashboard-offline.tar.gz
