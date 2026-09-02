"""Deterministic offline action/trust guard.

The guard has no publisher and no device access.  It is intentionally a pure
function plus small state machine so the same fixture can be replayed on the
laptop and X5 before any future integration is considered.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class GuardConfig:
    joint_count: int = 23
    action_limit: float = 1.0
    max_delta: float = 0.08
    max_l2_norm: float = 3.5
    max_observation_age_s: float = 0.12
    min_confidence: float = 0.60
    max_ood_score: float = 0.80
    latch_steps: int = 2


class ActionSafetyShield:
    def __init__(self, config: GuardConfig | None = None) -> None:
        self.config = config or GuardConfig()
        self.previous = np.zeros(self.config.joint_count, dtype=np.float32)
        self._latched = 0

    def reset(self) -> None:
        self.previous.fill(0.0)
        self._latched = 0

    def apply(
        self,
        action: np.ndarray,
        *,
        observation_age_s: float,
        confidence: float,
        ood_score: float,
    ) -> tuple[np.ndarray, str]:
        cfg = self.config
        value = np.asarray(action, dtype=np.float32)
        if value.shape != (cfg.joint_count,):
            return self._fail_close("shape_fault")
        if self._latched > 0:
            self._latched -= 1
            self.previous.fill(0.0)
            return self.previous.copy(), "latched_stop"
        if not np.isfinite(value).all():
            return self._fail_close("nonfinite")
        if not np.isfinite(observation_age_s) or observation_age_s > cfg.max_observation_age_s:
            return self._fail_close("stale_observation")
        if not np.isfinite(confidence) or confidence < cfg.min_confidence:
            return self._fail_close("low_confidence")
        if not np.isfinite(ood_score) or ood_score > cfg.max_ood_score:
            return self._fail_close("ood")
        if float(np.max(np.abs(value))) > cfg.action_limit:
            return self._fail_close("action_limit")
        if float(np.linalg.norm(value)) > cfg.max_l2_norm:
            return self._fail_close("action_norm")

        delta = value - self.previous
        if float(np.max(np.abs(delta))) > cfg.max_delta:
            value = self.previous + np.clip(delta, -cfg.max_delta, cfg.max_delta)
            event = "slew_limited"
        else:
            event = "pass"
        self.previous = value.astype(np.float32, copy=True)
        return self.previous.copy(), event

    def _fail_close(self, event: str) -> tuple[np.ndarray, str]:
        self._latched = self.config.latch_steps
        self.previous.fill(0.0)
        return self.previous.copy(), event


def finite_clamp_baseline(action: np.ndarray, action_limit: float = 1.0) -> np.ndarray:
    """A deliberately minimal baseline: finite substitution plus clipping."""

    value = np.asarray(action, dtype=np.float32)
    return np.clip(np.nan_to_num(value, nan=0.0, posinf=action_limit, neginf=-action_limit), -action_limit, action_limit)


def split_conformal_threshold(calibration_scores: np.ndarray, alpha: float = 0.05) -> float:
    """Finite-sample split-conformal upper threshold using the higher quantile."""

    scores = np.sort(np.asarray(calibration_scores, dtype=np.float64).reshape(-1))
    if scores.size == 0:
        raise ValueError("calibration_scores must not be empty")
    rank = int(np.ceil((scores.size + 1) * (1.0 - alpha))) - 1
    return float(scores[min(max(rank, 0), scores.size - 1)])
