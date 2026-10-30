"""PPG-DaLiA: does a detector stay quiet when people move about normally?

WESAD's calm and stress are both seated, so it cannot show what a detector
does on stairs. PPG-DaLiA (UCI 495, CC BY 4.0; Reiss et al. 2019) is 15
people wearing the same Empatica E4 wrist band for ~2.5 h of daily life --
sitting, stairs, table soccer, cycling, driving, lunch, walking, desk work --
with no stress protocol. every flag on it is a false alarm, and the activity
track says what the person was doing when it fired.

every detector is fitted on WESAD calm only (the deployment condition: built
on other people, never on this dataset) and thresholded on each person's own
SITTING windows -- "calm" for this person -- at 90% specificity. the question
is how much of each activity it then flags.

    hr        heart rate up, the board's rule
    ctx-acc   anomaly.context's model, accelerometer as context
    ctx-pulse the same, motion read off the pulse (runs on our rig)
    wave      the master's int8 waveform autoencoder

the release ships Python-2 pickles. a pickle can run code, so they are read
with an unpickler that only rebuilds numpy arrays and plain containers.

    python -m anomaly.dalia                 # unzips on first run, caches features
    python -m anomaly.dalia --live          # also replay through the live flag rule
    python -m anomaly.dalia --mixed         # context models with daily activity as normal
"""
from __future__ import annotations

import argparse
import io
import os
import pickle
import zipfile

import numpy as np

from .wesad import FS, FS_ACC, REPO_ROOT

DALIA_DIR = os.path.join(REPO_ROOT, "PPG_DaLiA")
FS_ACT = 4
ACTIVITY = {1: "sitting", 2: "stairs", 3: "table soccer", 4: "cycling", 5: "driving",
            6: "lunch", 7: "walking", 8: "working"}
SUBJECTS = ["S%d" % i for i in range(1, 16)]
WIN_S, STEP_S = 60, 5


class _SafeUnpickler(pickle.Unpickler):
    """rebuild numpy arrays and builtin containers; refuse everything else."""
    OK = {("numpy.core.multiarray", "_reconstruct"), ("numpy._core.multiarray", "_reconstruct"),
          ("numpy.core.multiarray", "scalar"), ("numpy._core.multiarray", "scalar"),
          ("numpy", "ndarray"), ("numpy", "dtype"), ("collections", "OrderedDict"),
          ("builtins", "dict"), ("builtins", "list"), ("builtins", "tuple"),
          ("__builtin__", "dict"), ("__builtin__", "list"), ("__builtin__", "tuple")}

    def find_class(self, module, name):
        if (module, name) not in self.OK:
            raise pickle.UnpicklingError("refusing %s.%s in a downloaded pickle" % (module, name))
        mod = {"numpy._core.multiarray": "numpy.core.multiarray", "__builtin__": "builtins"}.get(module, module)
        return super().find_class(mod, name)


def _find_pickles(root: str) -> dict:
    """subject -> path of S<n>.pkl, unzipping nested archives once."""
    for _ in range(3):
        found = {}
        for d, _, files in os.walk(root):
            for f in files:
                if f.endswith(".pkl") and f[:-4] in SUBJECTS:
                    found[f[:-4]] = os.path.join(d, f)
        if len(found) == len(SUBJECTS):
            return found
        zips = [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs if f.endswith(".zip")]
        done = os.path.join(root, ".unzipped")
        seen = set(open(done).read().split("\n")) if os.path.exists(done) else set()
        todo = [z for z in zips if z not in seen]
        if not todo:
            return found
        for z in todo:
            print("  unzipping %s…" % os.path.relpath(z, root), flush=True)
            with zipfile.ZipFile(z) as zf:
                for m in zf.namelist():           # no absolute paths, no ../ escapes
                    if os.path.isabs(m) or ".." in m.replace("\\", "/").split("/"):
                        raise ValueError("unsafe path in %s: %s" % (z, m))
                zf.extractall(os.path.dirname(z))
            with open(done, "a") as f:
                f.write(z + "\n")
    return found


def load_subject(path: str) -> dict:
    with open(path, "rb") as f:
        d = _SafeUnpickler(io.BufferedReader(f), encoding="latin1").load()
    w = d["signal"]["wrist"]
    return {"bvp": np.asarray(w["BVP"], np.float32).reshape(-1),
            "acc": np.asarray(w["ACC"], np.float32).reshape(-1, 3),
            "activity": np.asarray(d["activity"]).reshape(-1).astype(np.int8)}


def windows(d: dict):
    """60 s windows every 5 s over the whole recording. -> (bvp windows, acc
    windows, activity at the window's end, pure: one activity throughout)."""
    n = min(len(d["bvp"]) // FS, len(d["acc"]) // FS_ACC, len(d["activity"]) // FS_ACT)
    B, A, act, pure = [], [], [], []
    for e in range(WIN_S, n + 1, STEP_S):
        s = e - WIN_S
        B.append(d["bvp"][s * FS:e * FS])
        A.append(d["acc"][s * FS_ACC:e * FS_ACC])
        a = d["activity"][s * FS_ACT:e * FS_ACT]
        act.append(int(a[-1]))
        pure.append(bool(a.min() == a.max() and a[0] != 0))
    return np.asarray(B, np.float32), np.asarray(A, np.float32), np.asarray(act), np.asarray(pure)


def load_features(root: str = DALIA_DIR, with_wave: bool = True) -> dict:
    """subject -> dict(bvp_f, pulse_motion, acc_f, wave, act, pure), cached."""
    from .channels import acc_features
    from .context import pulse_motion
    from .features import extract_features
    cache_path = os.path.join(root, "_features_win60_step5.npz")
    if os.path.exists(cache_path):
        z = np.load(cache_path)
        return {s: {k.split("|", 1)[1]: z[k] for k in z.files if k.startswith(s + "|")}
                for s in SUBJECTS if s + "|act" in z.files}
    pk = _find_pickles(root)
    missing = [s for s in SUBJECTS if s not in pk]
    if missing:
        raise SystemExit("PPG-DaLiA pickles missing for %s under %s" % (missing, root))
    det = None
    if with_wave:
        from .infer import LiveAnomalyDetector
        det = LiveAnomalyDetector()
    out, flat = {}, {}
    for s in SUBJECTS:
        B, A, act, pure = windows(load_subject(pk[s]))
        F = np.array([extract_features(w, FS) for w in B], np.float32)
        r = {"bvp_f": F,
             "pulse_motion": np.array([pulse_motion(w, f) for w, f in zip(B, F)], np.float32),
             "acc_f": np.array([acc_features(a) for a in A], np.float32),
             "act": act, "pure": pure}
        if det is not None:
            r["wave"] = np.array([det.score(w) for w in B], np.float64)
        out[s] = r
        flat.update({"%s|%s" % (s, k): v for k, v in r.items()})
        print("  %s: %d windows, %.1f h" % (s, len(act), len(act) * STEP_S / 3600.0), flush=True)
    np.savez(cache_path, **flat)
    return out


# ------------------------------------------------------------------ detectors

def fit_detectors(names) -> dict:
    """name -> f(subject features) -> score per window. fitted on WESAD calm only."""
    from .channels import load_features as wesad_features
    from .context import SAVED, ContextAE, ContextScorer
    from .wesad import SUBJECTS as WESAD_SUBJECTS

    def hr_of(r):
        h = r["bvp_f"][:, 0].astype(np.float64)
        return np.where(np.isnan(h), np.nanmedian(h), h)

    out = {}
    if "hr" in names:
        out["hr"] = hr_of
    if "ctx-acc" in names:
        W = wesad_features(WESAD_SUBJECTS, 60.0, 5.0)
        calm = {s: W[s]["cond"] == 1 for s in W}
        hr = np.concatenate([W[s]["BVP"][calm[s], 0] for s in W]).astype(np.float64)
        mo = np.vstack([W[s]["ACC"][calm[s]] for s in W]).astype(np.float64)
        ae = ContextAE().fit(np.where(np.isnan(hr), np.nanmedian(hr), hr), mo)
        out["ctx-acc"] = lambda r: ae.score(hr_of(r), r["acc_f"].astype(np.float64))
    if "ctx-pulse" in names:
        sc = ContextScorer(SAVED)
        out["ctx-pulse"] = lambda r: sc.score(hr_of(r), r["pulse_motion"])
    if "wave" in names:
        out["wave"] = lambda r: r["wave"]
    return out


def live_flags(scores, mem_s: float = 180.0, k: float = 3.0) -> np.ndarray:
    """anomaly.fleet's live rule at 5 s ticks: EMA, the wearer's own recent
    p20 as centre and MAD as spread, flag after 5 s over / clear after 10 s under."""
    alpha = 1 - 0.8 ** STEP_S
    n_mem, n_min = int(mem_s / STEP_S), int(20 / STEP_S)
    rec, ema, flag, below, out = [], None, False, 0, []
    for r in scores:
        rec.append(r)
        rec = rec[-n_mem:]
        ema = r if ema is None else (1 - alpha) * ema + alpha * r
        if len(rec) < n_min:
            out.append(False)
            continue
        a = np.asarray(rec)
        c, med = np.percentile(a, 20), np.median(a)
        sp = max(1.4826 * np.median(np.abs(a - med)), 0.02 * abs(c), 1e-6)
        if (ema - c) / sp >= k:
            flag, below = True, 0
        else:
            below += 1
            if below >= 2:
                flag = False
        out.append(flag)
    return np.asarray(out)


def mixed(data, thin: int = 3):
    """the context models with daily activity counted as NORMAL.

    fitted on WESAD calm alone, a context model has never seen HR while
    walking, so it has nothing to discount stairs with. here its normal is
    DaLiA's whole day plus WESAD calm, and it is tested both ways, never on
    anyone it was fitted on:

      A. false alarms: each DaLiA person held out, through the live flag rule
      B. stress:       each WESAD person held out, PR-AUC / recall@90spec

    `thin` keeps every 3rd DaLiA window for fitting (5 s steps overlap 12x)."""
    from .channels import load_features as wesad_features
    from .context import ContextAE, load_pulse_motion
    from .metrics import summarize
    from .wesad import SUBJECTS as WS

    W = wesad_features(WS, 60.0, 5.0)
    wpm = load_pulse_motion(W)
    hr_of = lambda h: np.where(np.isnan(h), np.nanmedian(h), h).astype(np.float64)
    wmo = {"acc": {s: W[s]["ACC"] for s in W}, "pulse": wpm}
    dmo = {"acc": {s: data[s]["acc_f"] for s in data}, "pulse": {s: data[s]["pulse_motion"] for s in data}}

    def fit(dal, wes, kind):
        hr = np.concatenate([data[s]["bvp_f"][data[s]["act"] > 0][::thin, 0] for s in dal] +
                            [W[s]["BVP"][W[s]["cond"] == 1, 0] for s in wes])
        mo = np.vstack([dmo[kind][s][data[s]["act"] > 0][::thin] for s in dal] +
                       [wmo[kind][s][W[s]["cond"] == 1] for s in wes])
        return ContextAE(epochs=40, batch=256).fit(hr_of(hr), mo.astype(np.float64))

    print("\nA. daily life, each DaLiA person held out, live flag rule (3-min memory):\n")
    print("| detector | k | flagged time | false alarms / h | sitting / driving / working / walking |")
    print("|---|---|---|---|---|")
    scores = {"hr": {}, "ctx-acc (daily normal)": {}, "ctx-pulse (daily normal)": {}}
    for d in data:
        others = [s for s in data if s != d]
        h = hr_of(data[d]["bvp_f"][:, 0])
        scores["hr"][d] = h
        for kind in ("acc", "pulse"):
            ae = fit(others, WS, kind)
            scores["ctx-%s (daily normal)" % kind][d] = ae.score(h, dmo[kind][d].astype(np.float64))
        print("  %s done" % d, flush=True)
    for k in (3.0, 5.0):
        for m, S in scores.items():
            ft, fa, part = [], [], {1: [], 5: [], 8: [], 7: []}
            for d, v in S.items():
                fl = live_flags(v, k=k)
                ft.append(fl.mean())
                fa.append((np.sum(fl[1:] & ~fl[:-1]) + int(fl[0])) / (len(fl) * STEP_S / 3600.0))
                for q in part:
                    w = data[d]["act"] == q
                    if w.any():
                        part[q].append(fl[w].mean())
            print("| %s | %.0f | %.0f%% | %.1f | %s |" % (m, k, 100 * np.mean(ft), np.mean(fa),
                  " / ".join("%.0f%%" % (100 * np.mean(part[q])) for q in part)))

    print("\nB. WESAD stress, each WESAD person held out (fitted on all of DaLiA + other WESAD calm):\n")
    print("| detector | PR-AUC | recall@90% spec | flagged, busiest calm quarter |")
    print("|---|---|---|---|")
    res = {m: ([], [], []) for m in ("hr", "ctx-acc (daily normal)", "ctx-pulse (daily normal)")}
    for w in WS:
        y = (W[w]["cond"] == 2).astype(int)
        h = hr_of(W[w]["BVP"][:, 0])
        sc = {"hr": h}
        for kind in ("acc", "pulse"):
            ae = fit(list(data), [s for s in WS if s != w], kind)
            sc["ctx-%s (daily normal)" % kind] = ae.score(h, wmo[kind][w].astype(np.float64))
        act = W[w]["ACC"][:, 1]
        busy = (y == 0) & (act >= np.quantile(act[y == 0], 0.75))
        for m, v in sc.items():
            r = summarize(y, v)
            res[m][0].append(r["pr_auc"]); res[m][1].append(r["recall@90spec"])
            res[m][2].append(np.mean(v[busy] >= np.quantile(v[y == 0], 0.90)))
        print("  %s done" % w, flush=True)
    for m, (pr, rc, bz) in res.items():
        print("| %s | %.3f ± %.3f | %.3f | %.0f%% |" % (m, np.mean(pr), np.std(pr), np.mean(rc), 100 * np.mean(bz)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", nargs="+", default=["hr", "ctx-acc", "ctx-pulse", "wave"])
    ap.add_argument("--live", action="store_true", help="also replay through the live flag rule")
    ap.add_argument("--mixed", action="store_true",
                    help="refit the context models with daily activity as normal, test both ways")
    a = ap.parse_args(argv)
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

    print("loading PPG-DaLiA…")
    data = load_features(with_wave="wave" in a.models)
    if a.mixed:
        mixed(data)
        return 0
    dets = fit_detectors(a.models)
    acts = [k for k in ACTIVITY if k != 1]

    # thresholded on each person's own sitting, at 90% specificity
    flagged = {m: {k: [] for k in ACTIVITY} for m in dets}
    for s, r in data.items():
        for m, f in dets.items():
            v = f(r)
            sit = r["pure"] & (r["act"] == 1)
            thr = np.quantile(v[sit], 0.90)
            for k in ACTIVITY:
                w = r["pure"] & (r["act"] == k)
                if w.sum() >= 3:
                    flagged[m][k].append(float(np.mean(v[w] >= thr)))
    print("\nshare of windows flagged, by activity (line = each person's sitting p90; "
          "sitting reads 10%% by construction):\n")
    print("| detector | " + " | ".join(ACTIVITY[k] for k in acts) + " | all moving |")
    print("|---|" + "---|" * (len(acts) + 1))
    for m in dets:
        cells = [np.mean(flagged[m][k]) if flagged[m][k] else np.nan for k in acts]
        print("| %s | " % m + " | ".join("%.0f%%" % (100 * c) for c in cells) +
              " | %.0f%% |" % (100 * np.nanmean([cells[i] for i, k in enumerate(acts) if k in (2, 3, 4, 7)])))

    if a.live:
        print("\nthe live flag rule over each whole recording (3-min memory, 5 s ticks):\n")
        print("| detector | k | flagged time | false alarms / h | flagged time while sitting / driving / working |")
        print("|---|---|---|---|---|")
        for k in (3.0, 5.0):
            for m, f in dets.items():
                ft, fa, part = [], [], {1: [], 5: [], 8: []}
                for s, r in data.items():
                    fl = live_flags(f(r), k=k)
                    ft.append(fl.mean())
                    fa.append((np.sum(fl[1:] & ~fl[:-1]) + int(fl[0])) / (len(fl) * STEP_S / 3600.0))
                    for q in part:
                        w = r["act"] == q
                        if w.any():
                            part[q].append(fl[w].mean())
                print("| %s | %.0f | %.0f%% | %.1f | %s |" % (
                    m, k, 100 * np.mean(ft), np.mean(fa),
                    " / ".join("%.0f%%" % (100 * np.mean(part[q])) for q in part)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
