import numpy as np
import pytest

from roboto_upgrade.trust_guard import ActionSafetyShield, GuardConfig, split_conformal_threshold


def test_guard_passes_then_slew_limits() -> None:
    guard = ActionSafetyShield(GuardConfig(joint_count=3, max_delta=0.1))
    output, event = guard.apply(
        np.array([0.05, -0.05, 0.0], dtype=np.float32),
        observation_age_s=0.01,
        confidence=0.9,
        ood_score=0.1,
    )
    assert event == "pass"
    np.testing.assert_allclose(output, [0.05, -0.05, 0.0])

    output, event = guard.apply(
        np.array([0.5, -0.5, 0.0], dtype=np.float32),
        observation_age_s=0.01,
        confidence=0.9,
        ood_score=0.1,
    )
    assert event == "slew_limited"
    np.testing.assert_allclose(output, [0.15, -0.15, 0.0], atol=1e-7)


@pytest.mark.parametrize(
    ("action", "age", "confidence", "ood", "expected"),
    [
        (np.array([np.nan, 0.0]), 0.01, 0.9, 0.1, "nonfinite"),
        (np.zeros(2), 1.0, 0.9, 0.1, "stale_observation"),
        (np.zeros(2), 0.01, 0.1, 0.1, "low_confidence"),
        (np.zeros(2), 0.01, 0.9, 0.99, "ood"),
    ],
)
def test_guard_fails_closed(action: np.ndarray, age: float, confidence: float, ood: float, expected: str) -> None:
    guard = ActionSafetyShield(GuardConfig(joint_count=2))
    output, event = guard.apply(action, observation_age_s=age, confidence=confidence, ood_score=ood)
    assert event == expected
    np.testing.assert_array_equal(output, np.zeros(2, dtype=np.float32))


def test_split_conformal_threshold() -> None:
    assert split_conformal_threshold(np.array([0.1, 0.2, 0.3, 0.4]), alpha=0.25) == 0.4
    with pytest.raises(ValueError):
        split_conformal_threshold(np.array([]))
