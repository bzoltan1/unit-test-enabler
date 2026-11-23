#!/usr/bin/env python3
"""Build skills/ (the skills shipped in the image) from their sources.

- opensuse-packaging: NOT part of this repository. Taken from SKILLS_GIT
  (+ SKILLS_REF) or the local directory SKILLS_SRC (both in enabler.conf).
- upstream-tests-packaging: shipped in skills-src/ (already clean).

The script:
1. drops files that can publish outside the enabler's submission gates;
2. rewrites environment-specific values with placeholders, using generic
   patterns (mail addresses, SSH key file names, personal helper paths,
   hard-coded changelog authors, personal home project sections) and the
   identifiers in your configuration (OBS_USER, GIT_NAME, GIT_EMAIL and
   SANITIZE_EXTRA_IDS, a comma-separated list);
3. fails when a structural rule no longer matches (the source changed),
   or when the result still contains an identifier, a home path, a key
   file name, a private key or a token-shaped string;
4. writes skills/MANIFEST.sha256 (checked by build-image.sh) and
   skills/PROVENANCE.

skills/ is generated and never committed.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "skills")
E = os.environ

EXTERNAL = ["opensuse-packaging"]
SHIPPED = ["upstream-tests-packaging"]

DROP = {
    # Pushes a branch and opens a PR with curl and the tea token: a
    # publishing path the guard wrappers cannot see.
    "opensuse-packaging/scripts/leap-sync.sh",
}

# (file, regex, replacement, required)
RULES = [
    ("opensuse-packaging/SKILL.md", r"^- `leap-sync\.sh`[^\n]*\n", "", True),
    # A personal home project policy of the skill's author.
    ("opensuse-packaging/SKILL.md", r"(?s)^## Home project policy\n.*?(?=^## )", "", False),
    ("opensuse-packaging/references/update-build.md",
     r"(?s)^### Deployment cone in `home:[\w:.-]+`.*?(?=^##)", "", False),
    # Hard-coded default changelog author: use git's identity instead.
    ("opensuse-packaging/scripts/changes-prepend.sh",
     r"--author defaults to '[^']*'", "--author defaults to 'git config user.name <user.email>'",
     False),
    ("opensuse-packaging/scripts/changes-prepend.sh",
     r'author="[^"$]*<[^>]*>"', 'author="$(git config user.name) <$(git config user.email)>"',
     False),
    ("*", r"\(e\.g\. '[A-Z][a-z]+ [A-Z][a-z]+'\)", "(e.g. 'Jane Packager')", False),
    ("*", r'git config user\.name "[^"<$]+"', 'git config user.name "<Your Name>"', False),
    ("*", r"alongside [\w-]+'s maintainership", "alongside the packager's maintainership", False),
    ("*", r"\(SUSE GitLab key reused\)", "", False),
    ("*", r"often `id_[\w-]+`", "the key registered on that host", False),
    ("*", r"\(e\.g\. `id_[\w-]+`[^)]*\)", "(the key registered there)", False),
    ("*", r"~/\.ssh/id_[\w-]+", "~/.ssh/<your-key>", False),
    ("*", r"\(installed at `~/\.local/bin/[\w.-]+`\)", "(install it first if you need it)", False),
    ("*", r"[A-Za-z0-9._%+-]+@(?!example\.(?:com|org))[A-Za-z0-9-]+\.[A-Za-z0-9.-]+",
     "<email>", False),
]


def identifiers():
    ids = [E.get("OBS_USER", ""), E.get("GIT_EMAIL", "")]
    ids += E.get("GIT_NAME", "").split()
    ids += [x.strip() for x in E.get("SANITIZE_EXTRA_IDS", "").split(",")]
    return sorted({i for i in ids if len(i) >= 4}, key=len, reverse=True)


GENERIC_FORBIDDEN = [
    (r"/home/(?!abuild/|agent/)[a-z][a-z0-9_-]*/", "home path"),
    (r"~/\.ssh/id_[a-z0-9_]+", "personal key file name"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"(?i)(api[_-]?key|token|passw(or)?d|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}",
     "credential value"),
    (r"\b(ghp|gho|glpat|sk)-?[A-Za-z0-9]{20,}", "token-shaped string"),
    (r"\b[0-9a-f]{40}\b", "40-hex token-shaped string"),
]


def external_source():
    git = E.get("SKILLS_GIT")
    if git:
        tmp = tempfile.mkdtemp()
        ref = E.get("SKILLS_REF") or "main"
        subprocess.run(["git", "clone", "-q", "--depth", "1", "-b", ref, git, tmp], check=True)
        rev = subprocess.run(["git", "-C", tmp, "rev-parse", "HEAD"], capture_output=True,
                             text=True).stdout.strip()
        return tmp, f"git {git} {ref} {rev}"
    src = os.path.expanduser(os.path.expandvars(E.get("SKILLS_SRC") or "~/.config/opencode/skills"))
    return src, f"local directory {src}"


def main():
    src, origin = external_source()
    stage = tempfile.mkdtemp()
    for s in EXTERNAL:
        if not os.path.isdir(os.path.join(src, s)):
            sys.exit(f"skill {s} not found in {src} (set SKILLS_SRC or SKILLS_GIT in enabler.conf)")
        shutil.copytree(os.path.join(src, s), os.path.join(stage, s),
                        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
    for s in SHIPPED:
        shutil.copytree(os.path.join(HERE, "skills-src", s), os.path.join(stage, s))
    for rel in DROP:
        p = os.path.join(stage, rel)
        if not os.path.exists(p):
            sys.exit(f"drop rule: {rel} not in source (update DROP)")
        os.remove(p)
    files = [os.path.relpath(os.path.join(r, f), stage)
             for r, _, fs in os.walk(stage) for f in fs]
    ids = identifiers()
    rules = RULES + [("*", re.escape(i), "<redacted>", False) for i in ids]
    for rel_glob, rx, rep, required in rules:
        targets = files if rel_glob == "*" else [rel_glob]
        hits = 0
        for rel in targets:
            p = os.path.join(stage, rel)
            try:
                txt = open(p).read()
            except UnicodeDecodeError:
                continue
            new, n = re.subn(rx, rep, txt, flags=re.M | (re.I if rx in map(re.escape, ids) else 0))
            if n:
                open(p, "w").write(new)
                hits += n
        if required and not hits:
            sys.exit(f"rule did not match: {rel_glob}: {rx} (source changed? update RULES)")
    forbidden = GENERIC_FORBIDDEN + [(r"(?i)" + re.escape(i), "configured identifier") for i in ids]
    problems = []
    for rel in files:
        p = os.path.join(stage, rel)
        for i, line in enumerate(open(p, errors="replace"), 1):
            for rx, what in forbidden:
                if re.search(rx, line):
                    problems.append(f"{rel}:{i}: {what}: {line.strip()[:120]}")
    if problems:
        sys.exit("sanitized skills still contain:\n" + "\n".join(problems))
    shutil.rmtree(OUT, ignore_errors=True)
    shutil.copytree(stage, OUT)
    manifest = []
    for root, _, fs in sorted(os.walk(OUT)):
        for fn in sorted(fs):
            p = os.path.join(root, fn)
            manifest.append(f"{hashlib.sha256(open(p, 'rb').read()).hexdigest()}  "
                            f"{os.path.relpath(p, OUT)}")
    with open(os.path.join(OUT, "MANIFEST.sha256"), "w") as f:
        f.write("\n".join(manifest) + "\n")
    with open(os.path.join(OUT, "PROVENANCE"), "w") as f:
        f.write(f"opensuse-packaging: {origin}\n"
                f"upstream-tests-packaging: skills-src/ in this repository\n"
                f"dropped: {' '.join(sorted(DROP))}\n"
                f"rules: {len(RULES)} generic + {len(ids)} configured identifiers\n")
    print(f"skills/ built ({origin}): {len(manifest)} files, scan clean")


if __name__ == "__main__":
    main()
