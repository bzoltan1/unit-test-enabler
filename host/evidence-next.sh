#!/bin/bash
# The next N packages by priority_rank that have no evidence result yet.
#   host/evidence-next.sh 5
# STATUSES selects the list rows, default "valid weak_reference
# no_distro_reference". already_enabled rows are skipped.
source "$(dirname "$0")/common.sh"
python3 - "${1:-5}" "$data" "$here/results/evidence" "${STATUSES:-valid weak_reference no_distro_reference}" <<'PY'
import csv, os, sys
n, data, res, statuses = int(sys.argv[1]), sys.argv[2], sys.argv[3], set(sys.argv[4].split())
statuses.discard("already_enabled")
done = set(os.listdir(res)) if os.path.isdir(res) else set()
rows = sorted(csv.DictReader(open(f"{data}/sle15_fix_order.csv")),
              key=lambda r: int(r["priority_rank"]))
print(" ".join([r["package"] for r in rows
                if r["package"] not in done
                and r.get("list_status", "valid") in statuses][:n]))
PY
