#!/usr/bin/env python3
"""Apply validate_cache/buildlog_check.jsonl to sle15_fix_order.csv.

Adds the column sle_buildlog (what the shipped SLE 15 build really did)
and corrects list_status where the build log proves the tests run.
Hand-read findings are in NOTES (each from reading the %check section
or the whole log of the build named in the row).
"""
import csv
import json
import os

BASE = os.environ.get("ENABLER_DATA") or os.getcwd()   # the data directory
CSV = os.path.join(BASE, "sle15_fix_order.csv")

# Hand-read findings live in the data directory:
# buildlog_overrides.json {"already_enabled": {pkg: evidence}, "notes": {pkg: note}}
_ov = os.path.join(BASE, "buildlog_overrides.json")
_ovd = json.load(open(_ov)) if os.path.exists(_ov) else {}
ALREADY = _ovd.get("already_enabled", {})
NOTES = _ovd.get("notes", {})


def main():
    res = {}
    for l in open(os.path.join(BASE, "validate_cache", "buildlog_check.jsonl")):
        d = json.loads(l)
        res[d["package"]] = d
    rows = list(csv.DictReader(open(CSV)))
    fields = list(rows[0].keys())
    if "sle_buildlog" not in fields:
        fields.append("sle_buildlog")
    changed = []
    for r in rows:
        d = res.get(r["package"], {})
        where = d.get("build", "?")
        summary = {"no_check": "no %check in the shipped build",
                   "check_no_tests": "%check runs no tests",
                   "tests_ran": "tests run",
                   "tests_masked_failures": "tests run, failures masked",
                   "no_log": "no build log found"}.get(d.get("result"), d.get("result", "?"))
        if r["package"] in _ovd.get("summaries", {}):
            summary = _ovd["summaries"][r["package"]]
        r["sle_buildlog"] = f"{summary} ({r['work_package']}, {where})"
        p = r["package"]
        if p in ALREADY and r["list_status"] != "already_enabled":
            changed.append((p, r["list_status"]))
            r["list_status"] = "already_enabled"
            r["validation_note"] = ALREADY[p] + " (SLE build log, 2026-11-23)"
        elif p in NOTES and NOTES[p] not in r["validation_note"]:
            r["validation_note"] = (r["validation_note"] + "; " if r["validation_note"] else "") + NOTES[p]
    with open(CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    from collections import Counter
    print("changed to already_enabled:", changed)
    print(Counter(r["list_status"] for r in rows))
    print(Counter(res.get(r["package"], {}).get("result") for r in rows))


if __name__ == "__main__":
    main()
