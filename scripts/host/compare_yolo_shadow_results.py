#!/usr/bin/env python3
"""Compare S100 and S600 normalized YOLO11 detections."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def iou(a: dict, b: dict) -> float:
    left = max(a["x1"], b["x1"])
    top = max(a["y1"], b["y1"])
    right = min(a["x2"], b["x2"])
    bottom = min(a["y2"], b["y2"])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    area_a = (a["x2"] - a["x1"]) * (a["y2"] - a["y1"])
    area_b = (b["x2"] - b["x1"]) * (b["y2"] - b["y1"])
    return intersection / (area_a + area_b - intersection + 1e-9)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--s100", type=Path, required=True)
    parser.add_argument("--s600", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    s100 = json.loads(args.s100.read_text(encoding="utf-8"))
    s600 = json.loads(args.s600.read_text(encoding="utf-8"))
    left = s100["detections"]
    right = s600["detections"]
    if len(left) != len(right) or [row["class_id"] for row in left] != [row["class_id"] for row in right]:
        raise RuntimeError("detection count or class sequence mismatch")
    ious = [iou(a, b) for a, b in zip(left, right)]
    score_deltas = [abs(a["score"] - b["score"]) for a, b in zip(left, right)]
    geometry_and_decision_pass = min(ious) >= 0.95 and s100["shadow_decision"]["event"] == s600["shadow_decision"]["event"]
    strict_score_pass = max(score_deltas) <= 0.05
    payload = {
        "schema": "roboto_origin.yolo11_s100_s600_comparison.v1",
        "status": (
            "S100_S600_YOLO11_SHADOW_FUNCTIONAL_PARITY_PASS"
            if geometry_and_decision_pass
            else "S100_S600_YOLO11_SHADOW_FUNCTIONAL_PARITY_FAIL"
        ),
        "strict_numeric_status": (
            "S100_S600_YOLO11_STRICT_SCORE_PARITY_PASS"
            if strict_score_pass
            else "S100_S600_YOLO11_STRICT_SCORE_PARITY_FAIL"
        ),
        "fixture_sha256": s100["fixture"]["sha256"],
        "detection_count": len(left),
        "class_sequence": [row["class_id"] for row in left],
        "box_iou": {"min": min(ious), "mean": sum(ious) / len(ious)},
        "score_abs_delta": {"max": max(score_deltas), "mean": sum(score_deltas) / len(score_deltas)},
        "decision_equal": s100["shadow_decision"]["event"] == s600["shadow_decision"]["event"],
        "gates": {"min_box_iou": 0.95, "max_score_abs_delta": 0.05},
        "claims": {
            "same_model_family": True,
            "same_fixture": True,
            "cross_board_numerically_identical": False,
            "physical_robot_validated": False,
        },
    }
    if payload["fixture_sha256"] != s600["fixture"]["sha256"]:
        payload["status"] = "S100_S600_YOLO11_SHADOW_FUNCTIONAL_PARITY_FAIL"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "min_iou": payload["box_iou"]["min"]}))
    return 0 if payload["status"].endswith("PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
