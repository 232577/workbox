# 2026-11-16 implementation evidence

## Incremental package compatibility and staging

The package path allowlist now accepts only the exact PyInstaller runtime path
`_internal/base_library.zip` as an opaque file. Other ZIP names, locations and case
variants remain rejected. The inner archive is never inspected or extracted.

Staging now uses handle-relative exclusive creation. The initial staging root
identity is checked against the acquired root handle before creating child entries;
parent handles remain held through writing and flushing. Collisions never overwrite
files and failures retain their partial staging evidence.

The inherited uncommitted safe-create patch was preserved before modification in a
read-only local checkpoint, SHA-256
`1a4d33ffa6cc0e25183f319cce7318279945fa21e50b9958e162dfcfa2bdc670`.
An additional uncommitted last-instant reparse experiment was not run and is not
included in this change. Its text remains in that checkpoint. No existing baseline
test or assertion was removed or weakened. No claim is made for that extra coverage.

Regression evidence, using isolated anonymous temporary fixtures and
`python -m scripts.run_tests`:

| Change | Before implementation | After implementation |
| --- | --- | --- |
| Exact runtime ZIP and exclusive staging integration | 4 failed, 6 passed | Same 10 passed |
| Expected root identity before creation | 2 failed | Included in 41 passed ordinary regression selection |
| Expanded ordinary creation, collision, failure retention and builder/read/stage checks | — | 41 passed |

The ordinary selection did not execute symlink, junction or race experiments.
Full collection and frozen/installer validation are separate gates; these targeted
results do not establish them. Real databases, working materials, archive files and
existing installations were not accessed for these tests.

The subsequent affected-module run covered `test_delta_format.py`,
`test_incremental_package.py`, `test_update_file_ops.py`,
`test_incremental_update.py` and `test_ui_release_identity.py`: **217 passed**, with
one expected duplicate-ZIP-entry warning. The omitted extra experiment was not
present in this collection. Independent contract and implementation reviews found
no blocker; their checks are not a claim of a complete security audit.

## Independent release identity

The candidate identifies itself as `v1.0.0.20261116`; the software version remains
`1.0.0`. Identity regressions failed before the change and then passed: 34 identity
cases and three related metadata/build-probe compatibility cases. They verify that
the current r2 release can be offered this new full installer and that the new build
does not recommend itself as an update.

The current public r2 payload has no update helper. This release must therefore be
installed as a full package and becomes the first possible real incremental base.
No r2-to-candidate delta is generated or advertised. A source candidate is not a
completed build, installation or public release.

## Public presentation already delivered

PR #105 was squash-merged at `3406f7d71ba259702a49f6a3289aa43881d4740a` after all six
jobs of CI run `43786975628` passed. The website was deployed from that clean main
commit as Cloudflare version `34751182-ff24-4229-be9b-f027a11efea2`. Website tests:
162 passed. Local and public Chromium checks: 29 passed each. Public HTTP checks:
13 passed, including the full existing r2 installer hash, ranges and statistics
access restrictions. Existing Cloudflare beacon injection remains accounted for;
HTML is not described as byte-identical. The installer was unchanged by that deploy.
