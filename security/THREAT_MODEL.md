# Threat Model & Security Audit — Edge-AI Remote Health Monitoring
**Author:** Zayed Alnuami (S4)  
**Scope:** Fleet Master API (`anomaly/fleet.py`), Supervision Dashboard (`anomaly/static/fleet.html`), and ESP32 HTTP endpoints (`pulse_headless/pulse_headless.ino`, `pulse_display/pulse_display.ino`).

---

## 1. System Assets & Trust Boundaries

| Asset | Sensitivity | Why it matters |
|---|---|---|
| **Detection Thresholds & Tuning (`/api/tuning`)** | Safety-Critical | Raising `k_sigma` or smoothing constants suppresses stress flags; lowering them floods the supervisor with false alarms. |
| **Subject Roster & Baselines (`data/pulse.db`)** | Privacy & Integrity | Contains participant identifiers, personal calm baselines, recorded sessions, and flag history. |
| **Protocol & Calibration Control (`/api/calib`, `/api/protocol`)** | Experimental Integrity | Starting, advancing, or aborting a session corrupts experimental ground-truth phase marks. |
| **On-Board Verdict Endpoint (`GET /flag` on ESP32)** | Operational Integrity | Drives the wearable's display/verdict state when a host is present. |
| **Audit Trail (`data/audit_log.jsonl`)** | Forensic Non-Repudiation | Records who changed thresholds, deleted subjects, or acknowledged flags, and when. |

**Adversary Model:**
1. **LAN / Hotspot Peer:** Any unauthenticated device on the same Wi-Fi network or mobile hotspot (`0.0.0.0:8002`).
2. **Cross-Origin Web Page (CSRF):** A third-party tab open in the supervisor's browser issuing cross-origin `POST` requests to `http://localhost:8002`.
3. **Unprivileged Operator (Insider):** A safety reviewer who is authorized to view vitals and acknowledge alerts, but should not alter system thresholds or delete subjects.
4. **Post-Incident Log Tamperer:** Someone who edits or deletes lines from the audit file on disk to hide unauthorized actions.

---

## 2. STRIDE Vulnerability Findings (Code Audit)

| ID | STRIDE Category | Component & Location | Vulnerability Description | Status |
|---|---|---|---|---|
| **F1** | **Elevation of Privilege / Tampering** | `anomaly/fleet.py` (`build_app`, L1100–1283, L1363) | Fleet Master binds to `0.0.0.0:8002` with zero authentication on all 11 write routes (`POST /api/tuning`, `DELETE /api/subjects/{sid}`, `DELETE /api/devices/{id}/events`, `POST /api/calib/*`, `POST /api/protocol/*`). | **Mitigated (`anomaly/security.py`)** |
| **F2** | **Information Disclosure** | `anomaly/fleet.py` (L1086, L1115, L1158, L1221) | Subject identities, physiological baselines, and flag histories are readable by any host on the LAN without credentials. | **Mitigated (`anomaly/security.py`)** |
| **F3** | **Spoofing (CSRF)** | `anomaly/fleet.py` (L1100–1283) | Write endpoints do not validate `Origin` / `Host` headers; a malicious webpage visited by the operator can trigger state-changing requests against `localhost:8002`. | **Mitigated (`anomaly/security.py`)** |
| **F4** | **Repudiation** | `anomaly/fleet.py` / `anomaly/db.py` | Tuning changes, subject deletions, and unauthorized requests leave no tamper-evident forensic trail; a plain log file can be edited after the fact. | **Mitigated (`anomaly/security.py`)** |
| **F5** | **Spoofing / Tampering** | `pulse_headless.ino` (L1250–1300) | ESP32 `GET /flag` mutates board state (`hostFlag`, `hostLevel`, `hostBpm`, `hostSpo2`, `hostSubject`) over unauthenticated HTTP GET. | **Documented (Firmware Future Work)** |
| **F6** | **Tampering / DoS** | `pulse_headless.ino` (L1306–1379) | ESP32 `POST /wifi` accepts unauthenticated network changes on the LAN; `GET /calib` interpolates query parameter `p` into JSON via `snprintf` without escaping. | **Documented (Firmware Future Work)** |

---

## 3. Implemented Controls (`anomaly/security.py`)

### 3.1 Opt-In Role-Based Access Control (`--secure`)
- Generates 128-bit CSPRNG tokens (`secrets.token_urlsafe(16)`) at startup for two roles:
  - **`admin`**: Full read/write access across all `/api/*` endpoints.
  - **`reviewer`**: Read access (`GET /api/*`) plus alert acknowledgment (`POST /api/events/{eid}/ack`); all other mutations (`POST /api/tuning`, `DELETE /api/subjects/{sid}`, calibration, protocol control) return `HTTP 403 Forbidden`.
- Tokens are verified in constant time via `hmac.compare_digest` to prevent timing side-channel leaks.
- **Backward Compatibility:** Off by default so existing scripts and `Pulse Control.exe` workflows remain unchanged unless `--secure` is passed.

### 3.2 Cross-Origin Write Guard (CSRF Defense)
- On every state-changing method (`POST`, `PUT`, `PATCH`, `DELETE`), if an `Origin` header is present and its host does not match the request `Host` header, the middleware rejects the request with `HTTP 403 Forbidden` even if a valid token is present.

### 3.3 SHA-256 Hash-Chained Audit Log (`data/audit_log.jsonl`)
- Every `/api/*` write and every denied request is appended to `data/audit_log.jsonl`.
- Each record $i$ stores `prev = H(record_{i-1})` (with `GENESIS = "0" * 64`) and `hash = SHA256(canonical_json(record_i))`.
- Modifying a single field, deleting a row, or reordering entries invalidates the cryptographic chain and is caught by:
  ```bash
  python -m anomaly.security verify
  ```

---

## 4. Residual Risks & Limitations (Honest Scope)
1. **Plain HTTP on LAN:** Traffic between browser, master, and ESP32 uses plain HTTP/WebSocket without TLS; a passive packet sniffer on an open network could observe headers. Mitigated in demos by using a WPA2/WPA3 hotspot.
2. **Board-Side Endpoints (F5, F6):** Securing `GET /flag` and `POST /wifi` on the ESP32 requires shared-secret HMAC verification in firmware (`pulse_headless.ino` / `pulse_display.ino`), which is left as future work to avoid altering validated sensing firmware during hardware bring-up.
