#!/usr/bin/env python3
"""Verify artifacts from a complete official RoboParty Isaac acceptance run.

This verifier does not start Isaac Sim or access ROS/devices.  It validates the
recorded hashes, YAML files, checkpoints, TorchScript inference, ONNX structure,
and the evidence-local bounded-play additions.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import importlib.metadata as metadata
import json
import platform
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import onnx
import torch
import yaml


EXPECTED_TASKS = {
    "RPO-Flat",
    "RPO-Rough",
    "RPO-AMP",
    "RPO-AttnEnc",
    "RPO-Interrupt",
    "RPO-BeyondMimic",
    "RPO-Getup-Mimic",
    "RPO-Parkour",
}
EXPECTED_EXPORT_NAMES = {
    task: {"policy.onnx", "policy.pt"} for task in EXPECTED_TASKS - {"RPO-Parkour"}
}
EXPECTED_EXPORT_NAMES["RPO-Parkour"] = {"0-depth_encoder.onnx", "actor.onnx"}


def git_commit(path: Path) -> dict[str, Any]:
    commit = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    return {"commit": commit, "clean": not dirty}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_record(repo: Path, record: dict[str, Any]) -> Path:
    path = (repo / record["path"]).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size != record["bytes"]:
        raise ValueError(f"size mismatch: {path}")
    if sha256(path) != record["sha256"]:
        raise ValueError(f"sha256 mismatch: {path}")
    return path


def tensor_summary(value: Any) -> dict[str, Any]:
    if isinstance(value, torch.Tensor):
        tensor = value.detach().cpu()
        return {
            "type": "Tensor",
            "shape": list(tensor.shape),
            "dtype": str(tensor.dtype),
            "finite": bool(torch.isfinite(tensor).all()),
            "min": float(tensor.min()),
            "max": float(tensor.max()),
        }
    if isinstance(value, (tuple, list)):
        return {"type": type(value).__name__, "items": [tensor_summary(item) for item in value]}
    if isinstance(value, dict):
        return {"type": "dict", "items": {str(key): tensor_summary(item) for key, item in value.items()}}
    raise TypeError(f"unsupported TorchScript output: {type(value)!r}")


def all_finite(summary: dict[str, Any]) -> bool:
    if summary["type"] == "Tensor":
        return bool(summary["finite"])
    items = summary["items"]
    values = items.values() if isinstance(items, dict) else items
    return all(all_finite(item) for item in values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aggregate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[2]
    aggregate_path = args.aggregate.resolve()
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    task_results: dict[str, Any] = {}

    if aggregate.get("status") != "PASS":
        errors.append("aggregate status is not PASS")
    if not aggregate.get("complete_matrix_run"):
        errors.append("aggregate is not a complete matrix run")
    if set(aggregate.get("tasks", {})) != EXPECTED_TASKS:
        errors.append("aggregate task set differs from the expected eight tasks")

    for task_name, task in sorted(aggregate.get("tasks", {}).items()):
        task_errors: list[str] = []
        if task.get("status") != "PASS":
            task_errors.append("task status is not PASS")
        if not all(task.get("checks", {}).values()):
            task_errors.append("one or more acceptance checks are false")

        checkpoint = resolve_record(repo, task["checkpoint"])
        checkpoint_payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
        checkpoint_keys = sorted(str(key) for key in checkpoint_payload) if isinstance(checkpoint_payload, dict) else []
        if not checkpoint_keys:
            task_errors.append("checkpoint payload is not a non-empty dictionary")

        configs: list[dict[str, Any]] = []
        for record in task.get("resolved_configs", []):
            path = resolve_record(repo, record)
            # Isaac Lab emits Python-specific tuple/slice tags. BaseLoader
            # validates the YAML tree and keeps tagged values as plain data,
            # avoiding arbitrary Python-object construction.
            parsed = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
            if not isinstance(parsed, dict) or not parsed:
                task_errors.append(f"resolved YAML is empty: {path.name}")
            configs.append({"path": record["path"], "top_level_keys": sorted(parsed) if isinstance(parsed, dict) else []})
        if {Path(item["path"]).name for item in task.get("resolved_configs", [])} != {"env.yaml", "agent.yaml"}:
            task_errors.append("resolved configuration set is not exactly env.yaml and agent.yaml")

        exports: list[dict[str, Any]] = []
        onnx_shapes: list[list[int]] = []
        for record in task.get("exports", []):
            path = resolve_record(repo, record)
            export_result: dict[str, Any] = {"path": record["path"], "sha256": record["sha256"]}
            if path.suffix.lower() == ".onnx":
                model = onnx.load(str(path), load_external_data=True)
                onnx.checker.check_model(model)
                shapes = [
                    [dimension.dim_value if dimension.dim_value > 0 else 1 for dimension in item.type.tensor_type.shape.dim]
                    for item in model.graph.input
                ]
                onnx_shapes.extend(shapes)
                export_result.update({"format": "ONNX", "checker": "PASS", "input_shapes": shapes})
            elif path.suffix.lower() == ".pt":
                export_result["format"] = "TorchScript"
            else:
                task_errors.append(f"unexpected export extension: {path.suffix}")
            exports.append(export_result)

        torchscript_records = [record for record in task.get("exports", []) if Path(record["path"]).suffix.lower() == ".pt"]
        actual_export_names = {Path(record["path"]).name for record in task.get("exports", [])}
        if actual_export_names != EXPECTED_EXPORT_NAMES[task_name]:
            task_errors.append(
                f"export set mismatch: expected {sorted(EXPECTED_EXPORT_NAMES[task_name])}, got {sorted(actual_export_names)}"
            )
        if torchscript_records:
            if len(onnx_shapes) != 1:
                task_errors.append("TorchScript task does not have exactly one matching ONNX input shape")
            else:
                shape = onnx_shapes[0]
                count = int(np.prod(shape))
                input_tensor = torch.linspace(-0.1, 0.1, count, dtype=torch.float32).reshape(shape)
                for record in torchscript_records:
                    path = resolve_record(repo, record)
                    module = torch.jit.load(str(path), map_location="cpu").eval()
                    with torch.inference_mode():
                        output = module(input_tensor)
                    summary = tensor_summary(output)
                    if not all_finite(summary):
                        task_errors.append(f"non-finite TorchScript output: {path.name}")
                    for export_result in exports:
                        if export_result["path"] == record["path"]:
                            export_result.update({"load": "PASS", "inference": summary})

        harness = task.get("bounded_play_harness", {})
        source = resolve_record(repo, harness["source"])
        bounded = resolve_record(repo, harness["bounded_copy"])
        diff = list(
            difflib.ndiff(
                source.read_text(encoding="utf-8").splitlines(),
                bounded.read_text(encoding="utf-8").splitlines(),
            )
        )
        removed = [line[2:] for line in diff if line.startswith("- ")]
        added = [line[2:] for line in diff if line.startswith("+ ")]
        required_fragments = ("--max_steps", "completed_steps", "timestep >= args_cli.max_steps")
        if removed or not all(any(fragment in line for line in added) for fragment in required_fragments):
            task_errors.append("bounded-play copy contains an unexpected transformation")
        if harness.get("official_checkout_modified") is not False:
            task_errors.append("harness did not record official checkout as unmodified")

        task_results[task_name] = {
            "status": "PASS" if not task_errors else "FAIL",
            "errors": task_errors,
            "checkpoint": {"path": task["checkpoint"]["path"], "keys": checkpoint_keys},
            "configs": configs,
            "exports": exports,
            "bounded_play_additions": added,
            "bounded_play_removals": removed,
        }
        errors.extend(f"{task_name}: {message}" for message in task_errors)

    payload = {
        "schema_version": 1,
        "observed_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "offline artifact verification; no Isaac Sim, ROS, robot, or peripherals",
        "host": {"platform": platform.platform(), "python": platform.python_version()},
        "software": {
            name: metadata.version(name)
            for name in (
                "isaacsim",
                "isaaclab",
                "isaaclab_rl",
                "isaaclab_tasks",
                "torch",
                "tensordict",
                "flatdict",
                "onnx",
                "onnxruntime-gpu",
                "rsl-rl-lib",
                "robolab",
            )
        },
        "source_pins": {
            "isaac_lab": git_commit(repo / "h/il"),
            "roboparty_train": git_commit(repo / "upstream/roboparty_train"),
            "robolab": git_commit(repo / "upstream/roboparty_train/robolab"),
            "rsl_rl": git_commit(repo / "upstream/roboparty_train/rsl_rl"),
        },
        "aggregate": {
            "path": aggregate_path.relative_to(repo).as_posix(),
            "sha256": sha256(aggregate_path),
        },
        "expected_tasks": sorted(EXPECTED_TASKS),
        "tasks": task_results,
        "errors": errors,
        "status": "PASS" if not errors else "FAIL",
    }
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "tasks": len(task_results), "output": str(output_path)}))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
