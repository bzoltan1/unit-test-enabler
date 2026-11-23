#!/usr/bin/env python3
"""Cost report of the containerized runs.

  host/costs.py              one line per package, plus totals per model
  host/costs.py gvfs -v      per agent call for one package
  host/costs.py --csv        machine-readable, one row per package

Prices: prices.json (USD per 1M tokens). Models marked use_opencode_cost
use the cost opencode logged (models.dev list prices). This is a list-price
estimate; the provider invoices are authoritative.
"""
import csv
import glob
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "bin"))
import costlib  # noqa: E402


def fmt_usd(v):
    return "   n/a" if v is None else f"{v:7.2f}"


def mtok(n):
    return f"{n / 1e6:6.2f}M"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    verbose, as_csv = "-v" in sys.argv, "--csv" in sys.argv
    prices = costlib.load_prices(os.path.join(HERE, "prices.json"))
    dirs = sorted(d for d in glob.glob(os.path.join(HERE, "results", "*"))
                  if os.path.isdir(os.path.join(d, "agent-logs")))
    if args:
        dirs = [d for d in dirs if os.path.basename(d) in args]
    grand, models, rows = 0.0, {}, []
    for d in dirs:
        pkg = os.path.basename(d)
        calls, tot = costlib.package_costs(d, prices)
        tok = sum(c["input"] + c["output"] + c["cache_read"] + c["cache_write"] for c in calls)
        rows.append((pkg, len(calls), tok, tot))
        grand += tot["usd"]
        for m, v in tot["by_model"].items():
            g = models.setdefault(m, {"usd": 0.0, "priced": True, "input": 0, "output": 0,
                                      "cache_read": 0, "cache_write": 0})
            for k in ("usd", "input", "output", "cache_read", "cache_write"):
                g[k] += v[k]
            g["priced"] &= v["priced"]
        if verbose:
            print(f"\n{pkg}")
            print(f"  {'call':>4} {'phase':8} {'step':14} {'role':8} {'in':>8} {'out':>8} "
                  f"{'cache rd':>9} {'usd':>7}")
            for c in calls:
                print(f"  {c['call']:4} {c['phase']:8} {c['step'][:14]:14} {c['role']:8} "
                      f"{mtok(c['input'])} {mtok(c['output'])} {mtok(c['cache_read']):>9} "
                      f"{fmt_usd(c['usd'])}")
    if as_csv:
        w = csv.writer(sys.stdout)
        w.writerow(["package", "agent_calls", "tokens", "usd_priced", "unpriced_tokens"])
        for pkg, n, tok, tot in rows:
            w.writerow([pkg, n, tok, f"{tot['usd']:.4f}", tot["unpriced_tokens"]])
        return
    print(f"\n{'package':24} {'calls':>5} {'tokens':>8} {'usd':>8}  unpriced")
    for pkg, n, tok, tot in rows:
        un = mtok(tot["unpriced_tokens"]) if tot["unpriced_tokens"] else ""
        print(f"{pkg:24} {n:5} {mtok(tok)} {tot['usd']:8.2f}  {un}")
    print(f"\nper model:")
    for m, g in models.items():
        price_txt = f"${g['usd']:.2f}" if g["priced"] else "no price (set it in prices.json)"
        print(f"  {m:42} in {mtok(g['input'])} out {mtok(g['output'])} "
              f"cache rd {mtok(g['cache_read'])} wr {mtok(g['cache_write'])}  {price_txt}")
    print(f"\ntotal priced: ${grand:.2f}  (list-price estimate; packages: {len(rows)})")


if __name__ == "__main__":
    main()
