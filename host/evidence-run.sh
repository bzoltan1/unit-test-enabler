#!/bin/bash
# Runs inside the evidence container. Starts the worker, then the reviewer
# audit, then up to two fix rounds. Writes everything under
# /results/evidence/<pkg>/.
set -uo pipefail
export PATH=/opt/enabler/guard:/opt/enabler/bin:$PATH
pkg=${PKG:?PKG is not set}
out=/results/evidence/$pkg
mkdir -p "$out/logs" "$out/patches"
export ENABLER_PHASE_DIR=$out
cd /work || exit 1
if [ -r /opt/enabler/agent/AGENTS_EVIDENCE.md ]; then
  cp /opt/enabler/agent/AGENTS_EVIDENCE.md ~/.config/opencode/AGENTS.md
fi

PROMPT=/opt/enabler/evidence.md
[ -r "$PROMPT" ] || { echo "missing $PROMPT" >&2; exit 64; }

worker() {
  local round=$1 msg=$2
  timeout "${WORKER_SECONDS:-21600}" \
    opencode run --auto --agent worker --dir /work \
      --title "evidence $pkg r$round" "$msg" \
      > "$out/worker-$round.log" 2>&1
  echo "worker round $round rc=$?" > "$out/worker-$round.rc"
}

reviewer() {
  local round=$1 msg=$2
  timeout "${REVIEW_SECONDS:-3600}" \
    opencode run --auto --agent reviewer --dir /work \
      --title "evidence audit $pkg r$round" "$msg" \
      > "$out/reviewer-$round.log" 2>&1
  echo "reviewer round $round rc=$?" > "$out/reviewer-$round.rc"
}

first="Read $PROMPT and do the job for package $pkg. All inputs are in /data.
Write evidence.json, report.md, logs/ and patches/ under $out. Do not submit
anything and do not modify the package branches. The bar is a reproducible
experiment: the test exists in the affected tree, fails on the affected source
for the bug's reason, and passes with the fix. End with EVIDENCE DONE."

worker 1 "$first"
if [ ! -s "$out/evidence.json" ]; then
  echo "no evidence.json after the worker" >&2
  echo "INCOMPLETE" > "$out/status"
  exit 1
fi

for round in 1 2; do
  reviewer "$round" "Read $PROMPT and audit $out/evidence.json and $out/report.md.
Follow the Review section exactly. For every PROVEN case, verify the test
exists in the affected tree, read the affected and fixed logs, and rerun at
least one command if you can. Write $out/review.json with the exact schema in
the prompt, top level key cases, one verdict per case, confirmed, rejected or
needs_more. Update /results/evidence/LEDGER.md under flock. End with AUDIT DONE."
  rejected=$(python3 - "$out" <<'PY'
import json, os, sys
d = sys.argv[1]
try:
    r = json.load(open(os.path.join(d, "review.json")))
except Exception:
    print("1"); raise SystemExit
cases = r.get("cases") or r.get("case_verdicts") or [] if isinstance(r, dict) else r
bad = [c for c in cases if c.get("verdict") in ("rejected", "needs_more")]
print(len(bad))
PY
)
  if [ "$rejected" = "0" ]; then
    break
  fi
  [ "$round" = "2" ] && break
  worker "fix$round" "The reviewer rejected or doubted $rejected case(s) in
$out/review.json. Read it and $PROMPT. Fix a case only with real evidence.
Otherwise downgrade it to INCONCLUSIVE. Keep evidence.json and report.md in
sync. End with EVIDENCE DONE."
done

python3 - "$out" <<'PY'
import json, os, sys
d = sys.argv[1]
ev = json.load(open(os.path.join(d, "evidence.json")))
rv = {}
try:
    r = json.load(open(os.path.join(d, "review.json")))
    cases = r.get("cases") or r.get("case_verdicts") or [] if isinstance(r, dict) else r
    rv = {c.get("bug"): c.get("verdict") for c in cases}
except Exception:
    pass
for c in ev.get("candidates", []):
    v = rv.get(c.get("bug"))
    if c.get("verdict") == "PROVEN" and v != "confirmed":
        c["verdict"] = "INCONCLUSIVE"
        c["reason"] = f"reviewer did not confirm ({v or 'missing'})"
    c["review"] = v or "missing"
json.dump(ev, open(os.path.join(d, "evidence.json"), "w"), indent=1)
counts = {}
for c in ev.get("candidates", []):
    counts[c.get("verdict")] = counts.get(c.get("verdict"), 0) + 1
ev["summary"] = {"candidates": len(ev.get("candidates", []))}
ev["summary"].update({k.lower(): v for k, v in counts.items()})
json.dump(ev, open(os.path.join(d, "evidence.json"), "w"), indent=1)
print(json.dumps(ev["summary"]))
PY
echo "DONE" > "$out/status"
