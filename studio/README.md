# Desk Buddy Studio

Self-host and manage every service that runs Desk Buddy.

## Run

```bash
# from the repo root
python3 -m venv .venv
.venv/bin/pip install -r studio/requirements.txt
.venv/bin/python -m studio
```

A venv is required on distros with an externally-managed Python (Arch, Debian,
Fedora). `python -m studio` and `python studio/app.py` both work.

## Package it

Python is not a compiled language, so nothing becomes a `.exe` on its own. A
bundler freezes the interpreter, your code, and the Qt libraries into one file.

```bash
python studio/build.py
```

| Built on | You get |
|---|---|
| Windows | `dist/DeskBuddyStudio.exe` |
| macOS | `dist/DeskBuddyStudio.app` |
| Linux | `dist/DeskBuddyStudio.bin` |

**There is no cross-compiling.** A Windows `.exe` must be built on Windows. You
cannot produce one from this Linux box. That is what
`.github/workflows/build.yml` is for: it runs the same `build.py` on a Windows,
macOS, and Linux runner and uploads all three. Push, then download the artifacts.

Under the hood this is `pyside6-deploy` (Qt's own tool, wraps Nuitka). The spec
lives in `pysidedeploy.spec`. First build is slow — it downloads Nuitka and
compiles. Expect ~50-80MB per binary.

### Not done yet
- **Code signing.** Unsigned, Windows SmartScreen warns "unknown publisher" and
  macOS Gatekeeper refuses to open it. Needs a cert (~$100-400/yr) + Apple
  notarization. See `decide_early` in `../PLAN.md`.
- **Installer.** `build.py` makes a bare executable, not a setup wizard.

## Now

Hello world. A window, a sidebar of the six services from `../PLAN.md`, and a
placeholder page for each. Nothing is wired to a robot yet.

## Next

Phase 1 in `../PLAN.md`: move `mqtt_robot.py` into `studio/robot/`, declare
capabilities as data, build the simulator.

`calibration_tool/` is still the working app. It stays until Phase 3 replaces it.
