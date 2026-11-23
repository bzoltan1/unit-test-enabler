#!/usr/bin/python3
"""Submission gate in front of osc, git, git-obs and tea.

Installed as /opt/enabler/guard/{osc,git,git-obs,tea}. It blocks:
- any write outside home:$OBS_USER* projects and $OBS_USER/* git forks
- request creation until the orchestrator opened the gate for that
  target (after review approval; SLE only after the Factory request)
- request accept/decline and other maintainer actions, always
Everything else is passed through to the real binary.
"""
import os
import re
import sys

USER = os.environ.get("OBS_USER") or sys.exit("ENABLER GUARD: OBS_USER is not set")
GATES = os.environ.get("ENABLER_GATES", "/work/.gates")
REAL = {"osc": "/usr/bin/osc", "git": "/usr/bin/git",
        "git-obs": "/usr/bin/git-obs", "tea": "/usr/bin/tea"}
IBS_HOST = os.environ.get("IBS_API", "").split("://")[-1].rstrip("/")
IBS = tuple(x for x in (IBS_HOST, "ibs") if x)
IBS_GITEA = os.environ.get("IBS_GITEA", "")
IBS_TEA_LOGIN = os.environ.get("IBS_TEA_LOGIN", "")


def block(msg):
    sys.stderr.write(f"ENABLER GUARD BLOCKED: {msg}\n")
    log = os.environ.get("ENABLER_GUARD_LOG")
    if log:
        with open(log, "a") as f:
            f.write(" ".join(sys.argv) + f"  # {msg}\n")
    sys.exit(97)


def gate_open(name):
    return os.path.exists(os.path.join(GATES, name))


def find_up(fname, start="."):
    d = os.path.abspath(start)
    while True:
        p = os.path.join(d, ".osc", fname)
        if os.path.exists(p):
            return open(p).read().strip()
        if d == "/":
            return None
        d = os.path.dirname(d)


def osc(args):
    if any(a in ("-h", "--help") for a in args) or (args and args[0] == "help"):
        os.execv(REAL["osc"], ["osc"] + args)
    api = None
    rest = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-A", "--apiurl") and i + 1 < len(args):
            api = args[i + 1]
            i += 2
            continue
        if a.startswith("--apiurl="):
            api = a.split("=", 1)[1]
        elif a.startswith("-A") and len(a) > 2:
            api = a[2:]
        else:
            rest.append(a)
        i += 1
    if api is None:
        api = find_up("_apiurl") or "https://api.opensuse.org"
    is_ibs = any(x in api for x in IBS)
    target = "sle" if is_ibs else "factory"
    VALUE_OPTS = {"-m", "--message", "-F", "--file", "-a", "--attribute", "-r", "--repo",
                  "-p", "--project", "-P", "-s", "--state", "-t", "--type", "-d", "--data",
                  "-T", "--title", "-e", "-X", "--method", "-u", "--user", "-x", "-k", "-M",
                  "--multibuild-package", "--root", "--jobs", "--vm-type", "--vm-memory",
                  "--vm-disk-size", "-f"}
    words, skip = [], False
    for a in rest:
        if skip:
            skip = False
            continue
        if a in VALUE_OPTS:
            skip = True
            continue
        if not a.startswith("-"):
            words.append(a)
    cmd = words[0] if words else ""
    pos = words[1:]

    if cmd == "api":
        method, data = "GET", False
        for i, a in enumerate(rest):
            if a in ("-X", "--method") and i + 1 < len(rest):
                method = rest[i + 1].upper()
            elif a.startswith("--method="):
                method = a.split("=", 1)[1].upper()
            elif a.startswith("-X") and len(a) > 2:
                method = a[2:].upper()
            elif a in ("-d", "--data", "-f", "--file") or a.startswith(("--data=", "--file=")):
                data = True
        if method not in ("GET", "HEAD") or data:
            path = next((a for a in pos if a.startswith("/")), "")
            if path.startswith("/request") or "cmd=create" in path:
                if not gate_open(f"submit_{target}"):
                    block(f"{target} submission gate is closed (osc api {path})")
            elif not re.match(rf"^/(source|build|published)/home:{re.escape(USER)}(:|/|$)", path):
                block(f"'osc api -X {method} {path}': writes only to home:{USER}*")
    never = {"rdelete", "undelete", "release", "setdevelproject",
             "createincident", "lock", "unlock"}
    if cmd in never:
        block(f"'osc {cmd}' is never allowed")
    if cmd in ("rq", "request", "review"):
        sub = pos[0] if pos else ""
        if sub in ("accept", "decline", "reopen", "approve", "setincident",
                   "prioritize", "priorize", "wipe"):
            block(f"'osc {cmd} {sub}' is a maintainer action")
        if sub in ("supersede",):
            if not gate_open(f"submit_{target}"):
                block(f"{target} submission gate is closed")
    if cmd in ("sr", "submitreq", "submitrequest", "submitpac", "creq",
               "createrequest", "mr", "maintenancerequest", "aggregatepac"):
        if not gate_open(f"submit_{target}"):
            block(f"{target} submission gate is closed. Do not submit; "
                  "write the planned command and message to the plan file "
                  "named in your instructions.")
    if cmd in ("ci", "commit", "checkin", "addremove", "ar", "rremove"):
        prj = find_up("_project")
        if cmd == "rremove":
            prj = pos[0] if pos else ""
        if prj and not prj.startswith(f"home:{USER}"):
            block(f"commit to {prj}: only home:{USER}* is writable")
    writes_meta = cmd == "meta" and any(
        a in ("-e", "--edit", "-F", "--file", "-d", "--delete", "--set")
        or a.startswith(("--file=", "--set=")) for a in rest)
    if cmd in ("copypac", "linkpac", "setlinkrev", "detachbranch") or writes_meta:
        if cmd in ("copypac", "linkpac"):
            tgt = pos[2] if len(pos) >= 3 else ""
        elif cmd == "meta":
            tgt = pos[1] if len(pos) > 1 else (find_up("_project") or "")
        else:
            tgt = pos[0] if pos else (find_up("_project") or "")
        if not tgt.startswith(f"home:{USER}"):
            block(f"'osc {cmd}' targeting {tgt or '?'} is not allowed")
    os.execv(REAL["osc"], ["osc"] + args)


def git(args):
    cmd = next((a for a in args if not a.startswith("-")), "")
    if cmd == "push":
        import subprocess
        pos = [a for a in args[args.index("push") + 1:] if not a.startswith("-")]
        remote = pos[0] if pos else "origin"
        url = remote
        if "://" not in remote and "@" not in remote:
            r = subprocess.run([REAL["git"], "remote", "get-url", "--push", remote],
                               capture_output=True, text=True)
            url = r.stdout.strip()
        hosts = "|".join(re.escape(h) for h in ("src.opensuse.org", IBS_GITEA) if h)
        m = re.search(rf"(?:{hosts})[:/]+([^/]+)/", url)
        if not m or m.group(1) != USER:
            block(f"push to {url or remote}: only {USER}/* forks are writable")
    os.execv(REAL["git"], ["git"] + args)


def gitea_cli(prog, args):
    joined = " ".join(args)
    creating = re.search(r"\b(pr|pulls?)\s+(create|new|open)\b", joined) or \
        re.search(r"\b(pr|pulls?)\s+(merge|approve|close)\b", joined)
    if creating:
        if re.search(r"\b(merge|approve)\b", joined):
            block(f"'{prog} {joined}' is a maintainer action")
        host_sle = bool((IBS_TEA_LOGIN and IBS_TEA_LOGIN in args) or
                        (IBS_GITEA and IBS_GITEA in joined))
        if not host_sle and os.path.exists(".git"):
            import subprocess
            r = subprocess.run([REAL["git"], "remote", "-v"], capture_output=True, text=True)
            host_sle = bool(IBS_GITEA) and IBS_GITEA in r.stdout and "src.opensuse.org" not in r.stdout
        target = "sle" if host_sle else "factory"
        if not gate_open(f"submit_{target}"):
            block(f"{target} submission gate is closed. Do not open a PR; "
                  "write the plan file instead.")
    os.execv(REAL[prog], [prog] + args)


def main():
    prog = os.path.basename(sys.argv[0])
    args = sys.argv[1:]
    if prog == "osc":
        osc(args)
    elif prog == "git":
        git(args)
    elif prog in ("git-obs", "tea"):
        gitea_cli(prog, args)
    else:
        block(f"unknown wrapper {prog}")


if __name__ == "__main__":
    main()
