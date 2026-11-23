#!/usr/bin/env python3
"""Re-validate every row of sle15_fix_order.csv.

The original list matched packages by name only and used a permissive
test detector (any line with "check" or "test" counted). This script
checks each row again, strictly:

1. SLE 15 SP7 source (IBS, project links resolved): is it a wrapper
   or meta package without upstream sources? If so, find the real
   source package(s) it requires (openssl -> openssl-3, dovecot ->
   dovecot23).
2. Does the real SLE source already run tests in %check? (strict
   runner detection, masked runs flagged)
3. Factory: the same package (and the real package for wrappers).
4. Fedora spec and Debian rules: do they really run the upstream test
   suite (strict), and are they the same upstream project (URL /
   Homepage comparison)?

Output: validate_cache/validation.jsonl (raw evidence per row).
"""
import csv
import json
import os
import re
import subprocess
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.environ.get("ENABLER_DATA") or os.getcwd()   # the data directory
C = os.path.join(BASE, "validate_cache")
for d in ("sle", "factory", "fedora", "debian", "repology"):
    os.makedirs(os.path.join(C, d), exist_ok=True)
IBS, OBS = os.environ["IBS_API"], "https://api.opensuse.org"
SLE_PRJ = os.environ.get("SLE_UPDATE_PRJ", "SUSE:SLE-15-SP7:Update")
_fl = os.path.join(C, "factory.txt")
if not os.path.exists(_fl):
    subprocess.run(["osc", "-A", OBS, "ls", "openSUSE:Factory"], check=True,
                   stdout=open(_fl, "w"))
FACTORY = set(open(_fl).read().split())


def run(cmd, timeout=90):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except subprocess.TimeoutExpired:
        return 124, ""


def cached(kind, name, fetch):
    p = os.path.join(C, kind, re.sub(r"[^A-Za-z0-9._+-]", "_", name))
    if os.path.exists(p):
        return open(p).read()
    txt = fetch() or ""
    with open(p, "w") as f:
        f.write(txt)
    return txt


def curl(url):
    rc, out = run(["curl", "-sfL", "--max-time", "30", url], 40)
    return out if rc == 0 else ""


import threading
import time
_rl = threading.Lock()
_last = [0.0]
UA = os.environ.get("REPOLOGY_UA", "unit-test-enabler list validation")


def repology(pkg):
    """Fedora rawhide and Debian unstable source names for the same upstream."""
    def f():
        for repo in ("opensuse_tumbleweed", "opensuse_leap_15_6"):
            with _rl:
                wait = 1.2 - (time.time() - _last[0])
                if wait > 0:
                    time.sleep(wait)
                _last[0] = time.time()
                rc, out = run(["curl", "-sL", "--max-time", "40", "-A", UA,
                               "https://repology.org/tools/project-by?repo=" + repo +
                               "&name_type=srcname&target_page=api_v1_project&name=" +
                               urllib.parse.quote(pkg)], 60)
            try:
                data = json.loads(out)
            except Exception:
                continue
            if data:
                return json.dumps(data)
        return "[]"
    try:
        data = json.loads(cached("repology", pkg + ".json", f) or "[]")
    except Exception:
        data = []
    names = {"fedora": set(), "debian": set()}
    for x in data:
        if x.get("repo") == "fedora_rawhide" and x.get("srcname"):
            names["fedora"].add(x["srcname"])
        if x.get("repo") == "debian_unstable" and x.get("srcname"):
            names["debian"].add(x["srcname"])
    return {k: sorted(v) for k, v in names.items()}, bool(data)


# ------------------------------------------------------------------ spec parsing
def sle_spec(pkg):
    def f():
        rc, out = run(["osc", "-A", IBS, "cat", SLE_PRJ, pkg, f"{pkg}.spec"])
        if rc == 0 and out.strip():
            return out
        rc, ls = run(["osc", "-A", IBS, "ls", SLE_PRJ, pkg])
        specs = [l for l in ls.split() if l.endswith(".spec")] if rc == 0 else []
        if specs:
            rc, out = run(["osc", "-A", IBS, "cat", SLE_PRJ, pkg, specs[0]])
            return out if rc == 0 else ""
        return ""
    return cached("sle", pkg + ".spec", f)


def sle_files(pkg):
    def f():
        rc, out = run(["osc", "-A", IBS, "ls", SLE_PRJ, pkg])
        return out if rc == 0 else ""
    return cached("sle", pkg + ".files", f).split()


def factory_spec(pkg):
    if pkg not in FACTORY:
        return ""
    return cached("factory", pkg + ".spec",
                  lambda: curl(f"{OBS}/public/source/openSUSE:Factory/{pkg}/{pkg}.spec"))


def factory_files(pkg):
    if pkg not in FACTORY:
        return []
    def f():
        rc, out = run(["osc", "-A", OBS, "ls", "openSUSE:Factory", pkg])
        return out if rc == 0 else ""
    return cached("factory", pkg + ".files", f).split()


def fedora_spec(pkg):
    t = cached("fedora", pkg + ".spec",
               lambda: curl(f"https://src.fedoraproject.org/rpms/{pkg}/raw/rawhide/f/{pkg}.spec"))
    if "<html" in t[:600].lower() or "Anubis" in t[:600]:
        return ""
    return t


def debian_file(pkg, fname):
    def f():
        j = curl(f"https://sources.debian.org/api/src/{pkg}/")
        try:
            vers = json.loads(j)["versions"]
        except Exception:
            return ""
        sid = [v for v in vers if "sid" in v.get("suites", [])] or vers
        v = sid[0]["version"]
        return curl(f"https://sources.debian.org/data/main/{pkg[:4] if pkg.startswith('lib') else pkg[0]}"
                    f"/{pkg}/{v}/debian/{fname}")
    return cached("debian", f"{pkg}.{fname}", f)


def debian_srcname(pkg):
    """Debian source names differ sometimes; try the binary-to-source API."""
    for cand in (pkg.lower(), pkg.lower().replace("_", "-")):
        if debian_file(cand, "control"):
            return cand
    return None


def strip_comments(text):
    return "\n".join(l for l in text.split("\n") if not l.lstrip().startswith("#"))


SECTION = re.compile(r"^%(prep|build|install|check|files|changelog|description|package|pre|post|"
                     r"preun|postun|pretrans|posttrans|clean|generate_buildrequires|conf|"
                     r"trigger\w*|filetrigger\w*|transfiletrigger\w*|verifyscript)\b")


def section(spec, name):
    out, on = [], False
    for l in spec.split("\n"):
        s = l.strip()
        if s.startswith("#"):
            continue
        if re.match(rf"^%{name}\b", s):
            on = True
            continue
        if on and SECTION.match(s):
            break
        if on:
            out.append(s)
    return "\n".join(out) if on else None


RUNNER = re.compile(
    r"(%make_build\s+[^\n]*\b(check|test|tests)\b|%\{?make_build\}?[^\n]*\b(check|test)\b|"
    r"\b(g)?make\b[^\n]*\s(check|test|tests|testsuite|test-all|unittest)\b|%\{?__make\}?[^\n]*\b(check|test)\b|"
    r"%\{?ctest\}?|\bctest\b|--target\s+(test|check)\b|%\{?meson_test\}?|\bmeson\s+test\b|\bninja\b[^\n]*\btest\b|"
    r"%\{?pytest\}?|\bpytest\b|py\.test|%\{?pyproject_check\}?(?!_import)|%\{?python_expand\}?[^\n]*(pytest|unittest|setup\.py\s+test)|"
    r"-m\s+unittest|setup\.py\s+test|%\{?tox\}?\b|\bnosetests\b|"
    r"\bgo\s+test\b|%\{?gotest\}?|%\{?gocheck2?\}?|gotestsum|%\{?cargo_test\}?|\bcargo\s+test\b|"
    r"\bprove\b|\./Build\s+test|%\{?cabal_test\}?|\bR\s+CMD\s+check|\bnpm\s+(run\s+)?test|"
    r"\brake\s+test|%\{?gem_check\}?|\bbusted\b|\bmvn\b[^\n]*\btest\b|\bant\b[^\n]*\btest\b|"
    r"\./run[-_]?tests?|runtests?\b|run-tests?\b|\btestsuite\b|\bdune\s+(test|runtest)|"
    r"%\{?dune_check\}?|\bphpunit\b|\bcheck-local\b|%\{?ruby_check\}?|%\{?pyunittest\}?|%\{?perl_check\}?)",
    re.I)
MASK = re.compile(r"(\|\|\s*(:|true|exit 0|/bin/true|echo)|;\s*true\s*$|-k\s+[\"']?not\s)", re.I)


def check_status(spec):
    """tests | masked | packaging | optional_off | none (no %check)."""
    if not spec:
        return "unknown"
    body = section(spec, "check")
    java_build = section(spec, "build") or ""
    if body is None:
        # Maven/Ant packages run tests in %build unless skipped.
        if re.search(r"%\{?mvn_build\}?(?![^\n]*\s-f\b)", java_build):
            return "tests_in_build"
        return "none"
    lines = [l for l in body.split("\n") if l and not re.match(r"^%(if|else|endif|define|global)", l)]
    code = "\n".join(lines)
    if not code.strip():
        return "none"
    guard = re.search(r"%\{?(with|without)[ _]?(check|tests?|testsuite|run_tests|checks)\}?", body) or \
        re.search(r"^%if\s+.*(with[_ ]?(check|tests?|testsuite|run_tests)|run_tests|do_check|testsuite)",
                  section_raw(spec, "check") or "", re.M)
    hits = [l for l in lines if RUNNER.search(l)
            and not re.search(r"cppcheck|--check-level|pyproject_check_import|^%global|^%define", l)]
    if not hits:
        return "packaging"
    if guard and not guard_default_on(spec):
        return "optional_off"
    if all(MASK.search(l) for l in hits):
        return "masked"
    return "tests"


def section_raw(spec, name):
    out, on = [], False
    for l in spec.split("\n"):
        s = l.strip()
        if re.match(rf"^%{name}\b", s):
            on = True
            continue
        if on and SECTION.match(s):
            break
        if on:
            out.append(s)
    return "\n".join(out) if on else None


def guard_default_on(spec):
    for n in ("check", "tests", "test", "testsuite", "run_tests", "checks"):
        m = re.search(rf"^%bcond\s+{n}\s+([01])\b", spec, re.M)
        if m:
            return m.group(1) == "1"
        if re.search(rf"^%bcond_without\s+{n}\b", spec, re.M):
            return True
        if re.search(rf"^%bcond_with\s+{n}\b", spec, re.M):
            return False
    m = re.search(r"^%(define|global)\s+(run_tests|do_check|with_check|testsuite)\s+(\d)", spec, re.M)
    if m:
        return m.group(3) == "1"
    return False


def debian_status(rules):
    if not rules:
        return "not_in_debian"
    code = strip_comments(rules)
    if re.search(r"DEB_BUILD_OPTIONS\s*[+:]?=.*nocheck", code):
        return "disabled"
    m = re.search(r"^override_dh_auto_test[^:\n]*:[^\n]*\n((?:[ \t]+[^\n]*\n?|\n)*)", code, re.M)
    if m:
        body = "\n".join(l.strip() for l in m.group(1).split("\n") if l.strip())
        if not body:
            return "skipped"
        real = [l for l in body.split("\n")
                if not re.match(r"^(-?echo|@?echo|true|:|-?@?true|exit 0|#)", l)]
        if not real:
            return "skipped"
        if re.search(r"dh_auto_test|make\b.*(check|test)|ctest|meson|pytest|prove|unittest|go\s+test|"
                     r"run[-_]?tests?|testsuite|xvfb-run|cargo|tox|\btest\b", " ".join(real)):
            if all(re.search(r"(\|\|\s*(true|:)|-dh_auto_test)", l) for l in real
                   if re.search(r"dh_auto_test|check|test", l)):
                return "masked"
            # arch-limited test runs are still test runs
            return "explicit"
        return "custom"
    if re.search(r"^\s*dh\s|^%:\s*$", code, re.M) or "dh $@" in code or "cdbs" in code \
            or "DEB_MAKE_CHECK_TARGET" in code:
        return "default"
    return "unknown"


# ------------------------------------------------------------------ identity
def url_of(spec):
    m = re.search(r"^URL:\s*(\S+)", spec or "", re.M | re.I)
    return m.group(1) if m else ""


def homepage(control):
    m = re.search(r"^Homepage:\s*(\S+)", control or "", re.M | re.I)
    return m.group(1) if m else ""


def norm(u):
    if not u or "%" in u:
        return None
    p = urllib.parse.urlparse(u if "://" in u else "https://" + u)
    host = p.netloc.lower().removeprefix("www.")
    parts = [x for x in p.path.lower().split("/") if x]
    return host, parts


def same_project(a, b):
    """True, False, 'host_differs' (needs a look) or None (no URL)."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return None
    if na[0] == nb[0]:
        if na[0] in ("github.com", "gitlab.com", "gitlab.freedesktop.org", "gitlab.gnome.org",
                     "codeberg.org", "salsa.debian.org", "sourceforge.net", "pypi.org",
                     "metacpan.org", "search.cpan.org", "rubygems.org", "crates.io",
                     "pagure.io", "launchpad.net", "bitbucket.org"):
            return na[1][:2] == nb[1][:2] or (len(na[1]) > 1 and len(nb[1]) > 1 and na[1][1] == nb[1][1]) \
                or bool(set(na[1][:3]) & set(nb[1][:3]) - {"projects", "project", "p", "dist", "pypi", "pub"})
        return True
    common = {"www", "org", "com", "net", "io", "de", "info", "dev", "projects", "project",
              "software", "sourceforge", "github", "gitlab", "freedesktop", "gnome", "kde",
              "savannah", "gnu", "nongnu", "apache", "pagure", "launchpad", "co", "uk", "en"}
    ha = set(re.split(r"[.-]", na[0])) - common
    hb = set(re.split(r"[.-]", nb[0])) - common
    if ha & hb:
        return True
    # A code-hosting URL on one side naming the project host of the other
    code = re.compile(r"(github|gitlab|sourceforge|codeberg|pagure|launchpad|bitbucket|salsa|"
                      r"pypi|metacpan|cpan|crates|rubygems|savannah|kernel\.org|freedesktop|gnome|kde)")
    pa, pb = "/".join(na[1]), "/".join(nb[1])
    if code.search(nb[0]) and any(len(t) > 3 and t in pb for t in ha):
        return True
    if code.search(na[0]) and any(len(t) > 3 and t in pa for t in hb):
        return True
    return "host_differs"


def combine(src, url_same):
    """Repology match confirmed by the URL; a URL with a different host
    overrides Repology (it groups openSUSE docker with wmdocker)."""
    if url_same is None:
        return True if src == "repology" else None
    return url_same


# ------------------------------------------------------------------ wrappers
ARCHIVE = re.compile(r"\.(tar(\.\w+)?|tgz|tbz2?|txz|zip|obscpio|gem|jar|crate|7z|xz|bz2|gz|zst|rpm)$")
NOT_SOURCE = {"build.specials.obscpio", "_scmsync.obsinfo"}


def expand(spec):
    macros = {}
    m = re.search(r"^Name:\s*(\S+)", spec, re.M | re.I)
    if m:
        macros["name"] = m.group(1)
    for m in re.finditer(r"^%(?:define|global)\s+(\w+)\s+(\S+)", spec, re.M):
        macros[m.group(1)] = m.group(2)
    for _ in range(3):
        spec = re.sub(r"%\{\??(\w+)\}", lambda m: macros.get(m.group(1), m.group(0)), spec)
    return spec


def is_wrapper(spec, files):
    if not spec:
        return False
    if "_service" in files or any(ARCHIVE.search(f) for f in files if f not in NOT_SOURCE):
        return False
    build = section(spec, "build") or ""
    builds_code = re.search(r"configure|\bmake\b|%make|cmake|meson|ninja|setup\.py|pyproject|"
                            r"go\s+build|%gobuild|cargo|\bant\b|mvn|Makefile\.PL|Build\.PL|"
                            r"gcc|\bcc\b|qmake|scons|waf|rake|gem\s+build|%\{?\w*_build\}?", build)
    return not builds_code


def wrapper_targets(pkg, spec, getspec=None):
    """Real source packages a wrapper points at (Requires: pkgNN / pkg-N)."""
    base = re.escape(pkg.lower().rstrip("0123456789").rstrip("-_"))
    spec = expand(spec)
    names = set()
    for m in re.finditer(r"^(?:Requires|Recommends|Provides|Obsoletes)\s*:\s*(.+)$", spec, re.M | re.I):
        for tok in re.split(r"[\s,]+", m.group(1)):
            t = tok.strip()
            if re.match(rf"^{base}[-_]?\d[\w.-]*$", t, re.I):
                names.add(t)
    # map binary names to source packages that exist in SLE
    cands = set()
    for n in names:
        for c in (n, re.sub(r"[-_]?(devel|libs?|utils?)$", "", n)):
            cands.add(c)
    getspec = getspec or sle_spec
    real = [c for c in sorted(cands) if c != pkg and getspec(c)]
    return real


# ------------------------------------------------------------------ main
def validate(row):
    pkg = row["package"]
    out = {"package": pkg, "priority_rank": int(row["priority_rank"])}
    spec = sle_spec(pkg)
    files = sle_files(pkg)
    out["sle_spec_found"] = bool(spec)
    out["sle_url"] = url_of(spec)
    out["sle_check"] = check_status(spec)
    out["wrapper"] = is_wrapper(spec, files)
    real = [pkg]
    if out["wrapper"]:
        real = wrapper_targets(pkg, spec)
        out["real_packages"] = real
    rs = {}
    for r in real:
        s = sle_spec(r)
        rs[r] = {"sle_check": check_status(s), "url": url_of(s)}
    out["real"] = rs
    work = real[0] if real else pkg
    url = rs.get(work, {}).get("url") or out["sle_url"]
    # Factory: same name; for wrappers the real package(s) it points at
    fac = {}
    fs = factory_spec(pkg)
    if fs:
        fw = is_wrapper(fs, factory_files(pkg))
        fac[pkg] = {"check": check_status(fs), "wrapper": fw, "url": url_of(fs)}
        if fw:
            for r in wrapper_targets(pkg, fs, factory_spec):
                fac[r] = {"check": check_status(factory_spec(r)), "wrapper": False,
                          "url": url_of(factory_spec(r))}
    for r in real + [re.sub(r"[-_]?\d+(_\d+)?$", "", pkg)]:
        if r not in fac and factory_spec(r):
            fac[r] = {"check": check_status(factory_spec(r)), "wrapper": False,
                      "url": url_of(factory_spec(r))}
    out["factory"] = fac
    # Fedora / Debian: names from Repology (same upstream project); fall
    # back to guessed names, compared by URL, when Repology has nothing.
    rp, found = repology(work)
    if not (rp["fedora"] or rp["debian"]) and work != pkg:
        rp, found = repology(pkg)
    # Prefer the plain distro package over cross/mingw/compat variants.
    for k in ("fedora", "debian"):
        bases = {pkg.lower(), work.lower(), re.sub(r"[-_.]?\d+(_\d+)?$", "", pkg).lower(),
                 re.sub(r"[-_.]?\d+(_\d+)?$", "", work).lower()}
        exact = [n for n in rp[k] if n.lower() in bases or re.sub(r"[-_.]?[\d.]+$", "", n.lower()) in bases]
        if exact:
            rp[k] = exact
        else:
            rp[k] = [n for n in rp[k] if not re.match(r"(mingw|arm-|avr-|cross-|.*-cs$)", n)]
    out["repology_found"] = found
    out["repology"] = rp
    guess = list(dict.fromkeys([pkg, re.sub(r"[-_]?\d+(_\d+)?$", "", pkg)] + real))
    fed = {}
    for n, src in [(n, "repology") for n in rp["fedora"]] or [(n, "guess") for n in guess]:
        s = fedora_spec(n)
        if s:
            fed[n] = {"check": check_status(s), "url": url_of(s), "source": src,
                      "same": combine(src, same_project(url, url_of(s)))}
    out["fedora"] = fed
    deb = {}
    dnames = [(n, "repology") for n in rp["debian"]] or \
        [(n.lower(), "guess") for n in guess]
    for n, src in dnames:
        ctl = debian_file(n, "control")
        if ctl:
            rules = debian_file(n, "rules")
            deb[n] = {"status": debian_status(rules), "homepage": homepage(ctl), "source": src,
                      "same": combine(src, same_project(url, homepage(ctl)))}
    out["debian"] = deb
    return out


def main():
    rows = list(csv.DictReader(open(os.path.join(BASE, "sle15_fix_order.csv"))))
    if len(sys.argv) > 1:
        rows = [r for r in rows if r["package"] in sys.argv[1:]]
    res = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(validate, r): r["package"] for r in rows}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                res.append(f.result())
            except Exception as e:
                res.append({"package": futs[f], "error": repr(e)})
            if i % 25 == 0:
                print(f"{i}/{len(rows)}", file=sys.stderr)
    res.sort(key=lambda x: x.get("priority_rank", 9999))
    outp = os.path.join(C, "validation.jsonl" if len(sys.argv) == 1 else "validation-sample.jsonl")
    with open(outp, "w") as f:
        for r in res:
            f.write(json.dumps(r) + "\n")
    print(outp)


if __name__ == "__main__":
    main()
