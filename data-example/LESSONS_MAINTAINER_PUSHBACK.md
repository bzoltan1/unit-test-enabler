# Lessons: what maintainers push back on

From the grub2 case, and from reading the package's request history.

## Read the history before you write the request

The best predictor of a pushback is the package's own request history.
List every accepted, declined, revoked and superseded request for the
package and its devel project. Read the review comments on the declined
ones and on the accepted ones. On grub2, the accepted outsider requests
were bug fixes with a clear user impact and a bug reference, accepted
by the maintainer the same day. The refusals that existed were supersede
races, not rejections. That told us the gate is open and the shape
matters.

## Build only the targets the devel builds

Do not inherit the whole branch matrix and call it a test. In the grub2
branch, 20 targets were inherited. Five were blocked, four were
unresolvable because of a pre-existing branding choice, and one was a
target the devel has disabled. Nine of twenty could never finish. Copy
the devel's build disables, drop the repositories the submit target does
not need, and keep the arches that really build. Eight targets were
enough. A target the devel disables is not yours to enable in the same
change.

## Measure the cost, do not estimate it

Compare the `%check` section with the same target without it. On grub2 it
was 19s on Tumbleweed x86_64, 14s on i586, 23s on Factory x86_64 and 64s
on ppc64le, against a 5 to 7 minute build. State the numbers, and state
that `%check` runs after `%install` and does not change the shipped RPM.
A maintainer of a signing-critical package wants that sentence.

## Say what the tests protect

Do not sell "tests are good". Map each test to a code path this package
patches. grub2 carries patches in envblk and prep_loadenv, 31_efi_bootnext,
FDT handling, TPM/TPCM and the script parser, and the selected tests cover
exactly those. That is the answer to "what regression does this prevent".

## Flakiness is a different question

Count the repeated builds of the same target and their results. On grub2,
25 successful rebuilds with no mixed results, and s390x failing all four
attempts in image_unit_test and syslinux_test. That is deterministic, not
flaky, and s390x was not in the matrix. Never call a test flaky without a
build that passed after a build that failed.

## Expect these questions

- Why now, and is there a bug or a CVE.
- What does it cost, per target and on the slow arches.
- Does it build on the targets the devel builds.
- What do the tests actually check, and what do the skips hide.
- Is it flaky, and what happens when it fails on a Friday.
- Does it touch the shipped image, the signing, SBAT or Secure Boot.
- Is this the smallest possible change.
- Why is the message written like that, and who is the author.

For SLE maintenance, a bug or CVE reference is normally expected. A pure
test-only maintenance update is harder and should be discussed with the
maintainer first. For Factory a test-only change is possible, but only
with the matrix, cost, flakiness and RPM points answered.

## Heavy packages are not early candidates

grub2 is a bootloader, signed, with 20 targets and a frequent CVE stream.
The lesson is not that it is impossible. The accepted outsider patches
show it is possible. The lesson is to budget the matrix work and to bring
the pushback answers with the first request. Pick simple packages for the
early runs of a new tool.

## Tool note

The waiter must compare the branch against the devel matrix. Waiting for
every inherited target, including blocked and unresolvable ones, is what
kept the grub2 container busy for 23 hours. A per-target grace, a churn
limit and a build observation deadline belong in the orchestrator, not in
the worker's patience.
