#!/bin/bash
# Evidence job overview.
source "$(dirname "$0")/common.sh"
podman ps -a --filter label=evidence=1 --format '{{.Names}}\t{{.Status}}'
echo
python3 - "$here/results/evidence" <<'PY'
import glob, json, os, sys
base = sys.argv[1]
for f in sorted(glob.glob(os.path.join(base, "*", "evidence.json"))):
    try:
        d = json.load(open(f))
    except Exception as e:
        print(f"{os.path.basename(os.path.dirname(f)):20} unreadable: {e}")
        continue
    s = d.get("summary", {})
    keys = ("candidates", "proven", "recurrence_only", "not_caught",
            "not_runnable", "no_update", "inconclusive", "not_a_case")
    parts = " ".join(f"{k}={s.get(k, 0)}" for k in keys if k in s)
    print(f"{d.get('package', '?'):20} rank={d.get('rank', '?'):>4} {parts}")
if not glob.glob(os.path.join(base, "*", "evidence.json")):
    print("no evidence results yet")
PY
