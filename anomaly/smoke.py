"""smoke test the live system: the master and every board it sees.

read-only. it asks, it never tells: no flag, calibration, subject or tuning
write goes anywhere, so it is safe to run while the master is scoring people.

    python -m anomaly.smoke                    # master on :8002, every board it lists
    python -m anomaly.smoke --expect 2         # also fail unless two boards are there
    python -m anomaly.smoke --secs 15          # listen to each stream longer

what it checks, per board (all boards at once, so it is also a load test):
  /health   answers, the id matches the roster, self-test, PSRAM, IMU, heap,
            signal strength, sensor drops
  /ws       64 Hz of BVP with no index gaps, the accelerometer at ~32 Hz when
            the board has one, and the master's verdict arriving (src=master,
            wearer name pushed back)
  roster    connected, the master's pushes landing (fails not growing),
            finger state agreeing with contact, subject + calibration
  page      the board serves its own Pulse Watch page

each line is PASS, WARN (works, but not as intended) or FAIL. exits 1 on any
FAIL. a secure master needs a token: --token or PULSE_TOKEN (reviewer is
enough).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request

FS = 64            # conditioned BVP rate the master expects
ACC_FS = 32        # board's accelerometer rate after box-averaging
MIN_HEAP = 60_000  # below this the board stops sending frames
WEAK_RSSI = -75

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


class Report:
    def __init__(self):
        self.rows = []

    def add(self, who, level, what, detail=""):
        self.rows.append((who, level, what, detail))

    def check(self, who, ok, what, detail="", soft=False):
        self.add(who, PASS if ok else (WARN if soft else FAIL), what, detail)
        return ok

    def print(self):
        who_w = max([len(r[0]) for r in self.rows] + [6])
        last = None
        order = {}
        for r in self.rows:                      # master first, then boards as first seen
            order.setdefault(r[0], -1 if r[0] == "master" else len(order))
        for who, level, what, detail in sorted(self.rows, key=lambda r: order[r[0]]):
            if who != last:
                print()
                last = who
            print(f"  {level}  {who:<{who_w}}  {what}" + (f"  ({detail})" if detail else ""))
        n = {k: sum(1 for r in self.rows if r[1] == k) for k in (PASS, WARN, FAIL)}
        print(f"\n  {n[PASS]} pass, {n[WARN]} warn, {n[FAIL]} fail")
        return n[FAIL] == 0


def get(url, token=None, timeout=3.0):
    req = urllib.request.Request(url)
    if token:
        req.add_header("X-Pulse-Token", token)
    t = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return r.status, body, time.monotonic() - t, r.headers


def parse_health(text):
    out = {}
    for tok in text.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    return out


def roster(master, token):
    _, body, _, _ = get(master + "/api/devices", token)
    return json.loads(body.decode("utf-8"))


async def listen(ip, secs):
    """one board's stream for `secs`: rates, gaps, what the master sent back."""
    import websockets
    st = {"frames": 0, "bvp": 0, "acc": 0, "gaps": 0, "back": 0, "src": {},
          "subjects": set(), "motion": False, "hello": None, "err": None}
    nxt = None
    t0 = time.monotonic()
    try:
        async with websockets.connect(f"ws://{ip}/ws", open_timeout=8, max_queue=256) as ws:
            while (left := secs - (time.monotonic() - t0)) > 0:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=left)
                except asyncio.TimeoutError:
                    break
                m = json.loads(raw)
                if m.get("type") == "hello":
                    st["hello"] = m
                if m.get("type") != "f":
                    continue
                st["frames"] += 1
                bvp = m.get("bvp") or []
                idx = m.get("idx") or []
                st["bvp"] += len(bvp)
                st["acc"] += len(m.get("acc") or [])
                st["motion"] |= m.get("motion") is not None
                src = m.get("src")
                st["src"][src] = st["src"].get(src, 0) + 1
                if m.get("subject"):
                    st["subjects"].add(m["subject"])
                if idx:
                    if nxt is not None:
                        st["gaps"] += idx[0] > nxt
                        st["back"] += idx[0] < nxt
                    nxt = idx[-1] + 1
    except Exception as e:                       # noqa: BLE001 -- reported, not raised
        st["err"] = repr(e)
    st["el"] = time.monotonic() - t0
    return st


def check_health(rep, d):
    who, ip = d["id"], d["ip"]
    try:
        code, body, dt, _ = get(f"http://{ip}/health")
    except Exception as e:                       # noqa: BLE001
        rep.add(who, FAIL, "/health answers", repr(e))
        return None
    h = parse_health(body.decode("ascii", "ignore"))
    rep.check(who, code == 200 and body.startswith(b"ok "), "/health answers", f"{dt*1000:.0f} ms")
    rep.check(who, h.get("id") == who, "board id matches the roster", h.get("id", "?"))

    self_ = h.get("self")
    psram = int(h.get("psram", "0") or 0)
    if self_ == "pass":
        rep.add(who, PASS, "on-device detector self-test", f"self=pass, psram {psram/1e6:.1f} MB free")
    elif self_ == "off" and psram == 0:
        rep.add(who, WARN, "on-device detector self-test",
                "off: no PSRAM -- built without Tools > PSRAM > OPI PSRAM")
    else:
        rep.add(who, FAIL if self_ == "fail" else WARN, "on-device detector self-test",
                f"self={self_}, psram={psram}")

    if "imu" not in h:
        rep.add(who, WARN, "firmware reports the IMU",
                "no imu= in /health: firmware predates the accelerometer (2026-11-01)")
    elif h["imu"] == "on":
        hz = float(h.get("imuhz", 0) or 0)
        rep.check(who, 50 <= hz <= 80, "IMU answering", f"hub {hz:.1f} Hz, resets {h.get('imur')}",
                  soft=True)
    else:
        rep.add(who, WARN, "IMU answering", f"imu={h['imu']}")

    if "dmode" not in h:
        rep.add(who, WARN, "board detector mode reported", "no dmode= (older firmware)")

    heap = int(h.get("heap", 0) or 0)
    rep.check(who, heap > MIN_HEAP, "free heap", f"{heap/1000:.0f} KB", soft=True)
    rssi = int(h.get("rssi", -999) or -999)
    rep.check(who, rssi > WEAK_RSSI, "WiFi signal", f"{rssi} dBm", soft=True)
    rep.check(who, h.get("drop") == "0", "sensor samples dropped", f"drop={h.get('drop')}", soft=True)
    rep.check(who, h.get("skip") == "0", "ws frames skipped under backpressure",
              f"skip={h.get('skip')} of sent={h.get('sent')}", soft=True)
    return h


def check_stream(rep, d, st, h):
    who = d["id"]
    if st["err"]:
        rep.add(who, FAIL, "/ws stream", st["err"])
        return
    el = st["el"]
    bvp_hz = st["bvp"] / el
    rep.check(who, st["frames"] > 0, "/ws streams frames", f"{st['frames']/el:.1f} frames/s")
    rep.check(who, abs(bvp_hz - FS) < 4, "BVP rate", f"{bvp_hz:.1f} Hz (want {FS})")
    rep.check(who, st["gaps"] == 0 and st["back"] == 0, "BVP index continuous",
              f"gaps {st['gaps']}, backwards {st['back']}")
    if h and h.get("imu") == "on":
        acc_hz = st["acc"] / el
        rep.check(who, abs(acc_hz - ACC_FS) < 5, "accelerometer in frames", f"{acc_hz:.1f} Hz")
        rep.check(who, st["motion"], "motion in frames")
    master = st["src"].get("master", 0)
    rep.check(who, master == st["frames"] and master > 0, "verdict comes from the master",
              f"src {st['src']}")
    sub = d.get("subject")
    want = (sub or {}).get("name") or d.get("name") or who
    rep.check(who, want in st["subjects"], "master's wearer name reached the board",
              f"board shows {sorted(st['subjects'])}, roster {want!r}")


def check_roster(rep, before, after):
    who = after["id"]
    rep.check(who, after["connected"], "master connected", f"last frame {after['last_seen']} s ago")
    pushed = after["pushes"] - before["pushes"]
    failed = after["fails"] - before["fails"]
    rep.check(who, pushed > 0 and failed == 0, "master's /flag pushes land",
              f"+{pushed} ok, +{failed} failed during the test (total fails {after['fails']})")
    f = after.get("finger") or {}
    if f:
        rep.check(who, after["contact"] == (f.get("st") == "on"), "finger state agrees with contact",
                  f"contact={after['contact']}, finger={f.get('st')}")
    if after["subject"] is None:
        # scored zero-shot either way (api 14+); without a wearer nothing is stored
        rep.check(who, not after.get("recording", False), "nothing stored without a wearer",
                  f"session {after.get('session_id')}")
        rep.add(who, WARN, "subject assigned",
                "none -- scored zero-shot, but no flags or sessions are kept")
    else:
        rep.check(who, after["calibrated"], "subject calibrated",
                  f"{after['subject']['code']} k_sigma {after['k_sigma']}", soft=True)
    if after["contact"]:
        rep.check(who, after["state"] in ("warm", "ok", "zero", "hold"), "scoring with a finger on",
                  f"state={after['state']}, buf {after['buf']}/{after['win']}")
        rep.check(who, after["bpm"] is not None, "heart rate reported", f"bpm={after['bpm']}", soft=True)
    else:
        rep.check(who, after["state"] == "idle", "idle with no finger", f"state={after['state']}")


def check_page(rep, d):
    who = d["id"]
    try:
        code, body, dt, hdr = get(f"http://{d['ip']}/", timeout=5)
        rep.check(who, code == 200 and len(body) > 1000, "board serves Pulse Watch",
                  f"{len(body)/1000:.0f} KB in {dt*1000:.0f} ms")
    except Exception as e:                       # noqa: BLE001
        rep.add(who, FAIL, "board serves Pulse Watch", repr(e))


async def amain(a) -> int:
    rep = Report()
    master = a.master.rstrip("/")
    try:
        before = roster(master, a.token)
    except Exception as e:                       # noqa: BLE001
        print(f"  FAIL  cannot read {master}/api/devices: {e!r}")
        print("        is the master running? (python -m anomaly.fleet --hotspot)")
        return 1
    devs = before.get("devices", [])
    rep.add("master", PASS, "roster answers", f"api {before.get('api')}, subnets {before.get('subnet')}")
    if a.expect is not None:
        rep.check("master", len(devs) == a.expect, "boards found", f"{len(devs)} of {a.expect}")
    else:
        rep.check("master", len(devs) > 0, "boards found", str(len(devs)))
    ids = [d["id"] for d in devs]
    ips = [d["ip"] for d in devs]
    rep.check("master", len(set(ids)) == len(ids) and len(set(ips)) == len(ips),
              "board ids and IPs unique", ", ".join(f"{d['id']}@{d['ip']}" for d in devs))
    if not devs:
        return 0 if rep.print() else 1

    loop = asyncio.get_running_loop()
    healths = await asyncio.gather(*[loop.run_in_executor(None, check_health, rep, d) for d in devs])
    print(f"  listening to {len(devs)} board(s) for {a.secs:.0f} s…", flush=True)
    streams = await asyncio.gather(*[listen(d["ip"], a.secs) for d in devs])
    after = {d["id"]: d for d in roster(master, a.token).get("devices", [])}
    for d, h, st in zip(devs, healths, streams):
        check_stream(rep, d, st, h)
        if d["id"] in after:
            check_roster(rep, d, after[d["id"]])
        else:
            rep.add(d["id"], FAIL, "still in the roster after the test")
        check_page(rep, d)
    return 0 if rep.print() else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--master", default="http://localhost:8002")
    ap.add_argument("--expect", type=int, help="number of boards that should be there")
    ap.add_argument("--secs", type=float, default=10.0, help="seconds to listen to each stream")
    ap.add_argument("--token", default=os.environ.get("PULSE_TOKEN"),
                    help="reviewer/admin token for a --secure master")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    return asyncio.run(amain(a))


if __name__ == "__main__":
    sys.exit(main())
