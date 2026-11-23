---
name: upstream-tests-packaging
description: Use when creating an RPM -tests subpackage that ships an upstream project's functional testsuite for running against installed binaries. Trigger words: -tests subpackage, upstream testsuite, test package, installable tests, coreutils-tests, tar-tests, coverage testsuite, noinst_PROGRAMS, getlimits, init.sh, run installed tests.
---

# Skill: upstream-tests-packaging

Recipe for packaging upstream functional testsuites as installable RPM
`-tests` subpackages in openSUSE. Derived from the coreutils-tests and
tar-tests work. Applies to any GNU/autotools/Automake project where the
upstream tests exist in the source tree but are not installed by
`make install`.

## When to use

- Creating a new `-tests` subpackage for a package that has upstream tests
- Debugging or extending an existing `-tests` package (e.g. `tar-tests`)
- Measuring binary function coverage with funkoverage against installed tests
- Working on any of the 12 identified packages with both upstream tests and
  openQA modules: tar, systemd, coreutils, snapper, lvm2, firewalld, fwupd,
  mariadb, openvswitch, postgresql, sudo, samba

## Two upstream test architectures

### Type A: GNU autotest (tar-tests model)

The build produces a single `testsuite` shell script (concatenation of all
test cases) plus helper binaries. The `-tests` package ships these files.
Running is straightforward: `cd <tests_dir> && ./testsuite`.

**Known issues with tar-tests:**
1. Missing `atlocal` -- tests expect `decho` and `mkexcltest` shell functions
2. Missing `XFAILFILE` -- empty `$XFAILFILE` causes `test -f $XFAILFILE` to
   return true (shell quirk), marking every test as expected-failure
3. Missing helpers -- `ckmtime`, `checkseekhole` are built but not packaged

### Type B: Automake test harness (coreutils model)

No single test script. Hundreds of individual `.sh`/`.pl` test files that
depend on a framework (`init.sh`), configuration (`init.cfg`), Perl modules,
and helper binaries. Requires a wrapper script to set up the environment.

This is harder. The rest of this skill focuses on Type B.

## Spec file pattern for -tests subpackages

### Guard with flavor check

In a `_multibuild` spec, the `-tests` subpackage belongs in the main
(unflavored) build only. Guard all additions:

```spec
%if "%{name}" == "packagename"
%package tests
Summary:        Upstream testsuite for <Package Name>
Requires:       %{name} = %{version}
Requires:       glibc-locale
Requires:       perl
Requires:       strace
Recommends:     acl
Recommends:     gdb
Recommends:     python3
%endif
```

### Source files

Add new Source entries for any custom files (wrapper script, reimplemented
helpers):

```spec
Source10:       packagename-tests-getlimits.c
Source11:       packagename-tests-wrapper.sh
```

These files must be tracked with `osc add` before `osc commit`, or the
`source_validator` service will reject the build.

### %files splitting -- critical gotcha

If the main `%files` section has a broad glob like `%{_libdir}/%{name}`,
it will claim the test files too. Split it:

```spec
# BEFORE (captures everything including tests)
%{_libdir}/%{name}

# AFTER (explicit, does not capture tests)
%dir %{_libdir}/%{name}
%{_libdir}/%{name}/libstdbuf.so
```

Then add the tests files section:

```spec
%if "%{name}" == "packagename"
%files tests
%{_libdir}/%{name}/tests
%{_libdir}/%{name}/bin
%{_libdir}/%{name}/src
%{_libdir}/%{name}/init.cfg
%{_libdir}/%{name}/run-tests
%endif
```

### RPM macro expansion in Source files

RPM macros (`%{_libdir}`) are NOT expanded inside Source files. If the
wrapper script needs the install path, use a placeholder and replace it
during `%install`:

```spec
install -m 755 %{SOURCE11} %{buildroot}%{_libdir}/%{name}/run-tests
sed -i 's|%%{_libdir}|%{_libdir}|g' %{buildroot}%{_libdir}/%{name}/run-tests
```

Note the doubled `%%` in the sed pattern -- `%{_libdir}` in the sed command
itself IS expanded by RPM, so the placeholder in the source file must use
`%%{_libdir}` (literal text after RPM processing: `%{_libdir}`).

## Porting build-tree tests to installed binaries

### Environment variables

Tests written for Automake's harness expect these variables. All `*srcdir`
and `*builddir` variables must point to the tests ROOT directory, not the
`tests/` subdirectory (tests source `${srcdir}/tests/init.sh`):

| Variable | Set to | Why |
|---|---|---|
| `srcdir` | tests root (e.g. `/usr/lib64/pkg`) | Framework sources `${srcdir}/tests/init.sh` |
| `abs_srcdir` | same | Absolute path references |
| `abs_top_srcdir` | same | `init.cfg` references `$abs_top_srcdir/init.cfg` |
| `abs_top_builddir` | same | Tests reference `$abs_top_builddir/src/PROG` |
| `VERSION` | RPM version | Version checks in test framework |
| `built_programs` | space-separated binary list | Framework checks if utility exists |
| `host_os` | `linux-gnu` | OS-dependent skip logic |
| `CONFIG_HEADER` | `/dev/null` | Must exist; tests grep it |
| `EXEEXT` | `""` | No .exe suffix on Linux |

### File descriptor redirection

The gnulib `init.sh` test framework writes diagnostics to fd 9. Without
this, tests fail with "Bad file descriptor":

```sh
exec 9>&2
```

### The src/ symlink trick

Some tests hardcode `$abs_top_builddir/src/PROG` to run binaries. In the
build tree, compiled binaries live in `src/`. For installed binaries,
create a `src/` directory with symlinks:

```sh
mkdir -p %{buildroot}%{_libdir}/%{name}/src
for prog in $(ls /usr/bin/); do
    ln -sf /usr/bin/$prog %{buildroot}%{_libdir}/%{name}/src/$prog
done
# Special case: install -> ginstall (build tree naming)
ln -sf /usr/bin/install %{buildroot}%{_libdir}/%{name}/src/ginstall
```

### Perl test invocation

`.pl` tests using project-specific Perl modules need explicit `-M` imports:

```sh
perl -w -I"$TESTS_ROOT/tests" -MCuSkip -MCoreutils \
    -M"CuTmpdir qw($test_basename)" "$test_script"
```

The `CuTmpdir qw(...)` argument MUST be the basename only (e.g.
`files0-from.pl`), not the full path. Full paths cause `CuTmpdir` to
attempt creating deeply nested directories that don't exist.

## Handling noinst_PROGRAMS

Upstream projects declare some binaries as `noinst_PROGRAMS` -- built during
`make` but not installed. These often use internal/gnulib headers unavailable
after installation.

**Pattern: standalone reimplementation.** Write a minimal C file using only
standard headers that produces identical output. Example: `getlimits` for
coreutils (~95 lines, uses `<limits.h>`, `<stdint.h>`, `<float.h>` instead
of gnulib's `system.h` and `ftoastr.h`).

Compile during `%build`:
```spec
gcc %{optflags} -o getlimits %{SOURCE10}
```

Install to the tests helper directory:
```spec
install -m 755 getlimits %{buildroot}%{_libdir}/%{name}/bin/getlimits
```

## Version matching is critical

Tests from upstream git master will fail against installed binaries from an
older release due to:

1. **Error message differences** -- distribution i18n/l10n patches modify
   error output; tests do exact string comparison
2. **Behavior changes** -- newer tests exercise features not in the
   installed version

**Rule:** Always ship tests from the SAME source tarball used to build the
binaries. Extract during `%prep`, copy during `%install`. Never download
tests separately.

## Categorizing test failures

When running packaged tests against installed binaries, failures fall into
predictable categories:

| Category | Typical count | Fix |
|---|---|---|
| i18n/l10n patch mismatches | ~30-50% of failures | Error messages include extra errno text from distribution patches. Tests compare exact output. These are false failures. |
| Missing locales | ~10-20% | Tests need specific locales not installed by default. Add `glibc-locale` dependency. |
| Root required | ~30-40% of skips | Tests need UID 0 (chown, chgrp, install with special bits). Expected skips. |
| SELinux/Smack/AppArmor | ~10-15% of skips | Tests need security modules not enabled in default install. |
| strace-inject | 2-5 tests | Tests using `strace -e inject=...` to simulate kernel errors. Need specific strace capabilities. |
| TTY required | ~5 tests | Tests needing a controlling terminal (stty tests). |
| Missing filesystem features | ~10 tests | bind mounts, ext3 performance, immutable files, SEEK_DATA. |

## funkoverage coverage measurement

After installing the `-tests` package, measure function coverage:

### Setup

```sh
sudo zypper install <pkg> <pkg>-tests <pkg>-debuginfo \
    elfutils go git
sudo zypper modifyrepo --enable repo-debug  # for debuginfo

git clone https://github.com/ilmanzo/BinaryCoverage.git
cd BinaryCoverage && ./build.sh
```

### Copy-not-modify pattern

Never instrument the system binary directly. Copy it, instrument the copy:

```sh
cp /usr/bin/<binary> workspace/bin/<binary>
sudo funkoverage install workspace/bin/<binary>  # sets cap_bpf,cap_perfmon
# ... run tests against the copy ...
sudo funkoverage uninstall workspace/bin/<binary>
```

### Known funkoverage limitations

- **genfile --run** and similar fork-then-exec test patterns: the eBPF probe
  setup adds latency that breaks timing-dependent tests. Exclude these.
- **Function count varies with debuginfo version**: different Tumbleweed
  snapshots resolve different symbol counts for the same binary. Coverage
  percentages are valid within their own denominator.
- **SIGRTMIN/SIGRTMAX**: calling these from `getlimits` inside a shimmed
  binary can interfere with signal handling. Test standalone.

## Reference results

### tar (Type A -- autotest)

| | tar-tests | openQA tar.pm |
|---|---|---|
| Tests | 220/236 passed | ~12 invocations |
| Coverage | **42.0%** (402/957) | **30.6%** (182/595) |

### coreutils (Type B -- Automake, prototype run)

| | coreutils-tests (prototype) |
|---|---|
| Total | 692 |
| Passed | 547 (79.0%) |
| Failed | 41 (5.9%) -- 31 i18n, 8 locale, 2 strace |
| Skipped | 104 (15.0%) -- root, SELinux, fs features |

## Checklist for new -tests subpackage

1. [ ] Identify test architecture (Type A autotest vs Type B Automake)
2. [ ] List `noinst_PROGRAMS` helpers needed by tests
3. [ ] Write standalone replacements for noinst helpers (if needed)
4. [ ] Create wrapper script with correct env var setup
5. [ ] Add `%package tests` to spec (guarded by flavor check)
6. [ ] Fix `%files` glob conflicts with main package
7. [ ] Handle `%{_libdir}` placeholder in wrapper script
8. [ ] Track new Source files with `osc add`
9. [ ] Build on OBS, verify rpmlint output
10. [ ] Install RPM and run tests, categorize failures
11. [ ] Measure coverage with funkoverage
12. [ ] Document results (pass/fail/skip counts, failure categories)

## Reference implementation

The coreutils `-tests` subpackage in `Base:System/coreutils` (openSUSE Factory) follows this recipe.
