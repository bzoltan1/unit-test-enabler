# Data directory example

Copy this directory, point `ENABLER_DATA` in `enabler.conf` at the copy,
and replace the example list with your own.

| file | used by | content |
|---|---|---|
| `sle15_fix_order.csv` | `host/next-packages.sh`, the orchestrator | the packages to work on (format below) |
| `supported_codestreams.txt` | the orchestrator, SLE phase | product lines that may still receive a maintenance request |
| `ENABLE_TESTS_PROMPT.md` | every worker and reviewer | the master prompt: workflow, rules, writing style |
| `LESSONS*.md`, `*_learnings.md` | every worker and reviewer | lessons from earlier packages |
| `progress.md` | `host/sync-progress.py` | one row per package, merged from the results |

`host/build-image.sh` copies the prompt and lessons into the image, so
rebuild after editing them. The files may use these placeholders, which
the container replaces with your `enabler.conf` values: `@OBS_USER@`,
`@IBS_API@`, `@IBS_GITEA@`, `@IBS_TEA_LOGIN@`, `@OBS_TEA_LOGIN@`.

## sle15_fix_order.csv

Required columns:

| column | meaning |
|---|---|
| `package` | SLE source package name (the key: container `te-<package>`, `results/<package>/`) |
| `priority_rank` | integer; `next-packages.sh` picks the lowest ranks first |
| `list_status` | `valid`, `weak_reference`, `no_distro_reference` or `already_enabled` (never picked) |
| `work_package` | the real source package holding the code (differs for wrapper packages such as `openssl` -> `openssl-3`) |
| `validation_note` | what was checked; passed to the worker as a hint |

Every other column is passed to the worker unchanged as context, for
example the difficulty class, the Fedora/Debian references and what the
shipped SLE build log showed. `list-tools/` holds the scripts that
validate a list against the build services.

`supported_codestreams.txt` is the life cycle gate. `osc maintained`
lists every maintenance project a package ever had, including product
lines that are out of life cycle, so the SLE phase intersects that list
with this file. With `SUBMIT_SLE=1` it refuses to open a request for a
codestream that is not listed, and records the dropped ones in the
report. Keep the file current from https://www.suse.com/lifecycle/ and the
package maintainer. A missing or empty file stops the SLE phase with a
blocked outcome, so fill it before the SLE phase runs.

The example lessons are real lessons from the first packages
(bouncycastle, docker), with deployment details replaced.
