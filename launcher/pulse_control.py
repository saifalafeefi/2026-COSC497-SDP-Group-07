"""Pulse Control -- every command in the project behind a button.

a small window, not the project frozen into a binary: each button runs the
real module (`python -m anomaly.fleet`, ...) with the project's own virtualenv,
so a `git pull` changes what the buttons do with no rebuild. the bundle is only
this file; it needs nothing but tkinter.

    Windows   launcher\build_exe.bat  -> "Pulse Control.exe" in the repo root
    macOS     launcher/build_app.sh    -> "Pulse Control.app" in the repo root
    either    python launcher/pulse_control.py

one file, both platforms: the differences (the hotspot's name and how it is
switched on, the Tk theme and fonts, which COMMANDS file to open) are read off
sys.platform once, at the top.

it finds the repo by walking up from wherever it sits, and the virtualenv at
~/.venvs/sdp07 (the path the COMMANDS file sets up). if that is not where yours
is, Advanced > Choose Python remembers another, in ~/.pulse_control.json.
"""
from __future__ import annotations

import codecs
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

MAC = sys.platform == "darwin"
WIN = sys.platform == "win32"

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError as e:
    # Homebrew splits Tk out of python@3.12, so a perfectly good venv can have
    # every dependency in this project and still not open a window. the error
    # python gives for that ("No module named '_tkinter'") says nothing about
    # the fix, and this is the one place anybody meets it.
    sys.stderr.write(
        "\nPulse Control needs Tk, and this Python was built without it.\n  (%s)\n\n" % e
        + ("  brew install python-tk@3.12\n\n"
           "it installs into the python@3.12 the virtualenv is built on, so just run\n"
           "this again. if Tk is still missing, re-create the venv on top of it --\n"
           "COMMANDS-macos.md, section 1.\n\n" if MAC else
           "  install Python from python.org, or re-run the installer and tick\n"
           "  'tcl/tk and IDLE'. see COMMANDS-windows.md, section 1.\n\n"))
    raise SystemExit(2)

APP = "Pulse Control"
NOWIN = 0x08000000 if WIN else 0                          # CREATE_NO_WINDOW
CONFIG = os.path.join(os.path.expanduser("~"), ".pulse_control.json")
FLEET = "http://localhost:8002"
SERVE = "http://localhost:8001"

# what the OS calls its own hotspot, and what the buttons that drive it can
# promise. `anomaly.hotspot` is the module behind both; only the words differ.
if MAC:
    THIS_PC = "this Mac"
    HOTSPOT = "Internet Sharing"
    HOTSPOT_VERB = "This Mac switches Internet Sharing on"
    HOTSPOT_NOTE = ("Must be 2.4 GHz, and set up once by hand in System Settings > General > "
                    "Sharing > Internet Sharing — macOS gives no script the right to choose "
                    "which connection to share. After that these buttons read it, and will "
                    "try to switch it back on.")
    HOTSPOT_PASS = ("It reads the network name from macOS and asks you for the hotspot's "
                    "password once (macOS keeps it in the System keychain, which a script "
                    "cannot read), sends them down the cable, and shows the board's IP.")
    COMMANDS_DOC = "COMMANDS-macos.md"
else:
    THIS_PC = "this PC"
    HOTSPOT = "Mobile Hotspot"
    HOTSPOT_VERB = "This PC switches its Mobile Hotspot on"
    HOTSPOT_NOTE = ("Must be 2.4 GHz. Turn off Windows' power saving for it (Settings > "
                    "Network & internet > Mobile hotspot), or it switches itself off when idle.")
    HOTSPOT_PASS = ("It reads the hotspot's name and password from Windows — nothing to type — "
                    "sends them down the cable, and shows the board's IP.")
    COMMANDS_DOC = "COMMANDS-windows.md"

# Tk has neither OS's fonts on the other, and the same point size comes out
# visibly smaller under Windows than under aqua.
if MAC:
    UI, MONO = "Helvetica Neue", "Menlo"
    FS = {"title": 17, "head": 13, "body": 12, "log": 11}
    # aqua sets the same text ~25% taller, so the tallest tab (Board setup)
    # wants ~710 px of notebook. a shorter log and a taller window keep it
    # whole; on a small display the sash still gives the rest back.
    WANT = (1060, 880)
    MINSIZE = (900, 600)
    LOG_LINES = 8
    LOG_PX = 190
else:
    UI, MONO = "Segoe UI", "Consolas"
    FS = {"title": 15, "head": 11, "body": 10, "log": 9}
    WANT = (1040, 760)
    MINSIZE = (860, 600)
    LOG_LINES = 12
    LOG_PX = 170


# ------------------------------------------------------------------ locating

def find_repo() -> str | None:
    here = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)
    for start in (os.path.dirname(here), os.getcwd()):
        d = start
        for _ in range(8):
            if os.path.isdir(os.path.join(d, "anomaly")) and os.path.isdir(os.path.join(d, "pulse_display")):
                return d
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    return None


def load_config() -> dict:
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg: dict):
    try:
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def find_python(cfg: dict) -> str | None:
    home = os.path.expanduser("~")
    venv = os.path.join(home, ".venvs", "sdp07")
    native = (os.path.join(venv, "Scripts", "python.exe") if WIN
              else os.path.join(venv, "bin", "python"))
    other = (os.path.join(venv, "bin", "python") if WIN
             else os.path.join(venv, "Scripts", "python.exe"))
    for p in (cfg.get("python"), native, other):
        if p and os.path.isfile(p):
            return p
    return None


def find_python312() -> str | None:
    """an interpreter to build the venv with. 3.12 exactly: tensorflow-cpu<2.20
    has no wheels for 3.13 or 3.14, so pip just fails to resolve on a newer one."""
    names = ["python3.12"]
    if WIN:
        names = ["py"]                        # the launcher picks the version: py -3.12
    for d in ("", "/opt/homebrew/bin/", "/usr/local/bin/",
              "/Library/Frameworks/Python.framework/Versions/3.12/bin/"):
        for n in names:
            p = d + n
            exe = p if d else shutil.which(n)
            if not exe or not os.path.isfile(exe):
                continue
            try:
                r = subprocess.run([exe] + (["-3.12"] if WIN else []) +
                                   ["-c", "import sys; print(sys.version_info[:2])"],
                                   capture_output=True, text=True, timeout=15,
                                   creationflags=NOWIN)
            except Exception:
                continue
            if r.returncode == 0 and "(3, 12)" in r.stdout:
                return exe
    return None


def http_json(url: str, timeout: float = 1.5, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def http_text(url: str, timeout: float = 3.0, tries: int = 3) -> str:
    """GET a board endpoint, retrying a couple of times before giving up.

    MEASURED on the Rev F board over a phone hotspot: 0% packet loss but a
    round trip between 7 ms and 211 ms, so a single attempt lands in a stall
    often enough to report a healthy board as dead ("no route to host"). These
    endpoints are plain reads, so a retry costs nothing but the wait.
    """
    last = None
    for n in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read().decode("ascii", "replace")
        except Exception as e:
            last = e
            if n + 1 < tries:
                time.sleep(0.4 * (n + 1))
    raise last


# ---------------------------------------------------------------------- jobs

class Job:
    """one running command: its process, and a thread pumping its output."""

    def __init__(self, app, key, title, args, serial=False, on_exit=None, env=None,
                 python=None):
        self.app, self.key, self.title, self.args = app, key, title, args
        self.serial, self.on_exit = serial, on_exit
        self.extra_env = env or {}            # e.g. a password, kept off the command line
        # normally the project's venv; the first-run setup needs a different one,
        # because the venv it is about to build does not exist yet
        self.python = python
        self.proc = None

    def start(self):
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
                   **self.extra_env)
        cmd = [self.python or self.app.python, "-u"] + self.args
        self.proc = subprocess.Popen(cmd, cwd=self.app.repo, env=env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     creationflags=NOWIN)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        while True:
            chunk = self.proc.stdout.read1(4096)
            if not chunk:
                break
            self.app.q.put(("out", dec.decode(chunk)))
        rc = self.proc.wait()
        self.app.q.put(("exit", self.key, rc))

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.running():
            self.proc.terminate()


# ----------------------------------------------------------------------- app

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()
        self.repo = find_repo()
        self.python = find_python(self.cfg)
        self.q: queue.Queue = queue.Queue()
        self.jobs: dict[str, Job] = {}
        self.gated: list = []                 # (widget, predicate) re-evaluated twice a second
        self.devices: list = []
        self.hotspot = "checking…"
        self.tokens: dict = {}                # role -> token, from a --secure master's output
        self._tokbuf = ""
        self.cr = False                       # last output ended in a bare \r

        root.title(APP)
        # never taller than the display: a window whose title bar is off-screen
        # cannot be moved back on macOS
        w, h = WANT
        root.geometry("%dx%d" % (min(w, root.winfo_screenwidth() - 40),
                                 min(h, root.winfo_screenheight() - 120)))
        root.minsize(*MINSIZE)
        self._style()
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.quit)
        if MAC:
            # cmd-Q and the Dock's Quit bypass WM_DELETE_WINDOW entirely, which
            # would leave the master and a half-recorded session running with
            # their window gone.
            try:
                root.createcommand("tk::mac::Quit", self.quit)
            except tk.TclError:
                pass
        root.after(100, self._drain)
        root.after(500, self._refresh_gates)
        root.after(800, self._poll_fleet)
        root.after(300, self._poll_hotspot)
        self._intro()

    # ---- look ----

    def _style(self):
        s = ttk.Style()
        for theme in (("aqua",) if MAC else ("vista", "winnative")):
            if theme in s.theme_names():
                s.theme_use(theme)
                break
        s.configure("Big.TButton", padding=(14, 9), font=(UI, FS["body"], "bold"))
        s.configure("TButton", padding=(10, 6))
        s.configure("Head.TLabel", font=(UI, FS["head"], "bold"))
        s.configure("Dim.TLabel", foreground="#666")
        s.configure("Status.TLabel", font=(UI, FS["body"]))

    def _build(self):
        top = ttk.Frame(self.root, padding=(12, 10, 12, 4))
        top.pack(fill="x")
        ttk.Label(top, text=APP, font=(UI, FS["title"], "bold")).pack(side="left")
        self.status_lbl = ttk.Label(top, text="", style="Status.TLabel")
        self.status_lbl.pack(side="right")

        panes = self.panes = ttk.PanedWindow(self.root, orient="vertical")
        panes.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        nb = ttk.Notebook(panes)
        panes.add(nb, weight=3)
        for name, build in (("  Live  ", self._tab_live), ("  Board setup  ", self._tab_board),
                            ("  Stress session  ", self._tab_protocol), ("  Demo  ", self._tab_demo),
                            ("  Advanced  ", self._tab_advanced)):
            outer = ttk.Frame(nb)
            nb.add(outer, text=name)
            build(self._scroller(outer))

        logf = ttk.Frame(panes)
        panes.add(logf, weight=2)
        bar = ttk.Frame(logf)
        bar.pack(fill="x", pady=(6, 2))
        ttk.Label(bar, text="Log", style="Head.TLabel").pack(side="left")
        ttk.Button(bar, text="Clear", command=lambda: self.log.delete("1.0", "end")).pack(side="right")
        ttk.Button(bar, text="Copy", command=self._copy_log).pack(side="right", padx=6)
        self.log = tk.Text(logf, height=LOG_LINES, wrap="word", font=(MONO, FS["log"]),
                           background="#101418", foreground="#d8dee4", insertbackground="#d8dee4",
                           relief="flat", padx=8, pady=6)
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        # the panes would otherwise each take their requested height, and the
        # tallest tab asks for nearly the whole window -- leaving the log, which
        # is where every command's output goes, about four lines tall.
        self.root.after_idle(self._place_sash)   # retries until the window has a height
        self.log.tag_configure("head", foreground="#7cc4ff")
        self.log.tag_configure("ok", foreground="#5fd38d")
        self.log.tag_configure("bad", foreground="#ff7b72")

    def _scroller(self, parent):
        """tab content that scrolls when it is taller than the tab.

        Board setup wants ~650 px, and on a 1440x900 display that does not fit
        beside a log pane worth having. Clipping it just hides buttons with no
        hint they exist -- which is how the hotspot controls and the whole
        sensor-check section went missing. The scrollbar only appears when
        there is something below the fold.
        """
        canvas = tk.Canvas(parent, highlightthickness=0, borderwidth=0, takefocus=0)
        vsb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(canvas, padding=14)
        win = canvas.create_window((0, 0), window=body, anchor="nw")

        def resized(_e=None):
            # stretch the content frame over the whole canvas when it is shorter.
            # a canvas has no ttk background, so any of it left showing is a
            # visibly different grey -- this way none of it ever shows.
            canvas.itemconfigure(win, width=canvas.winfo_width(),
                                 height=max(body.winfo_reqheight(), canvas.winfo_height()))
            canvas.configure(scrollregion=canvas.bbox("all"))
            if body.winfo_reqheight() > canvas.winfo_height():
                if not vsb.winfo_ismapped():
                    vsb.pack(side="right", fill="y")
            elif vsb.winfo_ismapped():
                vsb.pack_forget()
                canvas.yview_moveto(0)

        body.bind("<Configure>", resized)
        canvas.bind("<Configure>", resized)

        def wheel(e):
            # macOS hands Tk small deltas, Windows multiples of 120
            step = -e.delta if MAC else -(e.delta // 120)
            canvas.yview_scroll(step, "units")

        # bind_all while the pointer is over THIS canvas, so the wheel scrolls
        # the tab under the cursor and nothing else
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        return body

    def _place_sash(self, tries: int = 40):
        """put the sash LOG_PX up from the bottom, once the window has a size.

        it has none while __init__ is still running, so this re-arms itself
        until it does -- a one-shot after_idle here silently does nothing.
        """
        try:
            h = self.panes.winfo_height()
            if h > 420:
                self.panes.sashpos(0, max(240, h - LOG_PX))
                return
        except Exception:
            pass
        if tries > 0:
            self.root.after(50, lambda: self._place_sash(tries - 1))

    # ---- small builders ----

    def _section(self, parent, title, note=""):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=(0, 12))
        ttk.Label(f, text=title, style="Head.TLabel").pack(anchor="w")
        if note:
            ttk.Label(f, text=note, style="Dim.TLabel", wraplength=900, justify="left").pack(anchor="w", pady=(1, 5))
        row = ttk.Frame(f)
        row.pack(fill="x", anchor="w")
        return row

    def _btn(self, row, text, cmd, when=None, big=False):
        b = ttk.Button(row, text=text, command=cmd, style="Big.TButton" if big else "TButton")
        b.pack(side="left", padx=(0, 8), pady=2)
        if when:
            self.gated.append((b, when))
        return b

    # ---- tabs ----

    def _tab_live(self, f):
        ttk.Label(f, text="The master", style="Head.TLabel").pack(anchor="w")
        ttk.Label(f, text="Finds the boards, runs the model on each, and pushes the verdict back. "
                          "Then open the roster to assign people and calibrate.",
                  style="Dim.TLabel").pack(anchor="w", pady=(1, 5))
        mode = ttk.Frame(f)
        mode.pack(fill="x", anchor="w")
        self.net_mode = tk.StringVar(value=self.cfg.get("net_mode", "hotspot"))
        ttk.Label(mode, text="Boards are on:").pack(side="left")
        for val, txt in (("hotspot", "%s's hotspot" % THIS_PC),
                         ("shared", "the same WiFi as %s (a phone's hotspot, a router)" % THIS_PC)):
            ttk.Radiobutton(mode, text=txt, value=val, variable=self.net_mode,
                            command=self._mode_changed).pack(side="left", padx=(8, 0))
        self.mode_hint = ttk.Label(f, text="", style="Dim.TLabel", wraplength=900, justify="left")
        self.mode_hint.pack(anchor="w", pady=(2, 4))
        self._mode_changed(save=False)
        # --secure: the master makes an admin and a reviewer token each start and
        # prints them; this app reads them from that output (see _drain)
        self.secure = tk.BooleanVar(value=bool(self.cfg.get("secure", False)))
        sec = ttk.Frame(f)
        sec.pack(fill="x", anchor="w", pady=(0, 4))
        ttk.Checkbutton(sec, text="Secure mode: the roster needs a token (admin changes "
                                  "anything, reviewer only views and acknowledges flags)",
                        variable=self.secure, command=self._secure_changed).pack(side="left")
        row = ttk.Frame(f)
        row.pack(fill="x", anchor="w", pady=(0, 12))
        self._btn(row, "▶  Start master", self.start_master,
                  when=lambda: self.ready() and not self.is_running("fleet"), big=True)
        self._btn(row, "■  Stop master", lambda: self.stop("fleet"),
                  when=lambda: self.is_running("fleet"))
        self._btn(row, "Open roster", self.open_roster,
                  when=lambda: self.is_running("fleet"))
        self._btn(row, "Copy admin token", lambda: self.copy_token("admin"),
                  when=lambda: self.is_running("fleet") and "admin" in self.tokens)
        self._btn(row, "Copy reviewer token", lambda: self.copy_token("reviewer"),
                  when=lambda: self.is_running("fleet") and "reviewer" in self.tokens)

        ttk.Label(f, text="Boards", style="Head.TLabel").pack(anchor="w")
        cols = ("board", "ip", "wearer", "finger", "bpm", "level", "state", "ondevice")
        heads = ("Board", "IP", "Wearer", "Finger", "BPM", "Level", "Master says", "Board says")
        widths = (130, 120, 120, 60, 60, 60, 160, 180)
        self.tree = ttk.Treeview(f, columns=cols, show="headings", height=5, selectmode="browse")
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="x", pady=(4, 6))
        self.tree.bind("<<TreeviewSelect>>", self._board_picked)
        row = ttk.Frame(f)
        row.pack(fill="x", anchor="w")
        self._btn(row, "Open board's dashboard", self.open_board, when=lambda: bool(self.tree.selection()))
        self._btn(row, "Board health check", self.board_health, when=lambda: bool(self.tree.selection()))
        self.empty_lbl = ttk.Label(f, text="", style="Dim.TLabel")
        self.empty_lbl.pack(anchor="w", pady=(6, 0))

    def _tab_board(self, f):
        row = self._section(f, "Put a board on WiFi  (USB, once per board)",
                            "Plug the board into %s by USB and press the button. %s Close the "
                            "Arduino Serial Monitor first: only one program can use the USB port."
                            % (THIS_PC, HOTSPOT_PASS))
        self._btn(row, "Connect board to %s's hotspot" % THIS_PC, lambda: self.run(
            "wifi", "Connect board to hotspot", ["-m", "anomaly.device_wifi", "--hotspot"], serial=True),
            when=self.serial_free, big=True)
        self._btn(row, "Which network is it on?", lambda: self.run(
            "wifi", "Board WiFi status", ["-m", "anomaly.device_wifi", "--status"], serial=True),
            when=self.serial_free)
        self._btn(row, "Forget all its networks", self.forget_wifi, when=self.serial_free)

        row = self._section(f, "Add a WiFi network to a board",
                            "For a network that isn't %s — a phone's hotspot, a router. "
                            "Save for later: the board tries it whenever its current network is out of "
                            "reach. Connect now: it leaves its current network and joins this one; if "
                            "that fails it comes back within 30 s. A board remembers up to 5 networks."
                            % THIS_PC)
        form = ttk.Frame(row)
        form.pack(side="left", anchor="w")
        self.w_via = tk.StringVar(value="wifi")
        self.w_ip, self.w_ssid, self.w_pass = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.w_show = tk.BooleanVar(value=False)
        self._ip_auto = True
        ttk.Label(form, text="Send to").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Radiobutton(form, text="over WiFi, board at IP", value="wifi",
                        variable=self.w_via).grid(row=0, column=1, sticky="w")
        ip = ttk.Entry(form, textvariable=self.w_ip, width=16)
        ip.grid(row=0, column=2, sticky="w", padx=(4, 16))
        ip.bind("<Key>", lambda e: setattr(self, "_ip_auto", False))
        ttk.Radiobutton(form, text="over USB", value="usb", variable=self.w_via).grid(row=0, column=3, sticky="w")
        ttk.Label(form, text="Network name").grid(row=1, column=0, sticky="w", pady=6, padx=(0, 6))
        ttk.Entry(form, textvariable=self.w_ssid, width=26).grid(row=1, column=1, columnspan=2, sticky="w")
        ttk.Label(form, text="Password").grid(row=2, column=0, sticky="w", padx=(0, 6))
        self.w_pass_entry = ttk.Entry(form, textvariable=self.w_pass, width=26, show="•")
        self.w_pass_entry.grid(row=2, column=1, columnspan=2, sticky="w")
        ttk.Checkbutton(form, text="show", variable=self.w_show,
                        command=lambda: self.w_pass_entry.configure(show="" if self.w_show.get() else "•")
                        ).grid(row=2, column=3, sticky="w")
        ttk.Label(form, text="IP: pick the board on the Live tab, or read it off the board's screen.",
                  style="Dim.TLabel").grid(row=3, column=0, columnspan=5, sticky="w", pady=(4, 0))
        row = self._section(f, "")
        net_ok = lambda: self.ready() and (self.w_via.get() == "wifi" or self.serial_free())
        self._btn(row, "Save for later", lambda: self.add_network("save"), when=net_ok)
        self._btn(row, "Connect now", lambda: self.add_network("now"), when=net_ok)
        self._btn(row, "Saved networks", self.list_networks, when=net_ok)
        self._btn(row, "Forget this network", self.forget_network, when=net_ok)

        row = self._section(f, "The hotspot  (%s)" % HOTSPOT, HOTSPOT_NOTE)
        self._btn(row, "Hotspot status", lambda: self.run(
            "hotspot", "Hotspot status", ["-m", "anomaly.hotspot"], on_exit=self._hotspot_soon),
            when=lambda: self.ready() and not self.is_running("hotspot"))
        self._btn(row, "Turn hotspot on", lambda: self.run(
            "hotspot", "Turn hotspot on", ["-m", "anomaly.hotspot", "--on"], on_exit=self._hotspot_soon),
            when=lambda: self.ready() and not self.is_running("hotspot"))

        row = self._section(f, "Check the sensor  (USB)",
                            "Live grip coach: is the finger on properly, is the pulse strong enough, "
                            "is it still? Stop it when you are done.")
        self._btn(row, "Start grip check", lambda: self.run(
            "grip", "Grip check", ["-m", "anomaly.device_check"], serial=True),
            when=self.serial_free)
        self._btn(row, "Stop grip check", lambda: self.stop("grip"), when=lambda: self.is_running("grip"))

    def _tab_protocol(self, f):
        row = self._section(f, "Induced-stress session",
                            "Needs the master running and someone assigned to the board on the "
                            "roster. Settle → baseline → induction → recovery; the log tells the "
                            "subject what to do at each phase.")
        form = ttk.Frame(row)
        form.pack(side="left", anchor="w")
        self.p_task = tk.StringVar(value="serial subtraction")
        self.p_base, self.p_ind, self.p_rec = tk.IntVar(value=180), tk.IntVar(value=180), tk.IntVar(value=180)
        self.p_freeze, self.p_raw = tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        self.p_dev = tk.StringVar(value="")
        ttk.Label(form, text="Task").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(form, textvariable=self.p_task, width=30).grid(row=0, column=1, columnspan=3, sticky="w")
        ttk.Label(form, text="Board").grid(row=0, column=4, sticky="w", padx=(16, 6))
        self.p_dev_box = ttk.Combobox(form, textvariable=self.p_dev, width=18, state="readonly")
        self.p_dev_box.grid(row=0, column=5, sticky="w")
        for i, (lbl, var) in enumerate((("Baseline s", self.p_base), ("Induction s", self.p_ind),
                                        ("Recovery s", self.p_rec))):
            ttk.Label(form, text=lbl).grid(row=1, column=2 * i, sticky="w", pady=6, padx=(0 if i == 0 else 16, 6))
            ttk.Spinbox(form, from_=30, to=1200, increment=30, textvariable=var, width=6).grid(row=1, column=2 * i + 1, sticky="w")
        ttk.Checkbutton(form, text="Pin the calm reference to the baseline (recommended)",
                        variable=self.p_freeze).grid(row=2, column=0, columnspan=4, sticky="w")
        ttk.Checkbutton(form, text="Save the waveform", variable=self.p_raw).grid(row=2, column=4, columnspan=2, sticky="w")

        row = self._section(f, "")
        self._btn(row, "▶  Start session", self.start_protocol,
                  when=lambda: self.is_running("fleet") and not self.is_running("protocol"), big=True)
        self._btn(row, "Next phase now", lambda: self.protocol_cmd("next"),
                  when=lambda: self.is_running("protocol"))
        self._btn(row, "■  Stop session", lambda: self.protocol_cmd("stop"),
                  when=lambda: self.is_running("protocol"))

        row = self._section(f, "Results", "Scores every recorded session: heart-rate check, AUROC, "
                                          "recall@90% specificity, and the on-device detector beside the host's.")
        self._btn(row, "Report", lambda: self.run("report", "Session report", ["-m", "anomaly.protocol", "report"]),
                  when=lambda: self.ready() and not self.is_running("report"))
        self._btn(row, "Report + plots", lambda: self.run(
            "report", "Session report + plots", ["-m", "anomaly.protocol", "report", "--plot"],
            on_exit=lambda rc: rc == 0 and self.open_path("data", "protocols")),
            when=lambda: self.ready() and not self.is_running("report"))
        self._btn(row, "List sessions", lambda: self.run("report", "Sessions", ["-m", "anomaly.protocol", "list"]),
                  when=lambda: self.ready() and not self.is_running("report"))
        self._btn(row, "Open plots folder", lambda: self.open_path("data", "protocols"))

    def _tab_demo(self, f):
        row = self._section(f, "Demo without hardware",
                            "Replays real WESAD stress data through the deployed model. This is the "
                            "one to show when somebody asks whether any of it is real.")
        self._btn(row, "▶  Start WESAD demo", lambda: self.run(
            "serve", "WESAD demo dashboard", ["-m", "anomaly.serve"],
            on_start=lambda: self.root.after(6000, lambda: webbrowser.open(SERVE))),
            when=lambda: self.ready() and not self.is_running("serve"), big=True)
        self._btn(row, "■  Stop demo", lambda: self.stop("serve"), when=lambda: self.is_running("serve"))
        self._btn(row, "Open Pulse Watch", lambda: webbrowser.open(SERVE), when=lambda: self.is_running("serve"))
        self._btn(row, "Open developer view", lambda: webbrowser.open(SERVE + "/dev"),
                  when=lambda: self.is_running("serve"))

        row = self._section(f, "One board over USB", "The same dashboard on the real sensor, no WiFi needed.")
        self._btn(row, "▶  Start USB dashboard", lambda: self.run(
            "serve", "USB dashboard", ["-m", "anomaly.serve", "--source", "device"], serial=True,
            on_start=lambda: self.root.after(8000, lambda: webbrowser.open(SERVE))),
            when=lambda: self.serial_free() and not self.is_running("serve"))

    def _tab_advanced(self, f):
        row = self._section(f, "Firmware", "Run after editing anything under pulse/ or anomaly/static/, "
                                           "then re-upload the sketch. Needed once on every fresh clone.")
        self._btn(row, "Rebuild board web pages", lambda: self.run(
            "assets", "Rebuild web assets", [os.path.join("pulse_display", "make_web_assets.py")]),
            when=lambda: self.ready() and not self.is_running("assets"))
        self._btn(row, "Open sketch folder", lambda: self.open_path("pulse_display"))

        row = self._section(f, "On-device detector", "Prove the C port's algorithm, or refit it on WESAD "
                                                     "(rewrites pulse_display/board_model.h; re-upload after).")
        self._btn(row, "Check C port", lambda: self.run(
            "export", "Check the on-device detector", ["-m", "anomaly.board_export", "--check", "300"]),
            when=lambda: self.ready() and not self.is_running("export"))
        self._btn(row, "Refit + export", self.refit_board,
                  when=lambda: self.ready() and not self.is_running("export"))

        row = self._section(f, "Data store", "Subjects, baselines, sessions and flags (data/pulse.db).")
        self._btn(row, "Summary", lambda: self.run("db", "Store summary", ["-m", "anomaly.db"]),
                  when=lambda: self.ready() and not self.is_running("db"))
        self._btn(row, "Back up…", self.backup_db, when=lambda: self.ready() and not self.is_running("db"))

        row = self._section(f, "This app")
        self._btn(row, "Set up the virtualenv…", self.setup_venv,
                  when=lambda: self.repo is not None and not self.is_running("setup"))
        self._btn(row, "Check setup", self.check_setup, when=lambda: self.python is not None)
        self._btn(row, "Choose Python…", self.choose_python)
        self._btn(row, "Open project folder", lambda: self.open_path())
        self._btn(row, "Open %s" % COMMANDS_DOC, lambda: self.open_path(COMMANDS_DOC))

    # ---- running things ----

    def ready(self) -> bool:
        return self.repo is not None and self.python is not None

    def is_running(self, key) -> bool:
        j = self.jobs.get(key)
        return j is not None and j.running()

    def serial_free(self) -> bool:
        return self.ready() and not any(j.serial and j.running() for j in self.jobs.values())

    def run(self, key, title, args, serial=False, on_exit=None, on_start=None, env=None,
            python=None):
        if self.repo is None or (python is None and self.python is None):
            self._need_setup()
            return
        if self.is_running(key):
            return
        if serial and not self.serial_free():
            messagebox.showinfo(APP, "Another USB job is using the board. Stop it first.")
            return
        job = Job(self, key, title, args, serial=serial, on_exit=on_exit, env=env,
                  python=python)
        self.jobs[key] = job
        self._write("\n── %s ──\n" % title, "head")
        try:
            job.start()
        except Exception as e:
            self._write("could not start: %s\n" % e, "bad")
            return
        if on_start:
            on_start()

    def stop(self, key):
        j = self.jobs.get(key)
        if j and j.running():
            self._write("stopping %s…\n" % j.title, "head")
            j.stop()

    def quit(self):
        live = [j for j in self.jobs.values() if j.running()]
        if live and not messagebox.askyesno(APP, "Still running:\n\n  " + "\n  ".join(j.title for j in live)
                                            + "\n\nStop them and quit?"):
            return
        if self.is_running("protocol"):
            self.protocol_cmd("stop", wait=True)
        for j in live:
            j.stop()
        self.root.destroy()

    # ---- actions ----

    def start_protocol(self):
        if not self.p_dev.get() and len(self.devices) > 1:
            messagebox.showinfo(APP, "More than one board is connected — pick one in 'Board'.")
            return
        args = ["-m", "anomaly.protocol", "run", "--task", self.p_task.get() or "serial subtraction",
                "--baseline", str(self.p_base.get()), "--induction", str(self.p_ind.get()),
                "--recovery", str(self.p_rec.get())]
        if self.p_dev.get():
            args += ["--device", self.p_dev.get()]
        if not self.p_freeze.get():
            args.append("--no-freeze")
        if not self.p_raw.get():
            args.append("--no-raw")
        self.run("protocol", "Stress session", args)

    def protocol_cmd(self, action, wait=False):
        args = [self.python, "-u", "-m", "anomaly.protocol", action]
        if self.p_dev.get():
            args += ["--device", self.p_dev.get()]
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

        def go():
            r = subprocess.run(args, cwd=self.repo, env=env, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", creationflags=NOWIN)
            self.q.put(("out", (r.stdout or "") + (r.stderr or "")))
        if wait:
            go()
        else:
            threading.Thread(target=go, daemon=True).start()

    # ---- networks ----

    def _mode_changed(self, save=True):
        hint = {"hotspot": "%s and the boards join it. Best when the board is near %s."
                           % (HOTSPOT_VERB, THIS_PC),
                "shared": "Put %s on the same WiFi as the boards first (e.g. join your phone's "
                          "hotspot), then start. Give a board that network on Board setup > Add a "
                          "WiFi network." % THIS_PC}[self.net_mode.get()]
        self.mode_hint.configure(text=hint)
        if save:
            self.cfg["net_mode"] = self.net_mode.get()
            save_config(self.cfg)

    def start_master(self):
        self.tokens, self._tokbuf = {}, ""    # new tokens every start
        sec = ["--secure"] if self.secure.get() else []
        tag = ", secure" if sec else ""
        if self.net_mode.get() == "hotspot":
            self.run("fleet", "Master (%s's hotspot%s)" % (THIS_PC, tag),
                     ["-m", "anomaly.fleet", "--hotspot"] + sec)
        else:
            self.run("fleet", "Master (shared WiFi%s)" % tag, ["-m", "anomaly.fleet"] + sec)

    def _secure_changed(self):
        self.cfg["secure"] = self.secure.get()
        save_config(self.cfg)
        if self.is_running("fleet"):
            self._write("\n(secure mode takes effect the next time the master starts)\n", "head")

    def _catch_tokens(self, text):
        """pick the master's '<role> token: <tok>' lines out of its output."""
        if not self.is_running("fleet") or len(self.tokens) == 2:
            return
        self._tokbuf = (self._tokbuf + text)[-4000:]
        for role, tok in re.findall(r"\b(admin|reviewer)\s+token:\s*(\S+)", self._tokbuf):
            self.tokens[role] = tok

    def copy_token(self, role):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.tokens[role])
        self._write("\n%s token copied. In the roster: Security > Set token, then paste.\n" % role,
                    "head")

    def open_roster(self):
        webbrowser.open(FLEET)
        if "admin" in self.tokens:
            self.copy_token("admin")

    def _board_picked(self, _e=None):
        d = self._selected_device()
        if d and (self._ip_auto or not self.w_ip.get()):
            self.w_ip.set(d["ip"])
            self._ip_auto = True

    def _net_inputs(self, need_pass: bool):
        ssid, pw = self.w_ssid.get().strip(), self.w_pass.get()
        if not ssid or len(ssid.encode()) > 32:
            messagebox.showinfo(APP, "Type the network's name (up to 32 characters).")
            return None
        if self.w_via.get() == "usb" and "," in ssid:
            messagebox.showinfo(APP, "Over USB the network name can't contain a comma. Send it over WiFi instead.")
            return None
        if need_pass and pw and not 8 <= len(pw) <= 63:
            messagebox.showinfo(APP, "A WiFi password is 8–63 characters (leave it empty for an open network).")
            return None
        if self.w_via.get() == "wifi" and not self.w_ip.get().strip():
            messagebox.showinfo(APP, "Which board? Pick it on the Live tab, or type its IP "
                                     "(it's at the top of the board's screen).")
            return None
        return ssid, pw

    def _board_call(self, ip, method, body=None, timeout=5.0):
        data = None
        if body is not None:
            data = urllib.parse.urlencode(body).encode()
        req = urllib.request.Request("http://%s/wifi" % ip, data=data, method=method,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode())
            except Exception:
                return {"ok": False, "error": "HTTP %d" % e.code}

    def add_network(self, action):
        got = self._net_inputs(need_pass=True)
        if not got:
            return
        ssid, pw = got
        if action == "now" and not messagebox.askyesno(APP,
                "The board will leave its current network and join %r.\n\n"
                "If it can't (wrong password, out of range), it comes back by itself within "
                "about 30 s.\n\nIf it does join, this PC must be on %r too to keep seeing it "
                "(Live tab: 'the same WiFi as this PC')." % (ssid, ssid)):
            return
        if self.w_via.get() == "usb":
            args = ["-m", "anomaly.device_wifi", "--ssid", ssid] + (["--save"] if action == "save" else [])
            self.run("wifi", "%s %r over USB" % ("Save" if action == "save" else "Join", ssid), args,
                     serial=True, env={"PULSE_WIFI_PASS": pw})
            return
        ip = self.w_ip.get().strip()
        self._write("\n── %s %r on the board at %s ──\n" % (
            "Save" if action == "save" else "Join", ssid, ip), "head")

        def go():
            try:
                before = self._board_call(ip, "GET")
                r = self._board_call(ip, "POST", {"action": action, "ssid": ssid, "pass": pw})
            except Exception as e:
                self.q.put(("out", "can't reach a board at %s: %s\n" % (ip, e)))
                return
            if not r.get("ok"):
                self.q.put(("out", "the board refused: %s\n" % r.get("error", r)))
                return
            if action == "save":
                self.q.put(("out", "saved. it tries %r whenever its current network is out of reach.\n" % ssid))
                return
            self.q.put(("out", "switching… (it was on %r)\n" % before.get("ssid", "?")))
            self._watch_switch(ip, ssid, before.get("ssid", ""))
        threading.Thread(target=go, daemon=True).start()

    def _watch_switch(self, ip, ssid, old):
        """after a join-now: did it come back (failed), or leave (probably joined)?"""
        t0, seen_gone = time.time(), False
        while time.time() - t0 < 50:
            time.sleep(3)
            try:
                st = self._board_call(ip, "GET", timeout=2.5)
            except Exception:
                seen_gone = True
                continue
            if st.get("switching"):
                continue
            last = st.get("last", "")
            if last.startswith("could not join"):
                self.q.put(("out", "✗ %s. check the password, that it's 2.4 GHz, and that it's in range.\n" % last))
                return
            if st.get("ssid") == ssid:
                self.q.put(("out", "✓ joined %r (still at %s).\n" % (ssid, ip)))
                return
        if seen_gone:
            self.q.put(("out", "✓ it left %r and didn't come back, so it's almost certainly on %r now.\n"
                               "  next: put this PC on %r, choose 'the same WiFi as this PC' on the Live tab,\n"
                               "  and start the master — it finds the board's new address by itself.\n"
                        % (old, ssid, ssid)))
        else:
            self.q.put(("out", "no clear answer from the board; check its screen for its network and IP.\n"))

    def list_networks(self):
        if self.w_via.get() == "usb":
            self.run("wifi", "Board networks over USB", ["-m", "anomaly.device_wifi", "--status"], serial=True)
            return
        ip = self.w_ip.get().strip()
        if not ip:
            messagebox.showinfo(APP, "Which board? Pick it on the Live tab, or type its IP.")
            return
        self._write("\n── networks on the board at %s ──\n" % ip, "head")

        def go():
            try:
                st = self._board_call(ip, "GET")
            except Exception as e:
                self.q.put(("out", "can't reach a board at %s: %s\n" % (ip, e)))
                return
            known = ["%s%s" % (k["ssid"], " (from secrets.h)" if k.get("from_secrets") else "")
                     for k in st.get("known", [])]
            lines = ["on now:  %s  (%s dBm)" % (st.get("ssid") or "-", st.get("rssi", "?")),
                     "saved:   %s" % (", ".join(known) or "none"),
                     "         tried in this order when the current one is out of reach"]
            if st.get("last"):
                lines.append("last:    " + st["last"])
            self.q.put(("out", "\n".join(lines) + "\n"))
        threading.Thread(target=go, daemon=True).start()

    def forget_network(self):
        got = self._net_inputs(need_pass=False)
        if not got:
            return
        ssid = got[0]
        if not messagebox.askyesno(APP, "Forget %r on the board?\n\nIf it's the network the board is on "
                                        "right now, it disconnects and tries its other networks." % ssid):
            return
        if self.w_via.get() == "usb":
            self.run("wifi", "Forget %r over USB" % ssid,
                     ["-m", "anomaly.device_wifi", "--forget", "--ssid", ssid], serial=True)
            return
        ip = self.w_ip.get().strip()

        def go():
            try:
                r = self._board_call(ip, "POST", {"action": "forget", "ssid": ssid})
                self.q.put(("out", "forgot %r.\n" % ssid if r.get("ok") else "the board refused: %s\n" % r.get("error")))
            except Exception as e:
                self.q.put(("out", "can't reach a board at %s: %s\n" % (ip, e)))
        threading.Thread(target=go, daemon=True).start()

    def forget_wifi(self):
        if messagebox.askyesno(APP, "Forget EVERY WiFi network saved on the board?\n\n"
                                    "It falls back to secrets.h, or to nothing."):
            self.run("wifi", "Forget board WiFi", ["-m", "anomaly.device_wifi", "--forget"], serial=True)

    def refit_board(self):
        if messagebox.askyesno(APP, "Refit the on-device detector on WESAD and rewrite "
                                    "pulse_display/board_model.h?\n\nYou will need to re-upload the sketch."):
            self.run("export", "Refit + export on-device detector", ["-m", "anomaly.board_export"])

    def backup_db(self):
        path = filedialog.asksaveasfilename(title="Back up the store", defaultextension=".db",
                                            initialfile="pulse-backup-%s.db" % time.strftime("%Y%m%d"),
                                            filetypes=[("SQLite", "*.db")])
        if path:
            self.run("db", "Back up store", ["-m", "anomaly.db", "--backup", path])

    def setup_venv(self):
        """build ~/.venvs/sdp07 and install the requirements, from in here.

        this is what makes the app worth double-clicking on a fresh clone: the
        window opens on any Mac with Tk, and the one thing it cannot do without
        is the virtualenv, so it offers to make it rather than only complaining.
        """
        py = find_python312()
        if not py:
            messagebox.showerror(APP, "Python 3.12 is not installed, and the project needs "
                                      "exactly 3.12 (tensorflow-cpu has no wheels for 3.13 "
                                      "or 3.14).\n\n" +
                                      ("Install it with:\n\n    brew install python@3.12\n\n"
                                       if MAC else
                                       "Install it from python.org, ticking 'tcl/tk and "
                                       "IDLE'.\n\n") +
                                      "Then come back and press this again.")
            return
        venv = os.path.join(os.path.expanduser("~"), ".venvs", "sdp07")
        vpy = (os.path.join(venv, "Scripts", "python.exe") if WIN
               else os.path.join(venv, "bin", "python"))
        if os.path.isfile(vpy) and not messagebox.askyesno(
                APP, "%s already exists.\n\nRe-install the dependencies into it?" % venv):
            return
        if not messagebox.askyesno(
                APP, "Create %s and install everything the project needs?\n\n"
                     "That downloads roughly 1 GB (TensorFlow is most of it) and takes a "
                     "few minutes on a good connection. The log below shows progress, and "
                     "you can keep using this window.\n\nUsing: %s" % (venv, py)):
            return

        def installed(rc):
            if rc != 0:
                self._write("\nthe install did not finish — see the messages above.\n", "bad")
                return
            self.python = find_python(self.cfg)
            self._write("\nready. every button above is live now.\n", "ok")

        def created(rc):
            if rc != 0:
                self._write("\ncould not create the virtualenv.\n", "bad")
                return
            self.run("setup", "Install the dependencies (a few minutes)",
                     ["-m", "pip", "install", "--upgrade", "pip"], python=vpy,
                     on_exit=lambda _rc: self.run(
                         "setup", "Install the dependencies (a few minutes)",
                         ["-m", "pip", "install",
                          "-r", os.path.join("baselines", "requirements.txt"),
                          "-r", os.path.join("pipeline", "requirements.txt")],
                         python=vpy, on_exit=installed))

        args = (["-3.12", "-m", "venv", venv] if WIN else ["-m", "venv", venv])
        self.run("setup", "Create the virtualenv", args, python=py, on_exit=created)

    def check_setup(self):
        code = ("import importlib, sys\n"
                "print('python', sys.version.split()[0], sys.executable)\n"
                "for m in ('numpy','scipy','fastapi','uvicorn','websockets','serial',\n"
                "          'matplotlib','tensorflow','tkinter'):\n"
                "    try:\n"
                "        importlib.import_module(m); print('  ok     ', m)\n"
                "    except Exception as e:\n"
                "        print('  MISSING', m, '-', type(e).__name__)\n")
        self.run("setup", "Check setup", ["-c", code])

    def choose_python(self):
        p = filedialog.askopenfilename(
            title="The project's Python (in its virtualenv)",
            filetypes=[("Python", "python.exe python"), ("All", "*")] if WIN else [("All", "*")])
        if p:
            self.python = p
            self.cfg["python"] = p
            save_config(self.cfg)
            self._write("using %s\n" % p, "ok")

    def open_path(self, *parts):
        if not self.repo:
            return
        p = os.path.join(self.repo, *parts)
        if not os.path.exists(p):
            os.makedirs(p, exist_ok=True) if not os.path.splitext(p)[1] else None
        try:
            if sys.platform == "win32":
                os.startfile(p)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception as e:
            self._write("could not open %s: %s\n" % (p, e), "bad")

    def _selected_device(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return next((d for d in self.devices if d["id"] == sel[0]), None)

    def open_board(self):
        d = self._selected_device()
        if d:
            webbrowser.open("http://%s/" % d["ip"])

    def board_health(self):
        d = self._selected_device()
        if not d:
            return
        self._write("\n── health of %s (%s) ──\n" % (d.get("name") or d["id"], d["ip"]), "head")

        def go():
            try:
                body = http_text("http://%s/health" % d["ip"])
            except Exception as e:
                self.q.put(("out", "no answer: %s\n" % e))
                return
            kv = dict(t.split("=", 1) for t in body.split() if "=" in t)
            lines = [
                "signal     finger IR %s, %s bpm, dropped %s, sensor restarts %s" % (
                    kv.get("ir", "?"), kv.get("bpm", "?"), kv.get("drop", "?"), kv.get("rec", "?")),
                "wifi       rssi %s dBm, free memory %s bytes" % (kv.get("rssi", "?"), kv.get("heap", "?")),
                "detector   self-test %s, state %s, level %s%%, %s ms per tick, movement holds %s, skipped %s s" % (
                    kv.get("self", "-"), kv.get("det", "-"), kv.get("dlv", "-"), kv.get("dms", "-"),
                    kv.get("dhold", "-"), kv.get("ddrop", "-")),
                "raw        " + body.strip(),
            ]
            self.q.put(("out", "\n".join(lines) + "\n"))
        threading.Thread(target=go, daemon=True).start()

    # ---- background polling ----

    def _poll_fleet(self):
        if self.is_running("fleet"):
            # in secure mode, read with the reviewer token: it can only look
            tok = self.tokens.get("reviewer") or self.tokens.get("admin")
            hdr = {"X-Pulse-Token": tok} if tok else None

            def go():
                try:
                    self.q.put(("devices", http_json(FLEET + "/api/devices", headers=hdr)
                                .get("devices", [])))
                except Exception:
                    self.q.put(("devices", None))
            threading.Thread(target=go, daemon=True).start()
        elif self.devices:
            self.q.put(("devices", []))
        self.root.after(2000, self._poll_fleet)

    def _hotspot_soon(self, rc=0):
        self.root.after(500, self._poll_hotspot_once)

    def _poll_hotspot_once(self):
        if not self.ready():
            self.hotspot = "—"
            return

        def go():
            try:
                r = subprocess.run([self.python, "-m", "anomaly.hotspot"], cwd=self.repo, capture_output=True,
                                   text=True, timeout=60, creationflags=NOWIN,
                                   env=dict(os.environ, PYTHONIOENCODING="utf-8"))
                m = re.search(r"hotspot '([^']*)': (\w+)", r.stdout)
                if m:
                    st = "%s %s" % (m.group(1), m.group(2).lower())
                elif "not set up" in r.stdout:
                    st = "not set up"          # macOS, never configured in Settings
                else:
                    st = "unavailable"
                self.q.put(("hotspot", st))
            except Exception:
                self.q.put(("hotspot", "unavailable"))
        threading.Thread(target=go, daemon=True).start()

    def _poll_hotspot(self):
        self._poll_hotspot_once()
        self.root.after(20000, self._poll_hotspot)

    # ---- the UI thread ----

    def _drain(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "out":
                    self._write(msg[1])
                    self._catch_tokens(msg[1])
                elif kind == "exit":
                    _, key, rc = msg
                    job = self.jobs.get(key)
                    title = job.title if job else key
                    self._write("\n── %s %s ──\n" % (title, "finished" if rc == 0 else "stopped (exit %s)" % rc),
                                "ok" if rc == 0 else "bad")
                    if job and job.on_exit:
                        try:
                            job.on_exit(rc)
                        except Exception:
                            pass
                elif kind == "devices":
                    self._show_devices(msg[1])
                elif kind == "hotspot":
                    self.hotspot = msg[1]
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _write(self, text, tag=None):
        at_end = self.log.yview()[1] > 0.98
        for part in re.split(r"(\r\n|\r|\n)", text):
            if part in ("\n", "\r\n"):
                self.log.insert("end", "\n")
                self.cr = False
            elif part == "\r":
                self.cr = True
            elif part:
                if self.cr:                       # a console redrawing its status line
                    self.log.delete("end-1c linestart", "end-1c")
                    self.cr = False
                self.log.insert("end", part, tag or ())
        n = int(self.log.index("end-1c").split(".")[0])
        if n > 5000:
            self.log.delete("1.0", "%d.0" % (n - 4000))
        if at_end:
            self.log.see("end")

    def _show_devices(self, devs):
        if devs is None:                      # master starting up, or busy
            return
        self.devices = devs
        keep = set()
        for d in devs:
            sub = d.get("subject") or {}
            ond = d.get("ond") or {}
            lvl = d.get("level")
            vals = (d.get("name") or d["id"], d.get("ip", ""), sub.get("name", "— nobody —"),
                    "yes" if d.get("contact") else "no", d.get("bpm") if d.get("bpm") is not None else "—",
                    "%d%%" % round(lvl * 100) if lvl is not None else "—",
                    ("FLAG · " if d.get("flag") else "") + (d.get("state") or "") if d.get("connected") else "offline",
                    ("%s%s" % (ond.get("st", ""), " · %d%%" % round(ond["l"] * 100) if ond.get("l") is not None else "")
                     ) if ond else "—")
            keep.add(d["id"])
            if self.tree.exists(d["id"]):
                self.tree.item(d["id"], values=vals)
            else:
                self.tree.insert("", "end", iid=d["id"], values=vals)
        for iid in self.tree.get_children():
            if iid not in keep:
                self.tree.delete(iid)
        ids = [d["id"] for d in devs]
        self.p_dev_box["values"] = [""] + ids
        if self.is_running("fleet") and not devs:
            self.empty_lbl.configure(text="No boards yet. Is the board powered, and on %s's hotspot? "
                                          "(Board setup > Connect board to %s's hotspot)"
                                          % (THIS_PC, THIS_PC))
        else:
            self.empty_lbl.configure(text="" if devs else "Start the master to see boards here.")

    def _refresh_gates(self):
        for w, pred in self.gated:
            try:
                w.state(["!disabled"] if pred() else ["disabled"])
            except Exception:
                pass
        running = [j.title for j in self.jobs.values() if j.running()]
        self.status_lbl.configure(text="hotspot: %s   ·   running: %s" % (
            self.hotspot, ", ".join(running) if running else "nothing"))
        self.root.after(500, self._refresh_gates)

    def _copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log.get("1.0", "end-1c"))

    def _need_setup(self):
        if self.repo is None:
            messagebox.showerror(APP, "Can't find the project. Put this app inside the "
                                      "Spring-2026-COSC497-SDP-Group-07 folder.")
        else:
            if messagebox.askyesno(APP, "The project's virtualenv isn't set up yet, so there "
                                        "is nothing for the buttons to run.\n\nSet it up now? "
                                        "(a few minutes, about 1 GB)\n\nAlready have one "
                                        "elsewhere? Choose No, then Advanced > Choose Python."):
                self.setup_venv()

    def _intro(self):
        self._write("%s\n" % APP, "head")
        self._write("project: %s\n" % (self.repo or "NOT FOUND — put the app inside the project folder"),
                    None if self.repo else "bad")
        self._write("python:  %s\n" % (self.python or "NOT FOUND — Advanced > Choose Python"),
                    None if self.python else "bad")
        if self.repo and not self.python:
            self._write("\nthe virtualenv isn't set up yet — Advanced > Set up the virtualenv, "
                        "and the rest of the buttons come alive.\n", "head")
        else:
            self._write("\nstart here: Board setup > Connect board (once per board), "
                        "then Live > Start master.\n")


TK_MIN = 8.6
TK_TOO_OLD = (
    "Pulse Control needs Tk 8.6 or newer. This Python has Tk %s.\n\n"
    "macOS still bundles Tk 8.5.9, a build from 2010 that Apple deprecated long ago. "
    "Under it ttk.PanedWindow never lays out -- every pane stays 1x1 and unmapped -- "
    "so this window opens completely empty and nothing reports an error.\n\n"
    "Use a different Python:\n\n"
    "    brew install python@3.12 python-tk@3.12\n\n"
    "\"Pulse Control.app\" picks a suitable one by itself; you only see this when "
    "running pulse_control.py directly.")


def main():
    root = tk.Tk()
    if tk.TkVersion < TK_MIN:
        # refuse rather than draw a blank rectangle: an empty window with no
        # error is the single most confusing thing this app could do, and it is
        # exactly what shipped before this check existed.
        msg = TK_TOO_OLD % tk.TkVersion
        sys.stderr.write("\n" + msg + "\n\n")
        if "--selftest" not in sys.argv:      # the dialog is modal; keep it out of scripts
            try:
                root.withdraw()
                messagebox.showerror(APP, msg)
            except Exception:
                pass
        raise SystemExit(2)
    if "--selftest" in sys.argv:              # build it for real, check it LAID OUT, exit
        app = App(root)
        deadline = time.time() + 2.0      # let the window map and the sash settle
        while time.time() < deadline:
            root.update()
            if getattr(app, "panes", None) is not None and app.panes.winfo_height() > 420:
                root.update_idletasks()
                break
            time.sleep(0.02)
        root.update_idletasks()
        bad = []
        # "the widgets exist" is not the test. Tk 8.5 builds every one of them
        # and then lays none of them out, which is how a blank window shipped.
        # Measure what actually got drawn.
        def walk(w):
            for c in w.winfo_children():
                if c.winfo_class() in ("TNotebook", "TPanedwindow", "Text"):
                    floor = 120 if c.winfo_class() == "Text" else 200
                    if not c.winfo_ismapped() or c.winfo_width() < 200 or c.winfo_height() < floor:
                        bad.append("%s %dx%d mapped=%s" % (c.winfo_class(), c.winfo_width(),
                                                           c.winfo_height(), c.winfo_ismapped()))
                walk(c)
        walk(root)
        tabs = [t for t in app.tabs] if hasattr(app, "tabs") else []
        root.destroy()
        if bad:
            print("selftest FAILED -- built but never laid out:")
            for b in bad:
                print("   ", b)
            raise SystemExit(1)
        print("selftest ok (Tk %s, panes laid out)" % tk.TkVersion)
        return
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
