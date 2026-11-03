# Update handoff hotfix delivery plan

> Execute with superpowers:executing-plans. Keep this catalog task separate from the frozen source checkout.

**Goal:** Deliver reviewed PR #22 so the locally installed old updater no longer remains stranded after exit.
**Architecture:** Immutable new release from main dcc87cdd60aa8075e5ca015a9a84143af8525507; additive website catalog; one explicitly assisted old-client transition, retaining the original install/data paths.
**Tech Stack:** Existing Python/PySide6/PyInstaller, Inno Setup, Node website tests and Cloudflare Worker.
**Spec:** docs/RELEASING.md and docs/ONLINE-UPDATES.md; maintenance issue #9.

## Constraints and review focus

Keep version 1.0.0, existing tags/assets, twelve old download routes, analytics bindings and Windows security controls. No force-stop, data reset or installer substitution. Source merge, public assets, website deployment and local installation are separately verified. Screenshots retain original .1 provenance; .2 changes launcher internals, not their UI. Initial old updater may require an explicit assisted continuation and cannot be reported as an unassisted success.

- [ ] Verify the exact .2 frozen payload, isolated installer and post-parent-exit helper; rerun complete Windows collection.
- [ ] Publish .2 assets only after hashes, original releases and reviewed source are verified.
- [ ] Run failing catalog/manifest/recovery-copy tests; add .2 without mutating old entries. Run website/repository checks, protected PR and all required CI.
- [ ] Deploy only the merged catalog; verify full anonymous download, manifest, Range, SHA-256 and old routes.
- [ ] Save a fresh local restore point, use the installed client's actual check/download/confirmation, explicitly assist the defective old helper once if necessary, and verify installed identity, restart and preserved business records/files.

Do not treat the final installed build checking itself as proof of a future build upgrade. Record any remaining failure without silently retrying unknown side effects.
