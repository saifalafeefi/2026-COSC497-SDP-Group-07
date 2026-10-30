"""statistical one-class baseline — the number the autoencoder must beat.

fit a Gaussian to the NORMAL feature distribution, score new windows by their
Mahalanobis distance from it. simple, transparent, no training loop — exactly
what a baseline should be.
"""
from __future__ import annotations

import numpy as np

from .features import extract_batch


class MahalanobisDetector:
    """fit on normal feature vectors; score = distance from the normal Gaussian.

    NaN features (e.g. windows with too few detectable pulses) are imputed with
    the train-set median before fitting, so they never poison the covariance.
    """

    def __init__(self, fs: int = 64, reg: float = 1e-3):
        self.fs = fs
        self.reg = reg

    def _impute_standardize(self, F, fit):
        if fit:
            self.median_ = np.nanmedian(F, axis=0)
        F = np.where(np.isnan(F), self.median_, F)
        if fit:
            self.mu_ = F.mean(axis=0)
            self.sd_ = F.std(axis=0) + 1e-8
        return (F - self.mu_) / self.sd_

    def fit(self, normal_windows: np.ndarray):
        return self.fit_features(extract_batch(normal_windows, self.fs))

    def score(self, windows: np.ndarray) -> np.ndarray:
        """anomaly score per window (higher = more deviant from normal)."""
        return self.score_features(extract_batch(windows, self.fs))

    # feature-level entry points — let callers (e.g. the calibration experiment)
    # pre-compute features once and reuse them across many fits.
    def fit_features(self, F: np.ndarray):
        F = self._impute_standardize(F, fit=True)
        cov = np.cov(F, rowvar=False) + self.reg * np.eye(F.shape[1])
        self.inv_cov_ = np.linalg.pinv(cov)
        self.center_ = F.mean(axis=0)
        return self

    def score_features(self, F: np.ndarray) -> np.ndarray:
        F = self._impute_standardize(F, fit=False)
        d = F - self.center_
        return np.einsum("ij,jk,ik->i", d, self.inv_cov_, d)   # squared Mahalanobis


class HeartRateDetector:
    """score = pulse rate, one-sided: only HR going UP reads as anomalous.

    the 10-feature Mahalanobis is two-sided and spreads its weight over shape
    and spectrum features that barely move under stress (and HRV from wrist
    BVP moves the WRONG way on WESAD -- peak jitter while talking). HR rising
    above calm is the one direction stress pushes. nothing is learned: within
    a person, ranking by HR is the score, and on the device the live scale
    measures it against that person's own recent calm. this is what the board
    runs (board_detector.h). windows with too few pulses get the train median.
    """

    def __init__(self, fs: int = 64):
        self.fs = fs

    def fit(self, normal_windows: np.ndarray):
        return self.fit_features(extract_batch(normal_windows, self.fs))

    def score(self, windows: np.ndarray) -> np.ndarray:
        return self.score_features(extract_batch(windows, self.fs))

    def fit_features(self, F: np.ndarray):
        self.median_ = float(np.nanmedian(F[:, 0]))
        return self

    def score_features(self, F: np.ndarray) -> np.ndarray:
        hr = F[:, 0].astype(float)
        return np.where(np.isnan(hr), self.median_, hr)
