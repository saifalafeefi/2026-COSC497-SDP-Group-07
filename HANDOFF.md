# Handoff — ESP32 live sensor, device calibration, board-hosted dashboard

Context for a fresh session. Branch `esp32`.

---

## Where the system is

ONE command runs the whole system:

```bash
./run anomaly.fleet          # roster at http://localhost:8002
```

It scans every local subnet, finds boards by their `/health`, opens a websocket
to each, runs the model on their streams, and pushes each verdict back to that
board's `/flag`.

```
browser --> ESP32 (its own dashboard)        roster --> localhost:8002
              ^  |                                        |
     GET /flag|  | ws:// waveform                         | opens ITS dashboard
              |  v                                        v
             master (anomaly.fleet)  <---- one task per device ----> ESP32 #2, #3...
```

| runs on the ESP32 | runs on the master |
|---|---|
| sensor + conditioning to 64 Hz | **the ML model, and only the model** |
| heart rate, SpO2 | per-device calibration |
| its own single-patient dashboard | the roster / discovery |
| TFT verdict, sensitivity slider | turning the slider into a threshold |

**Devices never see each other.** A board's dashboard shows only that board;
only the master sees the fleet. Clicking a device in the roster opens that
device's own page, served by the device.

**Per-device calibration:** `anomaly/saved/scorer_<device-id>.npz`, keyed to a
MAC-derived id the firmware reports (e.g. `pulse-a4f2c1`). An uncalibrated
device shows `--` and gets **no flag pushed** rather than being judged against
someone else's baseline. Calibration runs from the roster because the board can
stream but cannot score its own windows.

**When the master is gone** a board keeps sensing, keeps serving its page and
keeps showing HR/SpO2 — it just reports no verdict. That empty slot is what
an on-device model would fill.

Older, still working:

```bash
./run anomaly.serve                  # WESAD replay demo, no hardware
./run anomaly.serve --source device  # PC hosts dashboard, sensor over USB
./run anomaly.master --host <ip>     # single device, headless, for debugging
```

---

## The three things that matter most

### 1. The flag is calibrated on our own sensor

`anomaly/saved/scorer_device.npz` — 20 windows of our own calm, recorded through
the dashboard's Calibrate button. `scorer.npz` (WESAD wrist) is untouched.

**The transfer-delta result, measured on our rig:**

| | value |
|---|---|
| WESAD wrist threshold | 0.21215 |
| our calm **median** | 0.28187 |
| our device threshold (p90 of our calm) | 0.31164 |

Our calm median sits **above** the WESAD threshold, so a zero-shot wrist model
flags ~100% of our calm; device-calibrated it is 10% by construction. That is
the O6 number. **One subject, calm only.**

### 2. The flag has never been shown to respond to stress

Everything above establishes what *calm* looks like. Nobody has run an induced
stress session. Shaking a finger on the sensor makes the flag fire, but that is a
**motion artifact**, not stress — it proves the plumbing, nothing about the
method.

**This is the single most important open experiment.** 3 min calm → 3 min serial
subtraction (out loud, someone pushing the pace, sensor hand still) → 3 min
recovery. HR should rise 10–25 bpm as the control check. If HR rises and the
level does not, that is the domain-transfer finding and needs reporting.

### 3. Detection latency is ~10–40 s, dominated by the window

| stage | cost |
|---|---|
| 60 s window | a change at t=0 only fills the window at t=60 s |
| scoring cadence | 1 s |
| EMA (0.65/0.35) | ~2.3 s to 63%, ~5.3 s to 90% |

Appropriate for mental stress; far too slow for falls.

---

## Setup on a fresh machine

```bash
./run setup           # Windows: .\run.ps1 setup
```

Python 3.12 only — `tensorflow-cpu<2.20` has no 3.13/3.14 wheels, so pip simply
fails to resolve on anything newer. `./run setup` finds a 3.12 or tells you what
to install. Keep the venv outside the repo and outside any synced folder
(iCloud/OneDrive).

Or skip the terminal: open `Pulse Control.app` (macOS — it is in the repo) and
press **Advanced → Set up the virtualenv…**.

Step-by-step, per OS: [`COMMANDS-macos.md`](COMMANDS-macos.md) ·
[`COMMANDS-windows.md`](COMMANDS-windows.md).

**Arduino libraries:** ESP Async WebServer and Async TCP, both **by ESP32Async**
(older forks do not build against ESP32 core 3.x).

**Two firmware builds.** `pulse_display/` is the board with a TFT;
`pulse_headless/` is the same firmware for a board with no screen — the Rev F
wearable, which puts the MAX30102 on **GPIO8/GPIO9** (not 17/18) and brings its
only display-capable interface out to an unpopulated 24-pin FPC. They are
separate sketches on purpose: **a non-display fix to one is not a fix to the
other.** `make_web_assets.py` and `anomaly.board_export` both write their
generated headers into every sketch folder, so those cannot drift.

**Board settings:** ESP32S3 Dev Module, `USB CDC On Boot: Enabled`, and a
partition scheme with a large enough app partition.

For the Rev F wearable the module is an **ESP32-S3-WROOM-1-N8R8** (marking
`MCN8R8`), not the N16R8 the rest of these notes assume: set `Flash Size: 8MB
(64Mb)` with `Partition Scheme: 8M with spiffs (3MB APP/1.5MB SPIFFS)` or
`default_8MB`, and keep `PSRAM: OPI PSRAM` — the detector still needs it.

---

## Gotchas that cost real time

Symptom-to-fix lives in the COMMANDS files, section 6. This list is why each one
cost a day, and which are **still live** versus already fixed.

Still live — these will bite you:

1. **`USB CDC On Boot` must be Enabled.** With it off the port enumerates and
   opens fine, and you get total silence. Nothing anywhere reports it.
2. **Power-cycle, don't reset.** A reset mid-I2C leaves the MAX30102 holding SDA
   low; RESET does not re-power the peripherals. A white TFT after a flash is
   this.
3. **A board can report "no route to host" while being perfectly healthy.**
   Measured on the Rev F board over a phone hotspot: 0% packet loss across 20
   pings, but a round trip swinging between **7 ms and 211 ms** (stddev 44 ms).
   A single-shot HTTP probe lands in one of those stalls often enough to call a
   live board dead — which is what the roster did after its own subnet sweep
   had just found it. `http_text()` in Pulse Control and `probe()` for a known
   address now retry three times with backoff; the 254-address sweep still uses
   one try, because an empty address never answers however often it is asked.

   **Do NOT "fix" this by raising the radio.** `WiFi.setTxPower(WIFI_POWER_11dBm)`
   is deliberate — full transmit power sags the 3.3 V rail hard enough to take
   the MAX30102 down with it, and that is far likelier on the Rev F board's
   150 mAh cell than on USB. At `rssi` of -31 to -41 dBm there is already far
   more link margin than the -59 dBm that setting was chosen against, and
   `/health` reports `rec=0` — the sensor has never once had to be
   re-initialised. The weak link is the path, not the transmitter.

4. **The board resets when the serial port is opened**, so a host tool must wait
   for `# setup done` before sending commands. `device_wifi` learned this the
   hard way: it used to report the *old* network's IP as if it were the new one.
5. **HR below ~55 bpm reads double** — the dicrotic notch clears the prominence
   bar. Inherited from the host algorithm, so the board and the dashboard agree
   with each other and are both wrong.
6. **One process owns the serial port**, and **`make_web_assets.py` must be
   re-run after any dashboard edit** or the board serves the old page from
   flash. Both are in COMMANDS §6; they are listed here only because they
   account for most "it's broken" messages.

Already fixed — do not re-diagnose these:

7. **The sample loop crawling** looked exactly like the sensor dying. It was
   `Serial.write()` blocking until the CDC timeout once the buffer filled, with
   USB plugged in and nothing reading it. Fixed with `setTxTimeoutMs(0)` and an
   `if (!Serial) return` guard. If the loop ever crawls again, check `/health`
   first: `cond` frozen + `ticks` barely moving is the signature.
8. **Pulse Watch used to fall back to a MOCK replay** when the websocket did not
   open within 3 s, fabricated events and all — indistinguishable from working.
   It now stays **offline** and says so; the demo is a choice you make, never a
   fallback. Keep it that way.
9. **Weak WiFi showed up as lag, then a hang**: `ws.textAll()` only queues, and
   queueing faster than the link drains kills the heap. The firmware now skips
   frames when `availableForWriteAll()` is false. Below about −80 dBm, move the
   board closer anyway.

---

## Verified, not assumed

| what | result |
|---|---|
| C conditioning vs `device_source.py` | max relative difference **1.7e-9** (coefficient truncation; float32 on the board is ~1e-7) |
| board HR algorithm vs the host's | **identical to 0.0 bpm** on 19/21 test cases; the 2 disagreements are at 48 bpm, where BOTH report ~96 |
| contact detection | no finger **14,413** IR vs finger **125,007** — clean 8× separation |
| steady perfusion index | **0.2–0.4%**, below the "healthy 0.5–5%" range fixed gates assumed |
| sample rate with WiFi active | **40.0–40.2 Hz**, unaffected |
| web payload | 232 KB raw → **68 KB gzipped** in flash |
| calibration end-to-end | 20 windows, commit applies live, verified over the websocket |

---

## What is open

- **O3** — rig validated against a *reference oximeter*, 5 min, ≥3 people. Board
  and host HR agreeing is two of our own estimators agreeing, not validation.
- **O5 / IRB** — unstarted, still the bottleneck for every multi-subject claim.
- **DoD 4 / O7** — a model running on the board. The 520-byte Mahalanobis
  detector in `anomaly/baseline.py` (0.64 PR-AUC vs the autoencoder's 0.71) is
  the cheap path: no TFLM, no arena sizing, and that gap *is* the "accuracy cost"
  the DoD asks to be reported.
- **No accelerometer** on the rig.
- `/dev` still shows WESAD-only panels (ground truth, agreement) that are
  meaningless on this hardware.
- Signal-quality index not ported to C, so frames report `quality: null`.
- The fleet is untested with more than one board (only one exists).
- A 60 s refill after removing a finger is unavoidable: 3840 samples is the
  model's input shape. A shorter window means retraining and re-validating.

## Next steps, ranked

**1. Induced stress session.** Still the unvalidated premise under everything.
3 min calm -> 3 min serial subtraction (out loud, someone pushing the pace,
sensor hand still) -> 3 min recovery. HR rising 10-25 bpm is the control check.
If HR rises and the level does not, that IS the finding and needs reporting.
Nothing else is worth optimising until this is known.

**2. UBC site-shift experiment.** The strongest result available with no new
data and no IRB. `Code & Data/` has 31 participants recorded at fingertip AND
wrist (placements `IFT`, `MFT`, `IFB`, `WI`; labels are cardiac/occlusion, not
stress). Score the WESAD-trained model on both sites for the same people: the
difference is the pure site effect with subject held constant. Turns the n=1
transfer delta into a 31-subject controlled measurement. Raw trials are
continuous 50 Hz IR/RED at ~2 min each, re-windowable to 60 s.

**3. Mahalanobis on the board.** Closes DoD 4 / O7. 520 bytes (median, mu, sd and
a 10x10 inverse covariance) against the autoencoder's 4.2 MB — no TFLM, no
arena sizing, no partition changes. The 0.71 -> 0.64 PR-AUC gap IS the "accuracy
cost" the DoD asks to be reported, and it gives the board a verdict when the
master is away. *(done 2026-09-30; on 2026-10-30 its score was replaced by heart
rate going up, 0.865 PR-AUC on WESAD LOSO — see `anomaly/RESULTS.md`.)*

**4. Bottleneck sweep.** One command on the GPU box; 91% of the model is two
Dense layers, so a smaller bottleneck may cut it 4x for little accuracy loss:

```bash
./run anomaly.run --model ae --bottleneck 64 --ch-cap 32
```

**5. Dashboard honesty.** Strip `/dev`'s WESAD-only panels (ground truth,
agreement) which are meaningless on our hardware, and remove Pulse Watch's
mock-replay fallback that shows fabricated data when the socket is slow.

**Other people's lanes, still blocking:** O3 rig validation against a reference
oximeter (closes M1), and O5/IRB (gates every multi-subject claim).

## Architecture decision on record

Discussed a hybrid: small model on the device, better model on a master, prefer
the master when reachable. **Feasible and the right target** — and the fallback
mechanism already exists (the HR display prefers the host value and reverts after
5 s). Two cautions: the proposal's privacy pillar says *raw physiological data
stays on the device*, so the device should send **features or scores**, never raw
waveforms; and the project's own risk list says ONE target, ONE sensor combo, ONE
edge deployment — a multi-device fleet master is scope creep while only one
device exists.
