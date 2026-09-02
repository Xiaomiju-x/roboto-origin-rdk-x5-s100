#!/usr/bin/env python3
"""Bounded wrapper around RoboParty's official Parkour MuJoCo/ONNX loop.

The upstream script intentionally defaults to an effectively infinite,
interactive demo.  This wrapper leaves that source untouched, disables keyboard
input, supplies a finite duration, and records machine-readable evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("MPLBACKEND", "Agg")

import mujoco
import numpy as np
import onnxruntime as ort
import torch

if hasattr(ort, "preload_dlls"):
    ort.preload_dlls()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class NullListener:
    def stop(self) -> None:
        return None


class CountingSession:
    def __init__(self, session: ort.InferenceSession):
        self.session = session
        self.calls = 0
        self.all_finite = True
        self.max_abs_output = 0.0

    def get_inputs(self):
        return self.session.get_inputs()

    def run(self, *args, **kwargs):
        outputs = self.session.run(*args, **kwargs)
        self.calls += 1
        for output in outputs:
            array = np.asarray(output)
            self.all_finite = self.all_finite and bool(np.isfinite(array).all())
            if array.size:
                self.max_abs_output = max(self.max_abs_output, float(np.max(np.abs(array))))
        return outputs


def load_upstream_module(path: Path):
    spec = importlib.util.spec_from_file_location("roboparty_parkour_finite", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--scene", choices=("plane", "terrain", "stairs"), default="stairs")
    parser.add_argument("--duration", type=float, default=1.0)
    parser.add_argument("--provider", choices=("CPUExecutionProvider", "CUDAExecutionProvider"), default="CPUExecutionProvider")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    repo = args.repo.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    script_path = repo / "upstream/roboparty_train/robolab/scripts/mujoco/sim2sim_rpo_parkour.py"
    mjcf_dir = repo / "upstream/roboparty_train/robolab/data/robots/roboparty/rpo/mjcf"
    xml_path = mjcf_dir / {"plane": "rpo.xml", "terrain": "rpo_terrain.xml", "stairs": "rpo_stairs.xml"}[args.scene]
    encoder_path = repo / "upstream/roboparty_deploy/src/camera/models/encoder.onnx"
    actor_path = repo / "upstream/roboparty_deploy/src/inference/robots/rpo/models/policy_parkour.onnx"

    payload: dict = {
        "schema_version": 1,
        "scope": "finite official Parkour MuJoCo/ONNX loop; no robot or devices",
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "result": "FAIL",
        "scene": args.scene,
        "requested_duration_s": args.duration,
        "provider": args.provider,
        "sources": {
            "official_script": {"path": script_path.relative_to(repo).as_posix(), "sha256": sha256(script_path)},
            "mjcf": {"path": xml_path.relative_to(repo).as_posix(), "sha256": sha256(xml_path)},
            "encoder": {"path": encoder_path.relative_to(repo).as_posix(), "sha256": sha256(encoder_path)},
            "actor": {"path": actor_path.relative_to(repo).as_posix(), "sha256": sha256(actor_path)},
        },
        "runtime": {"mujoco": mujoco.__version__, "onnxruntime": ort.__version__, "torch": torch.__version__},
    }

    original_cwd = Path.cwd()
    original_mj_step = mujoco.mj_step
    stats = {"mj_step_calls": 0, "all_state_finite": True, "min_base_z": float("inf"), "max_base_z": float("-inf")}

    try:
        module = load_upstream_module(script_path)
        module.start_keyboard_listener = lambda: NullListener()
        module.print_controls_guide = lambda: None

        encoder = CountingSession(ort.InferenceSession(str(encoder_path), providers=[args.provider]))
        actor = CountingSession(ort.InferenceSession(str(actor_path), providers=[args.provider]))
        if encoder.session.get_providers()[0] != args.provider or actor.session.get_providers()[0] != args.provider:
            raise RuntimeError("Requested execution provider was not activated")

        cfg = SimpleNamespace(
            sim_config=SimpleNamespace(
                mujoco_model_path=str(xml_path),
                sim_duration=args.duration,
                dt=0.005,
                decimation=4,
                depth_camera_body="torso_link",
            ),
            robot_config=SimpleNamespace(
                kps=np.array([100, 100, 100, 150, 40, 40, 100, 100, 100, 150, 40, 40, 150, 40, 40, 40, 30, 20, 40, 40, 40, 30, 20], dtype=np.double),
                kds=np.array([3.3, 3.3, 3.3, 5.0, 2.0, 2.0, 3.3, 3.3, 3.3, 5.0, 2.0, 2.0, 5.0, 2.0, 2.0, 2.0, 1.5, 1.0, 2.0, 2.0, 2.0, 1.5, 1.0], dtype=np.double),
                default_pos=np.array([0, 0, -0.1, 0.3, -0.2, 0, 0, 0, -0.1, 0.3, -0.2, 0, 0, 0.18, 0.06, 0, 0.78, 0, 0.18, -0.06, 0, 0.78, 0], dtype=np.double),
                tau_limit=200.0 * np.ones(23, dtype=np.double),
                frame_stack=8,
                num_actions=23,
                action_scale=0.25,
                usd2urdf=[0, 6, 12, 1, 7, 13, 18, 2, 8, 14, 19, 3, 9, 15, 20, 4, 10, 16, 21, 5, 11, 17, 22],
            ),
        )

        def observed_mj_step(model, data, *step_args, **step_kwargs):
            original_mj_step(model, data, *step_args, **step_kwargs)
            stats["mj_step_calls"] += 1
            finite = bool(np.isfinite(data.qpos).all() and np.isfinite(data.qvel).all() and np.isfinite(data.ctrl).all())
            stats["all_state_finite"] = stats["all_state_finite"] and finite
            if data.qpos.size >= 3:
                stats["min_base_z"] = min(stats["min_base_z"], float(data.qpos[2]))
                stats["max_base_z"] = max(stats["max_base_z"], float(data.qpos[2]))

        module.mujoco.mj_step = observed_mj_step
        os.chdir(output_dir)
        start = time.perf_counter()
        module.run_mujoco_onnx(
            encoder,
            actor,
            cfg,
            headless=True,
            debug_obs=False,
            show_depth_vis=False,
            realtime_sync=False,
            quiet=True,
        )
        elapsed = time.perf_counter() - start
        artifacts = []
        for name in ("simulation_parkour.mp4", "joint_positions_parkour.png", "base_velocities_parkour.png"):
            path = output_dir / name
            artifacts.append({"name": name, "exists": path.exists(), "bytes": path.stat().st_size if path.exists() else 0})

        payload.update(
            {
                "elapsed_wall_s": elapsed,
                "physics": stats,
                "inference": {
                    "encoder_calls": encoder.calls,
                    "actor_calls": actor.calls,
                    "encoder_finite": encoder.all_finite,
                    "actor_finite": actor.all_finite,
                    "encoder_max_abs_output": encoder.max_abs_output,
                    "actor_max_abs_output": actor.max_abs_output,
                    "active_encoder_providers": encoder.session.get_providers(),
                    "active_actor_providers": actor.session.get_providers(),
                },
                "artifacts": artifacts,
            }
        )
        passed = (
            stats["all_state_finite"]
            and stats["mj_step_calls"] > 0
            and encoder.calls > 0
            and actor.calls == encoder.calls
            and encoder.all_finite
            and actor.all_finite
            and all(item["exists"] and item["bytes"] > 0 for item in artifacts)
        )
        payload["result"] = "PASS" if passed else "FAIL"
    except Exception as exc:
        payload["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        module_ref = locals().get("module")
        if module_ref is not None:
            module_ref.mujoco.mj_step = original_mj_step
        os.chdir(original_cwd)

    evidence_path = output_dir / "result.json"
    evidence_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": payload["result"], "output": str(evidence_path)}, ensure_ascii=False))
    return 0 if payload["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
