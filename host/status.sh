#!/bin/bash
# Overview of all enabler containers and their recorded state.
source "$(dirname "$0")/common.sh"
"$here/host/sync-progress.sh" >/dev/null
podman ps -a --filter label=test-enabler=1 --format '{{.Names}}\t{{.Status}}'
echo
for s in "$here"/results/*/state.json; do
  [ -e "$s" ] || continue
  python3 - "$s" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
ph = s.get("phases", {})
def p(n):
    x = ph.get(n, {})
    return f"{n}:{x.get('outcome') or x.get('status','-')} steps={','.join(x.get('steps_done',[]))} rev={x.get('review_round',0)} obs={x.get('obs_round',0)}"
print(f"{s['package']:24} {s.get('outcome','running'):28} {p('factory')} | {p('sle')} | calls={s.get('agent_calls')}")
PY
done
