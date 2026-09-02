#!/usr/bin/env python3

"""Capture the isolated host training environment without importing Isaac Sim."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import psutil
import torch
import onnxruntime as ort
import mujoco

if hasattr(ort, "preload_dlls"):
    ort.preload_dlls()


def run(command: list[str], cwd: Path | None = None) -> dict:
    process = subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True)
    return {
        "command": command,
        "returncode": process.returncode,
        "stdout": process.stdout.strip(),
        "stderr": process.stderr.strip(),
    }


def distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def git_commit(path: Path) -> dict:
    commit = run(["git", "rev-parse", "HEAD"], cwd=path)
    status = run(["git", "status", "--short", "--untracked-files=no"], cwd=path)
    return {
        "path": str(path),
        "commit": commit["stdout"] if commit["returncode"] == 0 else None,
        "tracked_clean": status["returncode"] == 0 and status["stdout"] == "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()

    packages = [
        "isaacsim",
        "isaacsim-core",
        "isaacsim-rl",
        "isaaclab",
        "isaaclab_assets",
        "isaaclab_contrib",
        "isaaclab_mimic",
        "isaaclab_rl",
        "isaaclab_tasks",
        "robolab",
        "rsl-rl-lib",
        "torch",
        "torchvision",
        "torchaudio",
        "onnx",
        "onnxruntime-gpu",
        "mujoco",
        "opencv-python",
    ]
    package_versions = {name: distribution_version(name) for name in packages}
    nvidia = run(
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total,compute_cap",
            "--format=csv,noheader,nounits",
        ]
    )
    pip_check = run([sys.executable, "-m", "pip", "check"])

    cuda_test = {
        "available": torch.cuda.is_available(),
        "torch_cuda": torch.version.cuda,
    }
    if torch.cuda.is_available():
        left = torch.arange(4096, device="cuda", dtype=torch.float32).reshape(64, 64)
        product = left @ left.T
        cuda_test.update(
            {
                "device": torch.cuda.get_device_name(0),
                "compute_capability": list(torch.cuda.get_device_capability(0)),
                "finite": bool(torch.isfinite(product).all().item()),
                "max_memory_allocated_bytes": int(torch.cuda.max_memory_allocated()),
            }
        )

    accepted_eula_value = os.environ.get("ACCEPT_EULA", "").strip().lower()
    eula_preaccepted = accepted_eula_value in {"y", "yes", "true", "1"}
    known_conflict = (
        "fastapi 0.115.7 has requirement starlette<0.46.0,>=0.40.0"
        in (pip_check["stdout"] + "\n" + pip_check["stderr"])
        and "starlette 0.49.1" in (pip_check["stdout"] + "\n" + pip_check["stderr"])
    )
    payload = {
        "schema_version": 1,
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "BLOCKED_EULA" if not eula_preaccepted else "READY_FOR_IMPORT_PROBE",
        "scope": "isolated laptop training environment; Isaac Sim not imported by this capture",
        "host": {
            "platform": platform.platform(),
            "python": sys.version,
            "executable": sys.executable,
            "cpu_logical": psutil.cpu_count(logical=True),
            "memory_bytes": int(psutil.virtual_memory().total),
            "nvidia_smi": nvidia,
        },
        "packages": package_versions,
        "runtime": {
            "torch": torch.__version__,
            "cuda": cuda_test,
            "onnxruntime": ort.__version__,
            "onnxruntime_providers": ort.get_available_providers(),
            "mujoco": mujoco.__version__,
        },
        "source_pins": {
            "roboparty_train": git_commit(repo / "upstream/roboparty_train"),
            "robolab": git_commit(repo / "upstream/roboparty_train/robolab"),
            "rsl_rl": git_commit(repo / "upstream/roboparty_train/rsl_rl"),
            "isaaclab": git_commit(repo / "h/il"),
        },
        "dependency_check": {
            "returncode": pip_check["returncode"],
            "stdout": pip_check["stdout"],
            "stderr": pip_check["stderr"],
            "known_upstream_fastapi_starlette_conflict_only": known_conflict,
        },
        "legal_gate": {
            "nvidia_omniverse_eula_preaccepted_in_process": eula_preaccepted,
            "status": "AUTHORIZED" if eula_preaccepted else "AWAITING_EXPLICIT_USER_ACCEPTANCE",
            "action_taken": "No Isaac Sim import or EULA acceptance was performed by this script.",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    environment_ok = (
        torch.cuda.is_available()
        and cuda_test.get("finite") is True
        and package_versions["isaacsim"] == "5.1.0.0"
        and package_versions["isaaclab"] is not None
        and package_versions["robolab"] is not None
        and package_versions["rsl-rl-lib"] is not None
        and (pip_check["returncode"] == 0 or known_conflict)
    )
    return 0 if environment_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
