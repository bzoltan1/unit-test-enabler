#!/usr/bin/env python3
"""Status of the requests and PRs the agents created, as people see them.

  host/requests.py                 every request/PR from results/*/*/report.json
  host/requests.py gvfs dovecot    only these packages
  host/requests.py --open          hide landed and closed ones
  host/requests.py --landed        only accepted and merged ones
The list also includes the request and PR links found in progress.md, so
manual and hand-made work shows up too.
  host/requests.py --json          machine-readable

For an OBS/IBS submit or maintenance request: state, every review (user,
group or project, its state and comment), comments, the supersede chain
(followed to the newest request), and the build results of the source
package. For a Gitea PR (src.opensuse.org or $IBS_GITEA): state, merge,
draft/WIP, requested reviewers, reviews (approved / changes requested /
comment), comments and the commit status (CI, staging bot).

Each item gets a verdict:
  LANDED   accepted or merged
  ACTION   declined, changes requested, a person commented after us, or
           the source build fails: someone has to look
  WAITING  open, nothing new
  PLAN     dry run: a submit plan waits for a person (results/<pkg>/<phase>/submit_plan.md)
  CLOSED   revoked, superseded without a successor, closed unmerged
Also writes results/REQUESTS.md.
"""
import glob
import json
import os
import re
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(HERE, "results")
E = os.environ
ME = E.get("OBS_USER", "")
BOTS = re.compile(r"(bot|autogits|factory-auto|licensedigger|maintenance-robot|"
                  r"repo-checker|staging|obs-service|-auto$)", re.I)


def osc_xml(api, path):
    r = subprocess.run(["osc", "-A", api, "api", path], capture_output=True, text=True,
                       timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-200:])
    return ET.fromstring(r.stdout)


def gitea_token(host):
    """Token for host from the tea config or IBS_GITEA_TOKEN (optional)."""
    if host == E.get("IBS_GITEA") and E.get("IBS_GITEA_TOKEN"):
        p = os.path.expanduser(os.path.expandvars(E["IBS_GITEA_TOKEN"]))
        if os.path.exists(p):
            return open(p).read().strip()
    try:
        import yaml
        cfg = yaml.safe_load(open(os.path.expanduser(os.path.expandvars(
            E.get("TEA_CONFIG", "~/.config/tea/config.yml")))))
        for lg in cfg.get("logins", []):
            if host in lg.get("url", ""):
                return lg.get("token")
    except Exception:
        pass
    return None


def gitea(host, path):
    tok = gitea_token(host)
    req = urllib.request.Request(f"https://{host}/api/v1{path}",
                                 headers={"Authorization": f"token {tok}"} if tok else {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def build_summary(api, prj, pkg):
    try:
        x = osc_xml(api, f"/build/{prj}/_result?package={pkg}&multibuild=1&locallink=1")
    except Exception as e:
        return {"error": str(e)[:120]}
    c, bad = {}, []
    for r in x.findall("result"):
        for s in r.findall("status"):
            code = s.get("code")
            if code in ("disabled", "excluded"):
                continue
            c[code] = c.get(code, 0) + 1
            if code in ("failed", "unresolvable", "broken"):
                bad.append(f"{r.get('repository')}/{r.get('arch')}:{code}")
    return {"counts": c, "bad": bad}


def obs_request(url):
    host, rid = re.search(r"https?://([^/]+)/request/show/(\d+)", url).groups()
    api = "https://api.opensuse.org" if host == "build.opensuse.org" else E.get("IBS_API")
    if not api:
        return {"url": url, "verdict": "UNKNOWN", "why": "IBS_API not set"}
    chain, x = [rid], osc_xml(api, f"/request/{rid}?withfullhistory=1")
    while x.find("state").get("name") == "superseded" and x.find("state").get("superseded_by"):
        rid = x.find("state").get("superseded_by")
        chain.append(rid)
        x = osc_xml(api, f"/request/{rid}?withfullhistory=1")
    st = x.find("state")
    created = (x.find("history") if x.find("history") is not None else st).get("when", "")
    reviews = [{"by": r.get("by_user") or r.get("by_group") or
                (f"{r.get('by_project')}/{r.get('by_package')}" if r.get("by_package")
                 else r.get("by_project")),
                "state": r.get("state"), "who": r.get("who"), "when": r.get("when"),
                "comment": (r.findtext("comment") or "").strip()[:300]}
               for r in x.findall("review")]
    comments = []
    try:
        for c in osc_xml(api, f"/comments/request/{rid}").findall("comment"):
            comments.append({"who": c.get("who"), "when": c.get("when"),
                             "text": (c.text or "").strip()[:300]})
    except Exception:
        pass
    act = x.find("action")
    src = act.find("source") if act is not None else None
    builds = build_summary(api, src.get("project"), src.get("package")) \
        if src is not None and src.get("project") else {}
    tgt = act.find("target") if act is not None else None
    if builds.get("bad") and tgt is not None and tgt.get("project"):
        # Failures the target package has too, or repos it does not build,
        # are not caused by this request.
        try:
            tx = osc_xml(api, f"/build/{tgt.get('project')}/_result?package="
                              f"{tgt.get('package') or src.get('package')}&multibuild=1&locallink=1")
            tcode = {f"{r.get('repository')}/{r.get('arch')}": s.get("code")
                     for r in tx.findall("result") for s in r.findall("status")}
        except Exception:
            tcode = {}
        pre = [b for b in builds["bad"] if tcode.get(b.rsplit(":", 1)[0], "disabled")
               in ("failed", "unresolvable", "broken", "disabled", "excluded")]
        builds["pre_existing"] = pre
        builds["bad"] = [b for b in builds["bad"] if b not in pre]
    state = st.get("name")
    mine = [c["when"] for c in comments if c["who"] == ME] + [created]
    human_after = [c for c in comments if c["who"] != ME and not BOTS.search(c["who"] or "")
                   and (c["when"] or "") > max(mine)]
    declined = [r for r in reviews if r["state"] == "declined"]
    if state == "accepted":
        verdict, why = "LANDED", f"accepted by {st.get('who')} {st.get('when', '')[:10]}"
    elif state in ("declined",) or declined:
        verdict = "ACTION"
        why = f"declined by {st.get('who') if state == 'declined' else declined[0]['who']}: " + \
            ((st.findtext("comment") or "").strip() or (declined[0]["comment"] if declined else ""))[:200]
    elif state in ("revoked", "superseded", "deleted"):
        verdict, why = "CLOSED", state
    elif human_after:
        verdict, why = "ACTION", f"comment by {human_after[-1]['who']}: {human_after[-1]['text'][:160]}"
    elif builds.get("bad"):
        verdict, why = "ACTION", "source build failing: " + ", ".join(builds["bad"][:4])
    else:
        open_rev = [r["by"] for r in reviews if r["state"] == "new"]
        verdict, why = "WAITING", ("waiting for " + ", ".join(open_rev)) if open_rev else state
    return {"url": url, "kind": "request", "id": rid, "chain": chain, "state": state,
            "created": created, "reviews": reviews, "comments": comments, "builds": builds,
            "source": f"{src.get('project')}/{src.get('package')}" if src is not None else "",
            "target": (f"{act.find('target').get('project')}/{act.find('target').get('package') or ''}"
                       if act is not None and act.find("target") is not None else ""),
            "verdict": verdict, "why": why}


def gitea_pr(url):
    host, org, repo, n = re.search(r"https?://([^/]+)/([^/]+)/([^/]+)/pulls/(\d+)", url).groups()
    base = f"/repos/{org}/{repo}"
    pr = gitea(host, f"{base}/pulls/{n}")
    reviews = [{"by": r["user"]["login"], "state": r["state"], "when": r.get("submitted_at"),
                "comment": (r.get("body") or "").strip()[:300]}
               for r in gitea(host, f"{base}/pulls/{n}/reviews") if r.get("state") != "PENDING"]
    comments = [{"who": c["user"]["login"], "when": c["created_at"],
                 "text": (c.get("body") or "").strip()[:300]}
                for c in gitea(host, f"{base}/issues/{n}/comments")]
    status = {}
    try:
        st = gitea(host, f"{base}/commits/{pr['head']['sha']}/status")
        status = {"state": st.get("state"),
                  "checks": [f"{s['context']}:{s['status']}" for s in (st.get("statuses") or [])]}
    except Exception:
        pass
    wip = pr.get("draft") or str(pr.get("title", "")).upper().startswith(("WIP", "[WIP]"))
    # Latest review state per reviewer counts.
    latest = {}
    for r in reviews:
        latest[r["by"]] = r
    changes = [r for r in latest.values() if r["state"] == "REQUEST_CHANGES"]
    mine = [c["when"] for c in comments if c["who"] == ME] + [pr.get("created_at") or ""]
    human_after = [c for c in comments if c["who"] != ME and not BOTS.search(c["who"] or "")
                   and c["when"] > max(mine)]
    if pr.get("merged"):
        verdict, why = "LANDED", f"merged by {(pr.get('merged_by') or {}).get('login', '?')} " \
                                 f"{(pr.get('merged_at') or '')[:10]}"
    elif pr.get("state") == "closed":
        verdict, why = "CLOSED", "closed without merge"
    elif changes:
        verdict, why = "ACTION", f"changes requested by {changes[-1]['by']}: {changes[-1]['comment'][:160]}"
    elif human_after:
        verdict, why = "ACTION", f"comment by {human_after[-1]['who']}: {human_after[-1]['text'][:160]}"
    elif status.get("state") in ("failure", "error"):
        verdict, why = "ACTION", "checks failing: " + ", ".join(status.get("checks", [])[:4])
    else:
        req = [u["login"] for u in pr.get("requested_reviewers") or []]
        verdict = "WAITING"
        why = ("WIP; " if wip else "") + (("waiting for " + ", ".join(req)) if req else
                                         f"approved by {', '.join(r['by'] for r in latest.values() if r['state'] == 'APPROVED')}"
                                         if any(r["state"] == "APPROVED" for r in latest.values())
                                         else "no review yet")
    return {"url": url, "kind": "pr", "state": "merged" if pr.get("merged") else pr.get("state"),
            "title": pr.get("title"), "created": pr.get("created_at"), "wip": wip,
            "reviews": reviews, "comments": comments, "status": status,
            "requested": [u["login"] for u in pr.get("requested_reviewers") or []],
            "verdict": verdict, "why": why}


def ledger_urls():
    """Requests and PRs mentioned in progress.md (manual and hand-made work)."""
    data = E.get("ENABLER_DATA")
    path = os.path.join(data, "progress.md") if data else None
    out = []
    if path and os.path.exists(path):
        for line in open(path, errors="replace"):
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            pkg = cells[0] if cells else "?"
            for u in re.findall(r"https?://\S+?(?:request/show|pulls)/\d+", line):
                out.append((pkg, "-", u.rstrip(".,;)")))
    return out


def collect(pkgs):
    items = []
    for rep in sorted(glob.glob(f"{RES}/*/*/report.json")):
        pkg, phase = rep.split("/")[-3], rep.split("/")[-2]
        if pkgs and pkg not in pkgs:
            continue
        try:
            urls = json.load(open(rep)).get("requests") or []
        except Exception:
            continue
        for u in urls:
            items.append((pkg, phase, str(u)))
    return items


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    want = set(args)
    out = []
    seen = set()
    items = collect(want) + [(p, ph, u) for p, ph, u in ledger_urls()
                             if not want or p in want]
    for pkg, phase, url in items:
        if url in seen:
            continue
        seen.add(url)
        try:
            if "/request/show/" in url:
                r = obs_request(url)
            elif "/pulls/" in url:
                r = gitea_pr(url)
            else:
                r = {"url": url, "verdict": "UNKNOWN", "why": "not a request or PR URL"}
        except Exception as e:
            r = {"url": url, "verdict": "UNKNOWN", "why": f"lookup failed: {str(e)[:150]}"}
        r.update(package=pkg, phase=phase)
        out.append(r)
    # Dry-run phases: nothing was submitted, a plan waits for a person.
    for plan in sorted(glob.glob(f"{RES}/*/*/submit_plan.md")):
        pkg, phase = plan.split("/")[-3], plan.split("/")[-2]
        if args and pkg not in args:
            continue
        if not any(r["package"] == pkg and r["phase"] == phase for r in out):
            out.append({"url": plan, "package": pkg, "phase": phase, "verdict": "PLAN",
                        "state": "not submitted",
                        "why": "dry run: submit it from the plan (or rerun with SUBMIT_SLE=1)"})
    # A dry-run plan is easier to read with the state of the other part.
    for r in out:
        if r["verdict"] != "PLAN":
            continue
        sib = [x for x in out if x["package"] == r["package"] and x["url"] != r["url"]
               and x.get("kind") in ("request", "pr")]
        if sib:
            r["why"] += " Other part: " + "; ".join(
                f"{x['verdict'].lower()} {x['url']}" for x in sib)

    if "--landed" in sys.argv:
        out = [r for r in out if r["verdict"] == "LANDED"]
    if "--open" in sys.argv:
        out = [r for r in out if r["verdict"] not in ("LANDED", "CLOSED")]
    if "--json" in sys.argv:
        json.dump(out, sys.stdout, indent=1)
        return
    order = {"ACTION": 0, "PLAN": 1, "WAITING": 2, "UNKNOWN": 3, "LANDED": 4, "CLOSED": 5}
    out.sort(key=lambda r: (order.get(r["verdict"], 9), r["package"]))
    md = ["# Requests and PRs created by the agents", "",
          "| verdict | package | phase | request | state | reviews | why |", "|---|---|---|---|---|---|---|"]
    for r in out:
        revs = ", ".join(f"{x['by']}:{x['state']}" for x in r.get("reviews", [])) or "-"
        chain = f" (supersedes {', '.join(r['chain'][:-1])})" if len(r.get("chain", [])) > 1 else ""
        print(f"{r['verdict']:8} {r['package']:14} {r['phase']:7} {r['url']}{chain}")
        print(f"{'':8} state {r.get('state', '?')}; reviews: {revs}")
        if r.get("builds", {}).get("counts"):
            pre = r["builds"].get("pre_existing")
            print(f"{'':8} source builds: {r['builds']['counts']}"
                  + (f" ({len(pre)} failing as in the target or not built there)" if pre else ""))
        if r.get("status", {}).get("checks"):
            print(f"{'':8} checks: {', '.join(r['status']['checks'])}")
        print(f"{'':8} {r['why']}")
        for c in [c for c in r.get("comments", []) if not BOTS.search(c["who"] or "")][-3:]:
            if c["who"] != ME:
                print(f"{'':8} comment {c['who']} {c['when'][:16]}: {c['text'][:150]!r}")
        for x in r.get("reviews", []):
            if x.get("comment") and x.get("by") != ME:
                print(f"{'':8} review {x['by']} {x['state']}: {x['comment'][:150]!r}")
        print()
        md.append(f"| {r['verdict']} | {r['package']} | {r['phase']} | {r['url']}{chain} | "
                  f"{r.get('state', '?')} | {revs} | {r['why'].replace('|', '/')} |")
    from collections import Counter
    c = Counter(r["verdict"] for r in out)
    summ = ", ".join(f"{k} {v}" for k, v in sorted(c.items(), key=lambda kv: order.get(kv[0], 9)))
    print(summ or "no requests found in results/*/*/report.json")
    with open(f"{RES}/REQUESTS.md", "w") as f:
        f.write("\n".join(md + ["", summ]) + "\n")


if __name__ == "__main__":
    main()
