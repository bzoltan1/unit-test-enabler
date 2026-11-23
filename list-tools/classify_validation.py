#!/usr/bin/env python3
"""Turn validate_cache/validation.jsonl into a verdict per list row."""
import json
import os

BASE = os.environ.get("ENABLER_DATA") or os.getcwd()   # the data directory
SLE_DONE = {"tests", "tests_in_build"}

# Hand-checked findings live in the data directory, not in the code:
# list_overrides.json {"identity_different": [pkg...],
#   "overrides": {pkg: {"verdict", "work_package", "notes": [...]}}}
_ov = os.path.join(BASE, "list_overrides.json")
_ovd = json.load(open(_ov)) if os.path.exists(_ov) else {}
IDENTITY_DIFFERENT = set(_ovd.get("identity_different", []))
OVERRIDES = {k: (v["verdict"], v["work_package"], v["notes"])
             for k, v in _ovd.get("overrides", {}).items()}


def verdict(r):
    flags = []
    work = r["package"]
    if r["wrapper"]:
        real = r.get("real_packages") or []
        if not real:
            return "manual", work, ["wrapper without an identifiable real package"], {}
        work = real[0]
        flags.append(f"wrapper: real source is {', '.join(real)}")
    sle = r["real"].get(work, {}).get("sle_check", r["sle_check"])
    fac = r.get("factory", {})
    fac_tests = [n for n, v in fac.items() if v["check"] in SLE_DONE and not v.get("wrapper")]
    fed = {n: v for n, v in r.get("fedora", {}).items()}
    deb = {n: v for n, v in r.get("debian", {}).items()}
    def same(v):
        if v["same"] == "host_differs":
            return r["package"] not in IDENTITY_DIFFERENT
        return v["same"] is True
    fed_ok = [n for n, v in fed.items() if v["check"] in SLE_DONE and same(v)]
    fed_q = [n for n, v in fed.items() if v["check"] in SLE_DONE and not same(v)]
    deb_x = [n for n, v in deb.items() if v["status"] == "explicit" and same(v)]
    deb_d = [n for n, v in deb.items() if v["status"] == "default" and same(v)]
    deb_q = [n for n, v in deb.items() if v["status"] in ("explicit", "default") and not same(v)]
    deb_no = [n for n, v in deb.items() if v["status"] in ("skipped", "disabled", "masked")]
    ev = {"sle": sle, "factory_tests": fac_tests,
          "fedora": {n: v["check"] for n, v in fed.items()},
          "debian": {n: v["status"] for n, v in deb.items()}}
    if sle in SLE_DONE:
        return "already_enabled_sle", work, flags + [f"SLE {work} %check already runs tests"], ev
    if sle in ("masked", "optional_off"):
        flags.append(f"SLE %check exists but is {sle}")
    if fac_tests:
        flags.append(f"Factory already runs tests ({', '.join(fac_tests)}): backport only")
    if fed_q or deb_q:
        flags.append("upstream identity differs from SLE URL for " +
                     ", ".join([f"fedora:{n}" for n in fed_q] + [f"debian:{n}" for n in deb_q]) +
                     " (check)")
    if deb_no:
        flags.append("Debian skips tests: " + ", ".join(deb_no))
    if fed_ok or deb_x:
        return "valid", work, flags, ev
    if deb_d:
        return "weak_reference", work, flags + [
            "only Debian dh default (runs only if upstream has a test target)"], ev
    if fed_q or deb_q:
        return "manual", work, flags, ev
    return "no_distro_reference", work, flags + [
        "neither Fedora nor Debian runs the tests"], ev


def main():
    out = []
    for l in open(os.path.join(BASE, "validate_cache", "validation.jsonl")):
        r = json.loads(l)
        v, work, flags, ev = verdict(r)
        if r["package"] in OVERRIDES:
            v, work, extra = OVERRIDES[r["package"]]
            flags = [f for f in flags if "identity" not in f and "wrapper without" not in f] + extra
        out.append({"package": r["package"], "priority_rank": r["priority_rank"],
                    "verdict": v, "work_package": work, "flags": flags, "evidence": ev,
                    "sle_url": r.get("sle_url"),
                    "fedora_urls": {n: x["url"] for n, x in r.get("fedora", {}).items()},
                    "debian_homepages": {n: x["homepage"] for n, x in r.get("debian", {}).items()}})
    with open(os.path.join(BASE, "validate_cache", "verdicts.jsonl"), "w") as f:
        for o in out:
            f.write(json.dumps(o) + "\n")
    from collections import Counter
    print(Counter(o["verdict"] for o in out))


if __name__ == "__main__":
    main()
