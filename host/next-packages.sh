#!/bin/bash
# next-packages.sh N: the next N packages by priority_rank that have no
# row in progress.md and no results directory yet. Uses list_status
# from the validated sle15_fix_order.csv:
#   STATUSES (default "valid weak_reference") selects which rows count;
#   already_enabled rows are never returned.
source "$(dirname "$0")/common.sh"
python3 - "${1:-5}" "$data" "$here/results" "${STATUSES:-valid weak_reference}" <<'PY'
import csv, os, re, sys
n, data, res, statuses = int(sys.argv[1]), sys.argv[2], sys.argv[3], set(sys.argv[4].split())
statuses.discard("already_enabled")
done = set(re.findall(r"^\|\s*([^|\s]+)\s*\|", open(f"{data}/progress.md").read(), re.M))
done |= set(os.listdir(res)) if os.path.isdir(res) else set()
rows = sorted(csv.DictReader(open(f"{data}/sle15_fix_order.csv")), key=lambda r: int(r["priority_rank"]))
print(" ".join([r["package"] for r in rows
                if r["package"] not in done and r.get("list_status", "valid") in statuses][:n]))
PY
