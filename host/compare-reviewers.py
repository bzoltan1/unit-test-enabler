#!/usr/bin/env python3
"""Compare the primary reviewer with the shadow reviewers.

  host/compare-reviewers.py           summary per model + results/REVIEWER_COMPARISON.md

Input: every review round that has shadow results
(review-N.shadow-<model>.json / claim-review-N.shadow-<model>.json), from
live runs (SHADOW_REVIEWERS) or replays (host/shadow-replay.sh).

Per model:
- verdict agreement with the primary reviewer; "stricter" = it asked for
  changes (or disputed) where the primary approved (or confirmed);
  "laxer" = the opposite;
- blocker/major findings per review;
- failed reviews (no valid verdict file);
- cost and time per review.
The markdown shows each round's findings side by side, so a person can
judge who found real problems: agreement only measures similarity to the
primary, not quality.
"""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(HERE, "results")
sys.path.insert(0, os.path.join(HERE, "bin"))
import costlib  # noqa: E402

POSITIVE = {"approve", "confirm"}
NEGATIVE = {"changes_requested", "dispute", "reject_impossible"}


def load(p):
    try:
        return json.load(open(p))
    except Exception:
        return None


def duration(path):
    ts = [int(m) / 1000 for m in re.findall(r'"timestamp":(\d{13})', open(path, errors="replace").read())]
    return (max(ts) - min(ts)) if ts else None


def main():
    prices = costlib.load_prices(os.path.join(HERE, "prices.json"))
    stats, md = {}, ["# Reviewer comparison", ""]
    for rdir in sorted(glob.glob(f"{RES}/*/")):
        pkg = os.path.basename(rdir.rstrip("/"))
        st = load(f"{rdir}state.json") or {}
        models = st.get("models", {})
        calls, _ = costlib.package_costs(rdir, prices)
        for shadow_file in sorted(glob.glob(f"{rdir}*/*review-*.shadow-*.json")):
            phase = os.path.basename(os.path.dirname(shadow_file))
            base, who = re.match(r"(.+)\.(shadow-[a-z0-9-]+)\.json$", shadow_file).groups()
            primary = load(base + ".json") or {}
            if not primary:
                continue
            key = (pkg, phase, os.path.basename(base))
            step = re.sub(r"^claim-review-", "claim", re.sub(r"^review-", "review",
                                                               os.path.basename(base)))
            rows = stats.setdefault("__rounds__", {}).setdefault(key, {"primary": primary,
                                                                       "shadows": {}})
            rows["shadows"][who] = load(shadow_file)
            # cost and time of this shadow review
            usd = dur = None
            for c in calls:
                if c["phase"] == phase and c["step"] == step and c["role"] == who:
                    usd = c["usd"]
                    lf = glob.glob(f"{rdir}agent-logs/{c['call']:03d}-{phase}-{step}-{who}.jsonl")
                    dur = duration(lf[0]) if lf else None
            pusd = next((c["usd"] for c in calls if c["phase"] == phase and c["step"] == step
                         and c["role"] == "reviewer"), None)
            for name, model, rv, u in (("primary", models.get("reviewer", "reviewer"), primary, pusd),
                                       (who, models.get(who, who), rows["shadows"][who], usd)):
                s = stats.setdefault(model, {"n": 0, "failed": 0, "agree": 0, "stricter": 0,
                                             "laxer": 0, "bm": 0, "all": 0, "usd": [], "sec": [],
                                             "primary": name == "primary", "keys": set()})
                if key in s["keys"]:
                    continue
                s["keys"].add(key)
                s["n"] += 1
                if u is not None:
                    s["usd"].append(u)
                if name != "primary" and dur:
                    s["sec"].append(dur)
                if not rv or not rv.get("verdict"):
                    s["failed"] += 1
                    continue
                f = rv.get("findings") or []
                s["bm"] += sum(1 for x in f if x.get("severity") in ("blocker", "major"))
                s["all"] += len(f)
                if name != "primary":
                    pv, sv = primary.get("verdict"), rv.get("verdict")
                    if sv == pv:
                        s["agree"] += 1
                    elif pv in POSITIVE and sv in NEGATIVE:
                        s["stricter"] += 1
                    elif pv in NEGATIVE and sv in POSITIVE:
                        s["laxer"] += 1
    rounds = stats.pop("__rounds__", {})
    if not rounds:
        print("no shadow reviews yet: set SHADOW_REVIEWERS for new runs, or "
              "run host/shadow-replay.sh on finished ones")
        return
    hdr = (f"{'model':42} {'reviews':>7} {'failed':>6} {'agree':>6} {'stricter':>8} "
           f"{'laxer':>5} {'B+M/rev':>7} {'all/rev':>7} {'usd/rev':>7} {'min/rev':>7}")
    print(hdr)
    md += ["| model | reviews | failed | agree with primary | stricter | laxer | blocker+major per review "
           "| findings per review | USD per review | minutes per review |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for model, s in sorted(stats.items(), key=lambda kv: not kv[1]["primary"]):
        ok = s["n"] - s["failed"]
        agree = "primary" if s["primary"] else (f"{100 * s['agree'] // ok}%" if ok else "-")
        usd = f"{sum(s['usd']) / len(s['usd']):.2f}" if s["usd"] else "n/a"
        mins = f"{sum(s['sec']) / len(s['sec']) / 60:.1f}" if s["sec"] else "-"
        bm = f"{s['bm'] / ok:.1f}" if ok else "-"
        al = f"{s['all'] / ok:.1f}" if ok else "-"
        print(f"{model[:42]:42} {s['n']:7} {s['failed']:6} {agree:>6} {s['stricter']:8} "
              f"{s['laxer']:5} {bm:>7} {al:>7} {usd:>7} {mins:>7}")
        md.append(f"| {model} | {s['n']} | {s['failed']} | {agree} | {s['stricter']} | {s['laxer']} "
                  f"| {bm} | {al} | {usd} | {mins} |")
    md += ["", "## Rounds", ""]
    for (pkg, phase, name), r in sorted(rounds.items()):
        md += [f"### {pkg} {phase} {name}", ""]
        for who, rv in [("primary", r["primary"])] + sorted(r["shadows"].items()):
            if not rv or not rv.get("verdict"):
                md += [f"- **{who}**: no valid review ({(rv or {}).get('_error', 'no file')[:200]})", ""]
                continue
            md.append(f"- **{who}**: {rv.get('verdict')}: {str(rv.get('summary', ''))[:400]}")
            for f in rv.get("findings") or []:
                if f.get("severity") in ("blocker", "major", "minor"):
                    md.append(f"  - {f.get('severity')}: {str(f.get('problem', ''))[:260]}")
            md.append("")
    with open(f"{RES}/REVIEWER_COMPARISON.md", "w") as fh:
        fh.write("\n".join(md) + "\n")
    print(f"\n{len(rounds)} round(s) compared; details: {RES}/REVIEWER_COMPARISON.md")


if __name__ == "__main__":
    main()
