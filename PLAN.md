# Desk Buddy Studio — Plan

Draft. **[OPEN]** = needs your call.

.venv/bin/python -m studio

---

## Map how mark thinks

# stack_plan

- desk_buddy_plan   - ESP32-S3-CAM    - 3D printed hobby robot arm
- desk_buddy_studio - PySide6 (Qt)    - Place to self host manage all services and control deskbuddy
- messages_plan     - Mosquitto Mqtt  - sends messages to all things on the stack

# desk_buddy_studio_services

- mosquitto_plan    - messages
- image_interpreter - YOLO, google one shot, or other systems we vet. connected to mqtt user
- workflow_manager  - code editor and studio tools to make workflows
- calibration_tool
- logs_plan
- manual_controller

# everything_else

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

### messages_plan — the bus
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
- the bus. everything else connects to it.
- Studio starts it, holds creds, shows connection state
- offline-first: no `deskbuddy.ai` account required to use your own robot
- **[OPEN]** does cloud broker stay the default, or local? Changes first-run.

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
│ [Messages][Vision][Workflows][Calib][Logs][Manual]     │  BAR 1 — service
├────────────────────────────────────────────────────────┤
│  Start   Stop   Restart   Config          ⏻ running    │  BAR 2 — context
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
