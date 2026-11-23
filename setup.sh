#!/bin/bash
# One-command setup of the test-enabler on a new host:
#   ./setup.sh            check prerequisites, create secrets, build image
#   ./setup.sh --smoke    also start a throwaway container and check every
#                         credential, the build VM and both models
# Rerun at any time; every step is idempotent.
set -euo pipefail
cd "$(dirname "$0")"
here=$PWD
ok() { printf '  [ok]   %s\n' "$*"; }
bad() { printf '  [FAIL] %s\n' "$*"; fail=1; }
warn() { printf '  [warn] %s\n' "$*"; }
fail=0

if [ ! -e enabler.conf ]; then
  cp enabler.conf.example enabler.conf
  echo "Created enabler.conf from the example. Edit it (identity, data dir,"
  echo "credential paths), then run ./setup.sh again."
  exit 1
fi
source host/common.sh

echo "== host prerequisites"
[ "$(uname -m)" = x86_64 ] && ok "x86_64 host" || bad "x86_64 host required (build VM and opencode binary)"
if command -v podman >/dev/null; then
  v=$(podman version --format '{{.Client.Version}}')
  if printf '4.3.0\n%s\n' "$v" | sort -V -C; then ok "podman $v"; else bad "podman >= 4.3 needed (have $v)"; fi
  [ "$(podman info --format '{{.Host.Security.Rootless}}')" = true ] && ok "rootless podman" \
    || warn "podman is not rootless; the scripts assume rootless"
else
  bad "podman not installed"
fi
[ -r /dev/kvm ] && [ -w /dev/kvm ] && ok "/dev/kvm read/write" \
  || bad "/dev/kvm not accessible (add your user to the kvm group)"
command -v python3 >/dev/null && ok "python3" || bad "python3 not installed"
cpus=$(nproc); mem=$(awk '/MemTotal/{print int($2/1048576)}' /proc/meminfo)
[ "$cpus" -ge 4 ] && ok "$cpus CPUs" || warn "$cpus CPUs (4+ per parallel container recommended)"
[ "$mem" -ge 16 ] && ok "${mem} GB RAM" || warn "${mem} GB RAM (BUILD_MEMORY + 2 GB per container)"
gr=$(podman info --format '{{.Store.GraphRoot}}' 2>/dev/null || echo "$HOME")
free=$(df -BG --output=avail "$gr" | tail -1 | tr -dc 0-9)
[ "$free" -ge 60 ] && ok "${free} GB free for podman storage ($gr)" \
  || bad "only ${free} GB free in $gr (image 3.4 GB + up to ~25 GB per running container)"

echo "== network (internal hosts may need VPN)"
for u in https://api.opensuse.org "$IBS_API" https://src.opensuse.org "https://$IBS_GITEA" \
         ${INTERNAL_CA_REPO:+"$INTERNAL_CA_REPO"} "${BUGZILLA_URL:-https://bugzilla.suse.com}" \
         https://github.com https://registry.opensuse.org/v2/; do
  code=$(curl -s -o /dev/null -m 15 -w '%{http_code}' "$u" || true)
  [ "$code" != 000 ] && ok "$u ($code)" || bad "$u unreachable"
done

echo "== data directory $data"
for f in sle15_fix_order.csv ENABLE_TESTS_PROMPT.md; do
  [ -r "$data/$f" ] && ok "$f" || bad "$data/$f missing"
done
[ -e "$data/progress.md" ] || { touch "$data/progress.md"; ok "created empty progress.md"; }

echo "== credential files named in enabler.conf"
for v in OSCRC SSH_KEY_IBS SSH_KEY_IBS_GITEA SSH_KEY_OBS_GITEA TEA_CONFIG \
         IBS_GITEA_TOKEN BUGZILLA_KEY OPENCODE_CONFIG; do
  p=${!v:-}
  [ -n "$p" ] && [ -r "$p" ] && ok "$v" || bad "$v: ${p:-unset} not readable"
done
if echo "$WORKER_MODEL,$REVIEWER_MODEL,${SHADOW_REVIEWERS:-}" | grep -q 'google-vertex/'; then
  [ -r "${VERTEX_ADC:-}" ] && ok "VERTEX_ADC" || bad "VERTEX_ADC: ${VERTEX_ADC:-unset} not readable (a google-vertex model is configured)"
fi
for v in SSH_KEY_IBS SSH_KEY_IBS_GITEA SSH_KEY_OBS_GITEA; do
  p=${!v:-}; [ -r "$p" ] || continue
  ssh-keygen -y -P '' -f "$p" >/dev/null 2>&1 && ok "$v has no passphrase" \
    || bad "$v needs a passphrase; the unattended container cannot use it"
done

if [ "$fail" -ne 0 ]; then
  echo; echo "Fix the [FAIL] items above, then rerun ./setup.sh"; exit 1
fi

echo "== skills"
"$here/host/build-skills.sh"

echo "== podman secrets"
"$here/host/create-secrets.sh"

echo "== image"
"$here/host/build-image.sh"

if [ "${1:-}" = --smoke ]; then
  echo "== smoke test (throwaway container)"
  podman run --rm --userns=keep-id:uid=1000,gid=1000 --device /dev/kvm "${SECRET_ARGS[@]}" \
    "${ENV_ARGS[@]}" \
    -e XDG_DATA_HOME=/tmp/xdg \
    localhost/test-enabler:latest exec /opt/enabler/bin/smoke-test
fi
echo
echo "Ready. Deploy with: host/run.sh \$(host/next-packages.sh 2)"
