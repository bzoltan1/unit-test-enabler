#!/bin/bash
# Replay the last review round of finished runs with other reviewer models:
#   SHADOW_REVIEWERS=google-vertex/gemini-3.1-pro,helmcode/glm5.3 host/shadow-replay.sh gvfs dovecot
# Each model gets the primary reviewer's exact prompt against the final
# checkout (te-work-<pkg> volume) and writes review-N.shadow-<model>.json.
# Compare with host/compare-reviewers.py. Runs packages in parallel.
set -euo pipefail
source "$(dirname "$0")/common.sh"
[ -n "${SHADOW_REVIEWERS:-}" ] || { echo "set SHADOW_REVIEWERS" >&2; exit 2; }
[ $# -ge 1 ] || { echo "usage: $0 <package>..." >&2; exit 2; }
for pkg in "$@"; do
  if podman ps --format '{{.Names}}' | grep -qx "te-$pkg"; then
    echo "$pkg: still running, skipped"; continue
  fi
  podman run --rm --userns=keep-id:uid=1000,gid=1000 "${SECRET_ARGS[@]}" \
    -v "te-work-$pkg:/work" -v "$here/results:/results" -v "$data:/data:ro" \
    "${ENV_ARGS[@]}" -e XDG_DATA_HOME=/work/.local/share \
    localhost/test-enabler:latest exec python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('o', '/opt/enabler/bin/orchestrate.py')
o = importlib.util.module_from_spec(spec); spec.loader.exec_module(o)
o.Run('$pkg').replay_shadows()" > "$here/results/$pkg/shadow-replay.log" 2>&1 &
  echo "$pkg: replay started (results/$pkg/shadow-replay.log)"
done
wait
echo "done; compare with host/compare-reviewers.py"
