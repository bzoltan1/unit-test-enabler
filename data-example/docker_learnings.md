# docker: enabling upstream unit tests — learnings

Package: `docker` (Moby engine), priority rank 2 in `sle15_fix_order.csv`.
Factory SR: <https://build.opensuse.org/request/show/1399583> (supersedes
1399538). SLE Phase B still pending.

## 1. Package-identity trap in the priority data

`classified_v2.csv` / `evidence.csv` listed `docker` with an `upstream_cmd`
of `test -f wmdocker` and "Debian has override_dh_auto_test". That is the
X11 system-tray dock named `docker`, a completely different project from
Moby. The `debian_rules_cache/` was also empty.

Lesson: before trusting the classification, confirm which upstream the
SLE package maps to (here Moby, `github.com/moby/moby`). A "custom"
classification for a name that collides with another project is a red
flag.

## 2. Where the package lives

- Factory devel: `Virtualization:containers/docker`
  (`osc -A https://api.opensuse.org develproject openSUSE:Factory docker`).
- IBS: `SUSE:SLE-15:Update`, `SUSE:SLE-15-SP6:Update`,
  `SUSE:SLE-15-SP7:Update`. Each has a `_link` package `docker` that
  points at `docker.<incident>` (a maintenance incident); read the real spec with
  `osc cat <project> docker.<incident> docker.spec`.
- All three SLE codestreams ship the same source: Docker 29.4.0.
- `osc maintained docker` also reports Gitea branches
  `pool/docker:slfo-1.2` and `slfo-main`.
- @IBS_GITEA@ access is currently blocked: HTTPS asks for a username,
  SSH returns `Permission denied (publickey)`. Needs credentials before
  Phase B/PR work there.

## 3. Upstream test layout

- Engine (`docker-*.tar.xz`) is `github.com/moby/moby/v2`; it has a
  vendor directory and a separate `api/` and `client/` module (no
  vendor, so their tests cannot run offline in OBS — skip them, as
  Fedora does).
- CLI (`docker-cli-*.tar.xz`, `github.com/docker/cli`) ships
  `vendor.mod`/`vendor.sum`, not `go.mod`. The spec's `%build` does
  `cp {vendor,go}.mod` and `cp {vendor,go}.sum`, so by `%check` a
  `go.mod` exists.
- Canonical entry point: `hack/test/unit`. It uses `gotestsum`, runs
  the engine, `api` and `client` modules, and treats `daemon/libnetwork`
  specially (needs iptables and serial execution).
- Fedora's `moby-engine.spec` is the best reference for the ignore list:
  `%gocheck2` with `-s TestX` and `-t <tree>` excludes.

## 4. What the spec change does

Factory (`Virtualization:containers/docker`) revision 5:

- `%check` after `%install`, guarded with
  `%if 0%{?suse_version} > 1500`.
- Engine: `go test -count=1 -v -timeout 15m -tags 'netgo journald'
  -skip "^(...)$" $pkgs`, package list from `go list ./...` minus
  `/vendor/`, `/integration`, `/daemon/libnetwork`,
  `/daemon/graphdriver`.
- CLI: same, package list minus `/vendor/` and `/e2e`.
- `export PATH="%{buildroot}%{_bindir}:${PATH}"` so config tests find
  `docker-proxy`.
- `BuildRequires: git-core` (guarded) for git-based build tests.
- `.changes` mentions the tests and the new BuildRequires.

## 5. RPM stage order matters

`%check` runs **after** `%install` even when written before it in the
spec. This is why the buildroot `docker-proxy` is available to the
config tests once `%{buildroot}%{_bindir}` is on `PATH`. Fedora does the
same (`export PATH="%{buildroot}%{_bindir}:${PATH}"`).

Symptoms without it:

    invalid userland-proxy-path: userland-proxy is enabled, but
    userland-proxy-path is not set

Locally this passed by accident because `/usr/bin/docker-proxy` from the
installed docker package was on `PATH`. Do not trust a local pass for
this class of test.

## 6. Build-environment gotchas

- `git` is not installed in the SUSE buildroot:
  `exec: "git": executable file not found in $PATH`.
  Fix: `BuildRequires: git-core`.
- The OBS build kernel has **no SCTP**:
  `TestSCTP4ProxyNoListener` / `TestSCTP6ProxyNoListener` fail with
  `protocol not supported`. Fedora keeps them enabled because its
  environment differs; here they must be skipped explicitly, with a
  comment.
- The 29.4.0 SLE source imports `daemon/libnetwork/internal/nftables`
  unless `no_libnftables` is set. The SLE spec's `%build` sets
  `DOCKER_BUILDTAGS="no_libnftables"` and has no libnftables
  BuildRequires. For Phase B, add `no_libnftables` to the test tags (or
  add the `pkgconfig(libnftables)` BR, which is the newer Factory
  approach).
- `TestExecSetPlatformOptAppArmor` failed on the host with
  `/sys/kernel/security/apparmor/profiles: permission denied`, but
  passed in OBS. Host `/sys` visibility is not representative.

## 7. `go test -skip` is an unanchored regexp

`-skip TestSCTP4Proxy` also skips `TestSCTP4ProxyNoListener`. Always
anchor:

    -skip "^(${engine_skip})\$"
    -skip "^(${cli_skip})\$"

`-skip` does not print `--- SKIP:` lines; only runtime `t.Skip()` does.
Counting `--- SKIP:` therefore counts runtime skips, not `-skip`
matches.

Verify a skip target exists in the source version before adding it.
`TestCloseRunningCommand` (from Fedora's list) does not exist in 29.4.0
or 29.9.2; the s390x block was dead code and was removed.

## 8. Build tags: upstream vs shipped binary

`hack/test/unit` runs with `-tags 'netgo journald'`. Production
(`hack/make.sh dynbinary`) adds `osusergo static_build nri_no_wasm`
and the spec's `apparmor selinux seccomp pkcs11`, and removes `netgo`
for the dynamic daemon.

Keeping upstream's unit-test tags is the defensible choice: they match
upstream CI and avoid the static-linking assumptions of the shipped
binary. State this in the spec next to the command; a reviewer will ask.

## 9. Local test runs and `/tmp`

- `/tmp` is a 30 GiB tmpfs here and was full (deleted qcow2 files held
  open by old QEMU processes). Compiles failed with
  `no space left on device`.
- `go test` sets the test binary's `TMPDIR` to `GOTMPDIR`, overriding an
  explicit `TMPDIR`. A long `GOTMPDIR` (e.g. under
  `.../sle15_review/.work/gotmp`) makes `t.TempDir()` paths exceed the
  108-byte `sockaddr_un` limit, producing
  `bind: invalid argument` in tests that create unix sockets
  (`TestCreateIfNotExists`, `daemon/logger/fluentd`).
- Practical split: `GOTMPDIR` on the big filesystem for build work, and
  run the last step with a short `GOTMPDIR`/`TMPDIR` (under `/tmp`) for
  socket tests. Never use `$HOME` or `/var` scratch (permission rules).
- Without libseccomp/libbtrfs/libnftables dev packages, many engine
  packages fail to build locally. `osc build` needs sudo, which is not
  available here, so OBS remote builds are the authoritative check.

## 10. OBS / SR mechanics

- `osc branch Virtualization:containers docker`, then `osc co` the branch.
- Committing to the branch triggers builds on every repo/arch of the
  devel project automatically.
- A submit request is pinned to the source revision (`.../docker@5`).
  Later commits do **not** update it; `osc request show --diff` still
  shows the old revision. To update:
  `osc sr -s <old-id> --yes -m "..."`
  which creates a new request and marks the old one superseded.
- `osc results` shows per repo/arch state. Builds of the devel project:
  `openSUSE_Tumbleweed`, `openSUSE_Tumbleweed_and_d_l_g`,
  `16.0`, `16.1` (tests enabled); `15.5`, `15.6`, `15.7`, `5.5`,
  `SLE_12_SP5` (tests must stay disabled).
- aarch64 workers can sit idle while jobs are queued; s390x has a very
  small, heavily backlogged worker pool (100+ queued builds). Waiting for
  s390x can take a long time; check other repos' s390x result as interim
  evidence (Leap 16.1 s390x finished while TW s390x did not).

## 11. Guard rationale

`%if 0%{?suse_version} > 1500` is needed because the devel spec is built
for Leap 15.5/15.6/15.7 (suse_version 1500), Leap Micro 5.5 and SLE 12
SP5 as well as TW/Leap 16. The guard keeps those older builds unchanged.
SLE 15 is not served by this spec; it gets the same change through its
own maintenance spec. Do not write that 15.x "uses its own maintenance
spec" — only SLE 15 does; Leap 15.x uses this spec.

## 12. Verification matrix and counts

Final Factory revision 5 (identical counts everywhere):

    engine: 1036 passed, 39 skipped
    CLI:    1074 passed,  2 skipped

Green:
- `openSUSE_Tumbleweed`: x86_64, aarch64, ppc64le
- `16.0`: x86_64, aarch64, ppc64le
- `16.1`: x86_64, aarch64, ppc64le, s390x
- `openSUSE_Tumbleweed_and_d_l_g`: x86_64, ppc64le (earlier rev)

Still queued: `openSUSE_Tumbleweed` s390x.

## 13. Review feedback that changed the result

1. Guard comment must match reality (Leap 15.x uses this spec).
2. Anchor skip regex; otherwise `TestSCTP4Proxy` silently skips
   `TestSCTP4ProxyNoListener` and the ppc64le block is dead.
3. Wait for aarch64 and s390x before claiming green.
4. Align test tags with `%build` or explain the difference.
5. Nits: `TZ=UTC`, `pipefail` on `go list | grep`, mention `git-core`
   in `.changes`.
6. After anchoring, the SCTP NoListener tests actually ran and exposed
   the missing-SCTP environment, so they were skipped explicitly with
   the error as justification.

## 14. Phase B (SLE 15) preparation notes

- Specs for `SUSE:SLE-15:Update`, `SUSE:SLE-15-SP6:Update` and
  `SUSE:SLE-15-SP7:Update` are identical (29.4.0, 624 lines).
- No `suse_version` guard in the SLE spec — it is SLE 15 only.
- Use `-tags 'netgo journald no_libnftables'` (the SLE `%build` strips
  libnftables).
- Add `git-core`, buildroot-`PATH` and the same anchored skip list; the
  `TestCloseRunningCommand` skip is not needed (test absent).
- 29.4.0 CLI is 1045 tests locally (vs 1074 in 29.9.2); SLE counts must
  be read from IBS logs, not copied from Factory.
- Maintenance request via `osc mbranch` / `osc maintenancerequest`, must
  reference the Factory SR.
- @IBS_GITEA@/slfo work is blocked on credentials.

## 15. Reusable checklist for the next package

1. Confirm the upstream project identity; distrust name collisions.
2. Read Fedora `%check` and Debian `debian/rules` for the test switch.
3. Find the upstream test entry point; note separate modules and vendor.
4. Add `%check` after `%install`; add buildroot `bin` to `PATH` if tests
   exec shipped helpers.
5. Anchor `-skip` regexes; prove the named test exists in this version.
6. Run it in OBS (not just locally); read the actual failure text before
   skipping.
7. Guard shared specs so older/other codestreams are unchanged.
8. Verify on every arch the devel project builds; record exact counts
   from the build log.
9. When the source changes after an SR, supersede it with
   `osc sr -s <id>`.
