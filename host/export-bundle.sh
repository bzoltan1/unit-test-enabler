#!/bin/bash
# Pack everything another engineer needs into one tarball:
#   host/export-bundle.sh [/path/to/test-enabler-YYYYMMDD.tar.gz]
# Excluded: enabler.conf (your paths), results/, the prompt and lessons
# copies (they come from the data directory), caches. No credentials are
# in the project, and the bundle is scanned to make sure.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
out=${1:-/tmp/opencode/test-enabler-$(date +%Y%m%d).tar.gz}
mkdir -p "$(dirname "$out")"
tar -C "$(dirname "$here")" -czf "$out" \
  --exclude=test-enabler/enabler.conf --exclude=test-enabler/results \
  --exclude='test-enabler/agent/prompts/*.md' --exclude='test-enabler/agent/lessons/*.md' \
  --exclude=__pycache__ --exclude='*.pyc' "$(basename "$here")"
if tar -xzOf "$out" | grep -aqE -- '-----BEGIN [A-Z ]*PRIVATE KEY-----|"apiKey"\s*:\s*"[A-Za-z0-9]'; then
  rm -f "$out"; echo "credential-like content found; bundle removed" >&2; exit 1
fi
echo "$out ($(du -h "$out" | cut -f1)). Recipient: unpack, cp enabler.conf.example enabler.conf, edit, ./setup.sh --smoke"
