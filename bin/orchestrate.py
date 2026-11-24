#!/usr/bin/python3
"""Unit test enabler orchestrator.

Drives one package through:
  factory phase: implement -> review loop -> publish -> observe/fix -> finalize
  sle phase:     same, started only after the Factory request exists

The worker (implementation) and reviewer models run as opencode
sessions. Every decision that must not depend on a model (limits,
gates, build polling, recording) is made here.

State lives in /results/<pkg>/state.json, so a restarted container
resumes where it stopped.
"""
import argparse
import csv
import datetime as dt
import fcntl
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback

sys.path.insert(0, "/opt/enabler/bin")

E = os.environ
WORKER = "worker"
REVIEWER = "reviewer"
GATES = E.get("ENABLER_GATES", "/work/.gates")
# Rendered by the entrypoint (deployment placeholders filled in).
AGENT_DIR = E.get("ENABLER_AGENT_DIR", "/opt/enabler/agent")
PROMPT = f"{AGENT_DIR}/prompts/ENABLE_TESTS_PROMPT.md"
LESSONS = f"{AGENT_DIR}/lessons"
IBS_API = E.get("IBS_API", "")
IBS_GITEA = E.get("IBS_GITEA", "")
FIX_ORDER = "/data/sle15_fix_order.csv"
OBS_USER = E["OBS_USER"]

LIMITS = {
    "local_builds": int(E.get("LOCAL_BUILD_LIMIT", 8)),
    "review_rounds": int(E.get("REVIEW_ROUNDS", 4)),
    "obs_rounds": int(E.get("OBS_ROUNDS", 4)),
    "wall_hours": float(E.get("WALL_HOURS", 24)),
}
SUBMIT = {"factory": E.get("SUBMIT_FACTORY", "1") == "1",
          "sle": E.get("SUBMIT_SLE", "0") == "1"}
COST_LIMIT_USD = float(E.get("COST_LIMIT_USD", 0) or 0)   # 0 = no limit
# Extra reviewer models run in parallel with the primary reviewer on the
# same prompt. Their verdicts are recorded for comparison only.
def shadow_name(model):
    """Stable agent/file name of a shadow reviewer model."""
    return "shadow-" + re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")


SHADOWS = {shadow_name(m.strip()): m.strip()
           for m in E.get("SHADOW_REVIEWERS", "").split(",") if m.strip()}
PRICES = "/opt/enabler/prices.json"
POLL_SECONDS = int(E.get("POLL_SECONDS", 600))
PENDING_GRACE_HOURS = float(E.get("PENDING_GRACE_HOURS", 6))
BUILD_WAIT_HOURS = float(E.get("BUILD_WAIT_HOURS", 6))
TARGET_GRACE_HOURS = float(E.get("TARGET_GRACE_HOURS", 3))
CHURN_LIMIT = int(E.get("BUILD_CHURN_LIMIT", 3))
WORKER_TIMEOUT = int(E.get("WORKER_TIMEOUT", 5 * 3600))
REVIEWER_TIMEOUT = int(E.get("REVIEWER_TIMEOUT", 2 * 3600))

FINAL_CODES = {"succeeded", "failed", "unresolvable", "broken", "disabled",
               "excluded", "locked"}
PROBLEM_CODES = {"failed", "unresolvable", "broken", "disabled", "excluded"}
BAD_CODES = {"failed", "unresolvable", "broken"}
WAIT_CODES = {"scheduled", "blocked"}


class Stop(Exception):
    """Ends the run with a recorded outcome."""

    def __init__(self, outcome, reason):
        super().__init__(reason)
        self.outcome = outcome
        self.reason = reason


def now():
    return dt.datetime.now(dt.timezone.utc)


def ts():
    return now().strftime("%Y-%m-%dT%H:%M:%SZ")


class Run:
    def __init__(self, pkg):
        self.pkg = pkg
        self.rdir = f"/results/{pkg}"
        self.wdir = f"/work/{pkg}"
        os.makedirs(f"{self.rdir}/agent-logs", exist_ok=True)
        os.makedirs(self.wdir, exist_ok=True)
        os.makedirs(GATES, exist_ok=True)
        self.state_file = f"{self.rdir}/state.json"
        self.state = self._load()
        self.state.setdefault("package", pkg)
        self.state.setdefault("started", ts())
        self.state.setdefault("phases", {})
        self.state.setdefault("agent_calls", 0)
        # The models a run started with stay recorded; replays and backfills
        # of old runs only add their shadows.
        models = self.state.setdefault("models", {})
        models.setdefault("worker", E.get("WORKER_MODEL", "helmcode/deepseek-v4-flash"))
        models.setdefault("reviewer", E.get("REVIEWER_MODEL", "helmcode/deepseek-v4-flash"))
        models.update(SHADOWS)
        self.lock = threading.RLock()
        self.save()
        self.deadline = dt.datetime.fromisoformat(
            self.state["started"].replace("Z", "+00:00")) + \
            dt.timedelta(hours=LIMITS["wall_hours"])

    # ---------------------------------------------------------------- state
    def _load(self):
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def save(self):
        tmp = self.state_file + f".tmp{threading.get_ident()}"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=2)
        os.replace(tmp, self.state_file)

    def log(self, msg):
        line = f"[{ts()}] {msg}"
        print(line, flush=True)
        with open(f"{self.rdir}/orchestrator.log", "a") as f:
            f.write(line + "\n")

    def ph(self, phase):
        return self.state["phases"].setdefault(phase, {
            "status": "pending", "review_round": 0, "obs_round": 0,
            "steps_done": [], "ignored_failures": []})

    def phase_dir(self, phase):
        d = f"{self.rdir}/{phase}"
        os.makedirs(f"{d}/logs", exist_ok=True)
        return d

    def costs(self):
        import costlib
        calls, total = costlib.package_costs(self.rdir, costlib.load_prices(PRICES))
        with open(f"{self.rdir}/cost.json", "w") as f:
            json.dump({"total_usd_priced": round(total["usd"], 4),
                       "unpriced_tokens": total["unpriced_tokens"],
                       "by_model": total["by_model"], "calls": calls}, f, indent=1)
        return total

    def check_budget(self):
        if COST_LIMIT_USD <= 0:
            return
        usd = self.costs()["usd"]
        if usd > COST_LIMIT_USD:
            raise Stop("limit_cost", f"priced model cost ${usd:.2f} exceeds "
                       f"COST_LIMIT_USD=${COST_LIMIT_USD:.2f}")

    def check_deadline(self):
        if now() > self.deadline:
            raise Stop("limit_wall_time",
                       f"wall time limit of {LIMITS['wall_hours']}h reached")

    # ---------------------------------------------------------------- gates
    @staticmethod
    def gate(name, open_=True):
        p = os.path.join(GATES, name)
        if open_:
            open(p, "w").close()
        elif os.path.exists(p):
            os.remove(p)

    def close_all_gates(self):
        for g in ("submit_factory", "submit_sle"):
            self.gate(g, False)

    # ---------------------------------------------------------------- agents
    def agent(self, who, message, phase, step, session=None, final=False):
        if not final:
            self.check_deadline()
        with self.lock:
            self.state["agent_calls"] += 1
            n = self.state["agent_calls"]
            self.save()
        logf = f"{self.rdir}/agent-logs/{n:03d}-{phase}-{step}-{who}.jsonl"
        with open(f"{self.rdir}/agent-logs/{n:03d}-{phase}-{step}-{who}.prompt.md", "w") as f:
            f.write(message)
        cmd = ["opencode", "run", "--auto", "--agent", who, "--format", "json",
               "--dir", self.wdir, "--title", f"{self.pkg} {phase} {step} {who}"]
        if session:
            cmd += ["--session", session]
        cmd.append(message)
        env = dict(E, ENABLER_PHASE_DIR=self.phase_dir(phase),
                   ENABLER_PACKAGE=self.pkg, ENABLER_PHASE=phase,
                   ENABLER_GUARD_LOG=f"{self.rdir}/guard.log")
        remaining = max(60, int((self.deadline - now()).total_seconds()))
        timeout = min(WORKER_TIMEOUT if who == WORKER else REVIEWER_TIMEOUT, remaining)
        if who.startswith("shadow"):
            timeout = min(REVIEWER_TIMEOUT, remaining)
        if final:
            timeout = 1800
        self.log(f"agent call {n}: {who} {phase}/{step} (timeout {timeout // 60} min)")
        sid = session
        for attempt in range(1, 4):
            events = 0
            with open(logf, "a") as out:
                p = subprocess.Popen(cmd if sid is None or session else
                                     cmd[:-1] + ["--session", sid, cmd[-1]],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, env=env, cwd=self.wdir)
                # A watchdog, because a silent hang produces no lines to check on.
                timer = threading.Timer(timeout, p.kill)
                timer.start()
                try:
                    for line in p.stdout:
                        out.write(line)
                        events += 1
                        if sid is None:
                            m = re.search(r'"sessionI[Dd]"\s*:\s*"([^"]+)"', line)
                            if m:
                                sid = m.group(1)
                    p.wait()
                finally:
                    timer.cancel()
            if p.returncode == 0 or events > 20:
                break
            # Provider or startup error: back off and retry (same session if any).
            self.log(f"agent call {n} attempt {attempt} failed rc={p.returncode} "
                     f"after {events} events; retrying in {5 * attempt} min")
            time.sleep(300 * attempt)
            if not final:
                self.check_deadline()
        self.log(f"agent call {n} finished rc={p.returncode} session={sid}")
        try:
            self.costs()
        except Exception as e:
            self.log(f"cost accounting failed: {e}")
        if not final and not who.startswith("shadow"):
            self.check_budget()
        return sid

    def worker_session(self, phase):
        return self.ph(phase).get("worker_session")

    def worker(self, phase, step, message, new_session=False):
        ph = self.ph(phase)
        sid = None if new_session else ph.get("worker_session")
        sid = self.agent(WORKER, message, phase, step, session=sid)
        if sid:
            ph["worker_session"] = sid
            self.save()
        return sid

    # ---------------------------------------------------------------- reports
    def read_json(self, path):
        try:
            with open(path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            return {"_error": f"{type(e).__name__}: {e}"}

    def snapshot(self, phase, name, src):
        if os.path.exists(src):
            shutil.copy(src, f"{self.phase_dir(phase)}/{name}")

    def validate_report(self, phase, rep, need_ready_builds=True):
        problems = []
        if "_error" in rep:
            return [f"report.json missing or not valid JSON ({rep['_error']})"]
        oc = rep.get("outcome")
        if oc not in ("ready", "impossible", "blocked", "already_enabled"):
            problems.append("outcome must be ready, impossible, blocked or already_enabled")
        if oc in ("impossible", "blocked", "already_enabled") and not rep.get("reason"):
            problems.append("outcome needs a 'reason' with evidence")
        if oc == "ready" and need_ready_builds:
            lbs = rep.get("local_builds") or []
            if not lbs:
                problems.append("'local_builds' is empty; a green local build with %check is required")
            for lb in lbs:
                logp = lb.get("log", "")
                if not os.path.exists(logp):
                    problems.append(f"local build log {logp!r} does not exist")
                    continue
                txt = open(logp, errors="replace").read()
                if "Executing(%check)" not in txt:
                    problems.append(f"{logp}: no 'Executing(%check)', the tests did not run")
                if not re.search(r"(finished \"build|RPMS/|Wrote: )", txt):
                    problems.append(f"{logp}: no built RPMs in the log, the build did not succeed")
                if not isinstance(lb.get("tests_passed"), int) or lb.get("tests_passed", 0) <= 0:
                    problems.append(f"{logp}: tests_passed must be a positive integer read from the log")
            for k in ("diff_file", "changes_entry", "request_message", "test_history", "targets"):
                if not rep.get(k):
                    problems.append(f"'{k}' is missing")
            if not rep.get("pushback"):
                problems.append("'pushback' is missing. Read the package's past requests "
                                "and their review comments and list the concerns a "
                                "maintainer may have, with evidence and an answer.")
            df = rep.get("diff_file")
            if df and not os.path.exists(df):
                problems.append(f"diff_file {df} does not exist")
        return problems

    def get_report(self, phase, step, retry_note):
        """Read report.json, re-prompting the worker up to 2 times."""
        path = f"{self.phase_dir(phase)}/report.json"
        for attempt in range(3):
            rep = self.read_json(path)
            probs = self.validate_report(phase, rep)
            if not probs:
                self.snapshot(phase, f"report-{step}.json", path)
                return rep
            if attempt == 2:
                break
            self.log(f"report problems: {probs}")
            self.worker(phase, f"{step}-fixreport",
                        "Your report is not acceptable yet:\n- " + "\n- ".join(probs) +
                        f"\n\n{retry_note}\nFix this and rewrite {path}. "
                        "If the problem is that the work cannot be done, set outcome "
                        "to impossible or blocked with the evidence.")
        raise Stop("error_report", f"worker did not produce a valid report: {probs}")

    # ---------------------------------------------------------------- messages
    def csv_row(self):
        try:
            with open(FIX_ORDER) as f:
                for row in csv.DictReader(f):
                    if row["package"] == self.pkg:
                        return row
        except FileNotFoundError:
            pass
        return {}

    def common_header(self, phase):
        pd = self.phase_dir(phase)
        row = self.csv_row()
        gate_txt = {
            "factory": ("Factory submission is enabled. The gate opens only in the "
                        "finalize step (for a git workflow PR: in the publish step, "
                        "as a WIP PR).") if SUBMIT["factory"] else
                       "Factory submission is in DRY RUN mode: never create requests or PRs.",
            "sle": ("SLE submission is enabled. The gate opens only in the finalize step.")
                   if SUBMIT["sle"] else
                   ("SLE submission is in DRY RUN mode: you may branch, commit and build "
                    f"in home:{OBS_USER} projects on IBS and push to your {IBS_GITEA} fork, "
                    "but never create a maintenance request or a PR. The finalize step "
                    "asks you for a plan file instead."),
        }[phase]
        return f"""You are the worker of the unit test enabler. Package: {self.pkg}
Phase: {phase} ({'Phase A, Factory' if phase == 'factory' else 'Phase B, SLE'})
Priority data for this package: {json.dumps(row) if row else 'not in sle15_fix_order.csv'}
{self.validation_hint(row)}

Read first: {PROMPT} (master prompt), ~/.config/opencode/AGENTS.md
(container rules) and every file in {LESSONS}/.

Work directory: {self.wdir}   Phase directory: {pd}
Local builds: `local-build <repo> <arch>` inside the checkout (limit
{LIMITS['local_builds']} builds in this phase; logs land in {pd}/logs).
{gate_txt}
"""

    @staticmethod
    def validation_hint(row):
        if not row.get("list_status"):
            return ""
        return (f"The list row was validated (see /data/SLE15_list_validation.md): "
                f"list_status={row['list_status']}, work_package={row.get('work_package')}. "
                f"Note: {row.get('validation_note') or '-'}\n"
                "work_package is the real source that holds the code (wrappers such as "
                "openssl or dovecot only require it). Work on work_package and its "
                "Factory counterpart, but confirm it yourself first. If the validation "
                "says the tests already run, verify and report already_enabled.\n")

    def report_schema(self, phase):
        pd = self.phase_dir(phase)
        return f"""Write the report to {pd}/report.json (JSON, overwrite it every
time). Fields:
{{
  "outcome": "ready" | "impossible" | "blocked" | "already_enabled",
  "reason": "evidence when not ready (what you found, logs, bugs)",
  "workflow": "obs" | "git",
  "targets": [{{"kind": "obs_devel|git_devel|ibs_maintenance|slfo_git",
               "name": "devel project/package, codestream or repo",
               "checkout": "/work/... path",
               "branch_project": "home:{OBS_USER}:branches:... (osc)",
               "branch_package": "package name in the branch project",
               "git_fork": "fork URL", "git_branch": "branch name"}}],
  "test_history": "never enabled | disabled <when> by <who> because <why> (<bug>); still valid: yes/no + evidence",
  "local_builds": [{{"target": "repo/arch", "log": "{pd}/logs/...log",
                    "tests_run": N, "tests_passed": N, "tests_failed": N,
                    "tests_skipped": N, "check_minutes": N}}],
  "skips": [{{"test": "name", "reason": "cause"}}],
  "diff_file": "{pd}/change.diff (full diff of all changed files vs the base)",
  "changes_entry": "the exact .changes entry text",
  "request_message": "the exact SR/MR/PR message",
  "build_targets": [{{"api": "https://api.opensuse.org|{IBS_API}",
                     "project": "project that builds the change",
                     "package": "package name or null for all"}}],
  "baseline_targets": [{{"api": "...", "project": "devel or update project",
                        "package": "..."}}],
  "no_build_reason": "only when no OBS/IBS project builds the change (git change without a PR in dry run)",
  "pushback": [{{"concern": "what a maintainer may object to",
                "evidence": "measurement or reference",
                "answer": "how the change or the message answers it"}}],
  "requests": ["request or PR URLs you created"],
  "obs_test_counts": [{{"target": "repo/arch", "tests_passed": N, "log": "how you read it"}}],
  "unrelated_failures": [{{"repo": "", "arch": "", "evidence": ""}}],
  "notes": "anything a human must know"
}}
local_builds must list only builds you read yourself; the orchestrator
checks that each log exists and contains Executing(%check)."""

    def msg_implement(self, phase):
        extra = ""
        if phase == "sle":
            fr = self.read_json(f"{self.rdir}/factory/report.json")
            extra = f"""
The Factory phase is done. Its report:
{json.dumps({k: fr.get(k) for k in ('outcome', 'reason', 'targets', 'requests', 'skips', 'test_history', 'local_builds')}, indent=1)}
Factory diff: {self.rdir}/factory/change.diff
Every SLE request must reference the Factory request: {', '.join(fr.get('requests') or []) or '(Factory tests already enabled, cite the Factory change)'}

Scope of this phase: every maintained SLE 15 codestream on IBS
(`osc -A ibs maintained <pkg>`; the SLE package name can differ) via
`osc -A ibs mbranch`, and the SLFO / {IBS_GITEA} repo if one exists.
SLE ships older versions: adapt the change, do not copy the Factory
spec. Do one local build against the oldest SLE 15 codestream you
target (in the mbranch checkout) and one against the newest.
"""
        else:
            extra = f"""
Scope of this phase: Factory only (the devel project, OBS or
src.opensuse.org git workflow). Do not touch IBS or {IBS_GITEA} yet,
except read only (to check history and existing work).
"""
        return self.common_header(phase) + extra + f"""
Do steps 2 to 9 of the master prompt for this phase, up to and
including the local gate (green local build with %check, tests
counted from the log, spec-cleaner, source_validator, changes lint)
and the draft request. The pushback analysis in step 8 is required.
Branch with `osc branch` (or fork for git) but DO NOT `osc ci` or
`git push` yet: the reviewer must approve first.

Write the full diff of your change to the phase directory
(`osc diff` in the checkout, or `git diff` against the base).

If tests already run in this phase's target, set outcome
already_enabled: then the local gate, the branch and the diff do not
apply, and target fields without a value are null. Prove it from a
build log (Executing(%check) followed by test results), not from the
spec text. If the work is impossible or needs a human decision
(see the stop rules of the master prompt), set impossible or blocked
with evidence. Do not loop.

{self.report_schema(phase)}

When the report is written, end your turn with one line: REPORT WRITTEN.
"""

    def recover_verdict(self, who, phase, step, out):
        """No verdict file: take the JSON verdict from the final reply, if any.

        Recovered files carry "_source": "text" so the comparison can tell
        a model that follows the instructions from one that does not.
        """
        if os.path.exists(out):
            return True
        texts = []
        for f in sorted(glob.glob(f"{self.rdir}/agent-logs/*-{phase}-{step}-{who}.jsonl")):
            for line in open(f, errors="replace"):
                if '"text"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("type") == "text":
                    texts.append(e.get("part", {}).get("text", ""))
        for t in reversed(texts):
            for m in reversed(list(re.finditer(r"\{", t))):
                try:
                    obj, _ = json.JSONDecoder().raw_decode(t[m.start():])
                except ValueError:
                    continue
                if isinstance(obj, dict) and obj.get("verdict"):
                    obj["_source"] = "text"
                    with open(out, "w") as fh:
                        json.dump(obj, fh, indent=1)
                    self.log(f"{who} {phase}/{step}: verdict recovered from its reply")
                    return True
        return False

    def shadow_view(self, who, tag=""):
        """A results view for one shadow reviewer, without other verdicts.

        Links what the primary reviewer also works from (the worker report,
        the diff, the build logs, the shadow's own earlier reviews) and
        leaves out everything that reveals another reviewer's verdict: the
        other review files, agent transcripts, state.json, the orchestrator
        log and learnings. opencode denies shadows any access to /results.
        """
        view = f"/work/.shadow-view/{who}/{self.pkg}{'-' + tag if tag else ''}"
        shutil.rmtree(view, ignore_errors=True)
        verdict = re.compile(r"^(claim-)?review-\d+(\..+)?\.json$")
        for phase in ("factory", "sle"):
            src = f"{self.rdir}/{phase}"
            if not os.path.isdir(src):
                continue
            os.makedirs(f"{view}/{phase}")
            for name in os.listdir(src):
                if verdict.match(name) and not name.endswith(f".{who}.json"):
                    continue
                os.symlink(f"{src}/{name}", f"{view}/{phase}/{name}")
        return view

    def shadow_review(self, who, phase, step, msg, out):
        """Run one shadow on msg (written for the real paths) inside its view."""
        try:
            view = self.shadow_view(who, f"{phase}-{step}")   # one view per round
            vout = out.replace(self.rdir, view)
            if os.path.islink(vout):
                os.unlink(vout)
            note = (f"\n\nYour copy of the run's results is {view}. Where a file names "
                    f"{self.rdir}/..., read it as {view}/... (the same file; /results itself "
                    "is not accessible to you). Review independently.\n")
            self.agent(who, msg.replace(self.rdir, view) + note, phase, step, final=True)
            if os.path.isfile(vout) and not os.path.islink(vout):
                shutil.copy(vout, out)
        except Exception as e:
            self.log(f"{who} {phase}/{step} failed: {e}")
        finally:
            self.mark_failed(who, phase, step, out)

    def mark_failed(self, who, phase, step, out):
        """A shadow that wrote no verdict leaves a record with the reason."""
        if self.recover_verdict(who, phase, step, out):
            return
        err = "no review file written"
        for f in sorted(glob.glob(f"{self.rdir}/agent-logs/*-{phase}-{step}-{who}.jsonl")):
            m = re.findall(r'"type":"error".*?"message":"((?:[^"\\]|\\.){0,300})', open(f, errors="replace").read())
            if m:
                err = m[-1]
        with open(out, "w") as fh:
            json.dump({"verdict": None, "_error": err}, fh)

    def reviewed(self, phase, step, kind, rnd, make_msg, out):
        """Primary review plus the shadow reviewers, in parallel.

        make_msg(out_path) builds the prompt; each reviewer writes its own
        file. Only the primary result is returned. Every round is recorded
        in the phase's review history.
        """
        started = ts()
        results = {}
        # Keep what this round reviewed: change.diff is overwritten later.
        diff = f"{self.phase_dir(phase)}/change.diff"
        if os.path.exists(diff):
            shutil.copy(diff, out.replace(".json", ".diff"))

        def run(who, path):
            try:                            # a shadow failure never stops the run
                self.shadow_review(who, phase, step, make_msg(path), path)
            except Exception as e:
                self.log(f"{who} {step} failed: {e}")
            results[who] = self.read_json(path)

        threads = []
        for who in SHADOWS:
            path = out.replace(".json", f".{who}.json")
            t = threading.Thread(target=run, args=(who, path), daemon=True)
            t.start()
            threads.append(t)
        self.agent(REVIEWER, make_msg(out), phase, step)
        self.recover_verdict(REVIEWER, phase, step, out)
        rv = self.read_json(out)
        for t in threads:
            t.join(timeout=REVIEWER_TIMEOUT + 600)

        def counts(r):
            c = {}
            for f in r.get("findings") or []:
                c[f.get("severity", "?")] = c.get(f.get("severity", "?"), 0) + 1
            return c
        with self.lock:
            self.ph(phase).setdefault("reviews", []).append({
                "step": step, "kind": kind, "round": rnd, "started": started, "finished": ts(),
                "verdict": rv.get("verdict"), "findings": counts(rv),
                "shadows": {w: {"model": SHADOWS[w], "verdict": results.get(w, {}).get("verdict"),
                                "findings": counts(results.get(w, {}))} for w in SHADOWS}})
            self.save()
        return rv

    def msg_review(self, phase, rnd, context, out=None):
        pd = self.phase_dir(phase)
        out = out or f"{pd}/review-{rnd}.json"
        prev = ""
        suffix = re.sub(r"^review-\d+|\.json$", "", os.path.basename(out))   # "" or ".shadow-x"
        for i in range(1, rnd):
            if os.path.exists(f"{pd}/review-{i}{suffix}.json"):
                prev += f"\nPrevious review {i}: {pd}/review-{i}{suffix}.json"
        return f"""You are the reviewer of the unit test enabler: a senior openSUSE /
SUSE packager. Be strict about correctness and honest evidence, and
pragmatic about taste. You do not edit files and you never commit,
push or submit.

Package: {self.pkg}   Phase: {phase}   Review round: {rnd} of {LIMITS['review_rounds']}
Context: {context}
Read: the master prompt {PROMPT}, the lessons in {LESSONS}/, the
worker report {pd}/report.json, the diff named in it, and the local
build logs in {pd}/logs/ (read the logs yourself, do not trust the
report). The checkout paths are in the report targets; you may run
read only commands there (osc cat, osc rdiff, rpmspec -P, grep, git
log, obs-results, the bugzilla MCP tools).{prev}

Check at least:
1. The tests really ran: Executing(%check) in the log, counts in the
   report match the log, no "0 tests", no masked failures (|| :,
   || true, exit 0, non-fatal %check).
2. Every skip is needed, as narrow as possible, named, and commented
   with its cause. The suite is not gutted (stop rule: about half).
3. The change is minimal: only needed BuildRequires (sorted, one per
   line), no unrelated edits, guards correct for shared specs
   (suse_version > 1500 when Leap 15 / SLE 15 build from the spec),
   sed edits fail loudly, no smuggled dependencies, no debug left.
4. Test history: checked, and when tests were disabled before, the
   reason was verified to no longer apply and is summarised briefly
   in .changes and the request message.
5. .changes entry and request message: short, plain, 72 columns, no
   em or en dashes, no AI wording, real UTC date, matches the diff,
   test counts match the logs.
6. Phase rules: Factory first; SLE adapted to the older version and
   references the Factory request.
7. The final diff was built: the newest local build log with a green
   %check is newer than the last change to the spec, patches and
   sources in the checkout (compare mtimes with ls -l
   --time-style=full-iso). Text-only .changes edits after it are fine.
8. The pushback analysis. The report lists the concerns a maintainer
   may have, each with evidence and an answer. Check the package's past
   requests and pull request comments yourself and list concerns the
   worker missed. The request message answers the top ones in one or
   two short sentences.
9. Anything that would make a maintainer decline the request.

Verdicts: "approve" (only minor issues or nits left, they go into
findings), "changes_requested" (blocker or major issues), or
"reject_impossible" (the approach cannot work or would gut the suite;
explain).

Write {out} with a bash heredoc, as JSON:
{{"verdict": "...", "summary": "2 to 4 sentences",
  "findings": [{{"severity": "blocker|major|minor|nit", "where": "file:line or log",
                 "problem": "...", "fix": "concrete fix"}}],
  "verified_counts": [{{"target": "repo/arch", "tests_passed": N, "from_log": "path"}}],
  "unrelated_failures_accepted": [{{"repo": "", "arch": ""}}]}}
Only list unrelated_failures_accepted when the context asks you to
judge build failures the worker called unrelated, and you agree with
the evidence.
Your final reply must also contain the same JSON object (if you run out
of tool steps you can only reply in text). End with one line: REVIEW WRITTEN.
"""

    def msg_fix_review(self, phase, rnd):
        pd = self.phase_dir(phase)
        return f"""The reviewer requested changes (round {rnd}). Review: {pd}/review-{rnd}.json
Address every blocker and major finding, and the minor ones that are
cheap. If you disagree with a finding, explain why in the report
notes with evidence (the reviewer reads it next round). After the
changes, run the local gate again (local-build, counts from the log),
regenerate the diff, and rewrite {pd}/report.json. Still no commit or
push. End with: REPORT WRITTEN."""

    def msg_publish(self, phase):
        pd = self.phase_dir(phase)
        if phase == "factory":
            how = """- OBS devel project: `osc ci` in your home branch checkout with
  a short message. Do NOT create the submit request yet.
- git workflow (src.opensuse.org): push one commit, rebased on the
  base branch, to your fork's feature branch and open the PR with the
  title prefixed "WIP: " (Gitea treats it as a draft). The staging
  bot builds it in home:autogits_obs_staging_bot:<org>:PR:<n>."""
            if not SUBMIT["factory"]:
                how = """- OBS devel project: `osc ci` in your home branch checkout.
- git workflow: push to your fork's feature branch only (DRY RUN, no
  PR). Without a PR there is no staging build; say so in the report."""
        else:
            how = f"""- IBS: `osc ci` in the mbranch checkout(s) (home:{OBS_USER} project),
  which builds all codestreams there. No maintenance request yet.
- {IBS_GITEA} (SLFO), if in scope: push to your fork's feature branch.
  """ + ("Open the PR with a \"WIP: \" title prefix." if SUBMIT["sle"] else
         "No PR (DRY RUN).")
        return f"""The reviewer approved. Publish the change for building:
{how}
Then fill build_targets (the project(s) that build the change, with
the package name) and baseline_targets (the devel or update project
and package that build the unchanged version, for comparison) in
{pd}/report.json, plus any PR URL in requests. Do not wait for the
builds: the orchestrator polls them. End with: REPORT WRITTEN."""

    def msg_fix_obs(self, phase, rnd, failures, pre_existing):
        pd = self.phase_dir(phase)
        fl = "\n".join(f"  {f['api']} {f['project']} {f['package']} {f['repo']}/{f['arch']}: "
                       f"{f['code']} {f['details']}" for f in failures)
        pe = "\n".join(f"  {f['repo']}/{f['arch']}: {f['code']}" for f in pre_existing) or "  none"
        return f"""Build round {rnd} of {LIMITS['obs_rounds']} finished with failures that the
baseline does not have (or that have no baseline):
{fl}
Failures the baseline has too (ignored): 
{pe}

Fetch the log of EVERY failed repository (`osc -A <api> remotebuildlog
<project> <package> <repo> <arch>`), save each one to {pd}/logs/, and
group the failures by cause and codestream. Then either:
- fix them locally (local-build, all known causes in one go), update
  the diff, .changes and the report; do not commit or push yet, the
  reviewer looks at the fix first; or
- if a failure is not caused by the change (infrastructure, worker
  problem, the same failure without the change), list it in
  unrelated_failures with the evidence. The reviewer must agree.
- if it cannot be fixed, set outcome impossible with the evidence.
Rewrite {pd}/report.json. End with: REPORT WRITTEN."""

    def msg_push_fix(self, phase):
        return """The reviewer approved the fix. Commit or push it the same way as
before (osc ci in the home branch, or push to the same fork branch;
for a git PR keep one commit and force-push the feature branch only).
No new request yet (that happens in the finalize step). Update the
report.
End with: REPORT WRITTEN."""

    def msg_finalize(self, phase, results_txt, coverage=""):
        pd = self.phase_dir(phase)
        submit = SUBMIT[phase]
        fr = self.read_json(f"{self.rdir}/factory/report.json") if phase == "sle" else {}
        if submit and phase == "factory":
            act = """Create the request now:
- OBS: `osc sr` from your home branch to the devel project, with the
  request message from the report (update the counts to the OBS
  results first). If one exists already, supersede it (`osc sr -s`).
- git: remove the "WIP: " prefix from the PR title and put the
  verification table (repo/arch, tests passed) into the PR
  description."""
        elif submit and phase == "sle":
            act = f"""Create the requests now:
- IBS: `osc -A ibs mr` (maintenancerequest) from the mbranch project,
  message references the Factory request {', '.join(fr.get('requests') or [])}.
- {IBS_GITEA}: remove the "WIP: " prefix and add the verification
  table to the PR description, if in scope."""
        else:
            act = f"""DRY RUN: do not create any request or PR. Write
{pd}/submit_plan.md with, for each request you would create: the
exact command(s), the target, and the exact message (with the
verified counts and the Factory reference). A human will run it."""
        return f"""All builds are done. Current results:
{results_txt}

1. For every repository that succeeded, read the build log enough to
   confirm %check ran and to take the test count (`osc remotebuildlog`).
   Fill obs_test_counts in the report.
2. {act}
3. Put every request/PR URL into requests and rewrite
   {pd}/report.json. End with: REPORT WRITTEN.

Target coverage:
{coverage or "  all targets the devel project builds are final"}
If a target is out of scope or still pending, say so in one or two short
sentences in the request message, with the reason. A maintainer should not
discover a missing target on their own."""

    # ---------------------------------------------------------------- review
    def review_loop(self, phase, context, rounds_key="review_round"):
        """Returns the final review dict when approved; raises Stop otherwise."""
        ph = self.ph(phase)
        pd = self.phase_dir(phase)
        while True:
            ph[rounds_key] += 1
            rnd = ph[rounds_key]
            self.save()
            if rnd > LIMITS["review_rounds"]:
                raise Stop("limit_review_rounds",
                           f"reviewer not satisfied after {LIMITS['review_rounds']} rounds")
            kind = "initial" if not ph.get("obs_round") else f"after build round {ph['obs_round']}"
            rv = self.reviewed(phase, f"review{rnd}", kind, rnd,
                               lambda o: self.msg_review(phase, rnd, context, o),
                               f"{pd}/review-{rnd}.json")
            verdict = rv.get("verdict")
            self.log(f"review round {rnd}: {verdict}")
            if verdict == "approve":
                minor = [f for f in rv.get("findings") or []
                         if f.get("severity") in ("minor", "nit")]
                if minor:
                    self.worker(phase, f"polish{rnd}", f"""The reviewer approved (round {rnd}) with minor findings:
{pd}/review-{rnd}.json
Apply the ones that only touch text: the .changes entry, the request
message, spec comments. Skip any that would change spec code (note
them in the report notes instead), so no rebuild is needed. Update the
diff file and {pd}/report.json. End with: REPORT WRITTEN.""")
                    self.get_report(phase, f"polish{rnd}", "Apply the text-only findings.")
                return rv
            if verdict == "reject_impossible":
                raise Stop("impossible", f"reviewer: {rv.get('summary')}")
            if verdict != "changes_requested":
                self.log("reviewer wrote no valid verdict; counting the round and retrying")
                continue
            self.worker(phase, f"fixreview{rnd}", self.msg_fix_review(phase, rnd))
            rep = self.get_report(phase, f"fixreview{rnd}",
                                  "Address the review and pass the local gate again.")
            if rep["outcome"] != "ready":
                raise Stop(rep["outcome"], rep.get("reason", ""))

    def verify_claim(self, phase, rep):
        """The reviewer checks impossible/blocked/already_enabled claims.

        Returns the final report: either the confirmed claim, or a ready
        report after the worker did the work the reviewer showed was
        possible.
        """
        ph = self.ph(phase)
        while True:
            ph["claim_round"] = ph.get("claim_round", 0) + 1
            rnd = ph["claim_round"]
            self.save()
            if rnd > 2:
                self.log("claim disputed twice; keeping the worker's claim for a human")
                rep["reason"] = (rep.get("reason", "") + " [reviewer disputed this claim, "
                                 f"see {self.phase_dir(phase)}/claim-review-*.json]")
                return rep
            out = f"{self.phase_dir(phase)}/claim-review-{rnd}.json"
            msg_t = f"""You are the reviewer of the unit test enabler. Package {self.pkg},
phase {phase}. The worker did not make a change and claims outcome
"{rep.get('outcome')}" with this reason:

@@REASON@@

Full report: {self.phase_dir(phase)}/report.json. Master prompt:
{PROMPT}. Verify the claim independently with read only commands
(osc cat / osc ls / osc rdiff / remotebuildlog on the right project
and package, git log, bugzilla MCP tools). Look for the mistakes a worker makes: the
wrong package (a wrapper or meta package, a renamed SLE package such
as openssl-1_1 or freerdp2), a %check that exists but runs 0 tests or
is masked, tests that run in Factory but not in a SLE codestream, a
blocker that a narrow skip would solve.

Write @@OUT@@ with a bash heredoc, JSON:
{{"verdict": "confirm" | "dispute", "summary": "...",
  "findings": [{{"severity": "blocker|major|minor", "where": "...",
                "problem": "...", "fix": "what the worker must do"}}]}}
Your final reply must also contain the same JSON object (if you run out
of tool steps you can only reply in text). End with: REVIEW WRITTEN."""
            reason = str(rep.get("reason"))
            rv = self.reviewed(phase, f"claim{rnd}", "claim", rnd,
                               lambda o: msg_t.replace("@@OUT@@", o).replace("@@REASON@@", reason),
                               out)
            self.log(f"claim review {rnd}: {rv.get('verdict')}")
            if rv.get("verdict") != "dispute":
                rep["claim_confirmed_by_reviewer"] = rv.get("summary")
                return rep
            self.worker(phase, f"claimfix{rnd}",
                        f"The reviewer disputes your claim: {out}\nRe-check the "
                        "findings. Either do the work (local gate, diff, report with "
                        "outcome ready) or rewrite the report with stronger evidence "
                        "for your claim. End with: REPORT WRITTEN.")
            rep = self.get_report(phase, f"claimfix{rnd}", "Answer the dispute.")
            if rep.get("outcome") == "ready":
                return rep

    # ---------------------------------------------------------------- builds
    def fetch(self, t):
        from importlib.machinery import SourceFileLoader
        mod = SourceFileLoader("obs_results", "/opt/enabler/bin/obs-results").load_module()
        rows = mod.results(t["api"], t["project"], t.get("package"))
        pkg = t.get("package")
        if pkg:
            rows = [r for r in rows if r["package"] == pkg or r["package"].startswith(pkg + ":")]
        for r in rows:
            r["api"], r["project"] = t["api"], t["project"]
        return rows

    def srcmd5(self, api, prj, pkg):
        """Current source revision of a package, or None on error."""
        import xml.etree.ElementTree as ET
        try:
            x = subprocess.run(["osc", "-A", api, "api",
                                f"/source/{prj}/{pkg}?expand=1"],
                               capture_output=True, text=True, timeout=120)
            if x.returncode != 0:
                return None
            return ET.fromstring(x.stdout).get("srcmd5")
        except Exception:
            return None

    def stale(self, rows):
        """Rows whose last build job is not of the current sources.

        Guards against reading the result of the previous revision in the
        minutes before OBS marks the package dirty after a commit.
        """
        import xml.etree.ElementTree as ET
        out = []
        for r in rows:
            if r["code"] not in ("succeeded", "failed"):
                continue
            base = r["package"].split(":")[0]
            cur = self.srcmd5(r["api"], r["project"], base)
            if not cur:
                continue
            try:
                x = subprocess.run(["osc", "-A", r["api"], "api",
                                    f"/build/{r['project']}/{r['repo']}/{r['arch']}/_jobhistory"
                                    f"?package={r['package']}"],
                                   capture_output=True, text=True, timeout=120)
                if x.returncode != 0:
                    continue
                jobs = ET.fromstring(x.stdout).findall("jobhist")
                if jobs and jobs[-1].get("srcmd5") and jobs[-1].get("srcmd5") != cur:
                    out.append(r)
            except Exception as e:
                self.log(f"stale check skipped for {r['repo']}/{r['arch']}: {e}")
        return out

    def baseline_codes(self, rep):
        """What the devel project does on the same repo/arch, per (repo, arch)."""
        base = {}
        for t in rep.get("baseline_targets") or []:
            try:
                for r in self.fetch(t):
                    base[(r["repo"], r["arch"])] = r["code"]
            except Exception as e:
                self.log(f"baseline fetch failed for {t}: {e}")
        return base

    def wait_builds(self, phase, rep):
        """Wait for the in-scope targets, not for every inherited one.

        A target is out of scope when the devel project does not build it at
        all, or builds it as disabled, blocked, unresolvable or failed. The
        branch then has no business waiting for it.

        A target is given up when it is not final after TARGET_GRACE_HOURS,
        or when it rebuilds BUILD_CHURN_LIMIT times with unchanged sources.
        After BUILD_WAIT_HOURS the whole wait ends and whatever is still
        building is reported as pending.
        """
        targets = rep.get("build_targets") or []
        if not targets:
            raise Stop("error_report", "no build_targets in the report after publish")
        base = self.baseline_codes(rep)
        self.log(f"waiting for builds: {targets}")
        self.log(f"baseline codes: {len(base)} repo/arch entries")

        def out_of_scope(r):
            if not base:
                return False
            code = base.get((r["repo"], r["arch"]))
            return code is None or code in ("disabled", "excluded", "blocked",
                                            "unresolvable", "broken", "failed")

        started = now()
        last_final = {}
        churn = {}
        src = {}
        waiting_since = {}
        give_up = set()
        stale_since = None
        while True:
            self.check_deadline()
            try:
                rows = [r for t in targets for r in self.fetch(t)]
            except Exception as e:
                self.log(f"poll error: {e}")
                time.sleep(POLL_SECONDS)
                continue
            out = [r for r in rows if out_of_scope(r)]
            scope = [r for r in rows if not out_of_scope(r)]
            active = []
            for r in scope:
                key = (r["repo"], r["arch"])
                final = r["code"] in FINAL_CODES and not r["dirty"]
                if final:
                    last_final[key] = r
                    waiting_since.pop(key, None)
                    continue
                if key in last_final:
                    base_pkg = r["package"].split(":")[0]
                    cur = self.srcmd5(r["api"], r["project"], base_pkg)
                    old = src.setdefault(key, cur)
                    if cur and old and cur != old:
                        self.log(f"{key}: sources changed, churn reset")
                        churn[key] = 0
                        src[key] = cur
                    else:
                        churn[key] = churn.get(key, 0) + 1
                        self.log(f"{key}: rebuilt with unchanged sources "
                                 f"({churn[key]}/{CHURN_LIMIT})")
                        if churn[key] >= CHURN_LIMIT:
                            give_up.add(key)
                    last_final.pop(key, None)
                waiting_since.setdefault(key, now())
                if key in give_up:
                    continue
                if (now() - waiting_since[key]).total_seconds() > TARGET_GRACE_HOURS * 3600:
                    self.log(f"giving up on {key} after {TARGET_GRACE_HOURS}h without a final result")
                    give_up.add(key)
                    continue
                active.append(r)
            pending = [r for r in scope
                       if (r["repo"], r["arch"]) in give_up
                       and not (r["code"] in FINAL_CODES and not r["dirty"])]
            if not active:
                st = [r for r in self.stale(scope) if not out_of_scope(r)]
                if st:
                    stale_since = stale_since or now()
                    if (now() - stale_since).total_seconds() < 2 * 3600:
                        self.log(f"{len(st)} results are from older sources; waiting")
                        time.sleep(POLL_SECONDS)
                        continue
                    self.log("stale-source check still differs after 2h, ignoring it")
                done = [r for r in scope if r["code"] in FINAL_CODES and not r["dirty"]]
                self.log(f"wait done: {len(done)} green/final, {len(pending)} given up, "
                         f"{len(out)} out of scope")
                return rows, pending, out
            if (now() - started).total_seconds() > BUILD_WAIT_HOURS * 3600:
                self.log(f"build wait limit reached after {BUILD_WAIT_HOURS}h, "
                         f"{len(active)} still building")
                return rows, pending + active, out
            self.log(f"builds: {len(scope) - len(active) - len(pending)}/{len(scope)} in scope final, "
                     f"{len(pending)} given up, {len(out)} out of scope")
            time.sleep(POLL_SECONDS)

    def classify(self, phase, rep, rows, out=(), pending=()):
        """Split real failures from pre-existing ones and out-of-scope targets."""
        base = self.baseline_codes(rep)
        ignored = {(x["repo"], x["arch"]) for x in self.ph(phase)["ignored_failures"]}
        skip = {(r["repo"], r["arch"]) for r in list(out) + list(pending)}
        new, pre = [], []
        for r in rows:
            key = (r["repo"], r["arch"])
            if key in skip:
                continue
            if r["code"] not in PROBLEM_CODES:
                continue
            if key in ignored:
                pre.append(r)
            elif key not in base:
                # The devel does not build this repo/arch at all.
                pre.append(r)
            elif base.get(key) in PROBLEM_CODES | {"disabled", "excluded"}:
                pre.append(r)
            else:
                new.append(r)
        return new, pre

    @staticmethod
    def table(rows):
        return "\n".join(f"  {r['project']} {r['package']} {r['repo']}/{r['arch']}: {r['code']}"
                         for r in rows)

    # ---------------------------------------------------------------- phase
    def done(self, phase, step):
        return step in self.ph(phase)["steps_done"]

    def mark(self, phase, step):
        self.ph(phase)["steps_done"].append(step)
        self.save()

    def run_phase(self, phase):
        ph = self.ph(phase)
        if ph["status"] in ("finished",):
            return ph["outcome"]
        ph["status"] = "running"
        self.save()
        self.close_all_gates()

        if not self.done(phase, "implement"):
            self.worker(phase, "implement", self.msg_implement(phase), new_session=True)
            rep = self.get_report(phase, "implement", "Finish the steps and write the report.")
            self.mark(phase, "implement")
        rep = self.read_json(f"{self.phase_dir(phase)}/report.json")
        if rep.get("outcome") != "ready":
            rep = self.verify_claim(phase, rep)
        if rep.get("outcome") != "ready":
            raise Stop(rep.get("outcome"), rep.get("reason", ""))

        if not self.done(phase, "review"):
            self.review_loop(phase, "initial review before anything is committed or pushed")
            self.mark(phase, "review")

        if not self.done(phase, "publish"):
            if phase == "factory" and SUBMIT["factory"] and rep.get("workflow") == "git":
                self.gate("submit_factory")
            self.worker(phase, "publish", self.msg_publish(phase))
            self.close_all_gates()
            rep = self.get_report(phase, "publish", "Publish and fill build_targets.")
            self.mark(phase, "publish")

        rows = []
        while not self.done(phase, "builds_green"):
            rep = self.read_json(f"{self.phase_dir(phase)}/report.json")
            if not rep.get("build_targets"):
                if (rep.get("workflow") == "git" and not SUBMIT[phase]) or rep.get("no_build_reason"):
                    self.log(f"no build project: {rep.get('no_build_reason') or 'git dry run, no PR'}")
                    break
            rows, pending, out = self.wait_builds(phase, rep)
            new, pre = self.classify(phase, rep, rows, out, pending)
            ph["out_of_scope"] = [f"{r['repo']}/{r['arch']}:{r['code']} (devel: "
                                  f"{self.baseline_codes(rep).get((r['repo'], r['arch']), 'not built')})"
                                  for r in out] if out else []
            self.log(f"build result: {len(new)} new failures, {len(pre)} pre-existing, "
                     f"{len(pending)} still pending")
            if not new:
                ph["pending_at_end"] = [f"{r['repo']}/{r['arch']}:{r['code']}" for r in pending]
                self.save()
                self.mark(phase, "builds_green")
                break
            ph["obs_round"] += 1
            self.save()
            if ph["obs_round"] > LIMITS["obs_rounds"]:
                raise Stop("limit_obs_rounds",
                           f"still failing after {LIMITS['obs_rounds']} build rounds:\n" +
                           self.table(new))
            rnd = ph["obs_round"]
            self.worker(phase, f"fixobs{rnd}", self.msg_fix_obs(phase, rnd, new, pre))
            rep = self.read_json(f"{self.phase_dir(phase)}/report.json")
            if rep.get("outcome") != "ready":
                raise Stop(rep.get("outcome", "error_report"), rep.get("reason", ""))
            claimed = rep.get("unrelated_failures") or []
            ctx = (f"fix after build round {rnd}. Failures: \n{self.table(new)}\n"
                   f"The worker calls these unrelated: {json.dumps(claimed)}. "
                   "Judge them and the fix.")
            rv = self.review_loop(phase, ctx)
            accepted = rv.get("unrelated_failures_accepted") or []
            ph["ignored_failures"] += accepted
            self.save()
            new_keys = {(r["repo"], r["arch"]) for r in new}
            acc_keys = {(a.get("repo"), a.get("arch")) for a in accepted}
            if new_keys <= acc_keys:
                self.log("all new failures accepted as unrelated")
                ph["pending_at_end"] = []
                self.mark(phase, "builds_green")
                break
            self.worker(phase, f"pushfix{rnd}", self.msg_push_fix(phase))
            self.close_all_gates()

        if not self.done(phase, "finalize"):
            if SUBMIT[phase]:
                self.gate(f"submit_{phase}")
            cov = []
            if ph.get("out_of_scope"):
                cov.append("Out of scope, the devel project does not build these:")
                cov += ["  " + x for x in ph["out_of_scope"]]
            if ph.get("pending_at_end"):
                cov.append("Still building or queued at the end:")
                cov += ["  " + x for x in ph["pending_at_end"]]
            self.worker(phase, "finalize",
                        self.msg_finalize(phase, self.table(rows) or "  (none)",
                                          "\n".join(cov)))
            self.close_all_gates()
            rep = self.get_report(phase, "finalize", "Finalize and list the requests.")
            if SUBMIT[phase] and not rep.get("requests"):
                raise Stop("error_report", "finalize did not record any request URL")
            if not SUBMIT[phase] and not os.path.exists(f"{self.phase_dir(phase)}/submit_plan.md"):
                raise Stop("error_report", "dry run finalize did not write submit_plan.md")
            self.mark(phase, "finalize")

        ph["status"] = "finished"
        ph["outcome"] = "submitted" if SUBMIT[phase] else "dry_run_ready"
        self.save()
        return ph["outcome"]

    # ---------------------------------------------------------------- replay
    def replay_shadows(self):
        """Run the shadow reviewers on finished runs, for comparison.

        Only the last review round of each phase is replayed: the checkout
        in /work matches that round only. The shadow gets the primary's
        exact prompt, with its own output file.
        """
        if not SHADOWS:
            raise SystemExit("set SHADOW_REVIEWERS")
        jobs = []
        for phase in ("factory", "sle"):
            prompts = sorted(glob.glob(f"{self.rdir}/agent-logs/*-{phase}-*-reviewer.prompt.md"))
            last = [p for p in prompts if re.search(r"-(review|claim)\d+-reviewer", p)]
            if not last:
                continue
            p = last[-1]
            step = re.search(r"-(review\d+|claim\d+)-reviewer", p).group(1)
            kind = "claim-review" if step.startswith("claim") else "review"
            primary = f"{self.phase_dir(phase)}/{kind}-{re.sub(r'\D', '', step)}.json"
            msg = open(p).read()
            if "final reply must also contain" not in msg:
                msg += ("\nYour final reply must also contain the same JSON object (if you "
                        "run out of tool steps you can only reply in text).\n")
            if primary not in msg:
                self.log(f"replay: {p} does not name {primary}; skipped")
                continue
            for who in SHADOWS:
                out = primary.replace(".json", f".{who}.json")
                if os.path.exists(out):
                    continue
                m = re.sub(r"(Previous review \d+: \S+?/(?:claim-)?review-\d+)\.json",
                           rf"\1.{who}.json", msg)
                jobs.append((who, m.replace(primary, out), phase, f"{step}", out))
        threads = []
        for who, msg, phase, step, out in jobs:
            def run(who=who, msg=msg, phase=phase, step=step, out=out):
                try:
                    self.shadow_review(who, phase, step, msg, out)
                except Exception as e:
                    self.log(f"replay {who} {phase}/{step} failed: {e}")
            t = threading.Thread(target=run, daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        self.save()
        self.log(f"replay: {len(jobs)} shadow review(s) done")

    # ---------------------------------------------------------------- learnings
    def learnings(self, outcome, reason):
        """results/<pkg>/learnings.md: the worker's lessons + mechanical facts."""
        out = f"{self.rdir}/learnings.md"
        phases = [p for p in ("sle", "factory") if self.ph(p).get("worker_session")]
        if phases and not os.path.exists(f"{self.rdir}/learnings-agent.md"):
            phase = phases[0]
            msg = f"""The run for {self.pkg} ended with outcome "{outcome}": {reason[:600]}

Write {self.rdir}/learnings-agent.md for the people who maintain this
system and its prompt. Read your own reports, the review-*.json and
claim-review-*.json files under {self.rdir}/ if there are any, and the
logs you saved. Be
specific and evidence-based: name the file, the log line, the command.
No praise, no summary of the work, nothing you did not observe.

Use exactly these sections (write "none" when a section is empty):

## Package facts
What a person (or the next run) must know about this package: where it
lives, wrappers or renames, test harness, needed BuildRequires, skips
and why, codestream differences, build quirks. Bullets.

## Reusable lessons
Lessons that apply to OTHER packages: a trap, a technique, a check that
caught a mistake. Each bullet: the lesson, then "Evidence:" with the
concrete case.

## Prompt and tooling problems
Where the master prompt, AGENTS.md, local-build, obs-results, the guard
or the orchestrator misled you, blocked you without reason, or was
missing something. Each bullet: what happened and a concrete fix.

## Reviewer findings you agreed with
The findings that changed the result, one line each.

Keep it under 80 lines. End with: LEARNINGS WRITTEN."""
            try:
                self.agent(WORKER, msg, phase, "learnings",
                           session=self.ph(phase)["worker_session"], final=True)
            except Exception as e:
                self.log(f"learnings call failed: {e}")
        parts = [f"# {self.pkg}: learnings ({ts()})\n",
                 f"Outcome: {outcome}. {reason[:500]}\n"]
        if os.path.exists(f"{self.rdir}/learnings-agent.md"):
            parts.append(open(f"{self.rdir}/learnings-agent.md").read().strip()
                         .replace("LEARNINGS WRITTEN", "").strip() + "\n")
        else:
            parts.append("(no worker learnings: the worker never ran or the call failed)\n")
        # Mechanical facts, no model involved.
        facts = ["## Run facts (orchestrator)"]
        for p in ("factory", "sle"):
            ph = self.ph(p)
            if ph.get("status", "pending") == "pending":
                continue
            facts.append(f"- {p}: outcome {ph.get('outcome')}, review rounds "
                         f"{ph.get('review_round', 0)}, build rounds {ph.get('obs_round', 0)}, "
                         f"claim reviews {ph.get('claim_round', 0)}, local builds "
                         f"{open(self.phase_dir(p) + '/local_build_count').read().strip() if os.path.exists(self.phase_dir(p) + '/local_build_count') else 0}")
            for f in sorted(glob.glob(f"{self.phase_dir(p)}/review-*.json")) + \
                    sorted(glob.glob(f"{self.phase_dir(p)}/claim-review-*.json")):
                rv = self.read_json(f)
                for x in rv.get("findings") or []:
                    if x.get("severity") in ("blocker", "major"):
                        facts.append(f"  - {os.path.basename(f)} {x.get('severity')}: "
                                     f"{str(x.get('problem'))[:220]}")
        if os.path.exists(f"{self.rdir}/guard.log"):
            g = open(f"{self.rdir}/guard.log").read().strip().splitlines()
            facts.append(f"- guard blocked {len(g)} command(s):")
            facts += [f"  - {l[:200]}" for l in g[-10:]]
        try:
            tot = self.costs()
            facts.append(f"- cost: ${tot['usd']:.2f} priced; {tot['unpriced_tokens'] / 1e6:.1f}M "
                         "unpriced tokens (see cost.json)")
        except Exception:
            pass
        parts.append("\n".join(facts) + "\n")
        tmp = out + ".tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(parts))
        os.replace(tmp, out)
        self.log("learnings written")

    # ---------------------------------------------------------------- record
    def record(self, outcome, reason):
        self.state["outcome"] = outcome
        self.state["reason"] = reason
        self.state["finished"] = ts()
        self.save()
        fr = self.read_json(f"{self.rdir}/factory/report.json")
        sr = self.read_json(f"{self.rdir}/sle/report.json")

        def counts(rep):
            c = [x for x in (rep.get("obs_test_counts") or rep.get("local_builds") or [])
                 if isinstance(x, dict)]
            if len(c) <= 6:
                return ", ".join(f"{x.get('target')}: {x.get('tests_passed')}" for x in c)
            vals = sorted({x.get("tests_passed") for x in c if isinstance(x.get("tests_passed"), int)})
            span = f"{vals[0]}" if len(vals) == 1 else f"{vals[0]} to {vals[-1]}" if vals else "?"
            return f"{len(c)} repo/arch targets, {span} tests passed each (details in report.json)"

        def reqs(rep):
            return " ".join(str(x) for x in (rep.get("requests") or [])) or "-"

        fph, sph = self.ph("factory"), self.ph("sle")
        cells = [
            self.pkg, self.csv_row().get("priority_rank", "?"),
            str(fr.get("test_history") or sr.get("test_history") or "-"),
            reqs(fr), fph.get("outcome") or fph.get("status"),
            ", ".join(str(t.get("name") or "") for t in (sr.get("targets") or [])
                      if t.get("kind") == "ibs_maintenance") or "-",
            reqs(sr) if SUBMIT["sle"] else
            (f"dry run: {self.rdir}/sle/submit_plan.md" if sph.get("outcome") == "dry_run_ready" else "-"),
            ", ".join(str(t.get("git_fork") or "") for t in (sr.get("targets") or [])
                      if t.get("kind") == "slfo_git" and t.get("git_fork")) or "-",
            f"TW: {counts(fr) or '-'} / SLE: {counts(sr) or '-'}",
            "; ".join(f"{s.get('test')} ({s.get('reason')})" for s in
                      (fr.get("skips") or [])[:8] if isinstance(s, dict)) or "-",
            f"[test-enabler {ts()}] outcome {outcome}: {reason[:400]} "
            f"(cost ${self.costs()['usd']:.2f} priced; results: {self.rdir})",
        ]
        row = "| " + " | ".join(str(c).replace("|", "/").replace("\n", " ") for c in cells) + " |\n"
        # Only per-package files here (no shared writes). host/sync-progress.py
        # merges the rows into progress.md under a lock.
        tmp = f"{self.rdir}/progress-row.md.tmp"
        with open(tmp, "w") as f:
            f.write(row)
        os.replace(tmp, f"{self.rdir}/progress-row.md")
        with open("/results/index.jsonl", "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps({"package": self.pkg, "outcome": outcome, "time": ts(),
                                "reason": reason[:400],
                                "factory_requests": fr.get("requests"),
                                "sle_requests": sr.get("requests")}) + "\n")
            fcntl.flock(f, fcntl.LOCK_UN)
        self.log(f"RECORDED outcome={outcome}")


OK = ("submitted", "dry_run_ready", "already_enabled")


def overall(run, phases):
    outs = {p: run.ph(p).get("outcome") for p in phases}
    if all(o == "already_enabled" for o in outs.values()):
        return "already_enabled"
    if all(o in OK for o in outs.values()):
        if any(o == "submitted" for o in outs.values()):
            return "ready_for_human_review"
        return "dry_run_ready"
    return "partial"


def exit_code(outcome):
    if outcome in ("ready_for_human_review", "dry_run_ready", "already_enabled"):
        return 0
    if outcome in ("impossible", "blocked"):
        return 2
    if outcome.startswith("limit"):
        return 3
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("package")
    ap.add_argument("--phases", default=E.get("PHASES", "factory,sle"))
    a = ap.parse_args()
    phases = a.phases.split(",")
    run = Run(a.package)
    run.log(f"start {a.package} phases={a.phases} limits={LIMITS} submit={SUBMIT}")
    outcome, reason = "error", ""
    try:
        for phase in phases:
            if phase == "sle":
                f = run.ph("factory")
                fr = run.read_json(f"{run.rdir}/factory/report.json")
                if "factory" in phases and f.get("outcome") not in OK:
                    raise Stop("blocked", "Factory phase not finished; SLE waits (Factory first)")
                if SUBMIT["sle"] and f.get("outcome") != "already_enabled" and not fr.get("requests"):
                    raise Stop("blocked", "no Factory request exists; SLE submission must wait")
            try:
                res = run.run_phase(phase)
            except Stop as s:
                if s.outcome != "already_enabled":
                    raise
                run.ph(phase).update(status="finished", outcome="already_enabled",
                                     reason=s.reason)
                run.save()
                res = "already_enabled"
            run.log(f"phase {phase}: {res}")
            if res not in OK:
                raise Stop(res, run.ph(phase).get("reason", ""))
        outcome = overall(run, phases)
        reason = "; ".join(f"{p}: {run.ph(p).get('outcome')}" +
                           (f" ({run.ph(p).get('reason', '')[:300]})"
                            if run.ph(p).get("outcome") == "already_enabled" else "")
                           for p in phases)
    except Stop as s:
        outcome, reason = s.outcome or "error", s.reason or ""
        for phase in phases:
            p = run.ph(phase)
            if p["status"] == "running":
                p.update(status="finished", outcome=outcome, reason=reason)
        run.save()
    except Exception as e:
        outcome, reason = "error", f"{type(e).__name__}: {e}"
        run.log(traceback.format_exc())
    finally:
        run.close_all_gates()
        # Build roots are large VM disks; the logs are in /results.
        shutil.rmtree("/work/buildroot", ignore_errors=True)
    try:
        run.learnings(outcome, reason)
    except Exception:
        run.log("learnings failed:\n" + traceback.format_exc())
    try:
        run.record(outcome, reason)
    except Exception:
        run.log("recording failed:\n" + traceback.format_exc())
    sys.exit(exit_code(outcome))


if __name__ == "__main__":
    main()
