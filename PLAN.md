# Desk Buddy Studio — Plan

Draft. **[OPEN]** = needs your call.

.venv/bin/python -m studio

---

## Map how mark thinks

# stack_plan

- desk_buddy_plan   - ESP32-S3-CAM    - 3D printed hobby robot arm
- desk_buddy_studio - PySide6 (Qt)    - Place to self host manage all services and control deskbuddy
- network_plan      - Mosquitto Mqtt  - sends messages to all things on the stack

# desk_buddy_studio_services

- mosquitto_plan    - the network hub
- image_interpreter - YOLO, google one shot, or other systems we vet. connected to mqtt user
- workflow_manager  - code editor and studio tools to make workflows
- calibration_tool
- logs_plan
- manual_controller

# everything_else

- network_page_plan - first real page. installs/owns the broker, then connects.
- objects_plan      - the nouns in code
- layout_plan       - where files go
- phases_plan       - build order
- decide_early      - blocking questions
- dont_break        - what's already good
- studio_menu_plan  - ui layout, two bars

---

# stack_plan

Runs on Windows / Mac / Linux. Nothing else in it. No Pi, no server.

### Why PySide6
- backend already Python — `mqtt_robot.py`, calibration flow, model glue
- one language, one process, no IPC
- native window, all 3 platforms, one codebase
- LGPL-3.0 → ship closed or open, no fee, forkers stay free (PyQt6 is GPL — trap)
- `QGraphicsView` = real node-graph canvas for the workflow builder
- cost: ~150MB install. Mac notarization + Windows signing eventually. **[OPEN]** who owns that.

### Rejected
| | Why not |
|---|---|
| Tauri v2 / Electron | UI is a WebView = a browser. Plus Rust/JS + Python split w/ IPC. |
| Tkinter | What we have. The reason it's ugly. Hand-rolled zoom, font fallback, Maximize button. |
| Flutter / Kivy | Dart, or non-native look. Backend stays Python → IPC anyway. |
| .NET MAUI | C#. Separate Windows app. Only if PySide6 fails. |

### network_plan — the hub
- everything talks MQTT. robot, models, workflows, Studio.
- contract is `firmware/MQTT_SPEC.md` — 665 lines, already written, good
- **[OPEN] Mosquitto vs embedded broker.** You named Mosquitto. Real tradeoff:
  - **Mosquitto** — the standard, battle-tested, users may already run it, firmware
    already expects it. But it's a **native binary**: 3 platform builds, Windows
    antivirus false positives, Mac Gatekeeper, install friction for a beginner.
  - **`amqtt`** — pure Python, embeddable `Broker` class, runs in our process.
    Zero install, zero signing, works everywhere free. Less proven.
  - **Middle path, my lean:** Studio *manages* Mosquitto if present, ships `amqtt`
    as the zero-config default. Advanced users point Studio at their own broker.
  - decide before Phase 2.

---

# desk_buddy_studio_services

Six. Each = start / stop / health / log / config. All managed from one place.

### mosquitto_plan
- the communication hub. everything else connects to it.
- Studio starts it, holds creds, shows connection state
- offline-first: no `deskbuddy.ai` account required to use your own robot
- **[OPEN]** does cloud broker stay the default, or local? Changes first-run.
- **Studio installs and owns a private broker** — verified it runs unprivileged
  (own conf/passwd/acl, high port, no root, no systemd), so no `sudo` and no
  touching `/etc/mosquitto/`. See `network_page_plan`.
- the UI for this is `network_page_plan` — first page to become real: broker
  installer first, then the port of `calibration_tool`'s proven MQTT code

#### implementation steps

Seven steps. Each one ships, is visible in the UI, and is testable before the
next starts. Step 0 is deletion; 1–3 are new code; 4–6 port what already works.

**0 — Strip the page back to nothing. `studio/ui/pages/network.py` — DONE**
- delete the three placeholder cards (`Broker` / `Connected` / `Traffic`) and
  their `build_*` methods
- delete every toolbar action — `build_actions()` returns `[]`, so the toolbar
  bar renders empty
- keep the side panel, but **empty**: `SidePanel("Brokers", [])`, not `None`.
  Returning `None` hides the dock entirely, which is a different layout — and
  the smoke test asserts every page has a side panel.
- the hardcoded `Local broker / mqtt.deskbuddy.ai / Custom…` rows go; that list
  gets rebuilt from real brokers in step 2
- `status` stops claiming "Broker stopped" — nothing knows that yet
- keep `title` / `subtitle` / `key` / `label`; the page still exists in the workspace bar
- `NOT BUILT YET` badge stays until step 4 — it is the honest label for a page
  that cannot yet reach a robot
- **why first:** every later step then *adds* something real. Nothing on the
  page is ever a lie, and there is no moment where a placeholder and a working
  control sit side by side looking identical.
- **exit:** the Network page is a title, an empty side panel, an empty context
  bar, and nothing else. Smoke test still passes.
- **done:** page is title + subtitle + empty `Brokers` panel + an empty toolbar.
  `build_actions()` is not overridden at all — the base already returns `[]`,
  so an empty override would have been noise. `build_side()` stays, because
  the base returns `None` there and that hides the dock.

**1 — Detect. `studio/network/mosquitto_finder.py` — DONE**
- find the binary: `shutil.which("mosquitto")`, plus the known install paths
  (Homebrew's `/opt/homebrew/sbin`, `C:\Program Files\mosquitto`)
- read the version from `mosquitto -h` — it prints `mosquitto version 2.1.2`
- also find `mosquitto_passwd`; we need it in step 3 and it can be missing
  even when the broker is present (some distros split the package)
- return one honest status: `missing` / `found(version, path)`
- per-OS install *advice*, as a copyable command — **never run it**
- Broker card renders that status. No Start button yet.
- **exit:** a machine without Mosquitto says so and shows exactly what to run
- **done.** `detect()` returns a `BrokerStatus`; the page renders it. Notes:
  - version comes from **stdout** — `mosquitto -h` also writes a "terminating"
    line to stderr, so merging the streams could read the wrong one
  - three states, not two: `installed` / `partial` (split package — one binary
    without the other) / missing. "Installed" would be a lie for `partial`.
  - **bug found while testing:** install advice used `shutil.which` for the
    package manager, so a GUI app's thin PATH degraded `sudo pacman -S
    mosquitto` to generic text. Now checks the standard bin dirs directly.
  - detects the *package manager binary*, not the distro name — this box
    reports `ID=omarchy`, which no hardcoded distro list would match
  - new `CommandCard` component: a read-only `QLineEdit` + Copy, because a
    command the user cannot select is useless. Kept separate from `Card`
    rather than adding a flag to it.
  - 22 tests in `studio/network/tests.py`, all simulating machines this one
    is not (nothing installed, split package, silent binary, broken binary)
  - **also lands "is one running?"** — a local TCP connect resolves in well
    under a millisecond, so `running_port()` polls rather than tracking a
    process we may not have started (a broker can also die without telling us)
  - **`DEFAULT_PORT = 18830`, deliberately not 1883** — this machine already
    has a system broker on the default port, so ours must never collide
  - The workspace bar now carries **two** always-true chips, `● broker on <port>` beside
    `○ no robot`. They belong there, not on this page: a robot dropping
    offline matters most while you are driving it from Manual.
  - The toolbar offers only what the state allows — Start when down, Stop/Restart
    when up. Offering to "Start" a running broker is its own small lie.
  - **the chip timer must not call `report()`** — that shells out to
    `mosquitto -h` (~2.25ms vs ~0.05ms for a socket check, 46x). `chip_text()`
    exists for the every-3-seconds path; `report()` is for page builds.

**2 — Run our own. `studio/network/broker_commands.py` — DONE (start/stop/restart)**
- generate into `AppLocalDataLocation/broker/`, created `0700`
  (`mosquitto_passwd` warns on world-readable files and will refuse them later)
- `mosquitto.conf`: high port bound to `127.0.0.1`, `allow_anonymous false`,
  our own `password_file` and `acl_file`, persistence on
- **bind localhost-only by default.** A broker on `0.0.0.0` is on every network
  the laptop joins. Reaching a real robot needs LAN access — make that an
  explicit choice, not the default. **[OPEN]** how that choice is presented.
- `process.py`: start with `subprocess.Popen`, keep the handle, stop on app
  exit, poll `is_running()`
- **capture stderr — it is genuinely good.** A bad config exits `rc=3` with
  `Error: 'listener port' value not a number. / Error found at bad.conf:1.`
  Surface that verbatim rather than "failed to start" (verified)
- **check the port first** — a system broker may already hold 1883 (there is one
  on this machine now). If found: offer to use it instead of failing to bind.
- Start / Stop / Restart on the toolbar become real
- **exit:** press Start on a fresh machine, get a running private broker, no root
- **done.** `start()` / `stop()` / `restart()` / `shutdown()`, each returning a
  `CommandResult(ok, message, detail)` rather than raising — a broker that will
  not start is a thing to show, not an exception to catch. Notes:
  - **start() waits for the port, not the process.** A broker that dies during
    startup is still a live Popen for a moment, so polling `poll()` alone would
    report success. Verified: SIGTERM exits 0 and releases the port, and an
    immediate rebind works (no TIME_WAIT problem).
  - **stop() refuses a broker Studio did not start.** `is_ours()` gates it, so
    the system broker on 1883 is never touched. Tested explicitly.
  - config regenerated on every start, `0700` dir and `0600` files
  - an **empty passwd file is correct** here: with `allow_anonymous false` the
    broker starts and refuses everyone until step 3 adds accounts
  - **bug found:** `QStandardPaths` needs org/app names, and once a
    QApplication exists Qt defaults `applicationName()` to the *script name* —
    broker files were landing in `DeskBuddy/<stdin>/broker`. Both names are now
    set unconditionally in `broker_dir()`.
  - `closeEvent` calls `shutdown_broker()` so a session never orphans a process
  - `ActionSpec` gained `on_click`; an action with a handler enables itself, so
    a button that does something cannot disagree about being clickable
  - `Page.rebuild()` added to the base: replaces the section stack in place and
    tells the window to re-read the actions and status
  - 10 tests in `studio/network/command_tests.py`, skipped when Mosquitto is
    absent, always on an unused port so they never disturb a real broker
- **bug found by mark, fixed:** with a system broker on 1883, the toolbar came up
  **empty** — no way to start Studio's own. Cause: `build_actions()` asked "is
  a broker running?" when the question is "is *ours* running?". A broker we did
  not start must not suppress the button that starts ours. `BrokerReport` now
  carries `ours`, the card says "another broker is on 1883 … Start Broker runs
  Studio's own on 18830", and the chip reads `◍ … (not ours)`.
- **still open for step 2:** the side panel's broker list, and how the
  localhost-vs-LAN choice is offered

**Diagnostics — `studio/diagnostics.py`.** Help → Copy Diagnostics
(`Ctrl+Shift+D`), or `python -m studio.diagnostics`. One pasteable block:
binaries and versions, both ports and who owns them, the exact strings the UI
is rendering, file paths with permissions, and what the toolbar is actually showing.
Read-only and carries no credentials. It exists because "the buttons are
missing" should be a paste, not a conversation — that bug above was invisible
in the code and obvious in one line of this output (`toolbar      EMPTY`).

**3 — Accounts. `studio/network/accounts.py` — DONE**
- `studio` account created on first run: strong generated password, full access
- "Add robot" → name it → generate password → `mosquitto_passwd -b` → append
  `user <name>` + `topic readwrite <name>/#` to the ACL → reload
- **`SIGHUP` reloads users and ACLs live — verified.** Added an account to the
  passwd + acl files, sent SIGHUP: the new user went from refused to publishing
  with no restart and no dropped connections
- Windows has no SIGHUP, so **[OPEN]** restart the process there, or use
  `mosquitto_ctrl dynsec`
- show credentials **once**, with a copy button — they cannot be recovered from
  the password file, only reset
- store our own creds in `user_config/`; **[OPEN]** password in plaintext INI
  vs `keyring`
- **exit:** credentials a firmware flash can actually use
- **done.** Accounts live on the Network page: the side panel is the list, the toolbar
  carries Add Account / Reset Password / Remove Account. Notes:
  - **an account is not a robot.** It is a credential — anything joining the
    system gets one, which is the point of everything speaking MQTT rather than
    adding an HTTP layer per service. A desk buddy *has* an account; so does a
    vision server or a web app. (Resolves the `objects_plan` [OPEN] in that
    direction: model accounts now, the Robot object comes with step 4.)
  - ACL policy: **scoped by name by default** (`black` → `black/#`), with one
    full-access override for a vision/web account. Matches the existing
    `desk_buddy_web` broker's own design.
  - **usernames are stricter than Mosquitto.** It only rejects a colon, and
    accepts `"with space"` and `"üñî"` — but the username is the topic prefix,
    so Studio requires `[a-z0-9][a-z0-9_-]{1,31}`.
  - re-running `mosquitto_passwd -b` on an existing user updates the hash in
    place, so **password reset is free** — no separate mechanism.
  - passwords shown once in a dialog with per-field copy buttons; Mosquitto
    stores a hash, so they are unrecoverable, only resettable
  - **bug found:** `report()` derived `ours` from `is_ours()` but `port` from
    scanning, so it could pair "ours" with *another* broker's port. Commands
    now record the port they started on; the finder only scans when we are not
    running one.
  - **bug found:** a rebuilt side panel never reached the dock — `_page_changed`
    refreshed the toolbar but not the panel. `select_page` and `_page_changed` now
    share one `_show_page`.
  - 12 tests in `studio/network/account_tests.py`, against a real broker:
    a new account actually connects, a wrong password does not, two accounts
    cannot reach each other's topics, reset invalidates the old password, and
    SIGHUP reloads **without restarting the process**

**4 — Connect. `studio/robot/`**
- `git mv` `mqtt_robot.py` → split into `client.py` / `payloads.py` / `state.py`
- swap `on_event(kind, payload)` for Qt signals — the seam is already right
- **paho runs its own thread; Qt widgets are GUI-thread only.** Emit from the
  paho callback, connect with `Qt.QueuedConnection`. Getting this wrong is a
  crash that looks random.
- Robot card renders `RobotState`; heartbeat age via `QTimer`, not `after()`
- keep three states: online (`<12s`) / stale / offline
- **exit:** online → stale → offline, driven by real heartbeats

**5 — Observe. Traffic card**
- live log of both topics, heartbeats hidden behind a checkbox
- **surface broker-side ACL denials.** A publish to a forbidden topic exits 0 for
  the publisher — only the broker log says `Denied PUBLISH`. Without this a
  mis-scoped robot looks like one that simply never replies.
- **exit:** a wrongly-scoped robot is diagnosable from the page alone

**6 — Settle. Config + cleanup**
- Config dialog: broker address + topic prefix visible; port, client id, sender,
  timeout, TLS, CA path behind "Advanced"
- persist to `user_config/`; delete `calibration_tool/config.py`'s hand-rolled
  env parser and the old Setup tab
- **exit:** survives a restart; no `.env` anywhere in the path

**whole-page exit:** a fresh checkout on a machine with no broker reaches a
connected robot without the user opening a terminal or editing a config file.

### image_interpreter
- photo in → detections out. connected as an **MQTT user**, like any other client.
- **Studio installs models. Studio does not bundle or wrap them.**
- a window into HuggingFace models we've vetted. Click install → Studio fetches,
  sets up, wires to MQTT. Model runs as its **own process**.
- our job as maintainers = **evaluate models and say "this one works on Desk Buddy."**
  That's the real, defensible value. Not wrapping someone's model.
- candidates: YOLO, Google one-shot, others we vet
- **[OPEN]** what's on the v1 list?
- **[OPEN]** how to run them — ONNX Runtime? torch? per-model venv? Lean: isolated
  env per model so their deps never touch ours.
- **[OPEN]** GPU vs CPU. Most beginners have no GPU. Assume CPU, small models.
- arm must work with vision off. non-negotiable.

**AGPL — this design is fine.** (YOLO/Ultralytics is AGPL-3.0.)
- obligations trigger on **conveying**. We don't distribute it — the user downloads
  from upstream onto their own machine.
- even if we did ship it: **mere aggregation**. AGPL §0 excludes "separate and
  independent works... not combined... to form a larger program."
- separate process over MQTT — a socket. FSF: *"Pipes, sockets and command-line
  arguments are communication mechanisms normally used between two separate programs."*
- §13 (the network clause) = offering the work to users over a network. User runs
  it locally. Doesn't apply.

**Hard constraints that keep it true:**
- never vendor model code into our repo
- never `import ultralytics` (or any AGPL lib) in Studio's process
- never link, share address space, or ship in our executable
- MQTT / subprocess / HTTP only
- our adapter says *how to launch and talk to* a model — must not contain or derive from it
- show each model's real license + upstream link at install time
- **[OPEN]** one lawyer read before v1. My reading, not advice.

### workflow_manager
- **the point of the whole product.**
- make behaviors without writing Python
- YAML on disk, in a folder the user can see + git
- two ways to edit, same file:
  - **node graph** — `QGraphicsView`, drag capabilities together
  - **text editor** — for people who'd rather type. Also what an AI writes.
- run it, watch it live, stop it mid-run
- ship 3–5 built-ins that are actually fun — **the demo is the product**
- **[OPEN]** YAML as truth w/ graph reading it, or graph-native format?
  Lean: YAML. diffable, hand-writable, AI-writable.

### calibration_tool
- the existing flow: Base+Perch → IK → Visual → Reach&Grab → Stencil
- works today. real domain knowledge. **port it, don't redesign.**
- **[OPEN]** unless you know parts are wrong/fragile — say which.
- becomes one page in Studio, not the whole app

### logs_plan
- one place. every service, the MQTT bus, the robot, workflow runs.
- filter by service, by topic, by run
- copy/export for bug reports
- today this is buried in `app.py`'s topic log — pull it out, make it first-class

### manual_controller
- direct drive. servos, base, gripper, camera.
- always available, every page — it's how you unstick a robot
- exists today on the right side of the Tk app, and works. keep the idea.
- **[OPEN] E-STOP.** MQTT_SPEC says firmware handlers are **synchronous and block
  dispatch** → probably no true interrupt, only stop-between-actions. Need to know
  before workflows run unattended on a physical arm. **Safety question.**

---

# everything_else

# studio_menu_plan

How the UI is laid out. **FreeCAD's structure + Autonomous Lamp's skin.**

### The two-bar rule (the thing you asked for)

- **Bar 1 — service switcher.** Highest level. Which of the six you're in.
  Always visible, never changes. This is FreeCAD's *workbench selector*.
- **Bar 2 — context bar.** Changes completely based on Bar 1's selection.
  Holds only the actions for that service. This is FreeCAD's *workbench toolbar*.

FreeCAD's core idea: **pick a workbench, the whole toolset below swaps.** Same
here. Pick `image_interpreter`, Bar 2 becomes Install Model / Test Photo / Logs.
Pick `manual_controller`, Bar 2 becomes Home / Perch / Open / Close / Photo.

```
┌────────────────────────────────────────────────────────┐
│ File  Edit  Robot  View  Help          ● connected     │  Qt menu (thin)
├────────────────────────────────────────────────────────┤
│ [Messages][Vision][Workflows][Calib][Logs][Manual]     │  the workspace bar — service
├────────────────────────────────────────────────────────┤
│  Start   Stop   Restart   Config          ⏻ running    │  the toolbar — context
├──────────────┬─────────────────────────────────────────┤
│              │                                         │
│   side       │            main panel                   │
│   list       │                                         │
│              │                                         │
├──────────────┴─────────────────────────────────────────┤
│ ▸ log / status strip                        [ STOP ]   │  always-visible e-stop
└────────────────────────────────────────────────────────┘
```

### From FreeCAD — structure
- workbench switch swaps the entire toolset below it
- dockable panels. user rearranges, closes, reopens from View
- persistent layout — remember size, position, docks, last service (`QSettings`)
- a real menu bar for the things a toolbar shouldn't hold
- status strip along the bottom
- keyboard-driven, discoverable, doesn't hide things

### From Autonomous Lamp — feel
- **warm minimalism.** the opposite of FreeCAD's dense grey.
- palette: charcoal, sand, cloud white, matcha green accent
- earthy and calm, not cold technology. it's a desk companion, not an instrument.
- generous whitespace. let panels breathe. FreeCAD's failure is density.
- **card-based** modular blocks, clear hierarchy
- clean sans-serif, mostly left-aligned
- big type for the thing that matters, quiet type for everything else
- **playful, a little alive** — this is a robot with personality, not a CNC mill

### The synthesis
- **FreeCAD's bones, Lamp's skin.**
- structure is professional and dense-capable; surface is warm and quiet
- default view is calm and simple. depth is available, not forced.
- a beginner sees six friendly buttons; a power user finds every panel dockable

### Concretely
- Bar 1 = `QToolBar`, big icon + label, checkable, exclusive `QActionGroup`
- Bar 2 = `QToolBar` cleared and repopulated on service change
- panels = `QDockWidget` so they rearrange like FreeCAD
- main area = `QStackedWidget` (already built)
- theme = one `QSS` stylesheet, tokens for the palette
- **[OPEN]** dark mode too, or one warm light theme? Lamp's site is light.
  Lean: light default, dark later.
- **[OPEN]** icons — which set? needs to look warm, not enterprise.
  Lucide? Phosphor? custom?
- **[OPEN]** do the six get illustrations/mascots? Lamp leans hard on personality.

### Rules
- **e-stop is always visible.** every service, every state. it's a physical arm.
- connection status always visible. you must never wonder if you're connected.
- Bar 1 never changes. it is the one fixed thing.
- Bar 2 holds only actions for the current service. no global junk.
- nothing important lives *only* in a right-click.

# objects_plan

| Object | One line |
|---|---|
| `Robot` | One physical arm. Speaks MQTT_SPEC. |
| `Capability` | One thing it can do. `perch` `reach` `grip` `photo` `detect_object`. |
| `Service` | One of the six above. start/stop/health/log. |
| `ModelPackage` | One entry in the curated model list. |
| `Workflow` | User behavior. YAML. |
| `Step` | One node in a workflow. |
| `Run` | One execution. Live, stoppable. |
| `Calibration` | Saved physical constants for one Robot. |
| `SimulatedRobot` | Fake arm, no hardware. |

**Robot** — name, topic, broker, creds, online?, last heartbeat. Owns Servos, Base,
Gripper, Camera. **[OPEN]** many robots or one? Lean: model many, ship UI for one.

**Capability** — name, inputs+types, preconditions, terminal response, timeout.
Already exist as payload builders in `mqtt_robot.py`, just unnamed. **Declaring
these as data is the highest-value refactor** — workflows, simulator, and docs all
read from one list.

**ModelPackage** — name, task, source URL, **license**, size, CPU/GPU, install
recipe, launch command, MQTT topic. State: not installed / downloading / installed
/ running. Data not code → adding a model is a PR anyone can send.
**[OPEN]** registry in-repo, or fetched so we add models without a Studio release?

**Step** — capability, args, on_success →, on_failure →, timeout. Args reference
earlier steps' variables. Kinds: capability call / conditional / loop / wait / AI call.

**Run** — workflow, started, status, current step, per-step results, log.

**SimulatedRobot** — speaks MQTT_SPEC, no hardware. **Highest-leverage item in the
plan.** Lets people build Studio and write workflows without owning an arm. Makes
CI possible on all 3 platforms.

---

# layout_plan

```
desk_buddy/
├── firmware/          desk_buddy_plan — ESP32-S3-CAM
├── hardware/          unchanged. BOM, FreeCAD, STLs.
├── docs/              build guide, MQTT_SPEC, calibration walkthrough
└── studio/          desk_buddy_studio
    ├── app.py         Qt entry
    ├── ui/            windows, panels, widgets
    ├── user_config/   preferences. one INI file, all 3 platforms.
    ├── database/      records: runs, telemetry, calibration. SQLite.
    ├── network/       broker services: find, configure, run, accounts
    ├── robot/         client + capabilities + simulator  ← from mqtt_robot.py
    ├── services/      the six. manager + one module each.
    ├── models/        registry + installer + runners
    ├── workflows/     engine, YAML, built-ins
    └── calibration/   ported flow
```

User workflows live outside the package. **[OPEN]** `~/DeskBuddy/workflows/`?

---

# phases_plan

Each ships. No long rewrite branch.

**0 — Ground truth**
- run `tests.py`, see what passes **[OPEN]** does it today?
- delete stray `26.2.1` at repo root
- write the real calibration sequence down from the code

**1 — Robot SDK + Simulator**
- `mqtt_robot.py` → `studio/robot/`, split transport from payloads
- declare Capabilities as data
- build SimulatedRobot
- old Tk app still runs, imports new package
- **exit:** drive a fake arm, tests green, no hardware

**2 — Services**
- service manager: start/stop/health/log
- broker decision (Mosquitto vs amqtt) → working offline
- model registry + installer. install ≠ bundle.
- **exit:** fresh Win/Mac/Linux box runs everything, no cloud account

**3 — Qt UI**
- port pages. controllers mostly survive, views replaced.
- delete Tk. delete zoom system, font fallback list, Maximize button.
- real first-run onboarding, not an `.env` form
- **exit:** `app.py`'s 1651 lines don't exist anywhere

**4 — Workflows**
- engine + runner + live view + stop
- node graph on `QGraphicsView` + text editor, same YAML
- 3–5 fun built-ins
- **exit:** someone adds a behavior with a file, no Python

**5 — Ship**
- **decided: `pyside6-deploy`** (Qt's own tool, wraps Nuitka). `studio/build.py`
  + `.github/workflows/build.yml` matrix already build .exe/.app/.bin. No
  cross-compiling — each OS builds its own on a CI runner.
- **[OPEN] code signing.** Unsigned = Windows SmartScreen "unknown publisher",
  macOS Gatekeeper refuses to open. Cert ~$100-400/yr + Apple notarization.
  Blocks a real beginner install.
- README rewrite: GIF on top, cost, build time, 3 paths
  (build one / set up mine / hack on it → simulator)
- good-first-issues. easiest way in = write a workflow.

---

# decide_early

| Q | Blocks |
|---|---|
| PySide6 confirmed? | everything |
| Mosquitto vs embedded amqtt | Phase 2, install friction |
| Local broker default, or cloud? | onboarding |
| v1 model list + how models run | Phase 2 |
| E-stop: can firmware interrupt a running action? | **safety**, Phase 4 |
| Lawyer read on installer/AGPL boundary | before v1 ships |
| Code signing certs — who buys, who holds keys? | shipping to beginners |
| Win + Mac machines to test on? | else 3-platform bar is fiction |
| One robot or many? | data model |

---

# dont_break

- **`firmware/MQTT_SPEC.md`** — real protocol contract. everything hangs off it.
- **firmware softAP Wi-Fi setup** (`WebServerForStartup.cpp`) — no USB, no serial,
  no COM driver, no esptool, no udev rules. Kills the worst cross-platform problem
  in hobby robotics. **Protect this.**
- payload builders in `mqtt_robot.py` — already an SDK
- the calibration sequence — hard-won, works
- MVC split from `47782a0` — right direction, unfinished

---

# persistence_plan

Settings don't survive a restart. Theme resets to light every launch. That's the
immediate bug, but the answer has to also hold `Run` history and simulation data,
so decide the whole storage story once.

### The short answer

**Both. They're not competing** — they solve different problems, and picking one
for everything is the actual mistake.

| | Settings file (`QSettings`) | Database (SQLite) |
|---|---|---|
| Holds | preferences, window state | runs, telemetry, calibrations |
| Shape | a few dozen key→value | thousands of rows, queried |
| Written | on user action | continuously, during a run |
| If deleted | app resets to defaults, no loss | you lose history |
| Reads | all at startup | filtered: "last 50 runs", "this robot" |
| Concurrency | single writer, fine | needs transactions |

Theme is a setting. A 40-minute simulation logging joint angles at 20Hz is 48,000
rows — that's a database. Putting runs in a config file means rewriting the whole
file on every append; putting the theme in SQL means a schema migration to add a
checkbox.

### What other PySide6 apps do

This is the standard Qt pattern, not a workaround:

- **`QSettings` for preferences.** Qt's built-in. Writes to the *native* location
  per platform — registry on Windows, `.plist` on macOS, `.conf` on Linux — so we
  never hardcode a path. No dependency, no schema, survives reinstalls.
- **A real DB for domain data.** Qt ships `QtSql`, but see the trap below.

Apps that only have preferences (most small Qt tools) stop at `QSettings`. Apps
with history, projects, or logs add SQLite next to it. We're the second kind.

**Nothing gets "installed."** SQLite is a single file and `sqlite3` is in the
Python standard library. No server, no daemon, no service to start, nothing for a
beginner to configure. It's the same class of thing as opening a `.txt` — which
is exactly why it's the right call for an app a beginner installs.

### Where the files go

Verified on this machine; Qt resolves the equivalent on Win/Mac:

```
~/.config/DeskBuddy/Studio.conf              settings   (QSettings)
~/.local/share/DeskBuddy/Studio/studio.db    database   (SQLite)
```

Both come from Qt (`QSettings`, `QStandardPaths.AppLocalDataLocation`), so the
paths are correct per-OS for free.

**Fix first:** `app.py` currently sets `"Desk Buddy"` / `"Desk Buddy Studio"`,
which yields `~/.config/Desk Buddy/Desk Buddy Studio.conf` — spaces in a path we
will be scripting against for years. Set them to `DeskBuddy` / `Studio` *before*
anything writes a file, or we inherit the ugly path forever.

### Traps

- **`QtSql` ships no drivers in our venv** — verified: `QSqlDatabase.drivers()`
  is empty. Qt's SQL layer would need the driver plugin bundled through
  `pyside6-deploy`, which is a packaging problem on three platforms. **Use
  stdlib `sqlite3` instead.** It's always there, and it's the same SQLite.
- **`QSettings` values come back as strings.** `value("zoom", 1.0)` returns
  `"1.0"` on some backends. Always pass `type=` or coerce — this is the #1 Qt
  settings bug.
- **A frozen app has no writable install dir.** Never store next to the binary;
  always `QStandardPaths`. This is why `build.py` needs no data-file changes.
- **Don't write the DB on the UI thread once runs are live.** Fine for settings
  (tiny, on user action); for 20Hz telemetry, batch inserts in a transaction.

### Shape

**Two packages, not one** — the config/records split from above, made visible in
the layout rather than buried inside a single package:

```
studio/
├── user_config/       things the user chose. Safe to delete.
│   ├── keys.py        every setting: name, type, default
│   └── settings.py    the INI file, typed get/set
└── database/          records. Deleting these loses work.   ← Phase 1
    ├── db.py          connection, schema version, migrations
    └── schema.sql     tables
```

The test that decides which package something belongs in: **is it safe to
delete?** Losing `user_config/` resets the app to defaults and costs nothing.
Losing `database/` loses run history. If a new thing fails that test, it is not
a preference no matter how small it is.

`keys.py` exists so no page ever calls `QSettings()` with a raw string key —
keys are typed constants with declared defaults, so a typo is an import error
rather than a silently-lost preference.

### Scope, in order

**Now — settings only. DONE.** `studio/user_config/`, one strategy on all
three OSes.
- **decided: force `QSettings.IniFormat` rather than the native backend.** The
  default would use the registry on Windows and a .plist on macOS — three
  storage engines, and on Windows a file the user cannot open or delete. INI
  gives one readable text file everywhere, at a path Qt still picks per-OS.
- persists theme, zoom index, window geometry + dock state, last page
- **trap found and handled:** `value(k, default, type=int)` returns **0**, not
  the default, when the stored text is junk — and 0 is a valid-looking index
  that survives a range check. `Settings.get()` coerces from the raw value
  instead. Covered by `user_config/tests.py`.
- a saved theme that no longer exists (an Omarchy settings file opened on
  Windows) falls back to light, keeping the other preferences
- **exit met:** close on Nord at 115% on the Manual page, reopen exactly there

**Phase 1 — `studio/database/`, when `SimulatedRobot` lands.** Not before;
there's nothing to record yet. Arrives with the first thing that generates rows.
- tables from `objects_plan`: `run`, `step_result`, `calibration`, `telemetry`
- `schema_version` from day one — the migration you skip is the one that hurts
- a `Run` is the natural unit: one row + N `step_result` + N `telemetry`

**Later, only if needed.** Export a run to CSV/JSON; prune old telemetry. Both
are queries against a schema we'd already have.

### [OPEN]

- **Calibration in the DB or a file?** It's ~20 numbers, but it's *per robot* and
  users will want to back it up and send it to someone debugging their arm. Lean:
  DB is the source of truth, with export/import to a readable file.
- **One DB or one per robot?** Follows the unresolved `objects_plan` question
  ("many robots or one?"). Lean: one DB, `robot_id` column — matches "model many,
  ship UI for one".
- **Does a workflow's YAML live in the DB or on disk?** `layout_plan` already
  leans `~/DeskBuddy/workflows/`. Keep files as truth, DB only references paths —
  users edit workflows in a text editor and put them in git.

---

# network_page_plan

The first page to become real. Everything else is blocked on it — no page can
show a robot until something is connected.

**Assume the user has nothing.** No broker installed, no users, no ACLs, no idea
what a topic is. The old tool started from "type your broker address", which
only works if someone already set one up for you. This page's first job is to
*create* the thing the rest of the app talks to, and only then connect to it.

So the page is a **broker manager first, MQTT client second**:

    install it → configure it → create users + topics → run it → connect → observe

A beginner should get a working broker by pressing one button and never learning
what a password file is. `calibration_tool/` is the reference for the *client*
half only — the behaviour there is proven and should be carried over, the shape
should not.

## What the old tool got right — keep all of it

- **`MqttRobot` is already an SDK.** 703 lines, transport separate from payload
  builders (`perch_payload`, `ik_payload`, `servo_payload`, …). It does not
  import Tk. This is the single most valuable thing in the repo and it ports to
  Qt almost untouched — `phases_plan` step 1 is mostly a `git mv`.
- **`RobotState` as one dataclass.** Every fact about the connection in one
  place: `broker_connected`, `robot_online`, `last_error`, `last_heartbeat`,
  `heartbeat_age()`. The page renders this; it never computes it.
- **The `on_event(kind, payload)` callback.** `_emit("status"|"error"|"state")`
  is already the right seam — it becomes Qt signals with no redesign.
- **Two derived topics from one prefix.** You type `esp32_5`, you get
  `esp32_5/test` and `esp32_5/HEARTBEAT`. Never make anyone type both.
- **Heartbeat age, not a boolean.** `< 12s` = online, older = *stale*, none =
  offline. Three states, because "connected but silent for a minute" is the
  case that actually happens and a boolean cannot say it.
- **The heartbeat filter in the log.** Heartbeats drown real traffic. Hidden by
  default, one checkbox to show them.
- **Refusing reserved sender names** (`ai_server`, `visual_ai`, `firmware`).
  Validation that prevents a confusing failure later.

## What to change

- **The `.env` form is the thing to kill.** Nine fields, all equal weight, all
  shown at once, including `Client ID` and `Timeout Seconds` — which a beginner
  must not have to think about. `phases_plan` already calls for "real first-run
  onboarding, not an `.env` form".
- **Settings go to `user_config/`, not `calibration_tool/.env`.** The INI file
  already works on all three platforms. `config.py`'s hand-rolled env parser
  (`_parse_bool`, `_quote_env`, quote-stripping) all deletes.
- **Except the password.** A broker password does not belong in a plaintext INI.
  **[OPEN]** `keyring` (native credential store, one dependency) vs. leaving
  cloud auth for later. Local broker needs no password, so this does not block
  offline-first.
- **Views must not take theme arguments.** `StatusView.__init__` takes
  `muted_color`, `ink_color`, `good_color`, `body_bold_font`… — eleven styling
  parameters threaded through the constructor. `theme/` already solves this;
  pages set an object name and the QSS does the rest.
- **No `self.after(1000, ...)` loop.** The heartbeat ticker becomes a `QTimer`.

## The page

The cards are a **ladder** — each one only matters once the one above it is
green, and the page shows you the first rung you have not cleared:

```
Broker      not installed / installed / running        [Install] [Start] [Stop]
Access      users + topic permissions                  [Add robot]
Robot       online / stale / offline + heartbeat age
Traffic     live log, heartbeats hidden by default
```

On a fresh machine only the Broker card is live; the rest render muted until
there is something to say. That is the same "real space, obviously inert" rule
the placeholder cards already follow, doing real work.

These cards are *built back up* one step at a time — step 0 deletes the
placeholder versions first, so nothing on the page is ever a lie.

the toolbar becomes `Install / Start / Stop / Restart | Add Robot | Config / Topics` —
`Install` replaces itself with the broker state once one exists. `Config` opens
broker settings, which is where the old Setup tab's fields go —
**progressive disclosure**:

- **Always shown:** broker address, robot topic prefix. Two fields.
- **Behind "Advanced":** port, client id, sender, timeout, TLS, CA cert path.
  Every one of these has a working default.

Side panel stays `Local broker / mqtt.deskbuddy.ai / Custom…` — that list is
the offline-first story made visible, and it is already right.

## Broker management — the part that has to exist first

**Verified on this machine, and it decides the design:** Mosquitto runs
*completely unprivileged*. Its own config file, own password file, own ACL
file, a high port, no root, no systemd, no service registration:

```
mosquitto -c ~/.local/share/DeskBuddy/Studio/broker/mosquitto.conf
mosquitto_passwd -b <passwd_file> studio <generated>     # no root
```

Tested end to end: wrong password refused, anonymous refused, and a valid user
publishing outside its ACL denied. All from files Studio writes itself.

That means **Studio owns a private broker instead of touching the system one.**
No `sudo`, no editing `/etc/mosquitto/`, no fighting a config the user may have
set up for something else, and uninstalling is deleting a directory.

### Install — the only genuinely per-OS part

Detection is uniform (`mosquitto -h`, parse the version). Installing is not:

| | How | Needs |
|---|---|---|
| Linux | the distro package manager, or point at an existing binary | root, varies by distro |
| macOS | `brew install mosquitto`, else download | brew may not exist |
| Windows | official installer `.exe`, or a bundled portable build | UAC prompt |

**Lean: never silently install.** Studio detects, then *offers* — showing the
exact command it would run and letting the user copy it instead. An app that
runs a package manager as root without asking is the kind of thing a beginner
should be taught to distrust.

**[OPEN] the real fallback question.** If Mosquitto is absent and the user
cannot or will not install it, the offline-first promise breaks. Options:
- **`amqtt`** — pure Python, `pip`-installable, runs in-process. Zero install,
  works everywhere, but less proven and another dependency. (Not currently in
  the venv.)
- **bundle a Mosquitto binary** per platform — reliable, but three binaries to
  ship and sign, and `phases_plan` already flags signing as unresolved.
- **require it** — simplest, and abandons anyone who hits a wall.

This is the same Mosquitto-vs-`amqtt` decision `mosquitto_plan` already has
open. It should be decided here, because this page is where it becomes real.

### Users and topics — generated, never typed

The user should never write a password file line or an ACL rule. Studio does:

```
Add robot  →  name it "esp32_5"
           →  generate a strong password
           →  mosquitto_passwd -b <passwd> esp32_5 <generated>
           →  append ACL:  user esp32_5
                           topic readwrite esp32_5/#
           →  reload the broker
           →  show the credentials ONCE, with a copy button
```

Those are the exact credentials that get flashed to the firmware, so the page
must show them in a form that is easy to copy and hard to lose. **[OPEN]** show
a QR / config snippet for firmware flashing?

Two accounts exist from first run: `studio` itself (full access, stored in
`user_config/`) and one per robot, scoped to its own topic tree.

### Traps found while testing

- **A broker may already be on 1883.** There is one on this machine right now.
  Studio must detect a listener and offer to use it rather than fail to bind —
  and its own broker should default to a distinctive port anyway.
- **An ACL denial is silent to the publisher.** `mosquitto_pub` to a forbidden
  topic exits 0; only the broker log says `Denied PUBLISH`. So the Traffic card
  must surface broker-side denials, or a mis-scoped robot looks like a robot
  that simply never replies.
- **`mosquitto_passwd` warns on world-readable files.** Create the broker
  directory `0700` from the start; a future version refuses to load them.

## Shape

```
studio/network/                 services. No Qt in here.
├── broker_finder.py     installed? running? what should the user run?
├── broker_commands.py   start / stop / restart OUR broker, and its config
└── accounts.py          add a robot: password, ACL rule, reload  (step 3)

studio/robot/                   talk to it. Ported from calibration_tool.
├── client.py       MqttRobot, ported. Qt signals instead of on_event.
├── payloads.py     the payload builders, unchanged
└── state.py        RobotState

studio/ui/pages/network/        the page, one file per section.
├── network.py      the layout: identity, actions, side panel, section order
├── broker.py       the Broker card
├── access.py       users and topics                              (step 3)
├── robot.py        heartbeat                                     (step 4)
└── traffic.py      the live log                                  (step 5)
```

**Services return finished strings, not fields to assemble.** A view should
never branch on `partial` or build a sentence out of a version and a path —
`report()` hands back `headline`, `detail`, `install_command`, `chip` and the
page renders them. Wrong wording is then a one-line fix in one place, and the
services stay testable with no window open.

**A page becomes a directory once it has more than one section**, with the
layout file as the only thing the rest of the app imports.

Two packages because they fail differently and are testable apart: `broker/`
is subprocesses and files on disk and needs no network; `robot/` is a socket
and needs no broker of its own (it points at any address). Keeping them
separate is also what lets `SimulatedRobot` skip the broker entirely.

All broker state lives under `AppLocalDataLocation` beside the database —
`broker/` config and credentials are records, not preferences, and deleting
them loses a working setup.

The page reads `RobotState` and renders. It does not own a socket, parse a
payload, or decide what "online" means — `client.py` does all of that, which is
what lets `SimulatedRobot` drop in behind the same signals later.

**Threading:** paho runs its own network loop thread; Qt widgets may only be
touched on the GUI thread. Signals across a thread boundary are the fix — emit
from the paho callback, connect with `Qt.QueuedConnection`, let Qt marshal it.
Getting this wrong is a crash that looks random, so it is worth stating once
here rather than debugging six times.

## Order

The six implementation steps live in `mosquitto_plan` → *implementation steps*,
so the broker work is described once, next to the service it builds.

Short version: **1** detect → **2** run our own → **3** accounts →
**4** connect → **5** observe → **6** settle.

**exit for the whole page:** a fresh checkout on a machine with no broker
reaches a connected robot without the user opening a terminal or a config
file — and remembers it all on the next launch.

## [OPEN]

- **The fallback if Mosquitto cannot be installed** — `amqtt`, bundle a binary,
  or require it. Decides whether offline-first survives a locked-down machine.
  This is `mosquitto_plan`'s open question, and this page is where it lands.
- **Does Studio ever offer to run the installer itself**, or only ever show the
  command? Lean: show it. Running a package manager as root unprompted is a
  habit a beginner should not be taught.
- **Firmware handoff** — once a robot account exists, how do its credentials
  reach the ESP32? Copy button, config snippet, QR?
- **Broker password storage** — `keyring`, or defer until cloud auth matters?
- **Does the Network page own connection for the whole app?** Every other page
  needs the same `MqttRobot`. Lean: one instance on the window, pages read it;
  the Network page is the only one that may connect or disconnect it.
- **Auto-connect on launch?** Convenient, but a beginner hitting a hang on a
  bad address with no obvious cause is worse. Lean: connect on launch only
  after a first successful manual connection.
