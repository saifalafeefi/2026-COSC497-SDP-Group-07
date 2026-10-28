"""Live before/after exploit & tamper-detection demonstration for SDP2.

Runs 5 control-plane attacks against the Fleet Master API in Open Mode (default)
and Secure Mode (`--secure`), then demonstrates SHA-256 hash-chain tamper
detection on the audit log. Uses a temporary directory so real data is untouched.

Usage:
    python security/attack_demo.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from anomaly.security import Security, install, verify

TOKENS = {"admin": "adm_demo_9f8a7b6c", "reviewer": "rev_demo_1a2b3c4d"}


def build_demo_master(log_path: str, secure: bool) -> tuple[TestClient, Security]:
    """Mount the Fleet Master's representative control routes with Security."""
    app = FastAPI()
    state = {"k_sigma": 2.5, "subjects": {1: "S01"}, "acked": []}

    @app.get("/api/subjects")
    async def get_subjects():
        return {"subjects": list(state["subjects"].values())}

    @app.post("/api/tuning")
    async def set_tuning():
        state["k_sigma"] = 99.0  # attacker suppresses stress alerts
        return {"ok": True, "k_sigma": state["k_sigma"]}

    @app.delete("/api/subjects/{sid}")
    async def delete_subject(sid: int):
        state["subjects"].pop(sid, None)
        return {"ok": True, "deleted": sid}

    @app.post("/api/events/{eid}/ack")
    async def ack_event(eid: int):
        state["acked"].append(eid)
        return {"ok": True, "acked": eid}

    sec = Security(secure=secure, log_path=log_path,
                   tokens=TOKENS if secure else None)
    install(app, sec)
    return TestClient(app), sec


ATTACKS = [
    (
        "A1 - Unauthenticated threshold tampering",
        "POST", "/api/tuning", {},
        "Attacker on Wi-Fi sets k_sigma=99 to silence all worker stress alarms",
    ),
    (
        "A2 - Unauthenticated subject deletion",
        "DELETE", "/api/subjects/1", {},
        "Attacker wipes Subject S01 and their recorded sessions/baselines",
    ),
    (
        "A3 - Unauthenticated roster & health data read",
        "GET", "/api/subjects", {},
        "Attacker scrapes subject list and physiological baseline metadata",
    ),
    (
        "A4 - Reviewer privilege escalation",
        "POST", "/api/tuning", {"X-Pulse-Token": TOKENS["reviewer"]},
        "Safety reviewer (read/ack role) attempts to alter detection tuning",
    ),
    (
        "A5 - Cross-Site Request Forgery (CSRF)",
        "POST", "/api/tuning",
        {"X-Pulse-Token": TOKENS["admin"], "Origin": "http://evil.example"},
        "Malicious browser tab fires cross-origin write to localhost:8002",
    ),
]


def run_suite(client: TestClient) -> list[tuple[str, int, str]]:
    out = []
    for name, method, path, headers, _ in ATTACKS:
        r = client.request(method, path, headers=headers)
        verdict = "VULNERABLE" if r.status_code == 200 else f"BLOCKED ({r.status_code})"
        out.append((name, r.status_code, verdict))
    return out


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        open_log = os.path.join(tmp, "open_audit.jsonl")
        sec_log = os.path.join(tmp, "secure_audit.jsonl")

        open_client, _ = build_demo_master(open_log, secure=False)
        sec_client, sec = build_demo_master(sec_log, secure=True)

        before = run_suite(open_client)
        after = run_suite(sec_client)

        # A6: Brute-force flood (10 failed attempts -> 11th triggers 429 lockout)
        for _ in range(10):
            open_client.post("/api/tuning", headers={"X-Pulse-Token": "guess"})
            sec_client.post("/api/tuning", headers={"X-Pulse-Token": "guess"})
        r_open_bf = open_client.post("/api/tuning", headers={"X-Pulse-Token": "guess"})
        r_sec_bf = sec_client.post("/api/tuning", headers={"X-Pulse-Token": "guess"})
        before.append(("A6 - Token brute-force flood (11th attempt)",
                       r_open_bf.status_code,
                       "VULNERABLE" if r_open_bf.status_code == 200 else f"BLOCKED ({r_open_bf.status_code})"))
        after.append(("A6 - Token brute-force flood (11th attempt)",
                      r_sec_bf.status_code,
                      f"LOCKED OUT ({r_sec_bf.status_code})"))

        # Clear lockout for legitimate role check demonstration
        sec._failures.clear()

        # Also show that legitimate role actions still succeed in secure mode
        rev_ack = sec_client.post(
            "/api/events/42/ack",
            headers={"X-Pulse-Token": TOKENS["reviewer"]},
        ).status_code
        adm_tune = sec_client.post(
            "/api/tuning",
            headers={"X-Pulse-Token": TOKENS["admin"], "Origin": "http://testserver"},
        ).status_code

        print("\n" + "=" * 78)
        print("  SDP2 SECURITY BENCHMARK: FLEET MASTER CONTROL-PLANE ATTACKS")
        print("=" * 78)
        print(f"  {'Attack Scenario':<44} | {'Default Mode':<12} | {'--secure Mode':<14}")
        print("-" * 78)
        for (name, _, v_open), (_, _, v_sec) in zip(before, after):
            print(f"  {name:<44} | {v_open:<12} | {v_sec:<14}")
        print("-" * 78)
        print(f"  Legitimate Reviewer alert ack (POST /api/events/42/ack) : HTTP {rev_ack} (ALLOWED)")
        print(f"  Legitimate Admin tuning write (POST /api/tuning)        : HTTP {adm_tune} (ALLOWED)")

        print("\n" + "=" * 78)
        print("  SHA-256 HASH-CHAINED AUDIT LOG INTEGRITY VERIFICATION")
        print("=" * 78)
        ok_before, n_entries, msg_before = verify(sec_log)
        print(f"  1. Before tampering : {'PASS' if ok_before else 'FAIL'} ({msg_before})")

        # Simulate an attacker editing line 2 on disk to hide their blocked 401/403
        with open(sec_log, encoding="utf-8") as f:
            lines = f.read().splitlines()
        forged = json.loads(lines[1])
        forged["result"] = 200
        forged["reason"] = "forged_ok"
        lines[1] = json.dumps(forged, sort_keys=True)
        with open(sec_log, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        ok_after, _, msg_after = verify(sec_log)
        print(f"  2. After editing L2 : {'PASS' if ok_after else 'DETECTED'} ({msg_after})")
        print("=" * 78 + "\n")

        assert all(s == 200 for _, s, _ in before)
        assert all(s in (401, 403, 429) for _, s, _ in after)
        assert rev_ack == 200 and adm_tune == 200
        assert ok_before and not ok_after
    return 0


if __name__ == "__main__":
    sys.exit(main())
