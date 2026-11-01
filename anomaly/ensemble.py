"""the master's score: the waveform autoencoder plus heart rate going up.

on WESAD (anomaly/RESULTS.md) the autoencoder alone catches stress at PR-AUC
0.732; heart rate up, the board's rule, at 0.865; the two added with equal
weight at 0.852, with about a third of heart rate's sustained false alarms
(1.3 vs 4.3 /h at K=8). the master runs the sum, so it keeps an AI model and
catches stress about as well as the rule.

"equal weight" means each term in units of its own spread over calm windows:

    score = ae + W * (hr - HR_MED),   W = AE_MAD / HR_MAD

which is z(ae) + z(hr) rescaled into the autoencoder's own units, so the
fleet's live scale, calibration and thresholds read it exactly as they read
the autoencoder alone. a window with no readable pulse adds nothing for HR.

the spreads are robust (1.4826 * MAD) over calm:
    HR_MED, HR_MAD  WESAD calm, all 15 subjects (71.8 / 8.26 bpm)
    AE_MAD          the int8 autoencoder on people it never saw: 0.0545 on
                    WESAD scored leave-one-subject-out, 0.0536 on PPG-DaLiA
                    sitting. on WESAD in-sample it reads 0.0374 -- the model
                    was trained on those people -- which would under-weight
                    the autoencoder on a new wearer.

boards with an accelerometer (the Rev F's BNO086) are scored with motion in
the picture instead: heart rate is replaced by heart rate ABOVE what the
wrist's movement explains (anomaly.context, saved/context_acc.npz), and the
autoencoder keeps a quarter share:

    score = AE_W_CTX * ae + AE_MAD * (ctx - CTX_MED) / CTX_MAD

                         WESAD PR-AUC   daily-life false alarms/h (3σ / 5σ)
    AE + HR                  0.852            9.7 / 2.3
    1/4 AE + HR vs motion    0.846            7.1 / 2.3
    HR vs motion alone       0.817            6.5 / 1.7

the quarter was picked from 0, 1/4, 1/2, 1 on these same data (RESULTS.md), so
it is mildly optimistic. CTX_MED / CTX_MAD are the context score's calm
spread scored leave-one-subject-out (people the model never saw).

    python -m anomaly.ensemble       # re-measure the constants on WESAD + PPG-DaLiA
"""
from __future__ import annotations

import argparse
import os

import numpy as np

from .features import extract_features

HR_MED = 71.78
HR_MAD = 8.256
AE_MAD = 0.054
W = AE_MAD / HR_MAD           # autoencoder units per bpm

AE_W_CTX = 0.25
CTX_MED = -0.170
CTX_MAD = 0.966
ACC_FS = 32                   # 1/64 g at 32 Hz, as the board sends it
ACC_MIN = 30 * ACC_FS         # at least half a window of movement to judge by


def combine(ae, hr):
    """ae score(s) + heart rate's share, both arrays or both floats."""
    hr = np.asarray(hr, dtype=np.float64)
    return np.asarray(ae, dtype=np.float64) + W * np.where(np.isnan(hr), 0.0, hr - HR_MED)


def combine_ctx(ae, ctx):
    """the motion-aware score: a quarter of the autoencoder + HR above motion."""
    return (AE_W_CTX * np.asarray(ae, np.float64)
            + AE_MAD * (np.asarray(ctx, np.float64) - CTX_MED) / CTX_MAD)


class MasterScorer:
    """wraps a LiveAnomalyDetector; score() is the autoencoder + heart rate, or
    with `acc` (the board's last minute of accelerometer) the autoencoder +
    heart rate above what that movement explains. every other attribute passes
    through, so it drops in wherever the detector was used."""

    def __init__(self, det):
        from .context import SAVED_ACC, ContextScorer
        self.det = det
        self.ctx = ContextScorer(SAVED_ACC)
        self.last_ae = self.last_hr = self.last_ctx = None
        self.last_mode = None

    def score(self, window: np.ndarray, acc=None) -> float:
        ae = self.det.score(window)
        hr = float(extract_features(np.asarray(window, dtype=np.float64))[0])
        self.last_ae, self.last_hr, self.last_ctx = ae, (None if np.isnan(hr) else hr), None
        if acc is not None and len(acc) >= ACC_MIN and not np.isnan(hr):
            from .channels import acc_features
            mo = acc_features(np.asarray(acc, dtype=np.float64)[-60 * ACC_FS:])
            ctx = float(self.ctx.score(np.array([hr]), mo[None])[0])
            self.last_ctx, self.last_mode = ctx, "motion"
            return float(combine_ctx(ae, ctx))
        self.last_mode = "hr"
        return float(combine(ae, hr))

    def __getattr__(self, name):
        return getattr(self.det, name)


def _robust(x):
    x = np.asarray(x, dtype=np.float64)
    x = x[~np.isnan(x)]
    m = float(np.median(x))
    return m, float(1.4826 * np.median(np.abs(x - m)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.parse_args(argv)
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    from .infer import LiveAnomalyDetector
    from .run import load_all
    from .wesad import SUBJECTS

    data = load_all(SUBJECTS, 60, 5)
    det = LiveAnomalyDetector()
    calm = [data[s][0][data[s][1] == 1] for s in SUBJECTS]
    hr = np.concatenate([[extract_features(w)[0] for w in X] for X in calm])
    ae = np.concatenate([[det.score(w) for w in X] for X in calm])
    m, d = _robust(hr)
    print("WESAD calm HR               median %.2f  mad %.3f   (module: %.2f / %.3f)"
          % (m, d, HR_MED, HR_MAD))
    m, d = _robust(ae)
    print("WESAD calm AE, in-sample    median %.4f mad %.4f  (not used: seen in training)"
          % (m, d))
    try:
        from .dalia import load_features
        r = load_features(with_wave=True)
        sit = np.concatenate([v["wave"][v["pure"] & (v["act"] == 1)] for v in r.values()])
        m, d = _robust(sit)
        print("PPG-DaLiA sitting AE        median %.4f mad %.4f  (module AE_MAD %.4f)"
              % (m, d, AE_MAD))
    except SystemExit as e:
        print("PPG-DaLiA not available (%s); AE_MAD left as %.4f" % (e, AE_MAD))
    print("W = %.5f per bpm" % W)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
