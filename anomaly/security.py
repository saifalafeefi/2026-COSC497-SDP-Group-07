"""Opt-in access control and a tamper-evident audit log for the fleet master.

Off by default: `python -m anomaly.fleet` behaves exactly as before. With
`--secure`, every /api/* request needs an `X-Pulse-Token` header:

    admin     everything
    reviewer  read-only, plus acknowledging flags (POST /api/events/{id}/ack)

Missing/wrong token -> 401, right token but wrong role -> 403, and any write
whose Origin is not this master -> 403 (blocks cross-site request forgery
from a page the supervisor happens to have open). The page itself (`/`) stays
public; it asks for a token on the first 401.

Every /api/* write -- allowed or refused, secure mode or not -- is appended to
data/audit_log.jsonl. Each line carries the SHA-256 of the line before it, so
editing or deleting an entry breaks the chain:

    python -m anomaly.security verify [path]

Scope, stated plainly: this protects the PC master only. The boards' own HTTP
endpoints (/flag, /wifi) are still unauthenticated, and nothing here is
encrypted (plain HTTP on the LAN). See security/THREAT_MODEL.md.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOG = os.path.join(os.path.dirname(HERE), "data", "audit_log.jsonl")
GENESIS = "0" * 64

# the only write a reviewer may make
_REVIEWER_WRITES = [re.compile(r"^/api/events/\d+/ack$")]
_WRITE = {"POST", "PUT", "PATCH", "DELETE"}


def _digest(entry: dict) -> str:
    body = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class AuditLog:
    """Append-only JSONL, each entry chained to the previous by SHA-256."""

    def __init__(self, path: str = DEFAULT_LOG, keep: int = 50):
        self.path = path
        self._lock = threading.Lock()
        self.recent: deque = deque(maxlen=keep)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._prev = GENESIS
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        e = json.loads(line)
                        self._prev = e.get("hash", self._prev)
                        self.recent.append(e)

    def append(self, **fields) -> dict:
        with self._lock:
            entry = {"ts": round(time.time(), 3), **fields, "prev": self._prev}
            entry["hash"] = _digest(entry)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, sort_keys=True) + "\n")
            self._prev = entry["hash"]
            self.recent.append(entry)
            return entry


def verify(path: str = DEFAULT_LOG) -> tuple[bool, int, str]:
    """(intact, entries checked, message). Detects edits, deletions, reorders."""
    if not os.path.exists(path):
        return True, 0, "no audit log yet"
    prev, n = GENESIS, 0
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except ValueError:
                return False, n, f"TAMPERED at line {lineno}: not valid JSON"
            claimed = e.pop("hash", None)
            if e.get("prev") != prev:
                return False, n, f"TAMPERED at line {lineno}: chain broken (entry missing or reordered)"
            if claimed != _digest(e):
                return False, n, f"TAMPERED at line {lineno}: contents edited"
            prev, n = claimed, n + 1
    return True, n, f"intact: {n} entries"


class Security:
    def __init__(self, secure: bool = False, log_path: str = DEFAULT_LOG,
                 tokens: dict | None = None, max_failures: int = 10,
                 window_s: float = 60.0):
        self.secure = secure
        self.log = AuditLog(log_path)
        self.tokens = tokens or ({"admin": secrets.token_urlsafe(16),
                                  "reviewer": secrets.token_urlsafe(16)}
                                 if secure else {})
        self.max_failures = max_failures
        self.window_s = window_s
        self._failures: dict[str, deque] = {}
        self.allowed = 0
        self.denied = 0

    def _rate_limited(self, ip: str, now: float) -> bool:
        q = self._failures.get(ip)
        if not q:
            return False
        cutoff = now - self.window_s
        while q and q[0] < cutoff:
            q.popleft()
        return len(q) >= self.max_failures

    def _record_failure(self, ip: str, now: float) -> None:
        q = self._failures.setdefault(ip, deque())
        q.append(now)

    def role_for(self, token: str | None) -> str | None:
        if not token:
            return None
        for role, t in self.tokens.items():
            if hmac.compare_digest(token.encode(), t.encode()):   # constant time
                return role
        return None

    def decide(self, method: str, path: str, token: str | None,
               origin: str | None, host: str | None,
               ip: str = "?") -> tuple[int, str, str | None]:
        """(status, reason, role). status 0 = allow."""
        if not self.secure:
            return 0, "open mode", None
        now = time.time()
        if self._rate_limited(ip, now):
            return 429, "rate limit exceeded (brute-force lockout)", None
        role = self.role_for(token)
        if role is None:
            self._record_failure(ip, now)
            return 401, "missing or invalid token", None
        if method in _WRITE:
            if origin and host and origin.split("://", 1)[-1] != host:
                self._record_failure(ip, now)
                return 403, f"cross-origin write from {origin}", role
            if role == "reviewer" and not any(p.match(path) for p in _REVIEWER_WRITES):
                self._record_failure(ip, now)
                return 403, "reviewer role cannot change this", role
        return 0, "ok", role

    def status(self, token: str | None = None) -> dict:
        ok, n, msg = verify(self.log.path)
        role = self.role_for(token) if self.secure else "open"
        return {"secure": self.secure, "role": role,
                "allowed": self.allowed, "denied": self.denied,
                "chain_ok": ok, "chain": msg, "entries": n,
                "recent": list(self.log.recent)[-20:]}


def install(app, sec: Security):
    """Attach the guard and GET /api/security to a FastAPI app."""
    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def guard(request, call_next):
        path = request.url.path
        if path == "/api/security":
            return JSONResponse(sec.status(request.headers.get("x-pulse-token")))
        if not path.startswith("/api/"):
            return await call_next(request)
        method = request.method
        ip = request.client.host if request.client else "?"
        status, reason, role = sec.decide(
            method, path, request.headers.get("x-pulse-token"),
            request.headers.get("origin"), request.headers.get("host"), ip=ip)
        if status:
            sec.denied += 1
            sec.log.append(ip=ip, method=method, path=path, role=role,
                           result=status, reason=reason)
            return JSONResponse({"error": f"{status}: {reason}"}, status_code=status)
        resp = await call_next(request)
        if method in _WRITE:
            sec.allowed += 1
            sec.log.append(ip=ip, method=method, path=path, role=role,
                           result=resp.status_code, reason=reason)
        return resp

    return app


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "verify":
        ok, n, msg = verify(sys.argv[2] if len(sys.argv) > 2 else DEFAULT_LOG)
        print(("OK  " if ok else "FAIL ") + msg)
        return 0 if ok else 1
    print("usage: python -m anomaly.security verify [path]")
    return 2


if __name__ == "__main__":
    sys.exit(main())
