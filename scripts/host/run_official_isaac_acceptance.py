#!/usr/bin/env python3
"""Run bounded official RoboParty Isaac training/export acceptance.

This runner deliberately refuses to start unless two independent legal gates
are present: the command-line acknowledgement and a project-specific process
environment variable.  It only forwards ``OMNI_KIT_ACCEPT_EULA=YES`` to child
processes after those gates pass.  Setting either gate is outside this script's
scope and must follow the user's explicit acceptance of NVIDIA's Omniverse EULA.

Official play scripts only stop automatically while video rendering is enabled.
For deterministic headless acceptance, this runner writes an evidence-local copy
that adds a ``--max_steps`` stop condition.  The official checkout is never
modified, and both source and bounded-copy hashes are recorded.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON = PROJECT_ROOT / "h" / "env" / "Scripts" / "python.exe"
TRAIN_ROOT = PROJECT_ROOT / "upstream" / "roboparty_train" / "robolab"
RSL_SCRIPTS = TRAIN_ROOT / "scripts" / "rsl_rl"
MATRIX_PATH = PROJECT_ROOT / "config" / "official_training_matrix.yaml"

PLAY_ROUTES = {
    "RPO-Flat": ("play.py", "RPO-Flat", []),
    "RPO-Rough": ("play.py", "RPO-Rough", []),
    "RPO-AMP": ("play_amp.py", "RPO-AMP-Play", []),
    "RPO-AttnEnc": ("play.py", "RPO-AttnEnc", []),
    "RPO-Interrupt": ("play.py", "RPO-Interrupt", []),
    "RPO-BeyondMimic": ("play_bm.py", "RPO-BeyondMimic", []),
    "RPO-Getup-Mimic": ("play_bm.py", "RPO-Getup-Mimic", []),
    "RPO-Parkour": ("play_parkour.py", "RPO-Parkour-Play", ["--exportonnx"]),
}
EXPECTED_EXPORT_NAMES = {
    task: {"policy.onnx", "policy.pt"} for task in PLAY_ROUTES if task != "RPO-Parkour"
}
EXPECTED_EXPORT_NAMES["RPO-Parkour"] = {"0-depth_encoder.onnx", "actor.onnx"}


def iso_now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def safe_stamp() -> str:
    return dt.datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(PROJECT_ROOT)),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def capture_nvidia() -> dict[str, Any]:
    command = [
        "nvidia-smi",
        "--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    process = subprocess.run(command, check=False, capture_output=True, text=True)
    return {
        "command": command,
        "returncode": process.returncode,
        "stdout": process.stdout.strip(),
        "stderr": process.stderr.strip(),
    }


def terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
    else:
        process.kill()


def run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
    log_path: Path,
) -> dict[str, Any]:
    started = time.monotonic()
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    timed_out = False
    try:
        output, _ = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate_process_tree(process)
        output, _ = process.communicate(timeout=30)
    elapsed = time.monotonic() - started
    log_path.write_text(output, encoding="utf-8")
    return {
        "command": command,
        "cwd": str(cwd),
        "elapsed_seconds": elapsed,
        "returncode": process.returncode,
        "timed_out": timed_out,
        "log": str(log_path.relative_to(PROJECT_ROOT)),
    }


def newest_checkpoint(log_root: Path) -> Path | None:
    candidates = [path for path in log_root.rglob("model_*.pt") if path.stat().st_size > 0]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def make_bounded_play_copy(source: Path, destination: Path) -> dict[str, Any]:
    """Create an auditable, non-rendering bounded copy of an official play script."""
    text = source.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    video_arg_indexes = [
        index for index, line in enumerate(lines) if 'parser.add_argument("--video_length"' in line
    ]
    if len(video_arg_indexes) != 1:
        raise RuntimeError(f"expected one --video_length declaration in {source}")
    newline = "\r\n" if lines[video_arg_indexes[0]].endswith("\r\n") else "\n"
    lines.insert(
        video_arg_indexes[0] + 1,
        'parser.add_argument("--max_steps", type=int, default=None, '
        f'help="Stop after this many policy steps."){newline}',
    )
    text = "".join(lines)
    loop_marker = f"    while simulation_app.is_running():{newline}"
    if text.count(loop_marker) != 1:
        raise RuntimeError(f"expected one simulation loop in {source}")
    bounded_loop = (
        loop_marker
        + "        if args_cli.max_steps is not None and timestep >= args_cli.max_steps:"
        + newline
        + '            print(f"[ROBOTO_BOUNDED_PLAY] completed_steps={timestep}")'
        + newline
        + "            break"
        + newline
        + "        timestep += 1"
        + newline
    )
    text = text.replace(loop_marker, bounded_loop, 1)
    destination.write_text(text, encoding="utf-8", newline="")
    return {
        "source": file_record(source),
        "bounded_copy": file_record(destination),
        "transformations": [
            "add --max_steps parser argument",
            "break before loop iteration max_steps + 1",
            "emit completed_steps marker",
        ],
        "official_checkout_modified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", nargs="*", choices=sorted(PLAY_ROUTES))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train-timeout", type=int, default=3600)
    parser.add_argument("--play-timeout", type=int, default=1800)
    parser.add_argument("--continue-on-failure", action="store_true")
    parser.add_argument(
        "--acknowledge-nvidia-eula",
        action="store_true",
        help="Required only after the user explicitly accepts NVIDIA's Omniverse EULA.",
    )
    args = parser.parse_args()

    legal_env = os.environ.get("ROBOTO_NVIDIA_EULA_ACCEPTED") == "YES"
    if not args.acknowledge_nvidia_eula or not legal_env:
        parser.error(
            "legal gate closed: explicit user acceptance is required before both "
            "--acknowledge-nvidia-eula and ROBOTO_NVIDIA_EULA_ACCEPTED=YES may be used"
        )

    if not PYTHON.is_file():
        parser.error(f"isolated Python is missing: {PYTHON}")
    if not MATRIX_PATH.is_file():
        parser.error(f"training matrix is missing: {MATRIX_PATH}")

    matrix = yaml.safe_load(MATRIX_PATH.read_text(encoding="utf-8"))
    matrix_tasks = {entry["task"]: entry for entry in matrix["tasks"]}
    selected = args.tasks or [entry["task"] for entry in matrix["tasks"]]
    unknown = sorted(set(selected) - set(PLAY_ROUTES))
    if unknown:
        parser.error(f"tasks have no audited play/export route: {unknown}")

    stamp = safe_stamp()
    run_root = PROJECT_ROOT / "training_runs" / "isaac_official" / stamp
    evidence_root = PROJECT_ROOT / "evidence" / "host" / "isaac" / stamp
    run_root.mkdir(parents=True, exist_ok=False)
    evidence_root.mkdir(parents=True, exist_ok=False)

    child_env = os.environ.copy()
    child_env["OMNI_KIT_ACCEPT_EULA"] = "YES"
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    child_env["PYTHONPATH"] = os.pathsep.join(
        [str(RSL_SCRIPTS), child_env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)

    aggregate: dict[str, Any] = {
        "schema_version": 1,
        "observed_at": iso_now(),
        "scope": "official RoboParty Isaac one-environment bounded training/export acceptance",
        "legal_gate": {
            "project_acknowledgement_flag": args.acknowledge_nvidia_eula,
            "project_environment_gate": legal_env,
            "accept_eula_forwarded_to_children_only": True,
            "child_environment_variable": "OMNI_KIT_ACCEPT_EULA",
        },
        "matrix": str(MATRIX_PATH.relative_to(PROJECT_ROOT)),
        "expected_tasks": list(PLAY_ROUTES),
        "selected_tasks": selected,
        "complete_matrix_run": set(selected) == set(PLAY_ROUTES),
        "python": str(PYTHON.relative_to(PROJECT_ROOT)),
        "nvidia_before": capture_nvidia(),
        "tasks": {},
        "status": "RUNNING",
    }
    aggregate_path = evidence_root / "aggregate.json"
    write_json(aggregate_path, aggregate)

    all_pass = True
    for task in selected:
        task_cfg = matrix_tasks[task]
        task_slug = task.lower().replace("rpo-", "").replace("-", "_")
        task_evidence = evidence_root / task_slug
        task_evidence.mkdir()
        experiment_name = task_cfg["experiment_name"]
        log_root = run_root / "logs" / "rsl_rl" / experiment_name

        train_command = [
            str(PYTHON),
            str(RSL_SCRIPTS / "train.py"),
            "--task",
            task,
            "--num_envs",
            "1",
            "--max_iterations",
            "1",
            "--seed",
            str(args.seed),
            "--logger",
            "tensorboard",
            "--headless",
            "--device",
            "cuda:0",
        ]
        result: dict[str, Any] = {
            "task": task,
            "experiment_name": experiment_name,
            "observed_at": iso_now(),
            "nvidia_before": capture_nvidia(),
        }
        result["train"] = run_command(
            train_command,
            cwd=run_root,
            env=child_env,
            timeout_seconds=args.train_timeout,
            log_path=task_evidence / "train.log",
        )

        checkpoint = newest_checkpoint(log_root)
        params = [] if checkpoint is None else [
            checkpoint.parent / "params" / "env.yaml",
            checkpoint.parent / "params" / "agent.yaml",
        ]
        result["checkpoint"] = file_record(checkpoint) if checkpoint else None
        result["resolved_configs"] = [file_record(path) for path in params if path.is_file()]

        if result["train"]["returncode"] == 0 and checkpoint and len(result["resolved_configs"]) == 2:
            play_script, play_task, extra_args = PLAY_ROUTES[task]
            bounded_play_script = task_evidence / f"bounded_{play_script}"
            result["bounded_play_harness"] = make_bounded_play_copy(
                RSL_SCRIPTS / play_script, bounded_play_script
            )
            play_command = [
                str(PYTHON),
                str(bounded_play_script),
                "--task",
                play_task,
                "--num_envs",
                "1",
                "--seed",
                str(args.seed),
                "--checkpoint",
                str(checkpoint),
                "--max_steps",
                "2",
                "--headless",
                "--device",
                "cuda:0",
                *extra_args,
            ]
            result["play_export"] = run_command(
                play_command,
                cwd=run_root,
                env=child_env,
                timeout_seconds=args.play_timeout,
                log_path=task_evidence / "play_export.log",
            )
        else:
            result["play_export"] = {
                "skipped": True,
                "reason": "training/checkpoint/resolved-configuration gate failed",
            }

        exported_dir = checkpoint.parent / "exported" if checkpoint else None
        exported = [] if exported_dir is None or not exported_dir.is_dir() else [
            file_record(path)
            for path in sorted(exported_dir.iterdir())
            if path.is_file() and path.stat().st_size > 0 and path.suffix.lower() in {".onnx", ".pt"}
        ]
        videos = [
            file_record(path)
            for path in sorted(run_root.rglob("*.mp4"))
            if task_slug in str(path).lower() or (checkpoint and checkpoint.parent in path.parents)
        ]
        result["exports"] = exported
        result["videos"] = videos
        play_ok = result["play_export"].get("returncode") == 0
        bounded_marker = "[ROBOTO_BOUNDED_PLAY] completed_steps=2"
        play_log_path = task_evidence / "play_export.log"
        bounded_steps_evidenced = (
            play_log_path.is_file()
            and bounded_marker in play_log_path.read_text(encoding="utf-8", errors="replace")
        )
        result["checks"] = {
            "train_exit_zero": result["train"]["returncode"] == 0,
            "train_not_timed_out": not result["train"]["timed_out"],
            "checkpoint_nonempty": checkpoint is not None,
            "resolved_configs_present": len(result["resolved_configs"]) == 2,
            "play_export_exit_zero": play_ok,
            "bounded_two_steps_evidenced": bounded_steps_evidenced,
            "export_nonempty": bool(exported),
            "exports_complete": {Path(record["path"]).name for record in exported}
            == EXPECTED_EXPORT_NAMES[task],
        }
        result["nvidia_after"] = capture_nvidia()
        result["status"] = "PASS" if all(result["checks"].values()) else "FAIL"
        write_json(task_evidence / "result.json", result)
        aggregate["tasks"][task] = result
        all_pass = all_pass and result["status"] == "PASS"
        write_json(aggregate_path, aggregate)
        if result["status"] != "PASS" and not args.continue_on_failure:
            break

    aggregate["nvidia_after"] = capture_nvidia()
    aggregate["completed_at"] = iso_now()
    aggregate["status"] = "PASS" if all_pass and len(aggregate["tasks"]) == len(selected) else "FAIL"
    write_json(aggregate_path, aggregate)
    print(f"RESULT={aggregate_path}")
    print(f"STATUS={aggregate['status']}")
    return 0 if aggregate["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
