#!/bin/bash
# Deploy one container per package:  host/run.sh openssl dovecot gvfs
#                          or:       host/run.sh $(host/next-packages.sh 3)
# Settings: enabler.conf, overridable from the environment:
#   SUBMIT_FACTORY [1]  SUBMIT_SLE [0]  PHASES [factory,sle]
#   LOCAL_BUILD_LIMIT [8] REVIEW_ROUNDS [4] OBS_ROUNDS [4] WALL_HOURS [24]
#   BUILD_MEMORY [8192] BUILD_JOBS [4]  CPUS / MEMORY [unset: cgroup limits]
#   COST_LIMIT_USD [0 = none]: stop a package when its priced model cost exceeds it
# The container name te-<pkg> is the claim: podman refuses a second
# container with the same name, so two deployments of one package race
# safely (the second fails).
set -euo pipefail
source "$(dirname "$0")/common.sh"
[ $# -ge 1 ] || { echo "usage: $0 <package>..." >&2; exit 2; }
mkdir -p "$here/results"
LIMIT_ARGS=()
[ -n "${MEMORY:-}" ] && LIMIT_ARGS+=(--memory "$MEMORY" --memory-swap=-1)
[ -n "${CPUS:-}" ] && LIMIT_ARGS+=(--cpus "$CPUS")
for pkg in "$@"; do
  name="te-${pkg//[^A-Za-z0-9_.-]/_}"
  if ! podman run -d --name "$name" --label test-enabler=1 --label package="$pkg" \
    --userns=keep-id:uid=1000,gid=1000 --device /dev/kvm \
    "${LIMIT_ARGS[@]}" "${SECRET_ARGS[@]}" \
    -v "te-work-$pkg:/work" \
    -v "$here/results:/results" \
    -v "$data:/data:ro" \
    "${ENV_ARGS[@]}" \
    -e SUBMIT_FACTORY="${SUBMIT_FACTORY:-1}" -e SUBMIT_SLE="${SUBMIT_SLE:-0}" \
    -e PHASES="${PHASES:-factory,sle}" \
    -e LOCAL_BUILD_LIMIT="${LOCAL_BUILD_LIMIT:-8}" -e REVIEW_ROUNDS="${REVIEW_ROUNDS:-4}" \
    -e OBS_ROUNDS="${OBS_ROUNDS:-4}" -e WALL_HOURS="${WALL_HOURS:-24}" \
    -e BUILD_MEMORY="${BUILD_MEMORY:-8192}" -e BUILD_JOBS="${BUILD_JOBS:-4}" \
    -e BUILD_DISK_MB="${BUILD_DISK_MB:-20000}" -e COST_LIMIT_USD="${COST_LIMIT_USD:-0}" \
    -e XDG_DATA_HOME=/work/.local/share \
    localhost/test-enabler:latest run "$pkg" >/dev/null; then
    echo "$name: not started (already exists? podman rm $name to redeploy)" >&2
    continue
  fi
  # Merge the result into progress.md when the container exits.
  nohup bash -c "podman wait '$name' >/dev/null 2>&1; '$here/host/sync-progress.sh'" \
    >> "$here/results/sync.log" 2>&1 &
  echo "started $name  (podman logs -f $name; results: $here/results/$pkg)"
done
