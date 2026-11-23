#!/usr/bin/env python3
"""Merge results/<pkg>/progress-row.md into progress.md, race-safely.

Containers never write progress.md. Each one writes only its own
results/<pkg>/progress-row.md (atomic rename). This script, run on the
host (automatically after every container exits, and by status.sh):

- takes an exclusive flock on progress.md.lock, so concurrent syncs
  serialise;
- upserts one test-enabler row per package: a row it wrote before
  (it carries "[test-enabler") is replaced, rows written by people are
  never touched, and a new row is appended otherwise;
- writes progress.md.tmp and renames it over progress.md, so a reader
  never sees a half-written file.

Edits by hand while a sync runs are the one case the lock cannot cover
(editors do not take the lock). Edit progress.md only when no sync is
running, or use `host/sync-progress.py --lock-only -- $EDITOR progress.md`.
"""
import fcntl
import glob
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ["ENABLER_DATA"]
PROGRESS = os.path.join(DATA, "progress.md")
LOCK = PROGRESS + ".lock"
MARK = "[test-enabler"
HEADER = ("| package | rank | test history | Factory SR | Factory status | "
          "SLE codestream(s) | SLE MR URL(s) | SLFO PR | tests passed (TW / SLE) "
          "| skips | notes |\n|" + "---|" * 11 + "\n")


def pkg_of(line):
    m = re.match(r"^\|\s*([^|\s]+)\s*\|", line)
    return m.group(1) if m else None


def sync():
    rows = {}
    for f in sorted(glob.glob(os.path.join(HERE, "results", "*", "progress-row.md"))):
        line = open(f).read().strip()
        if line.startswith("|") and pkg_of(line):
            rows[pkg_of(line)] = line + "\n"
    lines = open(PROGRESS).readlines() if os.path.exists(PROGRESS) else []
    if not lines:
        lines = ["# SLE 15 test-enablement progress\n", "\n", HEADER]
    out, seen = [], set()
    for line in lines:
        p = pkg_of(line)
        if p and MARK in line and p in rows:
            if p not in seen:
                out.append(rows[p])
                seen.add(p)
            continue                     # drop older duplicates of our row
        out.append(line)
    if out and not out[-1].endswith("\n"):
        out[-1] += "\n"
    added = [rows[p] for p in rows if p not in seen]
    out += added
    tmp = PROGRESS + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(out)
    if os.path.exists(PROGRESS):
        os.chmod(tmp, os.stat(PROGRESS).st_mode & 0o777)
    os.replace(tmp, PROGRESS)
    return len(seen), len(added)


def main():
    with open(LOCK, "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        if len(sys.argv) > 2 and sys.argv[1] == "--lock-only" and sys.argv[2] == "--":
            sys.exit(subprocess.call(sys.argv[3:]))
        updated, added = sync()
    print(f"progress.md: {updated} rows updated, {added} rows added")


if __name__ == "__main__":
    main()
