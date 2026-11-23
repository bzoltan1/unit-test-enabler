#!/bin/bash
# Write learnings.md for finished runs that have none (runs from before
# the learnings step existed):  host/backfill-learnings.sh gvfs dovecot
set -euo pipefail
source "$(dirname "$0")/common.sh"
for pkg in "$@"; do
  [ -e "$here/results/$pkg/learnings.md" ] && { echo "$pkg: has learnings.md"; continue; }
  podman run --rm --userns=keep-id:uid=1000,gid=1000 "${SECRET_ARGS[@]}" \
    -v "te-work-$pkg:/work" -v "$here/results:/results" -v "$data:/data:ro" \
    "${ENV_ARGS[@]}" \
    -e XDG_DATA_HOME=/work/.local/share \
    localhost/test-enabler:latest exec python3 -c "
import importlib.util, json
spec = importlib.util.spec_from_file_location('o', '/opt/enabler/bin/orchestrate.py')
o = importlib.util.module_from_spec(spec); spec.loader.exec_module(o)
r = o.Run('$pkg'); s = r.state
r.learnings(s.get('outcome', '?'), s.get('reason', ''))"
  echo "$pkg: $(head -1 "$here/results/$pkg/learnings.md")"
done
