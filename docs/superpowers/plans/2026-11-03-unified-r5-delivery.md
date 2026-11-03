# Integrated r5 delivery Implementation Plan

> For agentic workers: use superpowers:executing-plans; keep observed evidence separate from intended outcomes.

**Goal:** Deliver the integrated main at 812304bc907b21c66f21acf7dccf76fc53b7bc28 to the connected Windows installation, not merely merge source.
**Architecture:** Preserve all merged behavior; issue a distinct r5 build identity, freeze reviewed main, run offline consistency and anonymous installation checks, publish immutable assets, then update the stable catalog and verify the real in-app upgrade.
**Tech Stack:** Python 3.11, pinned PySide6/QFluentWidgets, PyInstaller, Inno Setup, existing website Worker.
**Spec:** AGENTS.md, docs/RELEASING.md, docs/UPDATE-RELEASE-GATE.md and the maintainer's request to deploy the integrated commit.

## Constraints and review focus

- Software version remains 1.0.0; new tag v1.0.0.20261103.5. Never overwrite r4 or old assets.
- Include #26 UI, #24 floating-orb-only topmost, #27 directory merge, #8 tests, #13 gate and #11 documentation.
- Keep the expanded manager non-topmost, preserve data and original files; do not reorganize historical nested folders at startup.
- Require exact artifact SHA/size/source and new installed build, not installer exit alone.
- Keep failures and skips; do not run tests on production data or publish private screenshots.

## Tasks

- [ ] Add a failing identity test proving this integrated build is newer than public r4.
- [ ] Update only the build identity; run relevant/full tests, review and merge through normal protection.
- [ ] Freeze exact merged source; verify bytecode/payload and anonymous installer/UI; explicitly run release consistency CLI.
- [ ] Publish new assets; update stable catalog through a separate PR without reverting merged product code or old routes.
- [ ] Verify public manifest and full downloads; take a verified recovery point, use the installed application's update flow, and check new executable, running window, topmost boundary and preserved data.
- [ ] Record source/release/catalog/installed identities and remaining limitations in shared handoff.
