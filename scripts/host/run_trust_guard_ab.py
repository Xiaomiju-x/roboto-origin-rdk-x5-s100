#!/usr/bin/env python3
"""Run a deterministic no-device A/B for the project-owned action guard."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from datetime import datetime
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42023)
    args = parser.parse_args()

    repo = args.repo.resolve()
    sys.path.insert(0, str(repo / "src"))
    from roboto_upgrade.trust_guard import (  # pylint: disable=import-outside-toplevel
        ActionSafetyShield,
        GuardConfig,
        finite_clamp_baseline,
        split_conformal_threshold,
    )

    rng = np.random.default_rng(args.seed)
    steps = 260
    joint_count = 23
    phase = np.arange(steps, dtype=np.float32)[:, None]
    offsets = np.linspace(0.0, 1.4, joint_count, dtype=np.float32)[None, :]
    clean = 0.22 * np.sin(phase * 0.035 + offsets)
    clean += rng.normal(0.0, 0.001, clean.shape).astype(np.float32)
    actions = clean.copy()
    ages = np.full(steps, 0.02, dtype=np.float32)
    confidence = np.full(steps, 0.96, dtype=np.float32)

    calibration_ood = np.clip(rng.normal(0.22, 0.06, 512), 0.0, 1.0)
    conformal_threshold = split_conformal_threshold(calibration_ood, alpha=0.05)
    ood = np.clip(rng.normal(0.22, 0.05, steps), 0.0, 1.0).astype(np.float32)

    faults = {
        40: "nonfinite",
        90: "stale_observation",
        140: "ood",
        190: "slew_limited",
        240: "action_limit",
    }
    actions[40, 5] = np.nan
    ages[90] = 0.50
    ood[140] = 0.99
    actions[190, 7] += 0.75
    actions[240, 3] = 1.40

    config = GuardConfig(max_ood_score=conformal_threshold)
    shield = ActionSafetyShield(config)
    candidate = np.zeros_like(actions)
    baseline = np.zeros_like(actions)
    events: list[str] = []
    for index in range(steps):
        baseline[index] = finite_clamp_baseline(actions[index], config.action_limit)
        candidate[index], event = shield.apply(
            actions[index],
            observation_age_s=float(ages[index]),
            confidence=float(confidence[index]),
            ood_score=float(ood[index]),
        )
        events.append(event)

    # Measure clean fidelity independently so recovery after an injected
    # fail-close is not mislabeled as damage to otherwise clean commands.
    clean_shield = ActionSafetyShield(config)
    clean_candidate = np.zeros_like(clean)
    clean_events: list[str] = []
    for index in range(steps):
        clean_candidate[index], clean_event = clean_shield.apply(
            clean[index],
            observation_age_s=0.02,
            confidence=0.96,
            ood_score=min(float(ood[index]), conformal_threshold - 1.0e-4),
        )
        clean_events.append(clean_event)

    injected = []
    candidate_detected = 0
    baseline_detected = 0
    for index, expected in faults.items():
        candidate_event = events[index]
        detected = candidate_event == expected
        candidate_detected += int(detected)
        baseline_stopped = bool(np.allclose(baseline[index], 0.0, atol=1.0e-7))
        baseline_detected += int(baseline_stopped)
        injected.append(
            {
                "step": index,
                "expected": expected,
                "candidate_event": candidate_event,
                "candidate_detected": detected,
                "baseline_full_stop": baseline_stopped,
            }
        )

    clean_error = np.abs(clean_candidate - clean)
    clean_preserved = float(np.mean(clean_error <= 1.0e-6))
    finite = bool(np.isfinite(candidate).all())
    bounded_transition_indices = [index for index in range(1, steps) if events[index] in {"pass", "slew_limited"} and events[index - 1] in {"pass", "slew_limited"}]
    max_candidate_delta = float(
        max(np.max(np.abs(candidate[index] - candidate[index - 1])) for index in bounded_transition_indices)
    )
    criteria = {
        "candidate_all_faults_detected": candidate_detected == len(faults),
        "candidate_beats_baseline_fault_detection": candidate_detected > baseline_detected,
        "clean_elements_preserved_gte_95pct": clean_preserved >= 0.95,
        "candidate_outputs_finite": finite,
        "candidate_slew_bounded": max_candidate_delta <= config.max_delta + 1.0e-6,
    }
    passed = all(criteria.values())

    source_path = repo / "src/roboto_upgrade/trust_guard.py"
    payload = {
        "schema_version": 1,
        "scope": "deterministic offline action filtering; no ROS or device access",
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "seed": args.seed,
        "source": {"path": source_path.relative_to(repo).as_posix(), "sha256": sha256(source_path)},
        "config": config.__dict__,
        "calibration": {
            "samples": int(calibration_ood.size),
            "alpha": 0.05,
            "conformal_ood_threshold": conformal_threshold,
        },
        "ab": {
            "faults": injected,
            "candidate_detected": candidate_detected,
            "baseline_full_stops": baseline_detected,
            "clean_elements_preserved": clean_preserved,
            "clean_nonpass_events": int(sum(event != "pass" for event in clean_events)),
            "max_candidate_delta": max_candidate_delta,
        },
        "criteria": criteria,
        "result": "PASS" if passed else "FAIL",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": payload["result"], "output": str(args.output), "criteria": criteria}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
