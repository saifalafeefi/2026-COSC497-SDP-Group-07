# macOS — start here

## Just want it running?

**Double-click `Pulse Control.app` in this folder.** That is the whole thing.

Every command in this file is a button in that window. You do not need a
terminal, a virtualenv, or any of the hundreds of lines below.

First time on this machine? The window still opens — press
**Advanced → Set up the virtualenv…**, wait a few minutes, done.

## The five things you will actually do

| I want to… | In the app | Or in a terminal |
|---|---|---|
| show the demo, no hardware | **Demo** → Start WESAD demo | `./run anomaly.serve` |
| run the real thing | **Live** → Start master | `./run anomaly.fleet` |
| put a board on WiFi | **Board setup** → Connect board | `./run anomaly.device_wifi --hotspot` |
| record a stress session | **Stress session** → Start | `./run anomaly.protocol run` |
| find out why something broke | **Advanced** → Check setup | `./run doctor` |

`./run` needs no virtualenv and no activation — it finds the right Python by
itself. `./run` with nothing after it lists everything it can do.

## Making the app

It is **already in this folder** — committed, so a fresh clone has it. Nothing
to build.

**Looking for it on github.com and not seeing an app?** You won't. Git has no
idea what a macOS app bundle is, so the website lists it as an ordinary folder
called `Pulse Control.app`, sorted between `pulse` and `pulse_display`. It only
becomes a double-clickable app once it is on a Mac's disk — `git clone`, or
Code → Download ZIP, both work and both keep the executable bit the bundle
needs.

If it is missing from your copy, or you changed `launcher/pulse_control.py` or
the icon, rebuild it in about a second:

```bash
bash launcher/make_app.sh
```

A plain `git pull` never needs a rebuild: the app runs
`launcher/pulse_control.py` out of the folder it sits in, so pulling new code
changes what the buttons do by itself.

The one case it cannot cover is a Mac where no Python has Tk 8.6 (macOS's own
ships 8.5 from 2010, which draws an empty window). The app says so if it hits
that; then build one that carries its own Python and Tk:

```bash
bash launcher/build_app.sh      # -> "Pulse Control (standalone).app", 29 MB
```

**That is it. You can stop reading.**

---

# Reference

Everything past this line is for when something breaks, or when you are
changing the model or the firmware. Skim the headings; ignore the rest.

| | |
|---|---|
| [0. running commands](#0-running-commands-by-hand) | `./run`, or activating a venv |
| [1. setup by hand](#1-setup-once-per-machine) | what the app's button does for you |
| [2. the demo](#2-the-demo) | the master, the board, dashboards, stress sessions |
| [3. the store](#3-the-store) | subjects, baselines, flags |
| [4. build steps](#4-build-steps) | only when the model or data changes |
| [5. latency](#5-detection-latency--what-to-expect) | why a flag takes 10–40 s |
| **[6. troubleshooting](#6-troubleshooting)** | **the one worth bookmarking** |
| [7. git](#7-git) · [8. prior work](#8-prior-work--kept-not-the-current-direction) · [9. where things live](#9-where-things-live) | |

On Windows? → [`COMMANDS-windows.md`](COMMANDS-windows.md)

## 0. running commands by hand

`./run <module>` — no activation, any tab, conda or not:

```bash
./run anomaly.fleet  # == python -m anomaly.fleet
./run setup          # build the venv and install everything
./run doctor         # what is installed, and where
```

prefer the venv? activate it, and every command below works as written:

```bash
source ~/.venvs/sdp07/bin/activate
```

two things make that appear to "stop working":

- it is **per terminal tab** — a new tab is back to the system python
- a `python` **alias or function** in `~/.zshrc` is resolved before `PATH`, so
  activation can look right (correct prompt, `VIRTUAL_ENV` set) while `python`
  still runs something else. `which python` shows the function, not the truth:

  ```bash
  python -c "import sys; print(sys.executable)"    # must be under .venvs/sdp07
  ```

`./run` is immune to both.

---

## what to run, and what to ignore

| command | status | what it is |
|---|---|---|
| `anomaly.fleet` | **the demo** | find every board on the LAN, score them all, roster at :8002 |
| `pulse_display/make_web_assets.py` | **required** | rebuild the board's web pages. after ANY dashboard edit, and on every fresh clone |
| `anomaly.serve` | current | single-stream dashboard at :8001 — WESAD replay, or one board over USB |
| `anomaly.db` | current | inspect the store, take a backup |
| `anomaly.device_wifi` | current | put a board on WiFi over USB, read back its IP. `--hotspot` = this Mac's Internet Sharing |
| `anomaly.hotspot` | current | this Mac's Internet Sharing: state, or `--on` |
| `anomaly.device_check` | current | live grip coach — is the finger on properly? |
| `anomaly.device_source` | current | sensor self-test, no model and no dashboard in the way |
| `anomaly.device_calibrate` | current, USB only | re-derive thresholds on your own calm, for `serve --source device` |
| `anomaly.protocol` | **the experiment** | record an induced-stress session on the fleet, then score it |
| `anomaly.board_export` | build step | export the on-device detector to the firmware |
| `anomaly.run` | build step | leave-one-subject-out evaluation (O1/O2) |
| `anomaly.export` + `anomaly.compress` | build step | train and ship a new model. always both, in that order |
| `anomaly.make_demo_clip` | build step | rebake the WESAD demo clip into Pulse Watch |
| `anomaly.calibrate` | build step | per-user calibration on WESAD (the O6 *method*) |
| `anomaly.make_plots` | build step | regenerate the result figures |
| `anomaly.master` | **superseded** | one board, headless, no roster and no store. `fleet` does this and more |
| `pipeline/server.py` | **prior work** | the old cardiac dashboard. do not point a demo at it |
| `pipeline/run_cli.py` | **prior work** | terminal-only cardiac pipeline |
| `baselines/train.py`, `quantize.py`, `inference_demo.py` | **prior work** | the supervised 3-class cardiac model, superseded by `anomaly/` |

"build step" means you run it when the model or the data changes, not to demo.
everything under **prior work** is kept as a reference point and is *not* the
current direction — see the pivot table in the project brief.

### if the app misbehaves

![the icon](launcher/icon/preview.png)

- **keep it inside the project folder** — it finds the project by sitting in
  it. drag it to the Dock for convenience; that leaves the original in place
- it picks a Python with **Tk 8.6+** itself, deliberately skipping macOS's own
  `python3` (Tk 8.5.9 from 2010 never lays out `ttk.PanedWindow`, so the window
  opens empty with no error). miniforge, python.org, or Homebrew's
  `python@3.12` + `python-tk@3.12` all qualify
- rebuilding it is `bash launcher/make_app.sh` — see "Making the app" above

---

## 1. setup (once per machine)

```bash
git clone <repo-url>
cd Spring-2026-COSC497-SDP-Group-07
./run setup
```

`./run setup` is the whole of this section: it finds Python 3.12, builds
`~/.venvs/sdp07`, and installs both requirements files. `brew install
python@3.12` first if it says 3.12 is missing. the rest of this section is what
it does, and why, for when you would rather do it by hand.

**make a virtualenv, and use python 3.12 specifically.**
`baselines/requirements.txt` pins `tensorflow-cpu<2.20`, and no TF below 2.20
ships wheels for 3.13 or 3.14 — on a newer interpreter pip simply fails to
resolve tensorflow.

```bash
brew install python@3.12
python3.12 -m venv ~/.venvs/sdp07
~/.venvs/sdp07/bin/python -m pip install --upgrade pip
~/.venvs/sdp07/bin/python -m pip install -r baselines/requirements.txt -r pipeline/requirements.txt
```

**for Pulse Control only, also install Tk.** Homebrew ships it as a separate
formula, so a venv can have every dependency in this project and still not open
a window (`ModuleNotFoundError: No module named '_tkinter'`):

```bash
brew install python-tk@3.12
```

it installs into the same `python@3.12` the venv is built on, so there is
usually nothing to re-create — just run the app again. nothing else in the
project needs it.

- **activate once per terminal** (section 0), then use
  `python`, not `python3`. `deactivate` when done, or just close the tab.
- **or skip activation and spell out the interpreter** —
  `~/.venvs/sdp07/bin/python -m anomaly.fleet`. more typing, but immune to shell
  aliases and to a stale `python3`. either is fine. what never works is a bare
  `python3`: that is the system interpreter, and none of the dependencies are
  installed there.

  to confirm activation took, ask which interpreter you actually have — it
  should be the one under `.venvs/sdp07`:

  ```bash
  which python
  python -c "import sys; print(sys.executable)"
  ```

  `which python` is not always enough on zsh: if your shell defines `python` as
  a *function* (a common dotfile trick, and what `which` will show you), the
  second line is the one that tells the truth. a function that falls back to
  Homebrew's python when `VIRTUAL_ENV` is unset looks exactly like a working
  setup right up until an import fails.

  one caveat: if your prompt already says `(base)`, conda is active too. both
  conda and a venv prepend to `PATH`, so a later `conda deactivate` can leave
  you pointing at the wrong interpreter with no warning. check with
  `python -c "import sys; print(sys.executable)"` if a command starts failing
  for no reason.
- **keep the venv outside the repo and outside any synced folder** (iCloud,
  Dropbox). a 1.7 GB venv under an iCloud-synced `~/Documents` made
  `import numpy` take **174 s** instead of 0.2 s. `.gitignore` does not stop a
  sync client. (a `.nosync` suffix on the folder is the other half of this fix.)
- **venvs are not relocatable** — console scripts hardcode the interpreter path.
  recreate rather than move.
- only `anomaly.device_check` / `anomaly.device_source` run without TensorFlow
  (numpy + scipy + pyserial); everything else needs the full install.
- **Intel Macs:** `tensorflow-cpu` stops at 2.16.2, which forces `numpy<2` and
  `setuptools<81`. both constraints are already in the requirements file. Apple
  Silicon is unaffected.

datasets are not in git and are only needed for build steps:
WESAD (~17 GB) unzips into `WESAD/`; the UBC PPG set (~3.8 GB, old baseline
only) into `Code & Data/` — [Borealis Data](https://borealisdata.ca/dataset.xhtml?persistentId=doi:10.5683/SP3/HF0OS9).

---

## 2. the demo

### 2.1 the master

```bash
python -m anomaly.fleet                  # roster -> http://localhost:8002
python -m anomaly.fleet --hotspot        # boards on this Mac's Internet Sharing
python -m anomaly.fleet --subnet 192.168.1   # only if a board is on another network
```

**on a Mac, plain `fleet` is usually the one you want.** it scans every network
this machine is on, so putting the laptop and the board on the same WiFi — a
phone's hotspot, or the lab router — needs no setup at all. `--hotspot` is for
when you want the Mac itself to be the access point; macOS makes that more work
than Windows does (§2.2).

scans every local subnet for boards, scores each one, pushes each verdict back.
no IP to look up. assign a subject to each board from the roster, then
calibrate — the baseline is stored against the **subject** in `data/pulse.db`,
not the board, so it follows the person from one board to another. a board with
nobody on it, or somebody with no baseline, gets no flag rather than someone
else's. sessions open and close on their own as contact comes and goes.

done from the roster, not the command line:

- **assign / rename / delete a subject** — the panel on the right
- **calibrate** — per board; the same button on the board's own dashboard now
  reaches the master too, and progress shows in both places
- **review a flag** — real / artifact / ? on each history row. these are stored
  for evaluation; nothing consumes them for flagging
- **clear a board's flag history** — `clear` in the History header. the saved
  waveforms under `data/flags/` stay on disk
- **tuning** — live sliders for the detection constants, persisted across
  restarts

useful flags: `--hotspot`, `--device <ip>` (repeatable, for a board off this
subnet), `--port`, `--rescan <seconds>`, `--db <path>`.

### 2.2 the board

**put it on WiFi.** two ways, and on a Mac the first is usually easier.

**(a) the same network as this Mac** — a phone's hotspot, or the lab router.
nothing to configure on the Mac at all. plug the board in by USB once:

```bash
python -m anomaly.device_wifi --ssid "Saif iPhone"          # join it now
python -m anomaly.device_wifi --ssid "Saif iPhone" --save   # remember it for later
```

then put the Mac on that same network and run plain `python -m anomaly.fleet`.
it scans every network the laptop is on and finds the board by itself.

**(b) this Mac's own hotspot — Internet Sharing.** the Mac sits next to the
board, so range stops being a problem. macOS keeps this behind an admin wall,
so there is a one-time setup by hand:

1. **System Settings → General → Sharing → Internet Sharing.** share from your
   Ethernet or iPhone USB connection, *to* Wi-Fi.
2. **Wi-Fi Options…** — set the network name, pick **channel 11 or lower**
   (the ESP32 cannot join 5 GHz), and set a **WPA2 password**.
3. switch Internet Sharing on.

no script can make those choices for you — macOS gives none the right. once it
is configured, this reads it and will try to switch it back on:

```bash
python -m anomaly.hotspot            # state, name, subnet (never the password)
python -m anomaly.hotspot --on       # switch it on if it is off
```

`--on` needs root, and only ever tries `sudo -n` — it will never sit waiting for
a password that nobody is there to type (which would hang Pulse Control). so in
practice it either works, or it prints the one command to paste:

```bash
sudo launchctl kickstart -k system/com.apple.NetworkSharing
```

then send the board at it over USB:

```bash
python -m anomaly.device_wifi --hotspot
```

it reads the shared network's name from macOS and asks you to type the password
once — macOS keeps it in the System keychain, which a script cannot read. the
password is not echoed, printed or saved; it goes straight down the cable. from
then on the board rejoins by itself on every boot.

two macOS habits to know:

- **Internet Sharing needs a connection to share** (Ethernet, or an iPhone over
  USB). no upstream connection in the room = no hotspot; keep a phone hotspot
  as the fallback and use route (a) instead.
- **it hands out `192.168.2.x`** on `bridge100`, which `fleet --hotspot` reads
  live and scans. that is a different subnet from Windows' `192.168.137.x`, so
  a board set up on one PC is still found on the other — the fleet scans every
  network it is on, not a hard-coded one.

**a board remembers up to 5 networks**, most recently used first, and tries
them in turn (15 s each) until one answers — so one board moves between this
Mac's hotspot, a phone's hotspot and a router by itself. add one over WiFi from
Pulse Control (Board setup → Add a WiFi network), or over USB:

```bash
python -m anomaly.device_wifi --ssid "Saif iPhone" --save   # remember it for later
python -m anomaly.device_wifi --ssid "Saif iPhone"          # join it now
python -m anomaly.device_wifi --forget --ssid "Old Network" # forget one
python -m anomaly.device_wifi --status                      # on now + saved list
```

**join now has a safety net:** if the board hasn't joined within 25 s (wrong
password, out of range) it goes back to the network it came from, and the new
one drops to the end of the list. over WiFi: `GET /wifi` lists the saved
networks (names only, never passwords); `POST /wifi` with `action=save|now|forget`
changes them. plain HTTP on the LAN — fine for a lab, not a product.

the other two ways; anything stored on the board wins over the header, since
NVS survives a reflash. (secrets.h is the last entry in the list, never stored.)

hard-coded: copy `pulse_display/secrets.example.h` to
`pulse_display/secrets.h` (gitignored) and fill in `WIFI_SSID` / `WIFI_PASS`.
the sketch does `#include "secrets.h"`, so it will **not compile until that copy
exists** — it is the first thing to do on a fresh clone. leave both values empty
(`""`) if you only ever set WiFi over USB. if the board already has credentials
stored, clear them once and power-cycle:

```bash
python -m anomaly.device_wifi --forget
```

**2.4 GHz only** — the ESP32 cannot join a 5 GHz-only SSID. the IP appears on
the TFT header and on serial as `# wifi connected ssid=... ip=...`.

**rebuild the web assets. this is not optional.** the dashboards are gzipped
into a C header and compiled into the firmware. `pulse_display/web_assets.h` is
gitignored, so a fresh clone does not have it and the sketch will not compile
until you generate it — and after any edit under `pulse/` or `anomaly/static/`
the board keeps serving the old copy until you re-run this and reflash:

```bash
python3 pulse_display/make_web_assets.py     # -> pulse_display/web_assets.h
```

stdlib only (`gzip`, `os`, `sys`), so this one really does run on a bare
`python3` — it is the only command here that does.

**flash it.** needs two Arduino libraries, both by **ESP32Async** (older forks
do not build against ESP32 core 3.x): **ESP Async WebServer** and **Async TCP**.
set **Tools → PSRAM → OPI PSRAM** (the N16R8's 8 MB) — the on-device detector
lives there. without it the board still does everything else and reports
`self=off` (§2.6).

```
http://<board-ip>/          Pulse Watch
http://<board-ip>/dev       developer dashboard
http://<board-ip>/health    plain text diagnostics -- try this FIRST
```

`/health` reports `ip rssi heap ir bpm cond head tail drop wsn sent skip rec ticks`:

- `cond` climbing = conditioning is running. frozen = the sample loop is stuck
- `ticks` should rise by hundreds between polls, not by one
- `skip` = frames dropped for websocket backpressure (a weak link)
- `rec` = times the sensor was re-initialised after stalling
- `det dsc dlv dms dhold ddrop self psram` = the on-device detector (§2.6)

the wearer's name shows top-right on the board's screen, and survives a reboot.

### 2.3 demo data, when there is no hardware

Pulse Watch has three data sources, picked in Settings:

- **Live** — the sensor over WebSocket. if it will not connect the page stays
  **offline**; it does not quietly fall back to a demo (it used to, and that was
  indistinguishable from working)
- **Simulated** — a scripted scenario, no model involved. edit the `SIM` table
  at the top of `pulse/Pulse Watch.dc.html`: `label` is ground truth, `level` is
  what the detector reports, and they are deliberately out of step so the
  10–40 s detection lag is visible. seeded, so it plays identically every time
- **WESAD S5** — a recorded clip of real physiology scored by the deployed
  model. this is the one to show when somebody asks whether any of it is real

both demos also drive the board's TFT, marked `DEMO` on the panel so a recording
can never be mistaken for a measurement. the "simulate poor signal" button
injects an artifact — seconds of raised amplitude, like a real knock — and the
detector holds its last verdict instead of flagging, which is the quality gate
doing its job.

### 2.4 the single-stream dashboard

separate from the fleet: one stream, one page, with a scorecard against ground
truth. run `serve` **or** `fleet`, not both.

```bash
python -m anomaly.serve                      # WESAD replay (default) -> :8001
python -m anomaly.serve --subject S17        # other clean subjects: S17, S7
python -m anomaly.serve --source device      # a board over USB, auto-detect port
python -m anomaly.serve --source device --device-port /dev/cu.usbmodem101
```

macOS names the board's port `/dev/cu.usbmodem…` or `/dev/cu.usbserial…`
(always the `cu.` device, never `tty.` — `tty.` blocks on carrier detect). the
port auto-detects; list them with `python -m anomaly.device_source --list-ports`.

- `/` (alias `/watch`) — Pulse Watch, the product UI
- `/dev` — developer dashboard: the model's flag against the WESAD label live
  (TP/FP/FN/TN, precision/recall, a true-stress band). this is the "is the flag
  from the model or from the dataset?" answer. real mode has no ground truth, so
  the scorecard goes blank; everything else works
- both run `ae_int8.tflite`, the same file the ESP32 runs

⚠️ the saved threshold in `scorer.npz` was calibrated on WESAD **wrist** BVP,
not a fingertip MAX30102. the waveform is live and correct but the flag is not
meaningful on real hardware until recalibrated (below). `/dev`'s subtitle says
which thresholds are in force.

**calibrate the USB rig on your own calm:**

```bash
python -m anomaly.device_check                # get a steady GOOD TO RECORD first
python -m anomaly.device_calibrate            # 5 min of calm, sit still
python -m anomaly.serve --source device       # now flags against your baseline
```

writes `anomaly/saved/scorer_device.npz` and leaves `scorer.npz` alone, so the
WESAD demo is unaffected. `serve --source device` picks it up automatically;
force either with `--scorer wesad` / `--scorer device` — that before/after *is*
the domain-transfer delta, live. windows are gated on the same grip checks
`device_check` prints, so a fidget never teaches the model "normal".
`--simulate --minutes 2 --min-windows 5` runs the whole path on a synthetic
pulse with no board attached and writes nothing. `--dry-run` reports without
writing. raw calm windows land in `anomaly/saved/device_calm.npz` (gitignored —
personal biometric data).

**check the hardware before blaming the model:**

```bash
python -m anomaly.device_source --list-ports
python -m anomaly.device_source            # live self-test, no TensorFlow
```

---

### 2.5 induced-stress protocol sessions

the experiment the whole method rests on: does the flag respond to stress, not
just to a knocked sensor? runs on the fleet, so start the master first and
assign the subject on the roster.

```bash
python -m anomaly.fleet                                   # terminal 1
python -m anomaly.protocol run                            # terminal 2: live console
python -m anomaly.protocol run --device pulse-a4f2c1 --task "1022 - 13"
```

settle (until a clean 60 s window exists) → baseline 3 min → induction 3 min →
recovery 3 min. the console prints what to do at each phase; ctrl+c stops it
and keeps what was recorded, marked aborted. the roster card has the same
controls (**Protocol**, then **Next phase now** / **Stop**).

- phase marks are written at their scheduled time, and they are the label
- change durations with `--baseline / --induction / --recovery <seconds>`
- the live calm reference is pinned to the baseline phase during the run;
  `--no-freeze` tests the flag exactly as it runs day to day
- `--no-raw` skips saving the waveform; per-second scores are always saved
- `python -m anomaly.protocol next` / `stop` from another terminal, if needed

score it:

```bash
python -m anomaly.protocol list
python -m anomaly.protocol report                         # every session, pooled
python -m anomaly.protocol report --session 12 --plot     # one, with a figure
```

pre-committed reading, per session: **HR control check** (induction median
minus baseline ≥ +10 bpm, or the task did not take and the session proves
nothing), **AUROC** of raw window scores baseline vs induction (≥ 0.70 =
responds), **recall@90spec** against the session's own baseline p90, and time
to first live flag. only windows lying entirely inside a phase count. verdicts:
`RESPONDS`, `HR ONLY` (the domain-transfer finding — report it),
`NO INDUCTION`, `UNSCOREABLE`.

files land in `data/protocols/session_<id>.npz` (+ `.png`), gitignored with
the rest of `data/`.

### 2.6 the on-device detector

the board scores itself once a second: the same movement gate the master uses,
the heart rate as the score, and the master's live self-referencing scale, which
only flags it going UP from the wearer's own recent calm. nothing is fitted. the master's verdict still wins while it is fresh (it
runs the autoencoder); when the master is gone the board's own takes over and
the panel says **ON-DEVICE**. the raw waveform never has to leave the board.

on WESAD leave-one-subject-out the board's rule scores PR-AUC **0.865** /
recall@90spec **0.813**, against the autoencoder's 0.706 / 0.545 on the host and
the 10-feature Mahalanobis it replaced (0.635 / 0.437). reproduce with
`python -m anomaly.run --model hr`. the board's verdict rides along in every
frame (`ond`), so a protocol session (§2.5) reports both side by side, on our
hardware.

at boot it runs baked test windows through the C++ and compares them to the
Python reference. serial prints `# on-device detector self-test pass …`, and
`/health` shows:

| field | meaning |
|---|---|
| `self` | `pass`, `fail` (detail on serial; it will not issue verdicts), or `off` (no PSRAM) |
| `det` | `idle` no finger · `warm` filling · `hold` movement or no readable pulse · `ok` scoring |
| `dsc` / `dlv` | score (heart rate, bpm) / level 0–100 (`-1` = none yet) |
| `dms` | milliseconds the last tick took, on core 0 |
| `dhold` | windows refused for movement |
| `ddrop` | seconds the gate skipped in the last scored window. climbing 1/s = every new second rejected, window back-filled from older ones — why `dsc` can sit frozen |

change it — both files in `pulse_display/` come from here (and
`board_model.h` is mirrored into `pulse_headless/` for you):

```bash
python -m anomaly.board_export --check 300   # prove the C algorithm vs scipy/quality
python -m anomaly.board_export               # rewrite board_model.h
```

`board_model.h` is generated (the constants and the self-test vectors) and
committed so a fresh clone compiles. `board_detector.h` is the algorithm, a
line-for-line port of the mirror in `board_export.py`: change one, change the
other, re-run `--check`, reflash, and read `self=`.

## 3. the store

one SQLite file, `data/pulse.db`, holding subjects, baselines (including the
`k_sigma` that survives a re-wear), sessions, readings, flags and the tuning
values. gitignored — it is personal biometric data.

```bash
python -m anomaly.db                         # row counts + every subject's baseline
python -m anomaly.db --backup pulse.bak.db   # consistent snapshot, safe while running
```

**use `--backup` to move subjects between machines.** copying `pulse.db` on its
own is not a backup: in WAL mode the newest commits sit in `pulse.db-wal` until
a checkpoint, so a plain copy silently comes back missing the most recent work.
on the destination, stop the master, drop the file in as `data/pulse.db`, and
delete any stale `pulse.db-wal` / `pulse.db-shm` beside it. the file is
portable between macOS and Windows — same schema, same SQLite.

`data/flags/event_*.npz` are the 60 s waveforms behind each flag. `window_path`
in the store is an absolute path, so those links do not survive a move —
copy the folder if you want the waveforms, but expect the roster not to find
them.

---

## 4. build steps

### evaluate (needs WESAD)

```bash
python -m anomaly.run --model baseline   # statistical floor (~0.64 PR-AUC)
python -m anomaly.run --model ae         # autoencoder, O1 (~0.67)
python -m anomaly.run --model ssl        # self-supervised, O2 (~0.68)
python -m anomaly.wesad                  # window counts per condition
```

leave-one-subject-out, subject-wise splits. numbers also in
`anomaly/RESULTS.md`. the first run reads ~13 GB of pickles once, then caches to
`WESAD/_harness_cache/`.

model-improvement levers, `--model ae` only:

```bash
python -m anomaly.run --model ae --bottleneck 256              # real latent
python -m anomaly.run --model ae --bottleneck 256 --ch-cap 32  # ESP32-sized  <- DEPLOYED
python -m anomaly.run --model ae --bottleneck 256 --ch-cap 32 --denoise 0.15
```

deployed config = LOSO **PR-AUC 0.706 / recall@90spec 0.545**.

### ship a new model

```bash
python -m anomaly.export --bottleneck 256 --ch-cap 32   # train + save ae.keras
python -m anomaly.compress                              # -> ae_int8.tflite + int8 scorer
```

**always run `compress` after `export`** — it rewrites `scorer.npz` on the int8
score scale, which the dashboards need to flag correctly. commit only
`ae_int8.tflite` (4 MB) and `scorer.npz`; `ae.keras` (46 MB) and
`ae_float32.tflite` (16 MB) are gitignored local artifacts. deployed int8:
4.0 MB, 1.49 ms/window, fits the ESP32-S3-N16R8.

### rebake the WESAD demo clip

only needed if the model or the clip changes — the baked clip travels inside
`pulse/Pulse Watch.dc.html`, which is tracked, so a fresh clone does not need
WESAD to run the demo.

```bash
python -m anomaly.make_demo_clip              # -> pulse/Pulse Watch.dc.html
python -m anomaly.make_demo_clip --dry-run    # report the size, write nothing
```

then re-run `make_web_assets.py` and reflash.

### the rest

```bash
python -m anomaly.calibrate      # per-user calibration on WESAD (O6 method)
python -m anomaly.make_plots     # result figures
```

---

## 5. detection latency — what to expect

dominated by the 60 s window, not the network:

| stage | cost |
|---|---|
| 60 s window | a change at t=0 only fills the window at t=60 s |
| scoring cadence | 1 s |
| EMA smoothing | ~2.3 s to 63%, ~5.3 s to 90% |
| push to the board | milliseconds |

so **~10–40 s** from event to flag. fine for mental stress, which builds over
minutes; far too slow for falls.

---

## 6. troubleshooting

**`ModuleNotFoundError` for scipy / tensorflow / fastapi, right after a clean install**
nothing is missing and nothing needs reinstalling — you ran a `python` that is
not the venv's, so the install went to one interpreter and the run went to
another. the fix is to stop choosing:

```bash
./run anomaly.fleet
./run doctor          # proves which interpreter it uses, and what is in it
```

if you want to know *why* a terminal you thought was activated did this, see the
section 0: a new tab, or a `python` alias/function in `~/.zshrc`, are the
two usual causes, and `which python` will not reveal either.

**`ModuleNotFoundError: No module named '_tkinter'`, only for Pulse Control**
Homebrew keeps Tk out of `python@3.12`. this only bites if you run
`python launcher/pulse_control.py` directly on the venv — `Pulse Control.app`
picks a Python that has it. to give the venv one anyway:
`brew install python-tk@3.12`. nothing else in the project needs it.

**the app window opens completely empty**
Tk 8.5. macOS's bundled Tk is from 2010 and never lays out this window's panes,
silently. the app refuses to use it now, so you should not see this — if you
do, you ran `pulse_control.py` directly on a Python with old Tk. check with:

```bash
python -c "import tkinter; print(tkinter.TkVersion)"    # must be 8.6 or more
```

**double-clicking the app says it needs a Python with Tk 8.6**
no Python on the Mac has a new enough Tk. any one of these fixes it:

```bash
brew install python@3.12 python-tk@3.12
```

or install Python from python.org (it bundles 8.6). `xcode-select --install`
does **not** help — that gives you macOS's own python3, which is the Tk 8.5 one.
if you would rather depend on nothing, `bash launcher/build_app.sh` freezes a
bundle that carries its own Python and Tk.

**"Pulse Control must stay inside the project folder"**
the app was moved out. put it back next to `anomaly/` and `launcher/`. to keep
it one click away without moving it, drag it onto the Dock.

**"Pulse Control.app is damaged and can't be opened"**
Gatekeeper on a bundle that arrived carrying a quarantine flag (a downloaded
zip, say — a `git clone` does not set one). right-click → Open once, or:

```bash
xattr -dr com.apple.quarantine "Pulse Control.app"
```

**`python -m anomaly.hotspot` says it is not set up**
Internet Sharing has never been configured on this Mac. §2.2(b) — it is a
one-time pass through System Settings, and no script can do it for you. if you
only need the boards and the laptop on one network, use §2.2(a) instead and run
plain `fleet`.

**the sketch will not compile: `web_assets.h` not found**
it is gitignored. run `python3 pulse_display/make_web_assets.py`.

**dashboard edits do not show on the board**
same command, then reflash. the board serves a compiled-in copy.

**the roster says the master is running older code**
it serves the page from disk but its routes were fixed at startup. restart it.
the page and the master exchange an API version so this says so plainly instead
of failing with a bare "failed".

**no serial port / "resource busy"**
only one process can hold the port. stop `serve` / `device_check` and close the
Arduino Serial Monitor. list what macOS sees:

```bash
python -m anomaly.device_source --list-ports
ls /dev/cu.*
```

use the `cu.` device, not `tty.`.

**upload fails at the post-flash reset**
`Serial data stream stopped: Possible serial noise or corruption` — one process
owns the serial port, as above. the flash itself usually succeeded, so check with
`device_check` before re-flashing. if the TFT comes back white, power-cycle the
USB; a RESET press does not re-power the display.

**the sample loop crawls and it looks like the sensor died**
do not leave USB plugged in with nothing reading it. `Serial.write()` blocks
until the CDC timeout when the buffer fills. guarded now with
`setTxTimeoutMs(0)` plus `if (!Serial) return`, but suspect it first.

**lag, then a hang, on weak WiFi**
`ws.textAll()` only queues; queueing faster than the link drains grows the queue
until the heap dies. the firmware skips frames when `availableForWriteAll()` is
false. below about −80 dBm, move the board closer.

**"Address already in use"**

```bash
lsof -i :8002          # find what is holding the port
kill <pid>
```

**the phone cannot reach a dashboard**
same WiFi, and use the host's LAN IP, not `localhost`. the servers bind
`0.0.0.0` already; if it still fails, macOS's firewall is blocking the port —
System Settings → Network → Firewall → Options, and allow the python binary.

```bash
ipconfig getifaddr en0      # this Mac's LAN address
```

---

## 7. git

```bash
git status --short          # local-only files are already excluded
git add .
git commit -m "your message here"
git push
git pull
```

undo the last commit, before pushing:

```bash
git reset --soft HEAD~1     # keep the changes
git reset --hard HEAD~1     # discard them too (destructive)
```

---

## 8. prior work — kept, not the current direction

these predate the pivot to one-class anomaly detection. they are a clean-signal
reference point and the streaming pattern the current dashboards were built
from. **do not point a demo at them.**

```bash
python pipeline/server.py               # old cardiac dashboard -> :8000
python pipeline/run_cli.py --once       # terminal-only, one 92 s pass
python baselines/train.py --preset phase_a     # supervised 3-class, ~12 min CPU
python baselines/quantize.py --preset phase_a  # -> model_int8.tflite
python baselines/inference_demo.py             # "Overall: 28/30 correct (93%)"
python baselines/make_plots.py
```

`anomaly.master` sits in the same category for a different reason: it scores one
networked board headlessly and pushes the verdict back, which is what `fleet`
does with discovery, a roster and a store on top.

```bash
python -m anomaly.master --host 10.49.10.173
```

---

## 9. where things live

| path | purpose |
|---|---|
| `anomaly/` | **the one-class anomaly detector — current direction** |
| `anomaly/fleet.py` + `static/fleet.html` | the master: discovery, scoring, roster, store |
| `anomaly/db.py` | the SQLite store behind the roster |
| `anomaly/hotspot.py` | this machine's hotspot — Internet Sharing here, Mobile Hotspot on Windows |
| `anomaly/serve.py` + `static/` | single-stream dashboards (`/` watch, `/dev`) |
| `anomaly/saved/ae_int8.tflite` | the deployed model — dashboards and ESP32 both run this |
| `anomaly/RESULTS.md` | PR-AUC, recall@90% spec, per-subject variance |
| `run` · `run.ps1` | run anything without activating a venv (`./run doctor`) |
| `Pulse Control.app` | **the double-click icon** — committed, 170 KB, runs the launcher below |
| `launcher/pulse_control.py` | Pulse Control — one window, both platforms |
| `launcher/make_app.sh` | rebuilds `Pulse Control.app` (the wrapper) |
| `launcher/icon/` | the icon drawing + `PulseControl.icns` |
| `launcher/build_app.sh` | freezes `Pulse Control (standalone).app`, for a Mac with no Tk |
| `launcher/build_exe.bat` | builds `Pulse Control.exe` (Windows) |
| `pulse/Pulse Watch.dc.html` | the product UI, and the baked demo sources |
| `pulse_display/` | ESP32-S3 firmware **with a TFT** + the web-asset baker |
| `pulse_headless/` | the same firmware for a board with **no screen** (Rev F: sensor on GPIO8/9) |
| `data/pulse.db` | subjects, baselines, sessions, flags (gitignored) |
| `WESAD/` | WESAD dataset (~17 GB, not in git) |
| `pipeline/` | prior work — streaming pattern, `vitals.py`, sensor setup guide |
| `baselines/` | prior work — the supervised cardiac model |
| `Code & Data/` | UBC PPG dataset (~3.8 GB, not in git) |
