#!/bin/bash
# Merge per-package result rows into progress.md (locked, atomic).
set -euo pipefail
source "$(dirname "$0")/common.sh"
exec python3 "$here/host/sync-progress.py" "$@"
