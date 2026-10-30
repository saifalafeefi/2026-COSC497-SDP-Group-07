"""heart rate in the context of motion: the cross-signal anomaly.

heart rate going up is the best single stress score on WESAD (PR-AUC 0.865,
anomaly/RESULTS.md), but it cannot tell stress from effort: stairs raise it
too. the anomaly the scope names is "high HR while the accelerometer says at
rest" -- heart rate higher than this much movement explains.

    hr        heart rate up, the rule the board runs. the bar to beat
    reg       linear regression of HR on the motion features, fitted on calm;
              score = HR minus what the motion predicts (the statistical twin)
    ae        a small masked autoencoder over [HR, motion]. trained on calm
              with the HR input hidden half the time, so it learns to infer HR
              from motion; scored with HR hidden, score = HR minus its estimate
    ae-recon  the same network's plain reconstruction error, two-sided, for
              reference: what a textbook autoencoder would report

all scores count only HR ABOVE expectation. features come from the channels
cache (anomaly.channels): the 10 BVP features (HR is the first) and 7 wrist
accelerometer features per 60 s window. leave-one-subject-out on calm.

WESAD's calm and stress are both seated, so this can show the context models
do not LOSE to the rule here; whether they beat it on movement needs data
where normal includes activity (PPG-DaLiA, or our own step test).

our rig has no accelerometer. `--motion pulse` swaps the 7 accelerometer
features for motion read off the pulse signal itself -- how uneven the
per-second amplitude is, how much of the window the spike test marks, how
rough and how broadband the waveform is -- which a pulse-only board can
compute. `--motion both` uses all of them.

    python -m anomaly.context
    python -m anomaly.context --motion pulse
    python -m anomaly.context --models hr reg --max-subjects 3
    python -m anomaly.context --export     # -> saved/context_pulse.npz, for protocol reports
"""
from __future__ import annotations

import argparse
import os

import numpy as np

from . import quality
from .channels import CACHE_DIR, load_features
from .metrics import aggregate, episode_metrics, summarize
from .wesad import FS, SUBJECTS

NORMAL, POSITIVE = 1, 2
ACC_NAMES = ["acc_mean", "acc_std", "acc_jerk", "acc_max", "acc_x_std", "acc_y_std", "acc_z_std"]
PULSE_NAMES = ["env_max", "env_cv", "env_loud", "spikes", "rough", "entropy", "band_ratio"]
MODELS = ("hr", "reg", "ae", "ae-recon")


def pulse_motion(w: np.ndarray, f_bvp: np.ndarray, fs: int = FS) -> np.ndarray:
    """motion as the pulse signal shows it: the movement gate's own measures
    (per-second amplitude against the window's quiet level, the spike mask)
    plus three of the pulse features that rise with artifact."""
    w = np.asarray(w, np.float64)
    k = len(w) // fs
    amp = w[:k * fs].reshape(k, fs).std(axis=1)
    ref = max(np.percentile(amp, quality.ENVELOPE_REF_Q), 1e-9)
    return np.array([amp.max() / ref,
                     amp.std() / (amp.mean() + 1e-9),
                     np.mean(amp / ref > quality.ENVELOPE_RATIO),
                     quality.artifact_mask(w).mean(),
                     f_bvp[9], f_bvp[8], f_bvp[7]], dtype=np.float32)


def load_pulse_motion(data: dict, win_sec: float = 60.0, step_sec: float = 5.0) -> dict:
    """subject -> (n_win x 7) pulse-derived motion, on the channels cache's grid
    (the same windows as the harness cache; asserted, not assumed)."""
    path = os.path.join(CACHE_DIR, f"pulse_motion_win{win_sec:g}_step{step_sec:g}.npz")
    cache = dict(np.load(path)) if os.path.exists(path) else {}
    out, new, raw = {}, False, None
    for s in data:
        if s not in cache:
            if raw is None:
                raw = np.load(os.path.join(CACHE_DIR, f"win{win_sec:g}_step{step_sec:g}.npz"))
            X, c = raw[s + "_X"], raw[s + "_c"]
            assert len(c) == len(data[s]["cond"]) and (c == data[s]["cond"]).all(), s
            cache[s] = np.array([pulse_motion(x, f) for x, f in zip(X, data[s]["BVP"])])
            new = True
        out[s] = cache[s]
    if new:
        np.savez(path, **cache)
    return out


def split(F_bvp, F_acc):
    """-> (hr, motion), HR NaNs left for the caller to impute."""
    return F_bvp[:, 0].astype(np.float64), F_acc.astype(np.float64)


class _Scaler:
    def fit(self, X):
        self.med = np.nanmedian(X, axis=0)
        X = np.where(np.isnan(X), self.med, X)
        self.mu, self.sd = X.mean(axis=0), X.std(axis=0) + 1e-8
        return self

    def __call__(self, X):
        return (np.where(np.isnan(X), self.med, X) - self.mu) / self.sd


class ContextRegression:
    """HR ~ motion, least squares on calm. score = HR above the prediction."""

    def fit(self, hr, motion):
        self.sx, self.sh = _Scaler().fit(motion), _Scaler().fit(hr[:, None])
        A = np.c_[np.ones(len(hr)), self.sx(motion)]
        self.w = np.linalg.lstsq(A, self.sh(hr[:, None])[:, 0], rcond=None)[0]
        return self

    def score(self, hr, motion):
        pred = np.c_[np.ones(len(hr)), self.sx(motion)] @ self.w
        return self.sh(hr[:, None])[:, 0] - pred


class ContextAE:
    """masked autoencoder over [HR, motion]. column 0 is HR."""

    def __init__(self, hidden: int = 16, latent: int = 4, mask_p: float = 0.5,
                 epochs: int = 150, seed: int = 0, batch: int = 64):
        self.hidden, self.latent, self.mask_p = hidden, latent, mask_p
        self.epochs, self.seed, self.batch = epochs, seed, batch

    def _net(self, d):
        import tensorflow as tf
        tf.keras.utils.set_random_seed(self.seed)
        x = tf.keras.Input((d + 1,))          # features + a "HR hidden" flag
        h = tf.keras.layers.Dense(self.hidden, activation="relu")(x)
        z = tf.keras.layers.Dense(self.latent, activation="relu")(h)
        h = tf.keras.layers.Dense(self.hidden, activation="relu")(z)
        y = tf.keras.layers.Dense(d)(h)
        m = tf.keras.Model(x, y)
        m.compile(optimizer=tf.keras.optimizers.Adam(3e-3), loss="mse")
        return m

    def _inp(self, X, hide):
        X = X.copy()
        X[hide, 0] = 0.0                       # standardised mean: "unknown"
        return np.c_[X, hide.astype(np.float64)]

    def fit(self, hr, motion):
        self.s = _Scaler().fit(np.c_[hr, motion])
        X = self.s(np.c_[hr, motion])
        rng = np.random.default_rng(self.seed)
        hide = rng.random(len(X)) < self.mask_p
        self.net = self._net(X.shape[1])
        self.net.fit(self._inp(X, hide), X, epochs=self.epochs, batch_size=self.batch,
                     verbose=0, shuffle=True)
        return self

    def score(self, hr, motion):
        X = self.s(np.c_[hr, motion])
        est = self.net.predict(self._inp(X, np.ones(len(X), bool)), verbose=0)[:, 0]
        return X[:, 0] - est                   # HR above what the motion implies

    def recon(self, hr, motion):
        X = self.s(np.c_[hr, motion])
        R = self.net.predict(self._inp(X, np.zeros(len(X), bool)), verbose=0)
        return ((R - X) ** 2).mean(axis=1)

    def save(self, path: str, motion: str):
        W = self.net.get_weights()
        np.savez(path, med=self.s.med, mu=self.s.mu, sd=self.s.sd, motion=np.array(motion),
                 **{"w%d" % i: w for i, w in enumerate(W)})


SAVED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saved", "context_pulse.npz")


class ContextScorer:
    """the exported ContextAE, in numpy: no TensorFlow needed to score.

    score_window(w) takes one 60 s window of 64 Hz pulse, the same input the
    master scores, and returns (heart rate, HR above what the pulse's own
    motion implies). pulse-derived motion only, so it runs on our rig."""

    def __init__(self, path: str = SAVED):
        z = np.load(path)
        self.med, self.mu, self.sd = z["med"], z["mu"], z["sd"]
        self.W = [z["w%d" % i] for i in range(sum(k.startswith("w") for k in z.files))]

    def score(self, hr, motion):
        X = np.c_[hr, motion].astype(np.float64)
        X = (np.where(np.isnan(X), self.med, X) - self.mu) / self.sd
        h = np.c_[X, np.ones(len(X))]
        h[:, 0] = 0.0                                    # HR hidden
        for i in range(0, len(self.W), 2):
            h = h @ self.W[i] + self.W[i + 1]
            if i + 2 < len(self.W):
                h = np.maximum(h, 0.0)
        return X[:, 0] - h[:, 0]

    def score_window(self, w):
        from .features import extract_features
        f = extract_features(w, FS)
        hr = f[0]
        return float(hr), float(self.score(np.array([hr]), pulse_motion(w, f)[None])[0])


def export(data, path: str = SAVED):
    """fit on ALL 15 subjects' calm, pulse-derived motion, and save."""
    pm = load_pulse_motion(data)
    calm = {s: data[s]["cond"] == NORMAL for s in data}
    hr = np.concatenate([data[s]["BVP"][calm[s], 0] for s in data]).astype(np.float64)
    mo = np.vstack([pm[s][calm[s]] for s in data]).astype(np.float64)
    hr = np.where(np.isnan(hr), np.nanmedian(hr), hr)
    ae = ContextAE().fit(hr, mo)
    ae.save(path, "pulse")
    # the numpy scorer must reproduce the network it was cut from
    ref = ae.score(hr[:200], mo[:200])
    got = ContextScorer(path).score(hr[:200], mo[:200])
    err = float(np.abs(ref - got).max())
    print("  wrote %s (%.1f KB, %d calm windows); numpy vs keras max err %.1e" % (
        path, os.path.getsize(path) / 1024.0, len(hr), err))
    return err < 1e-4


def run(data, models, motion: dict, k: int = 8):
    """motion: subject -> (n_win x d) context features (accelerometer, pulse, or both)."""
    subjects = list(data)
    rows = {m: [] for m in models}
    motion_fa = {m: [] for m in models}
    for test in subjects:
        others = [s for s in subjects if s != test]
        calm = lambda s: data[s]["cond"] == NORMAL
        hr_tr = np.concatenate([data[s]["BVP"][calm(s), 0] for s in others]).astype(np.float64)
        mo_tr = np.vstack([motion[s][calm(s)] for s in others]).astype(np.float64)
        hr_te, mo_te = split(data[test]["BVP"], motion[test])
        y = (data[test]["cond"] == POSITIVE).astype(int)
        med = float(np.nanmedian(hr_tr))
        hr_tr = np.where(np.isnan(hr_tr), med, hr_tr)
        hr_te = np.where(np.isnan(hr_te), med, hr_te)

        sc = {}
        if "hr" in models:
            sc["hr"] = hr_te
        if "reg" in models:
            sc["reg"] = ContextRegression().fit(hr_tr, mo_tr).score(hr_te, mo_te)
        if "ae" in models or "ae-recon" in models:
            ae = ContextAE().fit(hr_tr, mo_tr)
            if "ae" in models:
                sc["ae"] = ae.score(hr_te, mo_te)
            if "ae-recon" in models:
                sc["ae-recon"] = ae.recon(hr_te, mo_te)

        # false alarms on the most active quarter of this person's calm, at the
        # 90%-specificity line: the cost a context model is supposed to cut.
        # "active" is always judged by the accelerometer, whatever the model saw
        act = data[test]["ACC"][:, 1]
        busy = (y == 0) & (act >= np.quantile(act[y == 0], 0.75))
        for m, v in sc.items():
            r = summarize(y, v)
            r.update(episode_metrics(v, y, k=k))
            r["subject"] = test
            rows[m].append(r)
            motion_fa[m].append(float(np.mean(v[busy] >= np.quantile(v[y == 0], 0.90))))
        print("  %-4s " % test + "  ".join("%s %.3f" % (m, rows[m][-1]["pr_auc"]) for m in models),
              flush=True)
    return rows, motion_fa


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", nargs="+", default=list(MODELS), choices=MODELS)
    ap.add_argument("--max-subjects", type=int, default=0)
    ap.add_argument("--episode-k", type=int, default=8, dest="k")
    ap.add_argument("--motion", choices=["acc", "pulse", "both"], default="acc",
                    help="context: the wrist accelerometer, motion read off the pulse "
                         "signal (what a pulse-only board can see), or both")
    ap.add_argument("--export", action="store_true",
                    help="fit the pulse-motion model on all subjects' calm and write "
                         "anomaly/saved/context_pulse.npz (what protocol reports re-score with)")
    a = ap.parse_args(argv)
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

    subjects = SUBJECTS[: a.max_subjects] if a.max_subjects else SUBJECTS
    print("loading channel features…")
    data = load_features(subjects, 60.0, 5.0)
    if a.export:
        return 0 if export(data) else 1
    motion = {s: data[s]["ACC"] for s in data}
    if a.motion != "acc":
        pm = load_pulse_motion(data)
        motion = pm if a.motion == "pulse" else {s: np.c_[motion[s], pm[s]] for s in data}
    print("\nLOSO (context: %s), PR-AUC per held-out subject:" % a.motion)
    rows, motion_fa = run(data, a.models, motion, a.k)

    print("\n| model | PR-AUC | recall@90%% spec | ROC-AUC | K=%d episodes | FA/h | flagged, busiest calm quarter |" % a.k)
    print("|---|---|---|---|---|---|---|")
    for m in a.models:
        g = aggregate(rows[m])
        n = len(rows[m])
        caught = int(round(np.nansum([r["ep_recall"] for r in rows[m]])))
        print("| %s | %.3f ± %.3f | %.3f | %.3f | %d/%d | %.1f | %.0f%% |" % (
            m, *g["pr_auc"], g["recall@90spec"][0], g["roc_auc"][0], caught, n,
            g["fa_per_hr"][0], 100 * np.mean(motion_fa[m])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
