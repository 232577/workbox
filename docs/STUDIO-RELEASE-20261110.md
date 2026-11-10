# Studio install candidate 20261110 r1

This candidate closes the packaging gap after PR68. The shared source retains
the newer website improvements from PR70/71 and the r7 project/date/history work.
It is not a rebuild or replacement of r7's published assets.

The desktop build tag is `v1.0.0.20261110.1`; the software version stays 1.0.0.
The release asset name is `WorkBox-1.0.0-Setup-x64-20261110-r1.exe`.
Identity regressions require r7 to discover this build, and the installed new
build to reject itself or older builds as upgrades.

Release gates remain in `docs/RELEASING.md` and `docs/UPDATE-RELEASE-GATE.md`.
This source change alone does not assert that an installer was built, tested,
published or installed. Final artifact identities and actual acceptance scope
must be recorded in the new Release's `PUBLIC_BUILD_INFO.json`.

Acceptance focuses on Studio/checklist startup in the frozen program, XLSX
runtime dependencies, current versus historical documents, update handoff,
cancel/shutdown, original install location and unchanged business data.
Use anonymous fixtures for testing and the existing recovery policy for a real
device upgrade. Preserve all existing release assets and website layout.
