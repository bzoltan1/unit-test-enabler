#!/usr/bin/env python3
"""Check every list row against the real SLE 15 build log.

For each row's work_package:
1. find the build that produced the shipped SLE 15 SP7 binaries:
   - SUSE:SLE-15-SP7:Update/<pkg> links to <base>.<incident>; the build
     is in SUSE:Maintenance:<incident>, package <base>.SUSE_SLE-15-SPx_Update;
   - otherwise the GA build (SUSE:SLE-15-SP7:GA ... SUSE:SLE-15:GA);
2. take the x86_64 log (all multibuild flavours), cut the %check
   section (Executing(%check) up to "Processing files"), and classify:
   no_check | check_no_tests | tests_ran | tests_masked_failures | no_log
Evidence goes to validate_cache/buildlog_check.jsonl.
"""
import csv
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.environ.get("ENABLER_DATA") or os.getcwd()   # the data directory
C = os.path.join(BASE, "validate_cache", "logs")
os.makedirs(C, exist_ok=True)
IBS = os.environ["IBS_API"]
SLE_UPDATE_PRJ = os.environ.get("SLE_UPDATE_PRJ", "SUSE:SLE-15-SP7:Update")
TAIL = 6 * 1024 * 1024        # %check is near the end; fetch the last 6 MB

EVIDENCE = re.compile(
    r"(\b\d+ (tests? )?passed\b|\bpassed[,:]? \d+|^# ?PASS:\s+\d+|^# ?TOTAL:\s+\d+|"
    r"\bRan \d+ tests?\b|\d+% tests passed|tests? passed|All \d* ?tests (successful|passed)|"
    r"All tests successful|^Ok:\s+\d+|\bOK \(\d+ tests?\)|Tests run: \d+|"
    r"\d+ examples?, \d+ failures?|test result: ok\.|^ok\s+\S+\s+[\d.]+s|"
    r"^PASS: |^ok \d+ |\d+ assertions|^=+ \d+ passed|Result: PASS|"
    r"\b\d+ tests? (ok|succeeded|run)\b|\bTests: \d+|^PASSED|testsuite summary|"
    r"\[\s*PASSED\s*\]|\d+/\d+ Test +#\d+|^\s*\d+/\d+ \S+\s+OK\b|^Fail:\s+\d+)", re.I | re.M)
FAIL = re.compile(r"(^FAIL: |^# ?FAIL:\s+[1-9]|\b[1-9]\d* failed\b|tests? failed|^not ok \d+|"
                  r"^--- FAIL|FAILED \(|Result: FAIL|^\s*\d+/\d+ \S+\s+FAIL\b|^Fail:\s+[1-9])", re.I | re.M)


def osc(*a, timeout=120):
    try:
        r = subprocess.run(["osc", "-A", IBS] + list(a), capture_output=True, text=True,
                           errors="replace", timeout=timeout)
        return r.returncode, r.stdout
    except subprocess.TimeoutExpired:
        return 124, ""


OKCODES = ("succeeded", "locked")


def results(prj, pkg):
    rc, out = osc("api", f"/build/{prj}/_result?package={pkg}&arch=x86_64&multibuild=1&locallink=1")
    if rc != 0:
        return []
    rows = []
    for r in ET.fromstring(out).findall("result"):
        for s in r.findall("status"):
            if r.get("repository", "").startswith("SUSE_Updates_"):
                continue            # release targets, not builds
            rows.append((r.get("repository"), s.get("package"), s.get("code")))
    return rows


def locate(pkg):
    """[(project, repository, package)] builds of the shipped version."""
    rc, ls = osc("ls", SLE_UPDATE_PRJ, pkg)
    m = re.search(r"^# -> (\S+) (\S+)\.(\d+)", ls, re.M)
    if m:
        base, inc = m.group(2), m.group(3)
        rows = [r for r in results(f"SUSE:Maintenance:{inc}", f"{base}.")
                if r[2] in OKCODES] or []
        if not rows:
            # package filter needs the exact name: list the incident instead
            rc, pl = osc("ls", f"SUSE:Maintenance:{inc}")
            names = [n for n in pl.split() if n.startswith(base + ".SUSE_SLE-15")]
            rows = []
            for n in sorted(names, reverse=True):
                rows = [r for r in results(f"SUSE:Maintenance:{inc}", n) if r[2] in OKCODES]
                if rows:
                    break
        if rows:
            return [(f"SUSE:Maintenance:{inc}", r[0], r[1]) for r in rows], f"incident {inc}"
    # GA: a package inherited from an older SP shows "locked" without a
    # log; walk back to the project that really built it.
    for sp in ("SP7", "SP6", "SP5", "SP4", "SP3", "SP2", "SP1", ""):
        prj = f"SUSE:SLE-15-{sp}:GA" if sp else "SUSE:SLE-15:GA"
        rows = [r for r in results(prj, pkg) if r[2] in OKCODES and r[0] == "standard"]
        built = [(prj, r[0], r[1]) for r in rows if has_log(prj, r[0], r[1])]
        if built:
            return built, prj
    return [], "not found"


def has_log(prj, repo, pkg):
    rc, _ = osc("api", f"/build/{prj}/{repo}/x86_64/{pkg}/_log?view=entry")
    return rc == 0


def fetch_log(prj, repo, pkg):
    cp = os.path.join(C, re.sub(r"[^A-Za-z0-9._-]", "_", f"{prj}-{repo}-{pkg}.log"))
    if os.path.exists(cp):
        txt = open(cp, errors="replace").read()
        if "Executing(%install)" in txt or "Executing(%build)" in txt or "_complete_" in cp:
            return txt
    path = f"/build/{prj}/{repo}/x86_64/{pkg}/_log"
    rc, ent = osc("api", f"{path}?view=entry")
    size = 0
    if rc == 0:
        m = re.search(r'size="(\d+)"', ent)
        size = int(m.group(1)) if m else 0
    start = max(0, size - TAIL)
    rc, txt = osc("api", f"{path}?nostream=1&start={start}", timeout=300)
    if rc != 0:
        return None
    if start > 0 and "Executing(%install)" not in txt:
        # The window did not reach back to %install: take the whole log.
        rc, txt = osc("api", f"{path}?nostream=1", timeout=900)
        if rc != 0:
            return None
    open(cp, "w").write(txt)
    return txt


def check_section(log):
    i = log.find("Executing(%check)")
    if i < 0:
        return None
    j = log.find("Processing files:", i)
    sec = log[i:j if j > 0 else len(log)]
    return re.sub(r"(?m)^\[\s*\d+s\] ", "", sec)     # OBS timestamps


def classify(sections):
    if not sections:
        return "no_check", ""
    ev = [m.group(0).strip() for s in sections for m in EVIDENCE.finditer(s)]
    fl = [m.group(0).strip() for s in sections for m in FAIL.finditer(s)]
    lines = sum(s.count("\n") for s in sections)
    if ev:
        return ("tests_masked_failures" if fl else "tests_ran"), \
            f"{len(ev)} evidence lines, e.g. {ev[-1][:100]!r}; {len(fl)} failure lines" + \
            (f", e.g. {fl[0][:80]!r}" if fl else "") + f"; %check {lines} lines"
    return "check_no_tests", f"%check ran, {lines} lines, no test result line"


def run(row):
    pkg = row["work_package"]
    builds, where = locate(pkg)
    out = {"package": row["package"], "work_package": pkg,
           "priority_rank": int(row["priority_rank"]),
           "list_status": row["list_status"], "build": where,
           "logs": [f"{p} {r} {k}" for p, r, k in builds]}
    if not builds:
        out["result"], out["detail"] = "no_log", where
        return out
    secs, got = [], 0
    for prj, repo, k in builds[:6]:
        log = fetch_log(prj, repo, k)
        if log is None:
            continue
        got += 1
        s = check_section(log)
        if s:
            secs.append(s)
    if not got:
        out["result"], out["detail"] = "no_log", "log fetch failed"
        return out
    out["result"], out["detail"] = classify(secs)
    return out


def main():
    rows = [r for r in csv.DictReader(open(os.path.join(BASE, "sle15_fix_order.csv")))]
    if len(sys.argv) > 1:
        rows = [r for r in rows if r["package"] in sys.argv[1:]]
    res = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(run, r): r["package"] for r in rows}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                res.append(f.result())
            except Exception as e:
                res.append({"package": futs[f], "result": "error", "detail": repr(e)})
            if i % 20 == 0:
                print(f"{i}/{len(rows)}", file=sys.stderr)
    res.sort(key=lambda x: x.get("priority_rank", 9999))
    name = "buildlog_check.jsonl" if len(sys.argv) == 1 else "buildlog_check-sample.jsonl"
    with open(os.path.join(BASE, "validate_cache", name), "w") as f:
        for r in res:
            f.write(json.dumps(r) + "\n")
    from collections import Counter
    print(Counter(r["result"] for r in res))


if __name__ == "__main__":
    main()
