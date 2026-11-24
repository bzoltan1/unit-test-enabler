#!/bin/bash
# Evidence job overview.
source "$(dirname "$0")/common.sh"
podman ps -a --filter label=evidence=1 --format '{{.Names}}\t{{.Status}}'
echo
python3 - "$here/results/evidence" "$data" <<'PY'
import csv, glob, json, os, sys
base, data = sys.argv[1], sys.argv[2]
ranks = {}
try:
    ranks = {r["package"]: r["priority_rank"] for r in
             csv.DictReader(open(os.path.join(data, "sle15_fix_order.csv")))}
except Exception:
    pass
for f in sorted(glob.glob(os.path.join(base, "*", "evidence.json"))):
    try:
        d = json.load(open(f))
    except Exception as e:
        print(f"{os.path.basename(os.path.dirname(f)):20} unreadable: {e}")
        continue
    s = d.get("summary", {})
    keys = ("proven", "recurrence_only", "no_test", "not_caught",
            "not_runnable", "no_update", "not_a_case", "inconclusive")
    pkg = d.get("package", "?")
    rank = ranks.get(pkg, d.get("rank", "?"))
    nr = len(d.get("not_reached") or [])
    parts = " ".join(f"{k}={s.get(k, 0)}" for k in keys)
    print(f"{pkg:20} rank={str(rank):>4} candidates={s.get('candidates', 0):>3} "
          f"{parts}" + (f" not_reached={nr}" if nr else ""))
if not glob.glob(os.path.join(base, "*", "evidence.json")):
    print("no evidence results yet")
PY
