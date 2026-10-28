"""Tests for anomaly/security.py -- no TensorFlow, no board, throwaway log."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from anomaly.security import Security, install, verify

TOK = {"admin": "admin-tok", "reviewer": "rev-tok"}


def make(tmp_path, secure=True):
    app = FastAPI()

    @app.post("/api/tuning")
    async def tuning():
        return {"ok": True}

    @app.delete("/api/subjects/{sid}")
    async def delete(sid: int):
        return {"ok": True}

    @app.post("/api/events/{eid}/ack")
    async def ack(eid: int):
        return {"ok": True}

    @app.get("/api/subjects")
    async def subjects():
        return {"subjects": []}

    @app.get("/")
    async def index():
        return {"page": True}

    log = str(tmp_path / "audit.jsonl")
    sec = Security(secure=secure, log_path=log, tokens=TOK if secure else None)
    install(app, sec)
    return TestClient(app), sec, log


def h(tok=None, origin=None):
    d = {}
    if tok:
        d["X-Pulse-Token"] = tok
    if origin:
        d["Origin"] = origin
    return d


def test_open_mode_unchanged(tmp_path):
    c, _, log = make(tmp_path, secure=False)
    assert c.post("/api/tuning").status_code == 200
    assert c.get("/api/subjects").status_code == 200
    assert verify(log)[0]                       # still audited


def test_no_or_bad_token_401(tmp_path):
    c, sec, _ = make(tmp_path)
    assert c.post("/api/tuning").status_code == 401
    assert c.get("/api/subjects").status_code == 401
    assert c.post("/api/tuning", headers=h("wrong")).status_code == 401
    assert sec.denied == 3


def test_page_stays_public(tmp_path):
    c, _, _ = make(tmp_path)
    assert c.get("/").status_code == 200


def test_admin_full(tmp_path):
    c, _, _ = make(tmp_path)
    assert c.post("/api/tuning", headers=h("admin-tok")).status_code == 200
    assert c.delete("/api/subjects/1", headers=h("admin-tok")).status_code == 200


def test_reviewer_limited(tmp_path):
    c, _, _ = make(tmp_path)
    assert c.get("/api/subjects", headers=h("rev-tok")).status_code == 200
    assert c.post("/api/events/5/ack", headers=h("rev-tok")).status_code == 200
    assert c.post("/api/tuning", headers=h("rev-tok")).status_code == 403
    assert c.delete("/api/subjects/1", headers=h("rev-tok")).status_code == 403


def test_cross_origin_write_blocked(tmp_path):
    c, _, _ = make(tmp_path)
    r = c.post("/api/tuning", headers=h("admin-tok", "http://evil.example"))
    assert r.status_code == 403
    ok = c.post("/api/tuning", headers=h("admin-tok", "http://testserver"))
    assert ok.status_code == 200


def test_audit_chain_detects_edit(tmp_path):
    c, _, log = make(tmp_path)
    for _ in range(3):
        c.post("/api/tuning", headers=h("admin-tok"))
    assert verify(log) == (True, 3, "intact: 3 entries")
    lines = open(log).read().splitlines()
    e = json.loads(lines[1]); e["result"] = 200; e["role"] = "attacker"
    lines[1] = json.dumps(e)
    open(log, "w").write("\n".join(lines) + "\n")
    ok, _, msg = verify(log)
    assert not ok and "line 2" in msg


def test_audit_chain_detects_deletion(tmp_path):
    c, _, log = make(tmp_path)
    for _ in range(3):
        c.post("/api/tuning", headers=h("admin-tok"))
    lines = open(log).read().splitlines()
    del lines[1]
    open(log, "w").write("\n".join(lines) + "\n")
    assert not verify(log)[0]


def test_status_endpoint(tmp_path):
    c, _, _ = make(tmp_path)
    c.post("/api/tuning")                        # denied
    s = c.get("/api/security", headers=h("rev-tok")).json()
    assert s["secure"] and s["denied"] == 1 and s["chain_ok"]


def test_rate_limit_lockout_429(tmp_path):
    c, sec, _ = make(tmp_path)
    sec.max_failures = 3
    for _ in range(3):
        assert c.post("/api/tuning", headers=h("bad")).status_code == 401
    # 4th attempt is locked out with 429 even if a valid token is presented
    assert c.post("/api/tuning", headers=h("admin-tok")).status_code == 429

