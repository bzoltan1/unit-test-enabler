#!/bin/bash
# Start an evidence job for one package:
#   host/run-evidence.sh gvfs
# It proves, or disproves, that the unit test we would enable would have
# caught a shipped bug. One container per package, results in
# results/evidence/<pkg>/.
set -euo pipefail
source "$(dirname "$0")/common.sh"
[ $# -ge 1 ] || { echo "usage: $0 <package>" >&2; exit 2; }
pkg=$1
case "$pkg" in *[!A-Za-z0-9_.+-]*) echo "bad package name" >&2; exit 2;; esac
name="te-evidence-$pkg"
out="$here/results/evidence/$pkg"
[ -r "$data/EVIDENCE_PROMPT.md" ] || { echo "missing $data/EVIDENCE_PROMPT.md" >&2; exit 1; }
mkdir -p "$out"
if podman container exists "$name"; then
  echo "$name already exists (podman rm $name to restart)" >&2; exit 1
fi
podman run -d --name "$name" --label test-enabler=1 --label evidence=1 \
  --userns=keep-id:uid=1000,gid=1000 --device /dev/kvm \
  "${SECRET_ARGS[@]}" "${ENV_ARGS[@]}" \
  -v "te-evidence-$pkg:/work" \
  -v "$here/results:/results" \
  -v "$data:/data:ro" \
  -v "$data/EVIDENCE_PROMPT.md:/opt/enabler/evidence.md:ro" \
  -v "$here/host/evidence-run.sh:/opt/enabler/evidence-run.sh:ro" \
  -e PKG="$pkg" -e XDG_DATA_HOME=/work/.local/share \
  -e LOCAL_BUILD_LIMIT="${LOCAL_BUILD_LIMIT:-8}" \
  -e BUILD_MEMORY="${BUILD_MEMORY:-8192}" -e BUILD_JOBS="${BUILD_JOBS:-4}" \
  -e WORKER_SECONDS="${WORKER_SECONDS:-21600}" \
  -e REVIEW_SECONDS="${REVIEW_SECONDS:-3600}" \
  localhost/test-enabler:latest exec bash /opt/enabler/evidence-run.sh >/dev/null
echo "started $name"
echo "  logs     podman logs -f $name"
echo "  results  $out"
echo "  status   host/evidence-status.sh"
