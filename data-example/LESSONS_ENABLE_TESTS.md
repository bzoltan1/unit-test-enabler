# Lessons: enabling upstream tests in %check (bouncycastle case)

Case: `java-packages/bouncycastle` PR #5 on src.opensuse.org, bc-java
1.86, Nov 2026. Result: 2137 tests pass on 64-bit, 2108 on i586,
25 staging repositories green. Final commit `c24ff4c`.

This file records what to reuse and what to avoid for the other
packages in the SLE 15 / Factory test-enablement programme.

## 1. What the package actually contained

- The suite is upstream bc-java's Ant build (`ant/jdk18+.xml`, target
  `test`), 57 suites, 2952 test cases when nothing is excluded.
- `bouncycastle-notests.patch` (in place since 2015) removed the
  `build-test` target and its dependency from `build`.
- A large share of the suite reads vectors and certificates from
  **bc-test-data**, a separate ~5 GB repository. `TestResourceFinder`
  looks it up via `-Dbc.test.data.home`, `BC_TEST_DATA_HOME`, or a
  sibling directory, and throws `FileNotFoundException` otherwise.
  Upstream expects it to be checked out next to the source tree; no
  distro ships it (Fedora deletes the affected suites and comments
  out `#test`).
- The GitHub source tarball does not contain it.

## 2. Failure catalogue (all verified, all reusable)

Measure everything on a full local run; do not trust static analysis
for "does this test need the missing thing".

| Cause | Symptom | Fix used |
|---|---|---|
| external test data (5 GB repo) | `FileNotFoundException: Test data directory bc-test-data not found` | drop the suite registrations of the affected classes |
| missing packaged dep | test class does not compile, or `ClassNotFoundException` | drop the class (`X509LDAPCertStoreTest`, unboundid-ldapsdk not packaged) |
| upstream lists suites the crypto backend cannot do | `IllegalStateException: No usable cipher suites enabled`, 22 suites, identical on JDK 17/21/25 | extend the test's own `isIgnored` filter to skip `_DHE_DSS_`/`_DH_DSS_` |
| expired SHA1 fixtures | `CertificateException: Unable to construct a valid chain` | drop that test (certs from 2008) |
| JVM version self-check | `Tests are running on JVM version '25…' but the build expected …` | run with `JAVA_VERSION_PREFIX=any` (upstream-supported) |
| JDK 11 default charset | text comparison shows `?` instead of `é` (MIME quoted-printable test) | `export LC_ALL=C.UTF-8` in `%check` |
| 32-bit JVM heap | `Invalid maximum heap size: -Xmx4096m` (JVM never starts, every suite "ERROR") | probe at build time, see §4 |
| 32-bit heap too small for Argon2 | `Java heap space` in the OpenPGP suites | skip those suites on 32-bit |
| dependency name for activation | `NoClassDefFoundError: com/sun/activation/registries/LogSupport` in S/MIME tests | `BuildRequires: glassfish-activation` (implementation jar), not `-api` |

`%prep` deletes all bundled `*.jar`, so never point the build at a
bundled binary (a first attempt passed `libs/unboundid-ldapsdk-6.0.8.jar`
as `-Dldap.jar.home`, a file already deleted).

## 3. Measuring: the three traps

1. **Aggregators stop at the first failure.** The suite has a JUnit
   test (`SimpleTestTest`) that loops over a `RegressionTest.tests`
   array and calls `fail()` on the first bad entry, and `AllTests`
   classes with inline arrays. One OBS run therefore reveals exactly
   one hidden failure. The other model in this session pushed seven
   times, each round exposing one more.
2. **A JUnit XML parser that silently drops failures.** `el =
   tc.find('failure') or tc.find('error')` is wrong: an element with
   no child elements is false in Python, so `<failure>` (text only)
   is skipped. Use `is not None`.
3. **OBS keeps only the latest log per repository.** Save every local
   build log; once a repo rebuilds, the previous evidence is gone.

Working method: run the full suite once locally, parse the XML
correctly for per-testcase failures, and for the aggregator arrays run
every `SimpleTest` individually. `tools/Audit.java` does the latter:
reflection instantiation + `perform()` + 300 s timeout per class, one
line per class (`OK`/`FAIL`/`THROW`/`TIMEOUT`).

## 4. Spec techniques that were needed

- **Multi-line `%global`/`%define` bodies keep their line breaks.**
  `%global ant_args -f x.xml \` + continuation lines expands into
  several shell commands; only the first line ran, the arguments were
  silently lost, and the build failed in a confusing place. Use a
  shell array in each section, or a one-line macro. Check with
  `rpmspec -P foo.spec`.
- **`%ifarch` never matches in a `noarch` package.** The effective
  target is `noarch`, so `%ifarch %{ix86}` is dead code. Decide at
  build time instead:
  ```
  if java -Xmx4096m -version >/dev/null 2>&1; then
    echo 4096m > bc-junit-memory
  else
    echo 1400m > bc-junit-memory
    rm -v pg/src/test/java/.../openpgp/test/AllTests.java ...
  fi
  ```
  and in `%check`: `JM=$(cat bc-junit-memory)`, `-Djunit.maxmemory=$JM`.
- **Every source edit must fail loudly.** A `sed` whose pattern does
  not match is a silent no-op (two of them in the earlier attempt did
  nothing: the real form was `addTest(CipherSuitesTestSuite.suite())`,
  not `addTestSuite(CipherSuitesTestCase.class)`). Guard:
  ```
  drop_line() { grep -q -- "$2" "$1" || { echo "'$2' not in $1"; exit 1; }; sed -i "\|$2|d" "$1"; }
  ```
- **Deleting one `AllTests.java` breaks its siblings.** Removing
  `pqc/crypto/test/AllTests.java` left 21 other `AllTests*.java` in the
  same directory failing to compile. Remove the whole family
  (`AllTests*.java`) or fix the references.
- Removing an array entry only drops that entry. The class still
  compiles and is still shipped in the test jar; only unused imports
  remain behind.
- A test-only change can still fix packaging bugs: restoring the
  upstream `build` target made `javadoc-util`/`javadoc-pg` succeed
  (0 errors instead of 25) and the bcpg/bcutil javadoc is now shipped
  (143 MB -> 171 MB javadoc package). Mention such side effects in
  `.changes`; they are consumer-visible.

## 5. Verification beyond "it built"

- **Compare the shipped payload with the current devel build.** Download
  both with `osc getbinaries PROJECT PACKAGE REPOSITORY ARCH -d DIR`
  (argument order matters), extract (`rpm2cpio x.rpm > x.cpio` plus
  `bsdtar -xf x.cpio`; `cpio` was unreliable here), then diff the
  extracted jars file by file. The RPM payload digests differ on every
  rebuild (zip timestamps); the extracted content was identical in all
  seven jars. That is the evidence a reviewer wants for a test-only
  change.
- **rpmlint**: the `no-%check-section` warning disappeared; the
  remaining error/warnings were pre-existing.
- **Blocked/broken repositories are usually infra.** Check them against
  the pre-change state before spending time: s390x, ARM aarch64,
  Slowroll and riscv64 were "blocked" (no worker) or `_repository
  broken` before and after. Every repository that actually started a
  build succeeded.
- Per-codestream JDK matters: TW/Factory = 25, SLE 16 = 21, SLE 15 = 11,
  i586 in TW = 21. Test the oldest JDK locally; two failures were
  JDK 11 only.

## 6. Local build without host root

`sudo` needs a password on this workstation. Rootless podman works and
is fast enough (~9 minutes for this suite). `tools/local-build.sh`
(verified) runs `rpmbuild -ba --noclean` for the package in a Tumbleweed
container, installs the deps, and writes the log. `--noclean` matters:
the failed `%check` artifacts (JUnit XML, compiled classes) stay around
for inspection.

`osc service runall source_validator` is unavailable for scmsync
packages (`osc co` fails with `obs-scm-bridge failed`). Run the service
directly instead, in a directory holding the package files:

    /usr/lib/obs/service/source_validator --outdir /tmp/out

## 7. Process lessons

- One local build per fix. Seven OBS rounds happened before because
  nothing was built locally; each round rebuilds ~30 repositories.
- OBS rounds are expensive but not infinite: the accepted sequence here
  was 3 rounds (heap/JDK/locale issues surfaced one per round on
  architectures not available locally). Budget them: find arch- and
  codestream-specific causes by reading *every* failed repo's log and
  grouping by cause.
- Keep one squashed commit; force-push only the feature branch. Amend
  the single `.changes` entry every round so it matches the final diff.
- `%check` must never be masked (`|| :`, `|| true`, non-fatal), and the
  exclusion list must be derived from causes, not from what happened to
  fail last.
- Publishing the verification table in the PR description (repo rows,
  JDK, heap, test count, pass/fail) is what turns "it built" into a
  reviewable result.

## 8. What to fold into ENABLE_TESTS_PROMPT.md

The generic prompt already covers most of this. Concretely add:

1. The aggregator rule: check whether a single JUnit test iterates a
   sub-test array and stops at the first failure; if so, never discover
   failures one build at a time.
2. Never test a JUnit XML parser by eyeballing; assert
   `tc.find(...) is not None`.
3. `%ifarch` is a no-op in `noarch` packages; probe the JVM instead.
4. Multi-line `%global` bodies keep line breaks; use shell arrays.
5. On JDK 11 run `%check` under `LC_ALL=C.UTF-8`.
6. If a suite needs an external data repository, say so in the first
   report and quantify how many tests it affects before deciding.
7. Compare the shipped payload of the test-only change with the current
   devel build (jars file-by-file, not payload digest).
8. Save local build logs; OBS keeps only the latest per repository.
9. Check the pre-existing state of blocked/broken repositories before
   investigating them.

## 9. Numbers from this case

- Full suite: 2952 test cases; 781 failing without exclusions.
- Final: 2137 pass on 64-bit (JDKs 11/21/25), 2108 on i586.
- 815 cases not run: dominated by the bc-test-data dependency; 44 DSA
  cipher-suite cases; 1 cert-path case; the rest are cases inside
  classes excluded wholesale because part of the class needs the data.
- `%build` 1m10s, `%check` 8m25s (x86_64), about 30 min on i586, hours
  on emulated riscv64.
- Staging project: `home:autogits_obs_staging_bot:java-packages:PR:99`.

## 10. Artifacts

- `tools/local-build.sh` - rootless podman rpmbuild wrapper (verified).
- `tools/Audit.java` - runs every test class in a file list individually,
  with timeout, classifying failures.
- Working copies used: `/tmp/opencode/bc-push` (git clone of the fork
  branch), `/tmp/opencode/patchwork/{a,b}` (tarball extract plus edits,
  used to generate the patch), `/tmp/opencode/bc-base` (baseline build).
  All of `/tmp/opencode` is disposable.
