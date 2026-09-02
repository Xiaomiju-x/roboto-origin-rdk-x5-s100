#!/usr/bin/env python3
"""Aggregate X5 official offline evidence into O0-O6 gate status."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_json(relative: str) -> dict[str, Any]:
    path = PROJECT_ROOT / relative
    return json.loads(path.read_text(encoding="utf-8"))


def result_value(document: dict[str, Any]) -> str | None:
    return document.get("status") or document.get("result")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def evidence_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def latest_result(root: Path) -> Path | None:
    candidates = [path for path in root.glob("*/result.json") if path.is_file()]
    return max(candidates, key=lambda path: path.parent.name) if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "evidence" / "host" / "official_offline_summary_latest.json",
    )
    args = parser.parse_args()

    fixed_evidence = {
        "task_registry": "evidence/host/training/official_task_registry_20260829T0042+0800.json",
        "environment": "evidence/host/training/environment_20260829T0040+0800.json",
        "host_onnx_cuda": "evidence/host/onnx/onnx_backend_20260828T235919+0800.json",
        "x5_onnx": "evidence/x5/onnx_runtime/onnx_synthetic_deterministic_20260829T000810+0800.json",
        "x5_host_onnx_parity": "evidence/host/onnx/x5_host_parity_20260829T001003+0800.json",
        "x5_depth": "evidence/x5/depth_pipeline/2026-08-29T00-36-53+08-00/result.json",
        "x5_host_depth_parity": "evidence/host/vision/x5_host_depth_parity_20260829T0038+0800.json",
        "x5_navigation_plan": "evidence/x5/navigation_plan/2026-08-29T00-32-32+08-00/result.json",
    }
    loaded = {name: load_json(path) for name, path in fixed_evidence.items()}

    mujoco_prefixes = [
        "flat_cpu_",
        "rough_cpu_",
        "amp_cpu_",
        "attn_enc_cpu_",
        "interrupt_cpu_",
        "wave_cpu_",
        "dance0_cpu_",
        "dance1_cpu_",
        "getup_cpu_",
        "parkour_plane_cpu_",
        "parkour_stairs_cpu_",
        "parkour_stairs_cuda_",
    ]
    mujoco: dict[str, Any] = {}
    for prefix in mujoco_prefixes:
        matches = sorted((PROJECT_ROOT / "evidence" / "host" / "mujoco").glob(f"{prefix}*/result.json"))
        path = matches[-1] if matches else None
        mujoco[prefix.rstrip("_")] = {
            "status": result_value(json.loads(path.read_text(encoding="utf-8"))) if path else "MISSING",
            "evidence": evidence_record(path) if path else None,
        }

    localization_path = latest_result(PROJECT_ROOT / "evidence" / "x5" / "localization_synthetic")
    localization = json.loads(localization_path.read_text(encoding="utf-8")) if localization_path else {}

    isaac_aggregates = sorted((PROJECT_ROOT / "evidence" / "host" / "isaac").glob("*/aggregate.json"))
    isaac_path = isaac_aggregates[-1] if isaac_aggregates else None
    isaac = json.loads(isaac_path.read_text(encoding="utf-8")) if isaac_path else None
    environment_status = result_value(loaded["environment"])
    expected_isaac_tasks = {
        "RPO-Flat",
        "RPO-Rough",
        "RPO-AMP",
        "RPO-AttnEnc",
        "RPO-Interrupt",
        "RPO-BeyondMimic",
        "RPO-Getup-Mimic",
        "RPO-Parkour",
    }
    isaac_complete = bool(
        isaac
        and result_value(isaac) == "PASS"
        and set(isaac.get("tasks", {})) == expected_isaac_tasks
        and all(result_value(task) == "PASS" for task in isaac.get("tasks", {}).values())
    )
    if isaac_complete:
        isaac_gate_status = "PASS"
    elif environment_status == "BLOCKED_EULA":
        isaac_gate_status = "BLOCKED"
    elif isaac:
        isaac_gate_status = "FAIL"
    else:
        isaac_gate_status = "NOT_RUN"

    o5_pass = all(entry["status"] == "PASS" for entry in mujoco.values())
    o6_components = {
        name: result_value(loaded[name])
        for name in (
            "host_onnx_cuda",
            "x5_onnx",
            "x5_host_onnx_parity",
            "x5_depth",
            "x5_host_depth_parity",
            "x5_navigation_plan",
        )
    }
    localization_graceful = bool(localization.get("shutdown", {}).get("graceful"))
    o6_pass = (
        all(status == "PASS" for status in o6_components.values())
        and result_value(localization) == "PASS"
        and localization_graceful
    )

    gates = {
        "O0": {
            "status": "PASS" if result_value(loaded["task_registry"]) == "PASS" else "FAIL",
            "name": "source_and_contract",
        },
        "O1": {"status": isaac_gate_status, "name": "environment_import"},
        "O2": {"status": isaac_gate_status, "name": "simulation_reset_step"},
        "O3": {"status": isaac_gate_status, "name": "training_smoke"},
        "O4": {"status": isaac_gate_status, "name": "export"},
        "O5": {"status": "PASS" if o5_pass else "FAIL", "name": "finite_sim2sim"},
        "O6": {"status": "PASS" if o6_pass else "FAIL", "name": "x5_offline_deployment"},
    }
    overall = "PASS" if all(gate["status"] == "PASS" for gate in gates.values()) else "INCOMPLETE"

    output = {
        "schema_version": 1,
        "observed_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "official RoboParty algorithms on laptop and X5 offline only; no robot or peripherals",
        "gates": gates,
        "overall": overall,
        # Passing the official baseline opens only the X5 upgrade stage.  The
        # user-required XRD/frontier A/B stage must pass independently before
        # S100 can be requested.
        "s100_gate_open": False,
        "s100_gate_reason": (
            "X5 official baseline passed; XRD/frontier X5 upgrade acceptance is still required"
            if overall == "PASS"
            else "X5 official baseline is incomplete"
        ),
        "upgrade_gate_open": overall == "PASS",
        "isaac": {
            "environment_status": environment_status,
            "complete_eight_task_acceptance": isaac_complete,
            "latest_acceptance": evidence_record(isaac_path) if isaac_path else None,
        },
        "mujoco": mujoco,
        "x5": {
            "components": o6_components,
            "localization_status": result_value(localization) if localization else "MISSING",
            "localization_graceful_shutdown": localization_graceful,
            "localization_evidence": evidence_record(localization_path) if localization_path else None,
        },
        "evidence": {
            name: evidence_record(PROJECT_ROOT / path) for name, path in fixed_evidence.items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"overall": overall, "gates": gates}, indent=2, sort_keys=True))
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
