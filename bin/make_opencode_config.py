#!/usr/bin/env python3
"""Build the container opencode.json from the provider secret.

Nothing may ever ask for permission in the container: every permission
is "allow" (or "deny" for the reviewer's publishing commands, which
fails immediately instead of prompting), and the orchestrator runs
opencode with --auto as a second layer.
"""
import json
import os
import sys

with open(sys.argv[1]) as f:
    providers = json.load(f)["provider"]

worker = os.environ.get("WORKER_MODEL", "helmcode/deepseek-v4-flash")
reviewer = os.environ.get("REVIEWER_MODEL", "google-vertex/claude-opus-5-5@default")

PERMS = ["read", "edit", "glob", "grep", "list", "bash", "task", "skill", "lsp",
         "todoread", "todowrite", "webfetch", "websearch", "codesearch",
         "external_directory", "doom_loop"]


def allow_all(**over):
    p = {"*": "allow"}
    p.update({k: "allow" for k in PERMS})
    p["question"] = "deny"          # nobody can answer
    p.update(over)
    return p


# Commands that publish something. The reviewer never runs them; the
# worker is additionally gated by the /opt/enabler/guard wrappers.
publish = ["osc ci*", "osc commit*", "osc sr*", "osc submitreq*", "osc rq*",
           "osc request*", "osc mr*", "osc maintenancerequest*", "osc mbranch*",
           "osc branch*", "osc -A * ci*", "osc -A * sr*", "osc -A * rq*",
           "osc -A * mr*", "osc -A * branch*", "osc -A * mbranch*",
           "osc -A * maintenancerequest*", "osc -A * commit*",
           "git push*", "git commit*", "git-obs*", "tea *", "*rm -rf*"]

# Raw HTTP writes would bypass the guard wrappers (they only see osc, git,
# git-obs and tea). Denied for both agents; this is a pattern filter, not
# a sandbox: the real boundary is what the credentials may do.
raw_http_writes = ["curl *-X POST*", "curl *-X PUT*", "curl *-X PATCH*",
                   "curl *-X DELETE*", "curl *--request*", "curl *--data*",
                   "curl * -d *", "curl * -F *", "curl *--form*", "curl *-T *",
                   "curl *--upload-file*", "wget *--post*", "wget *--method*",
                   "http POST*", "http PUT*", "http PATCH*", "http DELETE*"]

# Bugzilla is read only for both agents (bugwarden also runs --read-only).
bz_write = {f"bugzilla_{t}": False for t in (
    "add_attachment", "add_cc_to_bug", "add_comment", "assign_bug", "create_bug",
    "mark_as_duplicate", "update_bug_dependencies", "update_bug_fields",
    "update_bug_status")}

cfg = {
    "$schema": "https://opencode.ai/config.json",
    "provider": providers,
    "model": worker,
    "autoupdate": False,
    "share": "disabled",
    "permission": allow_all(bash=dict({"*": "allow"}, **{p: "deny" for p in raw_http_writes})),
    "tools": bz_write,
    "mcp": {
        "bugzilla": {
            "type": "local",
            "command": ["bugwarden", "--transport", "stdio", "--read-only",
                        "--bugzilla-server", os.environ.get("BUGZILLA_URL", "https://bugzilla.suse.com"),
                        "--api-key-file", os.path.expanduser("~/.config/enabler/bugzilla.key"),
                        "--policy", "/etc/bugwarden/policy.toml"],
            "enabled": True,
        },
    },
    "agent": {
        "worker": {
            "mode": "primary",
            "model": worker,
            "description": "Enables upstream unit tests in a package",
            "permission": allow_all(
                bash=dict({"*": "allow"}, **{p: "deny" for p in raw_http_writes})),
        },
        "reviewer": {
            "mode": "primary",
            "model": reviewer,
            "description": "Reviews a test enablement change, read only",
            # Writes its verdict file with a bash heredoc; edit is denied.
            "permission": allow_all(
                edit="deny",
                bash=dict({"*": "allow"}, **{p: "deny" for p in publish + raw_http_writes})),
        },
    },
}
# Shadow reviewers: same read-only rules as the reviewer, other models.
shadows = [m.strip() for m in os.environ.get("SHADOW_REVIEWERS", "").split(",") if m.strip()]
import re  # noqa: E402
for m in shadows:
    name = "shadow-" + re.sub(r"[^a-z0-9]+", "-", m.lower()).strip("-")
    cfg["agent"][name] = dict(cfg["agent"]["reviewer"], model=m,
                              description=f"Shadow reviewer ({m}), comparison only")
json.dump(cfg, sys.stdout, indent=2)
