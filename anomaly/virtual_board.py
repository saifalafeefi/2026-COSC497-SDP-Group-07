"""WESAD subjects as boards on the network — the whole fleet, with no hardware.

the master (`anomaly.fleet`) finds a board by asking `http://<ip>/health`, reads
its stream over `ws://<ip>/ws`, and pushes its verdict back to `/flag`. nothing
in that pipeline cares whether the thing answering has a sensor in it. so a
replayed WESAD subject that speaks the same protocol joins the roster, gets
scored by the same autoencoder, opens the same session in `data/pulse.db` and
gets the same flag pushed back — the entire master side, on real stress data,
with no ESP32 in the room.

    python3 -m anomaly.virtual_board                       # S5 on :8101
    python3 -m anomaly.virtual_board --subjects S5,S10,S16 # three boards, :8101-8103

then point a master at them (it prints this line for you at startup):

    python3 -m anomaly.fleet --device 127.0.0.1:8101 --device 127.0.0.1:8102

one process hosts every subject asked for, one port each, because the pickles
are ~1 GB and loading them one after another costs a fraction of the memory
three processes would. the ports open immediately and each board answers
`/health` with 503 until its subject is in memory, so the master simply picks it
up on the next rescan rather than adopting a board that cannot stream yet.

what this is NOT: a simulator of the firmware. it answers `/health`, `/ws`,
`/flag` and `/calib` and serves the board's own Pulse Watch page, which is all
the master and the roster ever ask of a board. it reports no accelerometer, so
the master scores it on BVP alone exactly as it would a board with no IMU, and
its on-device-detector block reads `demo` rather than running the C port.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import deque

import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse

from .wesad import FS, SUBJECTS
from .wesad_replay import BVPReplay

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
PULSE_DIR = os.path.join(REPO_ROOT, "pulse")      # the page a real board serves

# the heart-rate estimator the dashboards use, on any PPG at any fs
sys.path.insert(0, os.path.join(REPO_ROOT, "pipeline"))
from vitals import estimate_heart_rate  # noqa: E402

WS_PERIOD = 0.125              # the firmware's WS_PERIOD_MS: 8 frames/s, 8 samples each
HR_EVERY = 1.0                 # re-estimate the heart rate once a second
HR_TAIL = 12 * FS              # over a 12 s tail, like `anomaly.serve`
FRESH_S = 5.0                  # a verdict older than this is stale, as on the board
WIN = 60 * FS                  # the model's window, for the warm-up bar
BASE_PORT = 8101               # 8001 is the demo dashboard, 8002 the master

# the replay knows the ground truth, which is the point of demoing on WESAD. a
# real board sends "-" here because a real wearer has no label.
LABELS = {0: "—", 1: "calm", 2: "STRESS", 3: "amusement", 4: "meditation"}


class Board:
    """one replayed subject: its 64 Hz timebase, and the master's last verdict."""

    # 5 minutes of each, not the dashboard demo's 2: the master re-estimates
    # this wearer's calm from their own last ~3 minutes of scores (fleet.NORM_N),
    # so on a 2+2 loop the stress phase ends up inside its own calm reference and
    # the level never rises. measured on S5/S10: a 2+2 loop never flagged in 150 s
    # of scoring, where 5+5 puts each phase well outside the other's reference.
    def __init__(self, subject: str, port: int, seg_sec: int = 300):
        self.subject = subject
        self.port = port
        self.id = "demo-%s" % subject.lower()
        self.seg_sec = seg_sec
        self.replay = None            # loaded off the event loop; see load()
        self.stream = None
        self.total = 0                # samples produced since this board booted
        self.ring: deque = deque(maxlen=4 * FS)    # (idx, value, label), for clients
        self.hist: deque = deque(maxlen=HR_TAIL)   # for the heart-rate estimate
        self.bpm = None
        self.clients = 0
        self.frames = 0
        self.t0 = None
        self.last_hr = 0.0
        # what the master last told us, in the board's own terms
        self.sens = 0.5
        self.host = {"flag": False, "level": None, "state": "idle", "wait": 0,
                     "subject": "", "thr": 0.42, "at": 0.0}
        self.sockets: set = set()     # open dashboards, for calib re-broadcasts

    # ---- the data ----

    def load(self):
        """read the subject. blocking and ~1 GB, so it runs in a worker thread."""
        self.replay = BVPReplay(self.subject, seg_sec=self.seg_sec)
        self.stream = self.replay.stream()
        self.t0 = time.monotonic()

    @property
    def ready(self) -> bool:
        return self.stream is not None

    def advance(self):
        """produce however many samples the wall clock says are due.

        the master's model is a 60 s window at 64 Hz: if this ran as fast as the
        loop allowed, a minute of signal would arrive in a second and every
        number downstream — heart rate, the session length, the warm-up — would
        be wrong. the clock is the authority, not the scheduler.
        """
        if not self.ready:
            return
        want = int((time.monotonic() - self.t0) * FS)
        # a long stall -- a laptop asleep, a load spike -- must not come back as
        # a burst of replayed signal. drop the gap by re-anchoring the clock, so
        # `total` still counts samples this board actually produced.
        if want - self.total > 4 * FS:
            self.t0 += (want - self.total - FS) / float(FS)
            want = self.total + FS
        while self.total < want:
            v, lab = next(self.stream)
            self.total += 1
            self.ring.append((self.total, round(float(v), 4), int(lab)))
            self.hist.append(float(v))

    def heart_rate(self):
        now = time.monotonic()
        if now - self.last_hr < HR_EVERY or len(self.hist) < 8 * FS:
            return
        self.last_hr = now
        self.bpm = estimate_heart_rate(
            np.fromiter(self.hist, dtype=np.float32, count=len(self.hist)), fs=FS)

    async def produce(self):
        while True:
            await asyncio.sleep(0.02)
            try:
                self.advance()
                self.heart_rate()
            except Exception as e:            # a dead board is worse than a loud one
                print("  %s: %s" % (self.id, e), flush=True)
                await asyncio.sleep(1.0)

    # ---- what a board says ----

    def fresh(self) -> bool:
        return self.host["at"] > 0 and (time.monotonic() - self.host["at"]) < FRESH_S

    def frame(self, batch: list) -> str:
        fresh = self.fresh()
        lab = batch[-1][2] if batch else 0
        return json.dumps({
            "type": "f", "running": True,
            "elapsed": round(self.total / FS, 1),
            "buf": min(self.total, WIN), "win": WIN,
            "idx": [b[0] for b in batch], "bvp": [b[1] for b in batch],
            "level": self.host["level"] if fresh else None,
            "flag": bool(fresh and self.host["flag"]),
            "score": 0,
            "bpm": None if self.bpm is None else round(self.bpm),
            "spo2": None, "quality": None,
            "label": LABELS.get(lab, "—"),
            "sens": round(self.sens, 3), "thr_level": self.host["thr"],
            "state": self.host["state"] if fresh else "stale",
            "warm_s": self.host["wait"],
            "subject": self.host["subject"] or self.id,
            "src": "demo",
            # this board has no C detector to run: say so rather than invent one
            "ond": {"st": "demo", "l": None, "f": 0, "s": 0.0, "ms": 0},
            "device": {"connected": True, "port": "wesad", "contact": True,
                       "dropped": 0,
                       # a replay always has a finger on it, and says so in the
                       # firmware's own words so the master's notes still read
                       "finger": {"st": "on", "n": 5, "of": 5, "keep": 0}},
        })

    def health(self) -> str:
        """the firmware's own token soup, so the same habits work on both."""
        return " ".join([
            "ok", "id=" + self.id, "ip=127.0.0.1:%d" % self.port,
            "rssi=-40", "heap=200000",
            "ir=120000", "ac=900.0", "r=1.00", "finger=1", "fst=on",
            "bpm=%d" % (round(self.bpm) if self.bpm else 0),
            "cond=%d" % self.total, "head=0", "tail=0", "drop=0",
            "wsn=%d" % self.clients, "sent=%d" % self.frames, "skip=0",
            "rec=0", "ticks=%d" % self.total,
            "ond=demo", "self=skip",
            # how the master tells a replay from a sensor, and whose data it is
            "src=demo", "demo=1", "wesad=" + self.subject,
        ])


def build_app(board: Board) -> FastAPI:
    app = FastAPI(title="virtual board %s" % board.id)

    @app.get("/health", response_class=PlainTextResponse)
    async def health():
        if not board.ready:
            # NOT "ok": a board the master adopts now would have nothing to
            # stream for the next 20 s. it rescans every 30 s anyway.
            return PlainTextResponse("loading %s\n" % board.subject, status_code=503)
        return board.health()

    @app.get("/flag", response_class=PlainTextResponse)
    async def flag(f: int = None, l: int = 0, t: int = None, s: str = "",
                   w: int = 0, n: str = "", b: int = 0, o: int = 0, d: int = 0):
        if f not in (0, 1):
            return PlainTextResponse("need ?f=0|1[&l=0..100]", status_code=400)
        board.host.update(flag=bool(f), level=max(0, min(100, l)) / 100.0,
                          state=s or "ok", wait=int(w), subject=n,
                          at=time.monotonic())
        if t is not None:
            board.host["thr"] = max(0, min(100, t)) / 100.0
        return "ok"

    @app.get("/calib", response_class=PlainTextResponse)
    async def calib(p: str = "record", w: int = 0, t: int = 1, c: int = 0):
        # the board cannot calibrate anything either: it forwards the master's
        # progress to whichever dashboards are open, as its own message type
        t = max(1, t)
        msg = json.dumps({"type": "calib", "phase": p, "windows": w,
                          "min_windows": t, "progress": round(w / float(t), 3),
                          "can_commit": bool(c)})
        for sock in list(board.sockets):
            try:
                await sock.send_text(msg)
            except Exception:
                board.sockets.discard(sock)
        return "ok"

    @app.get("/")
    async def index():
        # the same file the firmware bakes into flash, so "open its dashboard"
        # from the roster lands on the page a real board would have served
        return FileResponse(os.path.join(PULSE_DIR, "Pulse Watch.dc.html"),
                            media_type="text/html")

    @app.get("/support.js")
    async def support():
        return FileResponse(os.path.join(PULSE_DIR, "support.js"),
                            media_type="application/javascript")

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        board.clients += 1
        board.sockets.add(sock)
        cursor = board.total

        async def reader():
            """the master moves the slider through this socket, as on the board."""
            while True:
                try:
                    m = json.loads(await sock.receive_text())
                except (WebSocketDisconnect, RuntimeError):
                    return
                except Exception:
                    continue
                if m.get("cmd") == "set_sensitivity":
                    try:
                        board.sens = max(0.0, min(1.0, float(m.get("value", 0.5))))
                    except (TypeError, ValueError):
                        pass

        task = asyncio.create_task(reader())
        try:
            while True:
                await asyncio.sleep(WS_PERIOD)
                batch = [s for s in list(board.ring) if s[0] > cursor]
                if batch:
                    cursor = batch[-1][0]
                await sock.send_text(board.frame(batch))
                board.frames += 1
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            task.cancel()
            board.sockets.discard(sock)
            board.clients -= 1

    return app


class _Server(uvicorn.Server):
    """several servers in one process: let the outer Ctrl-C do the stopping.

    uvicorn installs its own SIGINT handler per server, and the last one to
    install wins — so with three boards running, Ctrl-C stopped one of them and
    left the process alive.
    """

    def install_signal_handlers(self):
        pass


async def run(boards: list, host: str) -> int:
    servers = [_Server(uvicorn.Config(build_app(b), host=host, port=b.port,
                                      log_level="warning")) for b in boards]
    tasks = [asyncio.create_task(b.produce()) for b in boards]

    async def loader():
        """one subject at a time: 15 GB of pickle does not want to be parallel."""
        for b in boards:
            t = time.monotonic()
            await asyncio.to_thread(b.load)
            print("  %-9s %s loaded on :%d  (%.1f s)"
                  % (b.id, b.subject, b.port, time.monotonic() - t), flush=True)
        print("\n  point the master at them:\n    python3 -m anomaly.fleet %s\n"
              % " ".join("--device 127.0.0.1:%d" % b.port for b in boards), flush=True)

    tasks.append(asyncio.create_task(loader()))
    try:
        await asyncio.gather(*(s.serve() for s in servers))
    finally:
        for t in tasks:
            t.cancel()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--subjects", default="S5",
                    help="comma-separated WESAD subjects, one board each (default S5)")
    ap.add_argument("--base-port", type=int, default=BASE_PORT,
                    help=f"first port; one per board (default {BASE_PORT})")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address (default 127.0.0.1; 0.0.0.0 to let a "
                         "master on another machine find these boards)")
    ap.add_argument("--seg", type=int, default=300,
                    help="seconds of baseline and of stress in the replay loop "
                         "(default 300)")
    args = ap.parse_args()

    subjects = [s.strip().upper() for s in args.subjects.split(",") if s.strip()]
    bad = [s for s in subjects if s not in SUBJECTS]
    if bad:
        print("not WESAD subjects: %s\n  released: %s"
              % (", ".join(bad), " ".join(SUBJECTS)), file=sys.stderr)
        return 2
    if not subjects:
        print("no subjects given", file=sys.stderr)
        return 2

    boards = [Board(s, args.base_port + i, seg_sec=args.seg)
              for i, s in enumerate(subjects)]
    print("\n  virtual boards — replayed WESAD, no hardware\n", flush=True)
    for b in boards:
        print("    %-9s %-3s  http://127.0.0.1:%d" % (b.id, b.subject, b.port), flush=True)
    print("\n  loading one subject at a time (~1 GB each); each board answers\n"
          "  /health once its own is in memory.\n", flush=True)
    try:
        return asyncio.run(run(boards, args.host))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
