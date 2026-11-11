"""build this repo's board firmware and flash it to the board on USB, in one go.

    python -m anomaly.flash                 # find the board, build, upload, check it booted
    python -m anomaly.flash --port COM6     # that port
    python -m anomaly.flash --no-verify     # skip reading the boot log afterwards

what it does, in order (Pulse Control's Board setup > Upload latest firmware):

    1. finds arduino-cli and the ESP32 core (the same ones the Arduino IDE uses)
    2. secrets.h: copies the empty template if the sketch has none, so it builds
       (WiFi is set later, from Pulse Control or the phone app)
    3. rebuilds the dashboards into web_assets.h (make_web_assets.py)
    4. the on-board autoencoder (ae_model.h, gitignored): generates it when the
       model and WESAD are here, otherwise builds without it and says so
    5. compiles pulse_headless for the Rev F board -- ESP32-S3 N8R8: 8 MB flash,
       OPI PSRAM, the sketch's own partitions.csv -- the settings the IDE gets
       wrong easily (see HANDOFF.md)
    6. uploads to the board, then reads its boot log: both detectors' self-tests,
       WiFi, Bluetooth, and that it didn't crash

the build is cached between runs (first build ~6 min because of the 14 MB model
header, later ones a minute or two). saved WiFi networks, the wearer's name and
the slider survive: the partition table keeps them where they were.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKETCH = "pulse_headless"
FQBN = ("esp32:esp32:esp32s3:FlashSize=8M,PartitionScheme=default_8MB,PSRAM=opi,"
        "CDCOnBoot=cdc,USBMode=hwcdc,FlashMode=qio,CPUFreq=240,UploadSpeed=921600")
# partitions.csv beside the sketch replaces default_8MB's table; the menu's
# size check (3.3 MB) still applies, so lift it to the csv's app slot
MAX_SIZE = 0x7E0000
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def say(msg: str = ""):
    print(msg, flush=True)


def fail(msg: str) -> int:
    say("\nFAILED: " + msg)
    return 1


def find_cli(hint: str | None) -> str | None:
    cands = [hint, os.environ.get("ARDUINO_CLI"), shutil.which("arduino-cli")]
    if os.name == "nt":
        la = os.environ.get("LOCALAPPDATA", "")
        cands += [os.path.join(la, "Programs", "arduino-cli", "arduino-cli.exe"),
                  os.path.join(os.environ.get("ProgramFiles", ""), "Arduino CLI", "arduino-cli.exe")]
    else:
        cands += ["/opt/homebrew/bin/arduino-cli", "/usr/local/bin/arduino-cli",
                  os.path.expanduser("~/bin/arduino-cli")]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def build_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/Library/Caches")
    if not os.path.isdir(base):
        base = tempfile.gettempdir()
    d = os.path.join(base, "pulse_firmware_build", SKETCH)
    os.makedirs(d, exist_ok=True)
    return d


def run_quiet(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=NOWIN, **kw)


def stream(cmd: list[str], keep, every_s: float = 20.0) -> tuple[int, list[str]]:
    """run, echo the lines `keep` lets through, and a heartbeat while it is quiet"""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", creationflags=NOWIN, cwd=ROOT)
    lines, t0, last = [], time.time(), time.time()
    for line in p.stdout:
        line = line.rstrip()
        lines.append(line)
        shown = keep(line)
        if shown:
            say("  " + shown)
            last = time.time()
        elif time.time() - last > every_s:
            say("  ... still working (%d s)" % (time.time() - t0))
            last = time.time()
    return p.wait(), lines


def board_port(hint: str | None) -> str | None:
    if hint:
        return hint
    from .device_source import find_port
    return find_port()


def verify(port: str, secs: float = 40.0) -> int:
    """ask the freshly flashed board how it is; 0 = both detectors passed, nothing crashed.

    the boot log goes out before the USB port comes back after the upload's
    reset, so the board is asked instead: "?" makes it print one status line
    (det= the heart-rate detector's self-test, ae= the autoencoder's state).
    """
    import serial
    say("\n[6/6] checking the board booted")
    time.sleep(2.0)                      # the upload's hard reset re-enumerates the USB port
    for _ in range(20):
        try:
            s = serial.Serial(port, 115200, timeout=0.3)
            break
        except Exception:
            time.sleep(0.5)
    else:
        say("  couldn't reopen %s to ask; the upload itself succeeded" % port)
        return 0
    buf, t0, asked, status = b"", time.time(), 0.0, None
    with s:
        while time.time() - t0 < secs:
            if time.time() - asked > 2.0:
                s.write(b"?\n")
                asked = time.time()
            buf += s.read(4096)
            txt = buf.decode("utf-8", "replace")
            if "Guru Meditation" in txt or "abort()" in txt:
                break
            lines = [l for l in txt.splitlines() if l.startswith("# status ")]
            if lines:
                status = lines[-1]
                # the autoencoder loads in its own task a few seconds after boot
                if " ae=loading" not in status:
                    break
    txt = buf.decode("utf-8", "replace")
    if "Guru Meditation" in txt or "abort()" in txt:
        crash = [l for l in txt.splitlines() if "Guru" in l or "abort" in l][:3]
        return fail("the board crashed at boot:\n    " + "\n    ".join(crash))
    if status is None:
        say("  the board didn't answer the status request (older firmware, or still booting).")
        say("  check its health page: http://<board-ip>/health -> self=pass")
        return 0
    head, _, detail = status.partition(" | ")
    fields = dict(kv.split("=", 1) for kv in head.replace("# status ", "").split() if "=" in kv)
    for part in detail.split(" | "):
        say("  " + part)
    say("  psram free %s, ip %s, bluetooth %s" % (fields.get("psram", "?"), fields.get("ip", "?"),
                                                    "on" if fields.get("ble") == "1" else "off"))
    if fields.get("det") == "off":
        return fail("the board's detector is off (no PSRAM) -- it can't score without the master")
    if fields.get("det") != "pass":
        return fail("the board's detector self-test failed")
    if fields.get("ae") == "fail":
        return fail("the on-board autoencoder self-test failed")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", help="the board's serial port (default: find it)")
    ap.add_argument("--cli", help="path to arduino-cli (default: find it)")
    ap.add_argument("--no-verify", action="store_true", help="don't read the boot log afterwards")
    a = ap.parse_args(argv)
    sk = os.path.join(ROOT, SKETCH)

    say("[1/6] tools")
    cli = find_cli(a.cli)
    if not cli:
        return fail("arduino-cli not found. Install it (https://arduino.github.io/arduino-cli/), "
                    "or pass --cli <path>. It shares the Arduino IDE's boards and libraries.")
    say("  arduino-cli: " + cli)
    cores = run_quiet([cli, "core", "list"]).stdout
    if "esp32:esp32" not in cores:
        return fail("the ESP32 core isn't installed for arduino-cli. In the Arduino IDE: Boards Manager > "
                    "esp32 by Espressif; or run: arduino-cli core install esp32:esp32")
    libs = run_quiet([cli, "lib", "list"]).stdout
    need = {"ESP Async WebServer": "by ESP32Async", "Async TCP": "by ESP32Async",
            "SparkFun MAX3010x": "SparkFun MAX3010x Pulse and Proximity Sensor Library"}
    missing = ["%s (%s)" % (n, w) for n, w in need.items() if n.lower() not in libs.lower()]
    if missing:
        return fail("missing Arduino libraries -- install them from the IDE's Library Manager: "
                    + "; ".join(missing))

    port = board_port(a.port)
    if not port:
        return fail("no board found on USB. Plug it in (a data cable, not charge-only) and close "
                    "the Arduino Serial Monitor, then try again.")
    say("  board on " + port)

    say("\n[2/6] WiFi credentials file")
    sec = os.path.join(sk, "secrets.h")
    if os.path.exists(sec):
        say("  secrets.h present")
    else:
        shutil.copyfile(os.path.join(sk, "secrets.example.h"), sec)
        say("  no secrets.h: copied the empty template (set WiFi later from Pulse Control or the phone app)")

    say("\n[3/6] dashboards -> web_assets.h")
    r = run_quiet([sys.executable, os.path.join(ROOT, "pulse_display", "make_web_assets.py")], cwd=ROOT)
    if r.returncode != 0:
        return fail("make_web_assets.py failed:\n" + (r.stdout + r.stderr)[-1500:])
    say("  " + ((r.stdout.strip().splitlines() or ["done"])[-1]).strip())

    say("\n[4/6] on-board autoencoder (ae_model.h)")
    model_h = os.path.join(sk, "ae_model.h")
    if os.path.exists(model_h):
        say("  present (%.1f MB)" % (os.path.getsize(model_h) / 1e6))
    elif os.path.exists(os.path.join(ROOT, "anomaly", "saved", "ae.keras")) and \
            os.path.isdir(os.path.join(ROOT, "WESAD")):
        say("  missing: generating it (a minute or two)")
        r = run_quiet([sys.executable, "-m", "anomaly.board_ae_export"], cwd=ROOT)
        if r.returncode != 0 or not os.path.exists(model_h):
            say("  couldn't generate it -- building WITHOUT the on-board autoencoder")
    else:
        say("  missing, and anomaly/saved/ae.keras or WESAD/ isn't here to make it --")
        say("  building WITHOUT the on-board autoencoder (the board's own detector still runs).")
        say("  copy ae_model.h from a teammate who has it, into pulse_headless/, to include it.")

    bdir = build_dir()
    say("\n[5/6] compiling %s (ESP32-S3 N8R8, 8 MB flash, OPI PSRAM, the sketch's partitions)" % SKETCH)
    say("  build folder: " + bdir)
    t0 = time.time()

    def keep_compile(l):
        if "error" in l.lower() or l.startswith("Sketch uses") or l.startswith("Global variables"):
            return l
        return None
    rc, lines = stream([cli, "compile", "--fqbn", FQBN, "--build-property",
                        "upload.maximum_size=%d" % MAX_SIZE, "--build-path", bdir, sk], keep_compile)
    if rc != 0:
        errs = [l for l in lines if "error" in l.lower()][:12]
        return fail("compile failed:\n    " + "\n    ".join(errs or lines[-15:]))
    say("  compiled in %d s" % (time.time() - t0))

    say("\n      uploading to %s (about 40 s; esptool redraws one line, so it looks quiet)" % port)

    def keep_upload(l):
        if any(k in l for k in ("Chip type", "MAC:", "Hard resetting", "rror", "Failed", "failed")):
            return l.strip()[:160]
        if "Wrote" in l and "bytes" in l:
            return "wrote " + l.split("Wrote", 1)[-1].strip()[:120]
        return None
    rc, lines = stream([cli, "upload", "-p", port, "--fqbn", FQBN, "--input-dir", bdir, sk], keep_upload)
    if rc != 0:
        tail = "\n    ".join(lines[-8:])
        return fail("upload failed. Is another program (Serial Monitor, a USB dashboard) holding "
                    "%s? Unplug and replug the board and try again.\n    %s" % (port, tail))
    say("  uploaded")

    if a.no_verify:
        say("\ndone.")
        return 0
    rc = verify(port)
    if rc == 0:
        say("\ndone: the board is running the latest firmware.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
