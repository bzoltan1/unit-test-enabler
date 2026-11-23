#!/usr/bin/env python3
"""How many review rounds did it take to land each change?

  host/review-report.py            table per package/phase + summary
  host/review-report.py -v         every review round
  host/review-report.py --no-live  skip the live request state lookup

Writes results/REVIEW_REPORT.md as well. For every phase it shows:
- the review rounds in order: why the round happened (initial review,
  after build round N, claim check), the verdict and the findings by
  severity (B blocker, M major, m minor, n nit);
- rounds until the first approval, review rounds after build failures,
  build (OBS/IBS) fix rounds;
- time from start to first approval and to the end of the phase;
- reviewer cost;
- the live state of every request/PR (new, review, accepted, declined,
  superseded, merged): "landed" means accepted or merged.
"""
import glob
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(HERE, "results")
sys.path.insert(0, os.path.join(HERE, "bin"))
import costlib  # noqa: E402

E = os.environ
SEV = (("blocker", "B"), ("major", "M"), ("minor", "m"), ("nit", "n"))
VERD = {"approve": "A", "changes_requested": "CR", "reject_impossible": "REJ",
        "confirm": "OK", "dispute": "DISP", None: "?"}


def jl_times(path):
    first = last = None
    for line in open(path, errors="replace"):
        m = re.search(r'"timestamp":(\d{13})', line)
        if m:
            t = int(m.group(1)) / 1000
            first = first or t
            last = t
    return first, last


def sev(findings):
    c = {}
    for f in findings or []:
        c[f.get("severity")] = c.get(f.get("severity"), 0) + 1
    return ",".join(f"{c[k]}{s}" for k, s in SEV if c.get(k))


def rounds(rdir, prices):
    """Review rounds per phase from the agent-log sequence."""
    calls, _ = costlib.package_costs(rdir, prices)
    cost = {(c["call"]): c for c in calls}
    out = {}
    build_round = {}
    for f in sorted(glob.glob(f"{rdir}/agent-logs/*.jsonl")):
        m = re.match(r"(\d+)-([a-z]+)-(.+)-(worker|reviewer|shadow-[a-z0-9-]+)\.jsonl$", os.path.basename(f))
        if not m:
            continue
        n, phase, step, who = int(m.group(1)), m.group(2), m.group(3), m.group(4)
        t0, t1 = jl_times(f)
        ph = out.setdefault(phase, {"rounds": [], "start": None, "end": None})
        if step == "learnings":            # written after the run, not part of it
            continue
        ph["start"] = min(x for x in (ph["start"], t0) if x) if t0 else ph["start"]
        ph["end"] = max(x for x in (ph["end"], t1) if x) if t1 else ph["end"]
        fm = re.match(r"fixobs(\d+)", step)
        if fm:
            build_round[phase] = int(fm.group(1))
        rm = re.match(r"(review|claim)(\d+)$", step)
        if not rm or who != "reviewer":
            continue
        kind = "claim" if rm.group(1) == "claim" else \
            ("initial" if not build_round.get(phase) else f"after build {build_round[phase]}")
        fn = f"{'claim-review' if kind == 'claim' else 'review'}-{rm.group(2)}.json"
        try:
            rv = json.load(open(f"{rdir}/{phase}/{fn}"))
        except Exception:
            rv = {}
        shadows = {}
        for sf in glob.glob(f"{rdir}/{phase}/{fn[:-5]}.shadow*.json"):
            try:
                s = json.load(open(sf))
            except Exception:
                s = {}
            shadows[sf.rsplit(".", 2)[-2]] = s
        c = cost.get(n, {})
        ph["rounds"].append({"call": n, "kind": kind, "verdict": rv.get("verdict"),
                             "sev": sev(rv.get("findings")), "t0": t0, "t1": t1,
                             "usd": c.get("usd"), "shadows": shadows})
    return out


def live_state(url):
    """(state, landed?) for an OBS/IBS request or a Gitea PR URL."""
    try:
        m = re.search(r"https?://([^/]+)/request/show/(\d+)", url)
        if m:
            host, rid = m.groups()
            api = "https://api.opensuse.org" if host == "build.opensuse.org" else E.get("IBS_API")
            if not api:
                return "unknown", False
            r = subprocess.run(["osc", "-A", api, "api", f"/request/{rid}"],
                               capture_output=True, text=True, timeout=60)
            st = re.search(r'<state name="(\w+)"', r.stdout)
            st = st.group(1) if st else "unknown"
            sup = re.search(r'superseded_by="(\d+)"', r.stdout)
            return (f"superseded by {sup.group(1)}" if st == "superseded" and sup else st,
                    st == "accepted")
        m = re.search(r"https?://([^/]+)/([^/]+)/([^/]+)/pulls/(\d+)", url)
        if m:
            host, org, repo, n = m.groups()
            req = urllib.request.Request(f"https://{host}/api/v1/repos/{org}/{repo}/pulls/{n}")
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.load(r)
            st = "merged" if d.get("merged") else d.get("state", "?")
            if d.get("draft") or str(d.get("title", "")).startswith("WIP"):
                st += " (WIP)"
            return st, bool(d.get("merged"))
    except Exception:
        return "unknown", False
    return "not a request URL", False


def hours(a, b):
    if not (a and b):
        return "-"
    d = b - a
    return f"{d / 60:.0f}m" if d < 3600 else f"{d / 3600:.1f}h"


def main():
    verbose, live = "-v" in sys.argv, "--no-live" not in sys.argv
    prices = costlib.load_prices(os.path.join(HERE, "prices.json"))
    rows, md = [], ["# Review rounds per package", "",
                    "Rounds: why, verdict (A approve, CR changes requested, REJ reject, "
                    "OK/DISP claim confirmed/disputed) and findings (B blocker, M major, "
                    "m minor, n nit).", "",
                    "| package | phase | outcome | review rounds | to approval | after build | "
                    "build rounds | time to approval | phase time | reviewer USD | requests |",
                    "|---|---|---|---|---|---|---|---|---|---|---|"]
    for sfile in sorted(glob.glob(f"{RES}/*/state.json")):
        rdir = os.path.dirname(sfile)
        pkg = os.path.basename(rdir)
        st = json.load(open(sfile))
        per = rounds(rdir, prices)
        for phase in ("factory", "sle"):
            ph = st.get("phases", {}).get(phase)
            if not ph:
                continue
            r = per.get(phase, {"rounds": [], "start": None, "end": None})
            rv = [x for x in r["rounds"] if x["kind"] != "claim"]
            initial = [x for x in rv if x["kind"] == "initial"]
            first_ok = next((x for x in rv if x["verdict"] == "approve"), None)
            to_appr = (initial.index(first_ok) + 1) if first_ok in initial else \
                (f">{len(initial)}" if initial else "-")
            seq = " > ".join(f"{VERD.get(x['verdict'], x['verdict'])}"
                             f"{'(' + x['sev'] + ')' if x['sev'] else ''}"
                             f"{'[claim]' if x['kind'] == 'claim' else ''}"
                             f"{'[b' + x['kind'].split()[-1] + ']' if x['kind'].startswith('after') else ''}"
                             for x in r["rounds"]) or "-"
            usd = sum(x["usd"] or 0 for x in r["rounds"])
            try:
                rep = json.load(open(f"{rdir}/{phase}/report.json"))
            except Exception:
                rep = {}
            reqs = []
            for u in rep.get("requests") or []:
                s, landed = live_state(u) if live else ("-", False)
                reqs.append(f"{u} [{s}{', LANDED' if landed else ''}]")
            row = {"pkg": pkg, "phase": phase, "outcome": ph.get("outcome") or ph.get("status"),
                   "seq": seq, "to_appr": to_appr,
                   "after_build": len([x for x in rv if x["kind"].startswith("after")]),
                   "build": ph.get("obs_round", 0),
                   "t_appr": hours(r["start"], first_ok["t1"]) if first_ok else "-",
                   "t_phase": hours(r["start"], r["end"]), "usd": usd, "reqs": reqs,
                   "rounds": r["rounds"]}
            rows.append(row)
            md.append(f"| {pkg} | {phase} | {row['outcome']} | {seq} | {to_appr} | "
                      f"{row['after_build']} | {row['build']} | {row['t_appr']} | "
                      f"{row['t_phase']} | {usd:.2f} | {'<br>'.join(reqs) or '-'} |")
    # terminal
    print(f"{'package':14} {'phase':7} {'outcome':16} {'appr':>4} {'post':>4} {'bld':>3} "
          f"{'t_appr':>7} {'t_phase':>7} {'usd':>6}  rounds")
    for x in rows:
        print(f"{x['pkg'][:14]:14} {x['phase']:7} {str(x['outcome'])[:16]:16} {str(x['to_appr']):>4} "
              f"{x['after_build']:>4} {x['build']:>3} {x['t_appr']:>7} {x['t_phase']:>7} "
              f"{x['usd']:6.2f}  {x['seq']}")
        for q in x["reqs"]:
            print(f"{'':16}request {q}")
        if verbose:
            for rr in x["rounds"]:
                sh = ", ".join(f"{k}:{VERD.get(v.get('verdict'), v.get('verdict'))}"
                               for k, v in rr["shadows"].items())
                print(f"{'':16}call {rr['call']:3} {rr['kind']:14} {VERD.get(rr['verdict'])} "
                      f"{rr['sev'] or '-':12} {hours(rr['t0'], rr['t1']):>6} "
                      f"${rr['usd'] or 0:.2f} {sh}")
    # summary
    changed = [x for x in rows if x["seq"] != "-" and "[claim]" not in x["seq"]]
    first = [x for x in changed if x["to_appr"] == 1]
    nums = [x["to_appr"] for x in changed if isinstance(x["to_appr"], int)]
    landed = sum(1 for x in rows for q in x["reqs"] if "LANDED" in q)
    open_r = sum(1 for x in rows for q in x["reqs"] if "LANDED" not in q)
    summ = [f"phases with a change reviewed: {len(changed)}",
            f"approved in the first round: {len(first)}"
            + (f" ({100 * len(first) // len(changed)}%)" if changed else ""),
            f"mean rounds to approval: {sum(nums) / len(nums):.1f}" if nums else
            "mean rounds to approval: -",
            f"review rounds after build failures: {sum(x['after_build'] for x in rows)}",
            f"requests landed: {landed}, not (yet): {open_r}"]
    print("\n" + "\n".join(summ))
    md += ["", "## Summary", ""] + [f"- {s}" for s in summ]
    with open(f"{RES}/REVIEW_REPORT.md", "w") as f:
        f.write("\n".join(md) + "\n")
    print(f"\n{RES}/REVIEW_REPORT.md")


if __name__ == "__main__":
    main()
