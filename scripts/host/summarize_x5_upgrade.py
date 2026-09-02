#!/usr/bin/env python3
"""Aggregate the selected X5 upgrade gates without running any algorithm."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ref(repo: Path, path: Path) -> dict:
    resolved = path.resolve()
    return {"path": resolved.relative_to(repo).as_posix(), "sha256": sha256(resolved)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--official", type=Path, required=True)
    parser.add_argument("--temporal", type=Path, required=True)
    parser.add_argument("--host-trust", type=Path, required=True)
    parser.add_argument("--x5-runtime", type=Path, required=True)
    parser.add_argument("--kiss-build", type=Path, required=True)
    parser.add_argument("--kiss-run", type=Path, required=True)
    parser.add_argument("--kiss-motion", type=Path, required=True)
    parser.add_argument("--fixture-comparison", type=Path, required=True)
    parser.add_argument("--fast-lio", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    repo = args.repo.resolve()
    official = load(args.official)
    temporal = load(args.temporal)
    host_trust = load(args.host_trust)
    x5_temporal = load(args.x5_runtime / "temporal_onnx.json")
    x5_trust = load(args.x5_runtime / "trust_guard.json")
    x5_runtime_safety = load(args.x5_runtime / "safety.json")
    kiss_build = load(args.kiss_build)
    kiss_run = load(args.kiss_run / "result.json")
    kiss_lifecycle = load(args.kiss_run / "lifecycle.json")
    kiss_safety = load(args.kiss_run / "safety_after.json")
    kiss_motion = load(args.kiss_motion / "result.json")
    kiss_motion_lifecycle = load(args.kiss_motion / "lifecycle.json")
    kiss_motion_safety = load(args.kiss_motion / "safety_after.json")
    fixture = load(args.fixture_comparison)
    fast_lio = load(args.fast_lio)

    host_faults = [(item["step"], item["candidate_event"]) for item in host_trust["ab"]["faults"]]
    x5_faults = [(item["step"], item["candidate_event"]) for item in x5_trust["ab"]["faults"]]
    model_hash = temporal["model"]["onnx_sha256"]
    vector_hash = temporal["x5_probe_vector"]["sha256"]
    temporal_sources_match = all(sha256(repo / item["path"]) == item["sha256"] for item in temporal["sources"])
    trust_source_hash = sha256(repo / host_trust["source"]["path"])
    runtime_services_safe = (
        x5_runtime_safety["ssh"]["stdout"] == "active"
        and x5_runtime_safety["vnc"]["stdout"] == "active"
        and all(item["stdout"] == "inactive" for item in x5_runtime_safety["frozen_services"].values())
        and x5_runtime_safety["residuals"]["returncode"] == 1
    )
    checks = {
        "official_o0_o6_pass": official.get("overall") == "PASS" and official.get("upgrade_gate_open") is True,
        "official_s100_gate_was_closed": official.get("s100_gate_open") is False,
        "u1_host_temporal_pass": temporal.get("result") == "PASS",
        "u1_x5_temporal_pass": x5_temporal.get("result") == "PASS",
        "u1_model_hash_matches": x5_temporal["model"]["sha256"] == model_hash,
        "u1_vector_hash_matches": x5_temporal["vectors"]["sha256"] == vector_hash,
        "u1_project_sources_match": temporal_sources_match,
        "u2_host_guard_pass": host_trust.get("result") == "PASS",
        "u2_x5_guard_pass": x5_trust.get("result") == "PASS",
        "u2_host_x5_events_identical": host_faults == x5_faults,
        "u2_host_x5_metrics_identical": host_trust["ab"] == x5_trust["ab"],
        "u2_project_source_matches_host_x5": host_trust["source"]["sha256"] == trust_source_hash == x5_trust["source"]["sha256"],
        "u3_build_pass": kiss_build.get("result") == "PASS",
        "u3_exact_source_and_clean": kiss_build["source"]["commit"] == "b16835283aee62f7d5e2bdf6c1c3bb2930de74ff" and kiss_build["source"]["clean"] is True,
        "u3_kiss_run_pass": kiss_run.get("result") == "PASS",
        "u3_known_motion_pass": kiss_motion.get("result") == "PASS",
        "u3_static_motion_fixture_matches": kiss_motion.get("fixture_xyz_sha256") == kiss_run.get("fixture_xyz_sha256"),
        "u3_same_fast_lio_fixture": fixture.get("result") == "PASS" and fixture.get("byte_identical") is True,
        "u3_fast_lio_reference_pass": fast_lio.get("status") == "PASS",
        "u3_clean_shutdown": kiss_lifecycle.get("forced_kill_required") is False and kiss_lifecycle.get("residual_processes") is False,
        "u3_motion_clean_shutdown": kiss_motion_lifecycle.get("forced_kill_required") is False and kiss_motion_lifecycle.get("residual_processes") is False,
        "u3_final_safety_pass": kiss_safety.get("result") == "PASS",
        "u3_motion_final_safety_pass": kiss_motion_safety.get("result") == "PASS",
        "x5_runtime_can_stayed_down": "state DOWN" in x5_runtime_safety["can0"]["stdout"] and "STOPPED" in x5_runtime_safety["can0"]["stdout"],
        "x5_runtime_services_and_residuals_safe": runtime_services_safe,
    }
    passed = all(checks.values())
    payload = {
        "schema_version": 1,
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "X5 laptop/board offline upgrade aggregation; no robot or peripherals",
        "official_baseline": {"overall": official.get("overall"), "upgrade_gate_open": official.get("upgrade_gate_open")},
        "upgrades": {
            "U1_temporal_occupancy_flow": {
                "result": "PASS" if all(checks[key] for key in checks if key.startswith("u1_")) else "FAIL",
                "candidate_iou": temporal["ab"]["candidate_mean_iou"],
                "persistence_iou": temporal["ab"]["persistence_mean_iou"],
                "candidate_flow_epe": temporal["ab"]["candidate_dynamic_flow_epe"],
                "zero_flow_epe": temporal["ab"]["zero_flow_dynamic_epe"],
                "x5_median_latency_ms": x5_temporal["latency_ms"]["median"],
            },
            "U2_trust_action_guard": {
                "result": "PASS" if all(checks[key] for key in checks if key.startswith("u2_")) else "FAIL",
                "candidate_faults": host_trust["ab"]["candidate_detected"],
                "baseline_faults": host_trust["ab"]["baseline_full_stops"],
                "clean_preserved": host_trust["ab"]["clean_elements_preserved"],
            },
            "U3_kiss_icp": {
                "result": "PASS" if all(checks[key] for key in checks if key.startswith("u3_")) else "FAIL",
                "commit": kiss_build["source"]["commit"],
                "fixture_points": kiss_run["fixture_points"],
                "stationary_drift_m": kiss_run["max_stationary_displacement_m"],
                "known_motion_expected_final_x_m": kiss_motion["trajectory"]["expected_final_x_m"],
                "known_motion_final_x_error_m": kiss_motion["trajectory"]["final_x_error_m"],
                "known_motion_x_rmse_m": kiss_motion["trajectory"]["x_rmse_m"],
                "official_fast_lio_stationary_drift_m": fast_lio["output"]["max_translation_norm_m"],
                "comparison_note": "Same XYZ fixture; ROS message schemas and estimator inputs differ, so drift values are diagnostic rather than a ranking.",
            },
        },
        "checks": checks,
        "overall": "PASS" if passed else "FAIL",
        "x5_upgrade_gate_complete": passed,
        "s100_gate_open": passed,
        "evidence": [
            ref(repo, path)
            for path in (
                args.official,
                args.temporal,
                args.host_trust,
                args.x5_runtime / "temporal_onnx.json",
                args.x5_runtime / "trust_guard.json",
                args.kiss_build,
                args.kiss_run / "result.json",
                args.kiss_run / "lifecycle.json",
                args.kiss_run / "safety_after.json",
                args.kiss_motion / "result.json",
                args.kiss_motion / "lifecycle.json",
                args.kiss_motion / "safety_after.json",
                args.fixture_comparison,
                args.fast_lio,
            )
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"overall": payload["overall"], "s100_gate_open": payload["s100_gate_open"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
