#!/usr/bin/env python3
"""Collect the learnings of finished runs into one inbox for review.

  host/collect-learnings.py                 inbox of runs not reviewed yet
  host/collect-learnings.py --all           inbox of every run
  host/collect-learnings.py --mark-reviewed gvfs dovecot
                                            leave these out of the next inbox

Writes results/LEARNINGS_INBOX.md:
  - a run table (outcome, rounds, cost) to spot expensive or stuck runs
  - all "Reusable lessons" and "Prompt and tooling problems" sections,
    tagged with the package, so repeated lessons stand out
  - per-package facts last

Nothing reaches the containers automatically. Promote what holds up into
$ENABLER_DATA/LESSONS_CONTAINERS.md (or fix the prompt / AGENTS.md /
tools), then run host/build-image.sh: every LESSONS*.md and
*_learnings.md in the data directory goes into the image.
"""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(HERE, "results")
REVIEWED = os.path.join(RES, ".learnings-reviewed")
sys.path.insert(0, os.path.join(HERE, "bin"))
import costlib  # noqa: E402


def sections(text):
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.*)", line)
        if m:
            cur = m.group(1).strip()
            out[cur] = []
        elif cur:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def main():
    if "--mark-reviewed" in sys.argv:
        pk = sys.argv[sys.argv.index("--mark-reviewed") + 1:]
        with open(REVIEWED, "a") as f:
            for p in pk:
                f.write(p + "\n")
        print(f"marked reviewed: {' '.join(pk)}")
        return
    reviewed = set(open(REVIEWED).read().split()) if os.path.exists(REVIEWED) else set()
    prices = costlib.load_prices(os.path.join(HERE, "prices.json"))
    runs = []
    for d in sorted(glob.glob(os.path.join(RES, "*", "state.json"))):
        rdir = os.path.dirname(d)
        pkg = os.path.basename(rdir)
        st = json.load(open(d))
        if not st.get("finished"):
            continue
        if pkg in reviewed and "--all" not in sys.argv:
            continue
        lf = os.path.join(rdir, "learnings.md")
        txt = open(lf).read() if os.path.exists(lf) else ""
        _, tot = costlib.package_costs(rdir, prices)
        ph = st.get("phases", {})
        runs.append({"pkg": pkg, "outcome": st.get("outcome"), "secs": sections(txt),
                     "has": bool(txt), "usd": tot["usd"], "unpriced": tot["unpriced_tokens"],
                     "calls": st.get("agent_calls", 0),
                     "rounds": "/".join(f"{ph.get(p, {}).get('review_round', 0)}r"
                                        f"{ph.get(p, {}).get('obs_round', 0)}b"
                                        for p in ("factory", "sle"))})
    L = ["# Learnings inbox", "",
         f"{len(runs)} finished run(s) not yet reviewed. Promote what holds up into "
         "`$ENABLER_DATA/LESSONS_CONTAINERS.md` or fix the prompt/tools, rebuild the "
         "image, then `host/collect-learnings.py --mark-reviewed <pkg>...`.", "",
         "## Runs", "",
         "| package | outcome | agent calls | rounds factory/sle (review r, build b) | "
         "priced USD | unpriced Mtok | learnings |", "|---|---|---|---|---|---|---|"]
    for r in runs:
        L.append(f"| {r['pkg']} | {r['outcome']} | {r['calls']} | {r['rounds']} | "
                 f"{r['usd']:.2f} | {r['unpriced'] / 1e6:.1f} | "
                 f"{'yes' if r['has'] else 'MISSING'} |")
    for title in ("Prompt and tooling problems", "Reusable lessons",
                  "Reviewer findings you agreed with", "Package facts", "Run facts (orchestrator)"):
        L += ["", f"## {title}", ""]
        n = 0
        for r in runs:
            body = r["secs"].get(title, "").strip()
            if body and body.lower() != "none":
                L += [f"### {r['pkg']}", "", body, ""]
                n += 1
        if not n:
            L.append("none")
    out = os.path.join(RES, "LEARNINGS_INBOX.md")
    with open(out + ".tmp", "w") as f:
        f.write("\n".join(L) + "\n")
    os.replace(out + ".tmp", out)
    print(f"{out}: {len(runs)} run(s)")


if __name__ == "__main__":
    main()
