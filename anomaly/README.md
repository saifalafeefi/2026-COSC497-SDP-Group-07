# anomaly — one-class stress detection (O1/O2)

the ML-method track: train on **normal** wrist-PPG, flag deviations (stress).
built on WESAD, evaluated with sensitivity-first, subject-wise metrics.

> **just want the commands?** → [`../COMMANDS-macos.md`](../COMMANDS-macos.md) or
> [`../COMMANDS-windows.md`](../COMMANDS-windows.md) — dashboards, eval,
> model-improvement flags, the export→compress deploy pipeline, calibration.

## what's here

| File | Status | What it does |
|---|---|---|
| `wesad.py` | ✅ | load a subject → wrist BVP @ 64 Hz + aligned labels → clean windows |
| `metrics.py` | ✅ | PR-AUC, ROC-AUC, recall@90% specificity (pure numpy, pre-committed) |
| `splits.py` | ✅ | leave-one-subject-out / k-fold-by-subject (no subject leakage) |
| `features.py` | ✅ | HR / HRV / spectral features per window, for the baseline |
| `baseline.py` | ✅ | Mahalanobis one-class detector (the number to beat) |
| `autoencoder.py` | ✅ | 1D-conv autoencoder, reconstruction error = anomaly score (O1); optional `--denoise` path (noise-robust) |
| `run.py` | ✅ | LOSO harness: fit on others' normal → score held-out → metrics |
| `calibrate.py` | ✅ | per-user calibration: zero-shot vs device-calibrated delta (O6) |
| `compress.py` | ✅ | TFLite float/int8 conversion + compression accuracy-cost (O7) |
| `ssl.py` | ✅ | self-supervised contrastive encoder + embedding-space scorer (O2) |
| `export.py` | ✅ | train one deployable autoencoder on all calm data → `saved/` |
| `infer.py` | ✅ | load the saved model → score / level / flag one live window |
| `serve.py` + `static/` | ✅ | **live dashboards**: dev view (`/`, model-vs-truth scorecard) + Pulse Watch (`/watch`) on one `/ws` |
| `virtual_board.py` | ✅ | **a WESAD subject as a board**: speaks the ESP32's `/health` + `/ws` + `/flag`, so `fleet.py` runs a whole roster with no hardware |
| `make_plots.py` | ✅ | regenerate the result figures (`fig1/2/3_*.png`) |

## run it

from the repo root (`./run` finds the project's interpreter — no venv to activate):

```bash
./run anomaly.run --model baseline                            # statistical baseline
./run anomaly.run --model ae --epochs 30                      # O1 autoencoder (original)
./run anomaly.run --model ae --bottleneck 256 --ch-cap 32     # DEPLOYED: real latent, ESP32-sized
./run anomaly.run --model ae --bottleneck 256 --denoise 0.15  # + noise robustness
./run anomaly.run --model ssl --epochs 50                     # O2 self-supervised encoder
./run anomaly.run --model baseline --max-subjects 3           # quick check
```

**model-improvement levers (compare against the plain `ae` before deploying):**

- `--bottleneck DIM` — the main fix. the original AE has **no real bottleneck**: for a
  3,840-sample window the conv latent is 240×128 = 30,720 values, ~8× *larger* than the
  input. that over-complete AE can near-copy anything, so stress reconstructs almost as
  well as calm and barely separates. a compressed latent makes the model learn only the
  normal-pulse manifold, so stress stands out. **256 won the sweep** — 32 / 64 / 128 all
  score lower ([RESULTS.md](RESULTS.md)).
- `--ch-cap N` — caps the encoder's channels (128 → 32). costs a little accuracy
  (PR-AUC 0.736 → 0.706) and buys the 4 MB that fits an ESP32-S3.
  **`--bottleneck 256 --ch-cap 32` is the deployed model.**
- `--denoise SIGMA` — trains on noise-corrupted calm windows (amplitude drift + baseline
  wander + jitter, clean target) so it keys on pulse *shape* and transfers better to noisy
  hardware. orthogonal to the bottleneck; can stack.

`0` (default for both) reproduces the validated baseline. once a config wins on PR-AUC /
recall@90% spec, train the deployable copy with the same flags, e.g.
`./run anomaly.export --bottleneck 256 --ch-cap 32`, then re-run `anomaly.compress`.

> reality check: these should sharpen separation, but **zero-shot cross-subject**
> recall@90% spec (~0.50) won't jump to 0.90 — the per-subject variance (PR-AUC 0.99→0.30)
> is the documented domain-transfer finding, not a bug a model tweak removes. the real route
> to high *per-user* recall is per-user calibration (0.46→0.69, `calibrate.py`) on the
> person's own data — which lands once the hardware/collection is in.

each held-out subject is scored by a detector fit only on the OTHER subjects'
baseline windows; results print per-subject and as mean ± std.

## live dashboard

```bash
./run anomaly.export               # train + save the deployable model (once)
./run anomaly.serve                # → http://localhost:8001
./run anomaly.serve --subject S17  # try a different subject
```

streams a WESAD subject's wrist BVP, runs the saved autoencoder on a rolling
60 s window (re-scored every second, EMA-smoothed), and shows a live anomaly
level + calm/stress flag. flag threshold = 90% specificity on calm. default
subject S5 sits low during calm and pegs the level (flagged) through the stress
(TSST) segment; S17 and S7 are also clean demos.

two front-ends share the one server + `/ws`:

- **`/`** (alias **`/watch`**) — the team's **Pulse Watch** product UI (Zayed, `pulse/`),
  the default view, running on this live pipeline. it auto-connects to `/ws` and, if that
  fails, **stays offline and says so** — it used to fall back to a built-in mock replay,
  which was indistinguishable from working. carries the Cautious / Balanced / Strict
  preset.
- **`/dev`** — developer dashboard. scores the model's flag against the WESAD
  ground-truth label **live** (TP / FP / FN / TN tally + precision / recall), with
  the true-stress period shaded behind the anomaly-level chart. this is the answer
  to "how do we know a flag is the model's decision, not the dataset's label" — the
  flag is the model output, the label is the truth, and you watch them agree/disagree.

**tunable sensitivity (O4):** both front-ends control the model's operating point —
the dev slider and the watch preset send `set_sensitivity` over the WS; the server
maps it to the flag threshold (`Engine.set_sensitivity` → `infer.score_for_level`)
and broadcasts a `thr` update, so the slider, preset, threshold line and live
precision/recall stay in sync across both views. higher sensitivity = lower
threshold = flags more (more recall, more false alarms). the threshold is global to
the running engine — one model, all viewers share the operating point.

it also shows a human-readable **heart rate (BPM)** tile + trend line (estimated
from the pulse via `pipeline/vitals.py`) — on S5, calm ≈ 77 BPM rises to ≈ 97 BPM
under stress, which makes the flag legible to a non-expert. BPM is context only;
the model still flags on the learned pulse *shape*, not on a heart-rate threshold.

## design choices

- **signal:** wrist BVP @ 64 Hz only — the consumer-grade analogue of our cheap
  sensor. chest ECG/RespiBAN is ignored on purpose (it would cheat on quality).
- **task:** one-class — train on `baseline`, evaluate stress (`TSST`) as the
  positive. amusement / meditation are left out of the binary task by default
  (`to_binary`); fold them into "normal" only deliberately.
- **windows:** fixed length, pure (a window must sit inside one condition).
- **metric, not accuracy:** stress is rare (~11 min/subject), so report PR-AUC
  and recall @ fixed specificity on **subject-wise** splits.

## quick look

```bash
./run anomaly/wesad.py            # window counts per condition, all 15 subjects
./run anomaly/wesad.py S2 --win 60 --step 5
```

(WESAD itself is ~17 GB and gitignored. it is a direct download — `COMMANDS-macos.md` section 1 has the one-line `curl` for it — and unzips straight into `WESAD/`.)
