# Floating orb visibility repair

**Goal / approved scope:** The small floating orb stays above other ordinary desktop applications; the expanded project manager is NOT always-on-top. The user's clarification overrides the initial mistaken manager-window interpretation. Its temporary live topmost change was reverted without moving or activating it. Manager flags, navigation and appearance remain unchanged.

**Architecture:** Keep the existing Qt topmost/no-activation flags. While the orb is visible, a bounded coarse timer reasserts its native Windows topmost order without activation, movement, resizing, or recreating the window. Stop on hide/close; defer during dragging and application menus/modal dialogs. No global hooks, external service, or focus-stealing activation loop. Native operations affect only a visible window owned by this process. Ordinary management, background mode, and safe shutdown stay unchanged.

**Tech stack:** existing Python/PySide6, ctypes Win32 APIs; no new dependencies. Software version 1.0.0. New release builds must be distinguishable from .20261103.2; old tags/assets remain immutable.

**References:** Microsoft SetWindowPos (HWND_TOPMOST, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, SWP_NOOWNERZORDER) and Qt WindowStaysOnTopHint/WA_ShowWithoutActivating. Do not promise visibility over UAC secure desktop or exclusive fullscreen.

## Tasks / acceptance
- [ ] Reproduce a later topmost window obscuring the orb in isolated real Windows Qt; keep manager normal.
- [ ] Red then green regression: repair timer lifecycle, popup/modal/drag suspension, exact native flags, invalid/foreign/hidden HWND refusal, manager remains non-topmost.
- [ ] Native red/green proof: covering window, actual Z-order recovery, original focus/rect unchanged, hide stays hidden, recreation works.
- [ ] Affected/full suites, review exact diff, normal PR + required checks; no real data as test fixtures.
- [ ] Deliver from reviewed main with verified build/payload, consistent backup and actual update; report source integration separately from installation.

## Review focus
Never raise the expanded manager. No activateWindow/foreground calls in the repair. No hidden orb resurrection in background mode. Menus and modal confirmations must stay usable. Stop owned timers on hide/disposal. Preserve existing dragging/click/drop routing and the update helper fix.

## Verified lifetime ruling
The affected test ordering also fails on untouched main: a zero-delay manager resize callback outlives its deleted window during shutdown. A deterministic destruction regression reproduces this. Bind the two existing resize callbacks to their QObject context so Qt cancels them on destruction; do not change manager flags or suppress exceptions. This is a shutdown safety fix, not manager topmost behavior.
