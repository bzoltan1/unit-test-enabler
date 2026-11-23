#!/usr/bin/python3
"""Token and cost accounting from opencode JSON event logs.

Every opencode step writes a step_finish event with token counts and the
cost opencode computed (non-zero only for models with a known price).
The model comes from the agent role in the log file name
(NNN-<phase>-<step>-<worker|reviewer>.jsonl) and the models recorded in
state.json.
"""
import glob
import json
import os
import re

DEFAULT_MODELS = {"worker": "helmcode/deepseek-v4-flash",
                  "reviewer": "google-vertex/claude-opus-5-5@default"}


def load_prices(path):
    try:
        return {k: v for k, v in json.load(open(path)).items() if not k.startswith("_")}
    except FileNotFoundError:
        return {}


def scan_log(path):
    t = {"steps": 0, "input": 0, "output": 0, "reasoning": 0,
         "cache_read": 0, "cache_write": 0, "opencode_cost": 0.0}
    for line in open(path, errors="replace"):
        if '"step_finish"' not in line:
            continue
        try:
            p = json.loads(line)["part"]
        except Exception:
            continue
        tok = p.get("tokens") or {}
        cache = tok.get("cache") or {}
        t["steps"] += 1
        t["input"] += tok.get("input", 0) or 0
        t["output"] += tok.get("output", 0) or 0
        t["reasoning"] += tok.get("reasoning", 0) or 0
        t["cache_read"] += cache.get("read", 0) or 0
        t["cache_write"] += cache.get("write", 0) or 0
        t["opencode_cost"] += p.get("cost", 0) or 0
    return t


def price(model, t, prices):
    """(usd or None, source)."""
    pr = prices.get(model, {})
    if pr.get("use_opencode_cost") or (not pr and t["opencode_cost"] > 0):
        return t["opencode_cost"], "opencode"
    keys = ("input", "output", "cache_read", "cache_write")
    if pr and all(pr.get(k) is not None for k in keys):
        usd = ((t["input"] + t["reasoning"]) * pr["input"] + t["output"] * pr["output"] +
               t["cache_read"] * pr["cache_read"] + t["cache_write"] * pr["cache_write"]) / 1e6
        return usd, "prices.json"
    return None, "no price"


def package_costs(rdir, prices):
    """Per-call rows and totals for one results/<pkg> directory."""
    models = dict(DEFAULT_MODELS)
    try:
        st = json.load(open(os.path.join(rdir, "state.json")))
        models.update(st.get("models") or {})
    except Exception:
        pass
    calls, total = [], {"usd": 0.0, "unpriced_tokens": 0, "by_model": {}}
    for f in sorted(glob.glob(os.path.join(rdir, "agent-logs", "*.jsonl"))):
        m = re.match(r"(\d+)-([a-z]+)-(.+)-(worker|reviewer|shadow-[a-z0-9-]+)\.jsonl$", os.path.basename(f))
        if not m:
            continue
        role = m.group(4)
        model = models.get(role, role)
        t = scan_log(f)
        usd, src = price(model, t, prices)
        calls.append({"call": int(m.group(1)), "phase": m.group(2), "step": m.group(3),
                      "role": role, "model": model, "usd": usd, "price_source": src, **t})
        bm = total["by_model"].setdefault(model, {"usd": 0.0, "priced": True, "input": 0,
                                                  "output": 0, "cache_read": 0, "cache_write": 0,
                                                  "steps": 0})
        for k in ("input", "output", "cache_read", "cache_write", "steps"):
            bm[k] += t[k]
        if usd is None:
            bm["priced"] = False
            total["unpriced_tokens"] += t["input"] + t["output"] + t["cache_read"] + t["cache_write"]
        else:
            bm["usd"] += usd
            total["usd"] += usd
    return calls, total
