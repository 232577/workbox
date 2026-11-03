# Windowless update handoff correction

Goal: the verified download must launch a real installer helper after safe shutdown, not merely return a zero exit status without running its script.

Observed on two isolated controls and the old frozen runtime: Windows PowerShell with DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP returns zero without executing a harmless marker script. CREATE_NO_WINDOW + CREATE_NEW_PROCESS_GROUP executes it; ordinary console mode also executes it. DLL-path and script-syntax hypotheses were tested and did not explain this reproduction. Preserve those observations; do not disable execution policy, Internet-origin tagging or OS security.

- [ ] Add a real pythonw parent/PowerShell child test with no installer or user data. It must fail before the fix and prove helper work happens after parent exit.
- [ ] Change only helper process creation, set explicit null standard handles and independent frozen restart environment. Record helper-started before waiting; keep hash/size/original path/data identity checks unchanged.
- [ ] Run affected and full Windows collections, site and repository checks, and verify exact CI before normal PR merge. Inspect native frozen behavior separately.
- [ ] New immutable build v1.0.0.20261103.2, software version1.0.0. Published .1 assets remain unchanged. Build reviewed main, verify source/payload and anonymous installer, publish new assets and catalog.
- [ ] Old binaries cannot run corrected code before installation; any one-time assisted continuation must be labeled as such, not described as an unassisted online upgrade. Keep recovery points, original data and handoff failure evidence.
