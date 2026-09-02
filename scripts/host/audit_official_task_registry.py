#!/usr/bin/env python3

"""Static O0 audit for every official RoboParty training/deployment task."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

import onnx
import yaml


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit(path: Path) -> str:
    process = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    )
    return process.stdout.strip()


def value_source(source: str, node: ast.AST) -> str:
    return ast.get_source_segment(source, node) or ast.dump(node, include_attributes=False)


def collect_registrations(task_root: Path) -> tuple[list[dict], list[dict]]:
    registrations: list[dict] = []
    syntax_errors: list[dict] = []
    for path in sorted(task_root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as error:
            syntax_errors.append({"path": str(path), "line": error.lineno, "error": str(error)})
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if not (isinstance(function, ast.Attribute) and function.attr == "register"):
                continue
            keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
            id_node = keywords.get("id")
            if id_node is None:
                continue
            try:
                task_id = ast.literal_eval(id_node)
            except (ValueError, TypeError):
                continue
            if not isinstance(task_id, str) or not task_id.startswith("RPO-"):
                continue
            kwargs_node = keywords.get("kwargs")
            kwargs: dict[str, str] = {}
            if isinstance(kwargs_node, ast.Dict):
                for key_node, value_node in zip(kwargs_node.keys, kwargs_node.values):
                    try:
                        key = ast.literal_eval(key_node)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(key, str):
                        kwargs[key] = value_source(source, value_node)
            registrations.append(
                {
                    "id": task_id,
                    "path": str(path),
                    "line": node.lineno,
                    "entry_point": value_source(source, keywords.get("entry_point")),
                    "kwargs": kwargs,
                }
            )
    return registrations, syntax_errors


def onnx_shape(value_info) -> list[int | str | None]:
    output: list[int | str | None] = []
    for dimension in value_info.type.tensor_type.shape.dim:
        if dimension.HasField("dim_value"):
            output.append(int(dimension.dim_value))
        elif dimension.HasField("dim_param"):
            output.append(dimension.dim_param)
        else:
            output.append(None)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--matrix", type=Path, default=Path("config/official_training_matrix.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    matrix_path = (repo / args.matrix).resolve() if not args.matrix.is_absolute() else args.matrix
    matrix = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))

    train_root = repo / "upstream/roboparty_train"
    task_root = train_root / "robolab/robolab/tasks"
    registrations, syntax_errors = collect_registrations(task_root)
    actual_ids = [item["id"] for item in registrations]
    expected_ids: list[str] = []
    for task in matrix["tasks"]:
        expected_ids.append(task["task"])
        if task.get("play_task"):
            expected_ids.append(task["play_task"])

    model_roots = [
        repo / "upstream/roboparty_deploy/src/camera/models",
        repo / "upstream/roboparty_deploy/src/inference/robots/rpo/models",
    ]
    model_records: dict[str, dict] = {}
    failures: list[str] = []
    for task in matrix["tasks"]:
        sim2sim = train_root / "robolab/scripts/mujoco" / task["sim2sim_script"]
        if not sim2sim.is_file():
            failures.append(f"missing sim2sim script for {task['task']}: {sim2sim}")
        for name in task["deployment_models"]:
            matches = [root / name for root in model_roots if (root / name).is_file()]
            if len(matches) != 1:
                failures.append(f"deployment model {name} has {len(matches)} matches")
                continue
            if name in model_records:
                continue
            path = matches[0]
            model = onnx.load(str(path), load_external_data=False)
            onnx.checker.check_model(model)
            model_records[name] = {
                "path": path.relative_to(repo).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "inputs": [
                    {"name": item.name, "shape": onnx_shape(item)} for item in model.graph.input
                ],
                "outputs": [
                    {"name": item.name, "shape": onnx_shape(item)} for item in model.graph.output
                ],
            }

    required_scripts = [
        "train.py",
        "play.py",
        "play_amp.py",
        "play_bm.py",
        "play_parkour.py",
    ]
    script_records = {}
    for name in required_scripts:
        path = train_root / "robolab/scripts/rsl_rl" / name
        script_records[name] = {
            "exists": path.is_file(),
            "sha256": sha256(path) if path.is_file() else None,
        }
        if not path.is_file():
            failures.append(f"missing training/play script: {name}")

    if syntax_errors:
        failures.append(f"Python syntax errors under task tree: {len(syntax_errors)}")
    if len(actual_ids) != len(set(actual_ids)):
        failures.append("duplicate official RPO task registration IDs")
    if set(actual_ids) != set(expected_ids):
        failures.append(
            f"matrix/registry mismatch missing={sorted(set(expected_ids) - set(actual_ids))} "
            f"extra={sorted(set(actual_ids) - set(expected_ids))}"
        )
    for registration in registrations:
        for key in ("env_cfg_entry_point", "rsl_rl_cfg_entry_point"):
            if key not in registration["kwargs"]:
                failures.append(f"{registration['id']} lacks {key}")

    source = matrix["source"]
    actual_pins = {
        "roboparty_train_commit": git_commit(train_root),
        "robolab_commit": git_commit(train_root / "robolab"),
        "rsl_rl_commit": git_commit(train_root / "rsl_rl"),
    }
    for key, value in actual_pins.items():
        if source.get(key) != value:
            failures.append(f"source pin mismatch {key}: matrix={source.get(key)} actual={value}")

    payload = {
        "schema_version": 1,
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "gate": "O0_source_and_contract",
        "status": "PASS" if not failures else "FAIL",
        "matrix": str(matrix_path.relative_to(repo)),
        "expected_registry_ids": sorted(expected_ids),
        "actual_registry_ids": sorted(actual_ids),
        "registrations": registrations,
        "syntax_errors": syntax_errors,
        "models": model_records,
        "training_scripts": script_records,
        "source_pins": actual_pins,
        "version_note": {
            "official_requirement": "Isaac Sim 5.1.0 and mutable Isaac Lab main",
            "project_reproducibility_pin": source["isaac_lab"],
        },
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "tasks": len(actual_ids), "models": len(model_records), "failures": failures}))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
